"""Usage: python run_builder.py "add a greet() function in hello.py" """
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError

from agents.builder import build_builder
from llm import get_llm
from sandbox.sandbox import Sandbox
from tools import make_tools
from tools.memory import make_memory_tools
from transcripts import save_transcript

MAX_STEPS = 60  # each model call and each tool run counts as a step
MEMORY_DIR = Path(__file__).parent / "memory"


def repo_name(url: str) -> str:
    """https://github.com/me/my-repo.git -> my-repo (used as the memory folder name)."""
    return url.rstrip("/").split("/")[-1].removesuffix(".git")


def sandbox_context(sb) -> str:
    """Tell the agent what the harness already knows, so it doesn't guess or waste steps."""
    from tools.terminal import BASE_PREFIXES

    langs = ", ".join(p.name for p in sb.profiles) or "none detected"
    tests = ", ".join(cmd for _, cmd in sb.test_commands()) or "none (no tests found)"
    allowed = ", ".join(BASE_PREFIXES + sb.allowed_prefixes())
    return (
        "## Sandbox\n"
        f"Languages: {langs}. Dependencies are already installed.\n"
        f"git_commit runs these tests automatically: {tests}\n"
        f"run_command accepts commands starting with: {allowed}"
    )


def load_memory_context(memory_tools) -> str:
    """Put the memory index and the full org/ rules into the system prompt up front,
    so the agent can't skip reading them."""
    t = {tool.name: tool for tool in memory_tools}
    index = t["memory_list"].invoke({})
    org = [
        t["memory_read"].invoke({"path": line.split(" - ")[0]})
        for line in index.splitlines()
        if line.startswith("org/")
    ]
    return "## Memory index\n" + index + "\n\n## org/ rules\n" + ("\n".join(org) or "(none)")


def short(text, n=300):
    text = str(text)
    return text if len(text) <= n else text[:n] + " ..."


def main():
    load_dotenv()
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python run_builder.py "what to build"')
    request = sys.argv[1]

    sb = Sandbox(os.environ["REPO_URL"])
    messages = [HumanMessage(request)]
    try:
        memory_tools = make_memory_tools(MEMORY_DIR, repo_name(os.environ["REPO_URL"]))
        context = sandbox_context(sb) + "\n\n" + load_memory_context(memory_tools)
        graph = build_builder(get_llm(), make_tools(sb), memory_tools, context)
        try:
            # stream_mode="values" yields the full state after each step.
            for state in graph.stream(
                {"messages": messages},
                config={"recursion_limit": MAX_STEPS},
                stream_mode="values",
            ):
                # One step can add several messages (the model may call two tools at once,
                # and ToolNode then returns two results), so print every new message.
                new_msgs = state["messages"][len(messages):]
                messages = state["messages"]
                for m in new_msgs:
                    if m.type == "ai":
                        for tc in m.tool_calls:
                            print(f"\n[builder] {tc['name']}({short(tc['args'], 200)})")
                        if not m.tool_calls:
                            print(f"\n[builder] done:\n{m.content}")
                    elif m.type == "human":  # only the harness adds human messages mid-run
                        print("\n[harness] task done -> reflect phase (saving memory)")
                    elif m.type == "tool":
                        print(f"  -> {m.name}: {short(m.content)}")
        except GraphRecursionError:
            print(f"\n[stopped] hit the {MAX_STEPS}-step limit")

        print("\n--- result ---")
        print(sb.run("git log --oneline --all -5")["stdout"])
        print(f"repo copy kept at: {sb.workdir}")
    finally:
        path = save_transcript(sb.session_id, "builder", request, messages)
        print(f"transcript: {path}")
        sb.close()


if __name__ == "__main__":
    main()
