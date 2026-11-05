"""The loop every agent runs: model -> tool -> model ... until it calls `finish`.

The harness, not the prompt, decides when an agent is done: it must call `finish` with a
verdict. If it stops without one, it gets nudged; if it still won't, the run ends with no verdict.
"""
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from llm import invoke_with_retry

MAX_STEPS = 60  # each model call and each tool run counts as a step
MAX_NUDGES = 2
NUDGE = "[harness] You stopped without calling finish. Continue the task, then call finish with your verdict."


def make_finish_tool(verdicts: tuple[str, ...]):
    def finish(verdict: str, summary: str) -> str:
        if verdict not in verdicts:
            return f"error: verdict must be one of: {', '.join(verdicts)}"
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


def make_agent(llm, tools, system_prompt: str, verdicts: tuple[str, ...]):
    tools = tools + [make_finish_tool(verdicts)]
    llm_t = llm.bind_tools(tools, parallel_tool_calls=False)
    system = SystemMessage(system_prompt)

    def agent(state: MessagesState):
        msg = invoke_with_retry(llm_t, [system] + state["messages"])
        return {"messages": [one_tool_call(msg)]}

    def nudge(state: MessagesState):
        return {"messages": [HumanMessage(NUDGE)]}

    def after_agent(state: MessagesState):
        if state["messages"][-1].tool_calls:
            return "tools"
        nudges = sum(1 for m in state["messages"] if m.type == "human" and m.content == NUDGE)
        return "nudge" if nudges < MAX_NUDGES else END

    def after_tools(state: MessagesState):
        last = state["messages"][-1]
        return END if (last.name == "finish" and last.content == "recorded") else "agent"

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(tools))
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
