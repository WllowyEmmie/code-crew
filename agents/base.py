"""The loop every agent runs: model -> tool -> model ... until it calls `finish`.

The harness, not the prompt, decides when an agent is done: it must call `finish` with a
verdict. If it stops without one, it gets nudged; if it still won't, the run ends with no verdict.
"""
import json
import re
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from llm import invoke_with_retry

import os

MAX_STEPS = int(os.getenv("CREW_MAX_STEPS", "100"))  # each model call and each tool run is a step
BUDGET_WARN = 12  # steps left when the harness starts telling the agent to wrap up
BUDGET_NOTE = ("[harness] Step budget: about {left} steps left before you are stopped, and a stopped "
               "agent counts as a failed session. Stop exploring: record anything important "
               "(memory_write / record_finding) in at most one or two calls, then call finish with "
               "your verdict and what you checked.")
MAX_NUDGES = 2
NUDGE = "[harness] You stopped without calling finish. Continue the task, then call finish with your verdict."
TEXT_CALL_NUDGE = ("[harness] Your last reply contained a tool call written out as plain text, so nothing ran. "
                   "Call the tool through the tool-calling interface, not as text in your message.")
RESAMPLE_NUDGE = ("[harness] Your last reply had no tool call, so it was discarded. Every reply must be "
                  "a tool call. If your work is complete, that call is finish(verdict, summary): put "
                  "what you would have written into the summary. Otherwise call the next tool you need.")
TEXT_CALL = re.compile(r'function_call|"name"\s*:\s*"\w+"\s*,\s*"(args|arguments)"|(.{1,4})\2{40,}')

# Harness refusals: each means an agent made a mistake that future sessions could avoid.
FAILURE_PREFIXES = (
    "error: tests failed",
    "error: code doesn't meet the standard style",
    "error: nothing committed",
    "error: this edit repeats or undoes",
    "error: the work isn't done",
    "error: you haven't committed any tests",
    "error: you may not change",
)
LEARN_HINT = ("\n\n[harness] If this mistake teaches something future sessions should know, record it "
              "now with record_finding (what went wrong, how to avoid it).")
ASK_MARKER = "[harness] Before you finish:"
FAIL_LINE = re.compile(r"(FAILED|FAIL:|--- FAIL|\.\.\. FAILED|panicked at|error\[E\d+\]|AssertionError|✕)")
SAME_FAILURE_LIMIT = 3
STEP_BACK = ("\n\n[harness] The same failure has now come back {n} times in a row after different "
             "edits: you're probably changing the wrong part of the code. Stop editing. Read the full "
             "output above (expected vs actual), then trace that one test case by hand through EVERY "
             "step of the code, not just the part you've been changing. Also re-read any compiler "
             "warnings: they often point straight at the bug.")


def failure_signature(content: str) -> str:
    return "\n".join(sorted({l.strip() for l in content.splitlines() if FAIL_LINE.search(l)}))


def make_finish_tool(verdicts: tuple[str, ...], validate=None):
    """validate(verdict, summary) -> error message or None lets the harness check a claim
    (e.g. "pass" really added tests) before accepting it."""
    def finish(verdict: str, summary: str) -> str:
        if verdict not in verdicts:
            return f"error: verdict must be one of: {', '.join(verdicts)}"
        if validate:
            err = validate(verdict, summary)
            if err:
                return f"error: {err}"
        return "recorded"

    return StructuredTool.from_function(
        finish,
        name="finish",
        description=(
            f"Call this exactly once, when your work is done. verdict must be one of: "
            f"{', '.join(verdicts)}. summary: what you did, and for a negative verdict, "
            f"exactly what is wrong and what must change."
        ),
    )


def one_tool_call(msg):
    """parallel_tool_calls=False is a request the provider can ignore (DeepSeek on
    OpenRouter does), so enforce it here: keep only the first tool call."""
    if len(msg.tool_calls) > 1:
        msg.tool_calls = msg.tool_calls[:1]
        raw = msg.additional_kwargs.get("tool_calls")
        if raw:
            msg.additional_kwargs["tool_calls"] = raw[:1]
    return msg


RESAMPLES = 2  # extra tries when a reply has no usable tool call (bad replies are discarded)
# Shared by every agent: flips to False the first time the provider rejects tool_choice="required".
TOOL_CHOICE = {"required": os.getenv("CREW_TOOL_CHOICE", "required") != "auto"}


def unsupported_tool_choice(e: Exception) -> bool:
    return "tool_choice" in str(e) and ("No endpoints" in str(e) or "not support" in str(e).lower())


def repair_text_call(content: str, tool_names: set[str]) -> dict | None:
    """Recover a tool call the model wrote out as JSON text instead of actually calling it,
    e.g. {"name": "write_file", "arguments": {...}} or {"function": {"name": ..., "arguments": "..."}}."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(content):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(content[i:])
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        fn = obj.get("function") if isinstance(obj.get("function"), dict) else obj
        name = fn.get("name")
        args = fn.get("arguments", fn.get("args", fn.get("parameters", {})))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                continue
        if name in tool_names and isinstance(args, dict):
            return {"name": name, "args": args, "id": f"repaired_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
    return None


def is_failure(content: str) -> bool:
    return content.startswith(FAILURE_PREFIXES)


def make_agent(llm, tools, system_prompt: str, verdicts: tuple[str, ...], validate=None,
               learn_from_failures: bool = False):
    """learn_from_failures: when the harness refuses something, remind the agent it can record a
    lesson; and before accepting finish, if the agent hit failures but wrote no memory, ask once
    for a lesson or a reason not to (the agent decides, but can't silently skip it)."""
    tools = tools + [make_finish_tool(verdicts, validate)]
    tool_names = {t.name for t in tools}
    # tool_choice="required": every reply MUST be a tool call. Ending is still possible because
    # finish is a tool, but replying in prose (instead of calling finish or a tool) isn't.
    # If no provider supports "required" (DeepSeek on OpenRouter doesn't), the first call fails
    # and every agent falls back to "auto"; repair/retry/nudge then catch prose replies.
    llm_required = llm.bind_tools(tools, parallel_tool_calls=False, tool_choice="required")
    llm_auto = llm.bind_tools(tools, parallel_tool_calls=False)

    def call(messages):
        if TOOL_CHOICE["required"]:
            try:
                return invoke_with_retry(llm_required, messages)
            except Exception as e:
                if not unsupported_tool_choice(e):
                    raise
                TOOL_CHOICE["required"] = False
                print("  [harness] this model's provider doesn't support tool_choice='required'; "
                      "using 'auto' (text replies are repaired or retried instead)")
        return invoke_with_retry(llm_auto, messages)
    system = SystemMessage(system_prompt)
    tool_node = ToolNode(tools)

    def steps_left(msgs) -> int:
        # Every node run (agent, tools, nudge) adds exactly one message and is one graph step,
        # so the messages after the opening instructions count the steps used so far.
        opening = next((i for i, m in enumerate(msgs) if m.type != "human"), len(msgs))
        return MAX_STEPS - (len(msgs) - opening)

    def agent(state: MessagesState):
        history = [system] + state["messages"]
        left = steps_left(state["messages"])
        if left <= BUDGET_WARN:  # shown on every call near the end, not stored in the history
            if left in (BUDGET_WARN, BUDGET_WARN - 1):
                print(f"  [harness] {left} steps left: told the agent to wrap up and finish")
            history = history + [HumanMessage(BUDGET_NOTE.format(left=max(left - 1, 1)))]
        msg = call(history)
        for attempt in range(RESAMPLES + 1):
            if msg.tool_calls:
                break
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            repaired = repair_text_call(text, tool_names)
            if repaired:  # a real call written as text: run it as a real call
                print(f"  [harness] repaired a tool call written as text: {repaired['name']}")
                msg.tool_calls = [repaired]
                msg.content = ""
                break
            if attempt == RESAMPLES:
                break  # give up: keep it, and the nudge path takes over
            # Unusable reply (prose or garbage): discard it instead of adding it to the history,
            # where the model would keep seeing it, and ask again with a reminder.
            print(f"  [harness] reply had no tool call; discarded it and asked again: {short(text, 160)!r}")
            nudge_text = TEXT_CALL_NUDGE if TEXT_CALL.search(text) else RESAMPLE_NUDGE
            msg = call(history + [HumanMessage(nudge_text)])
        return {"messages": [one_tool_call(msg)]}

    def nudge(state: MessagesState):
        last = state["messages"][-1]
        # A tool call written out as text (or a runaway repeating reply) needs a specific nudge.
        text = last.content if isinstance(last.content, str) else str(last.content)
        return {"messages": [HumanMessage(TEXT_CALL_NUDGE if TEXT_CALL.search(text) else NUDGE)]}

    def tools_step(state: MessagesState):
        out = tool_node.invoke(state)
        if not learn_from_failures:
            return out
        history = state["messages"]
        for m in out["messages"]:
            content = str(m.content)
            if is_failure(content):
                m.content = content + LEARN_HINT
            elif m.name == "run_command" and content.startswith("exit_code:") and not content.startswith("exit_code: 0"):
                sig = failure_signature(content)
                if sig:
                    previous = [failure_signature(str(h.content)) for h in history
                                if h.type == "tool" and h.name == "run_command"
                                and str(h.content).startswith("exit_code:")]
                    streak = 1
                    for s in reversed(previous):
                        if s != sig:
                            break
                        streak += 1
                    if streak >= SAME_FAILURE_LIMIT:
                        m.content = content + STEP_BACK.format(n=streak)
            elif m.name == "finish" and content == "recorded":
                failures = [str(h.content).split("\n")[0][:120] for h in history
                            if h.type == "tool" and is_failure(str(h.content))]
                wrote = any(h.type == "tool" and ((h.name == "memory_write" and str(h.content).startswith("saved"))
                                                  or (h.name == "record_finding" and str(h.content).startswith("recorded")))
                            for h in history)
                asked = any(h.type == "tool" and str(h.content).startswith("error: " + ASK_MARKER)
                            for h in history)
                if failures and not wrote and not asked:
                    m.content = ("error: " + ASK_MARKER + " this session the harness refused you "
                                 f"{len(failures)} time(s), e.g. " + " | ".join(dict.fromkeys(failures))[:500]
                                 + ". Record what future sessions should learn with memory_write, or "
                                 "say why not by including 'Memory: no lesson, because ...' in your "
                                 "summary. Then call finish again.")
        return out

    def after_agent(state: MessagesState):
        if state["messages"][-1].tool_calls:
            return "tools"
        nudges = sum(1 for m in state["messages"] if m.type == "human" and str(m.content).startswith("[harness]"))
        return "nudge" if nudges < MAX_NUDGES else END

    def after_tools(state: MessagesState):
        last = state["messages"][-1]
        return END if (last.name == "finish" and last.content == "recorded") else "agent"

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_node("tools", tools_step)
    g.add_node("nudge", nudge)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", after_agent, {"tools": "tools", "nudge": "nudge", END: END})
    g.add_conditional_edges("tools", after_tools, {"agent": "agent", END: END})
    g.add_edge("nudge", "agent")
    return g.compile()


def short(text, n=300):
    text = str(text)
    return text if len(text) <= n else text[:n] + " ..."


def run_agent(graph, messages: list, label: str) -> list:
    """Run one agent to completion, printing each step. Returns its full message list."""
    print(f"\n========== {label} ==========")
    try:
        for state in graph.stream({"messages": messages}, config={"recursion_limit": MAX_STEPS},
                                  stream_mode="values"):
            new_msgs = state["messages"][len(messages):]
            messages = state["messages"]
            for m in new_msgs:
                if m.type == "ai":
                    for tc in m.tool_calls:
                        if tc["name"] == "finish":
                            print(f"\n[{label}] FINISH {tc['args'].get('verdict')}: {tc['args'].get('summary')}")
                        else:
                            print(f"\n[{label}] {tc['name']}({short(tc['args'], 200)})")
                    if not m.tool_calls and m.content:
                        print(f"\n[{label}] {short(m.content, 500)}")
                elif m.type == "tool" and m.name != "finish":
                    print(f"  -> {m.name}: {short(m.content)}")
                elif m.type == "tool" and m.content != "recorded":
                    print(f"  -> finish REJECTED by harness: {short(m.content)}")
                elif m.type == "human":
                    print(f"\n{m.content}")
    except GraphRecursionError:
        print(f"\n[{label}] stopped: hit the {MAX_STEPS}-step limit")
    return messages


def get_verdict(messages: list) -> tuple[str | None, str]:
    """The (verdict, summary) from the agent's last accepted finish call."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.type == "tool" and m.name == "finish" and m.content == "recorded":
            call_id = m.tool_call_id
            for a in reversed(messages[:i]):
                for tc in getattr(a, "tool_calls", None) or []:
                    if tc["id"] == call_id:
                        return tc["args"].get("verdict"), tc["args"].get("summary", "")
    return None, ""
