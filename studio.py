"""The crew as a graph for LangGraph Studio:  langgraph dev --allow-blocking

Studio shows the same builder -> test-writer -> reviewer graph as run_crew.py, with a setup
step before it (sandbox, memory, agents) and a wrap-up step after it (publish, transcript,
close the sandbox). Start a run from Studio's input form with a request (and optionally a
branch to continue or extra languages); each node's output in Studio includes that agent's
full conversation. Detailed agent steps also print in the `langgraph dev` terminal.

The sandbox can't live in the graph state (it's a live Docker session), so setup keeps it in
SESSIONS under the session id and every node looks it up there.
"""
import os
import sys
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent))  # so `agents`, `tools`, ... import

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from agents.crew import CrewState, add_crew, build_crew_parts
from llm import get_llm
from run_crew import end_session, start_session

load_dotenv(Path(__file__).resolve().parent / ".env")

SESSIONS: dict[str, dict] = {}  # session id -> {"sb", "nodes", "routes", "request"}


class StudioInput(TypedDict, total=False):
    """What Studio's input form asks for."""
    request: str           # what the crew should do (required)
    branch: str            # optional: continue this code-crew/ branch (like --branch)
    langs: list            # optional: languages the repo doesn't use yet (like --lang rust)


class StudioState(CrewState, StudioInput, total=False):
    session_id: str
    pushed_branch: str
    pr_url: str
    commits: str
    transcript: str


def setup(state: StudioState):
    if not (state.get("request") or "").strip():
        raise ValueError("give the crew a request")
    langs = [l.strip().lower() for v in (state.get("langs") or []) for l in str(v).split(",") if l.strip()]
    sb, store, context = start_session(os.environ["REPO_URL"], state.get("branch") or None, langs or None)
    try:
        nodes, routes = build_crew_parts(get_llm(), sb, store, context)
    except BaseException:
        sb.close()
        raise
    SESSIONS[sb.session_id] = {"sb": sb, "nodes": nodes, "routes": routes, "request": state["request"]}
    print(f"[studio] session {sb.session_id} started")
    return {"session_id": sb.session_id, "status": "running", "round": 0, "conversations": {}}


def _abort(state: StudioState):
    """A node crashed: still save the transcript and close the sandbox (nothing is published)."""
    s = SESSIONS.pop(state.get("session_id", ""), None)
    if s:
        end_session(s["sb"], {**state, "status": "failed: crashed"}, s["request"],
                    os.environ["REPO_URL"], publish_it=False)


def crew_node(name: str):
    def node(state: StudioState):
        try:
            return SESSIONS[state["session_id"]]["nodes"][name](state)
        except BaseException:
            _abort(state)
            raise
    node.__name__ = name
    return node


def crew_route(name: str):
    def route(state: StudioState):
        return SESSIONS[state["session_id"]]["routes"][name](state)
    route.__name__ = f"after_{name}"
    return route


def wrap_up(state: StudioState):
    s = SESSIONS.pop(state["session_id"])
    result = end_session(s["sb"], state, s["request"], os.environ["REPO_URL"])
    return {"pushed_branch": result.get("branch", "") if (result.get("published") or result.get("draft")) else "",
            "pr_url": result.get("pr_url", ""), "commits": result.get("commits", ""),
            "transcript": result.get("transcript", "")}


g = StateGraph(StudioState, input_schema=StudioInput)
g.add_node("setup", setup)
g.add_node("wrap_up", wrap_up)
g.add_edge(START, "setup")
add_crew(g,
         {n: crew_node(n) for n in ("builder", "test_writer", "reviewer", "give_up")},
         {n: crew_route(n) for n in ("builder", "test_writer", "reviewer")},
         start="setup", end="wrap_up")
g.add_edge("wrap_up", END)
graph = g.compile()
