from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from llm import invoke_with_retry

SYSTEM_PROMPT = """You are the builder, a coding agent working inside a git repository.
You make the change the user asks for, then commit it.

You have a memory that lasts between sessions. Its index and the org/ rules are below.
- org/ rules come from your human. Follow them.
- team/ notes were written by agents in earlier sessions. memory_read the ones relevant to
  this task before exploring; check them against the code if they look stale.

How to work:
1. Explore: use list_dir, grep and read_file to fill in what memory doesn't tell you.
   The repo may be empty; that's fine.
2. Call create_branch with a short name like agent/add-greet before editing.
3. Make the change with write_file (new files only) or edit_file (existing files).
   Read a file before editing it.
4. If the repo has tests, run them with run_command. git_commit also runs them and refuses
   to commit if they fail; fix any failures you caused.
5. Call git_diff and check the change is correct, complete and follows the org/ rules.
6. Call git_commit with a clear message.
7. Finish with a short summary: what you changed, which files, and whether tests passed.

Rules:
- Paths are relative to the repo root.
- There is no network: you can't install packages or download anything.
- run_command only accepts allowlisted commands, one at a time (no &&, |, ;, >).
- If a tool returns "error: ...", read it and adjust; don't repeat the same call.
- Keep changes focused on the request. Don't refactor unrelated code.

{memory_context}
"""

REFLECT_PROMPT = """The task is finished. Now update team/ memory for future sessions.

Check the memory index. These files should exist and be accurate for this repo:
- team/repo-layout.md: the important files/folders and what each contains
- team/testing.md: how tests are run, where test files live, and any test gotchas
- team/conventions.md: style the code follows (naming, docstrings, line endings, structure)
Create any that are missing, and fix any that are now wrong (memory_read, then memory_write the full file).
Also save any gotcha you hit this session (an error, a wrong assumption) so it isn't repeated.

Don't record the task itself ("added farewell()"); git history has that.
Only reply "nothing to save" if all three files already exist and are still accurate.
When done, reply with one line listing the files you saved."""


def build_builder(llm, repo_tools, memory_tools, memory_context: str):
    """Two phases, enforced by the graph rather than the prompt:
    1. build:   model <-> all tools, until it replies without a tool call
    2. reflect: model <-> memory tools only, to save what it learned
    """
    system = SystemMessage(SYSTEM_PROMPT.format(memory_context=memory_context))
    # parallel_tool_calls=False: one tool per reply, so the model sees git_diff's output
    # before deciding to git_commit.
    build_llm = llm.bind_tools(repo_tools + memory_tools, parallel_tool_calls=False)
    reflect_llm = llm.bind_tools(memory_tools, parallel_tool_calls=False)

    def one_tool_call(msg):
        """parallel_tool_calls=False is a request the provider can ignore (DeepSeek on
        OpenRouter does), so enforce it here: keep only the first tool call."""
        if len(msg.tool_calls) > 1:
            msg.tool_calls = msg.tool_calls[:1]
            raw = msg.additional_kwargs.get("tool_calls")
            if raw:
                msg.additional_kwargs["tool_calls"] = raw[:1]
        return msg

    def agent(state: MessagesState):
        msg = invoke_with_retry(build_llm, [system] + state["messages"])
        return {"messages": [one_tool_call(msg)]}

    def start_reflect(state: MessagesState):
        return {"messages": [HumanMessage(REFLECT_PROMPT)]}

    def reflect(state: MessagesState):
        msg = invoke_with_retry(reflect_llm, [system] + state["messages"])
        return {"messages": [one_tool_call(msg)]}

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(repo_tools + memory_tools))
    g.add_node("start_reflect", start_reflect)
    g.add_node("reflect", reflect)
    g.add_node("memory_tools", ToolNode(memory_tools))

    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: "start_reflect"})
    g.add_edge("tools", "agent")
    g.add_edge("start_reflect", "reflect")
    g.add_conditional_edges("reflect", tools_condition, {"tools": "memory_tools", END: END})
    g.add_edge("memory_tools", "reflect")
    return g.compile()
