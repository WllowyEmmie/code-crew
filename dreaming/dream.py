"""Dreaming: an out-of-band batch pass with a single objective, curating memory.

Follows the "Inside a dreaming pass" design:
  1. clone the input memory store ($MEM) into an output store ($MEM_OUT)
  2. an orchestrator starts one subagent per session transcript (LangGraph Send, in parallel)
  3. subagents read/write $MEM_OUT to verify, organize and enrich it
Then a human checks the diff and promotes $MEM_OUT to live memory (or rolls a promotion back).
"""
import difflib
import json
import operator
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agents.base import get_verdict, make_agent, run_agent
from tools.memory import make_memory_tools
from tools.conventions_check import conventions_error
from tools.memory_store import MemoryStore

from .condense import condense, load
from .prompts import ORGANIZER, SUBAGENT

ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT / "memory"
TRANSCRIPT_DIR = ROOT / "transcripts"
RUNS_DIR = Path(__file__).resolve().parent / "runs"
STATE_FILE = Path(__file__).resolve().parent / "state.json"
MAX_PARALLEL = 4  # subagents at once; keeps OpenRouter rate limits happy


# ---------- bookkeeping ----------
def load_state() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {"dreamed": []}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def pending_transcripts(repo: str, include_dreamed: bool = False, include_evals: bool = False) -> list[Path]:
    done = set(load_state()["dreamed"])
    out = []
    for p in sorted(TRANSCRIPT_DIR.glob("sess_*.jsonl"), key=lambda p: p.stat().st_mtime):
        header, msgs = load(p)
        if header.get("repo", repo) != repo or not msgs:  # older transcripts have no repo field
            continue
        if header.get("kind") == "eval" and not include_evals:
            continue  # learning from eval sessions would teach memory the exam answers
        if include_dreamed or header["session_id"] not in done:
            out.append(p)
    return out


def sessions_awaiting_review() -> set[str]:
    """Sessions already in a dreaming run that hasn't been promoted yet."""
    out = set()
    for run in RUNS_DIR.glob("dream_*"):
        if (run / "run.json").exists() and not (run / "promoted.json").exists():
            out |= set(json.loads((run / "run.json").read_text(encoding="utf-8"))["sessions"])
    return out


def new_sessions(repo: str) -> list[Path]:
    """Sessions no dreaming run has seen yet, for the automatic trigger. Sessions sitting in an
    unpromoted run are left out, so an unreviewed run doesn't re-trigger dreaming after every
    task (a manual `run_dream.py` still includes them)."""
    waiting = sessions_awaiting_review()
    return [p for p in pending_transcripts(repo) if p.stem not in waiting]


def clone_memory(repo: str, out_root: Path):
    """$MEM -> $MEM_OUT: copy org/ and this repo's team/ notes (not the live history)."""
    shutil.copytree(MEMORY_DIR / "org", out_root / "org")
    shutil.copytree(MEMORY_DIR / "team" / repo, out_root / "team" / repo)


# ---------- the dreaming graph ----------
# File names with a code/config extension, e.g. text_stats.py, src/App.tsx, go.mod
CODE_PATH = re.compile(
    r"(?<![\w/.-])([\w./-]+\.(?:py|go|mod|ts|tsx|js|jsx|rs|rb|java|kt|cs|php|swift|dart|ex|exs|"
    r"c|cc|cpp|h|hpp|scala|toml|json|ya?ml|cfg|ini|sh))\b")
CITATION = re.compile(r"\((sess_[0-9a-f]{12})")


def make_write_check(out_store: MemoryStore, known_sessions: set[str]):
    """Verify, in the harness, what a dreaming subagent is about to write. Every line it adds or
    changes must (1) cite a real session as provenance and (2) only name files that really
    exist on the base branch (per repo-facts). This is what stops stale notes, such as files
    from unmerged branches, being copied back into memory."""
    facts = out_store.read("team/repo-facts.md")[0] if out_store.file("team/repo-facts.md").exists() else ""
    tracked = {l.strip()[2:] for l in facts.splitlines() if l.startswith("  - ")}

    def check(path: str, content: str) -> str | None:
        tag_error = conventions_error(path, content)
        if tag_error:
            return "write refused by harness verification: " + tag_error
        old = out_store.read(path)[0] if out_store.file(path).exists() else ""
        old_lines = {l.strip() for l in old.splitlines()}
        body = content.replace("\r\n", "\n").split("\n---", 1)[-1]  # skip frontmatter
        problems = []
        for line in (l.strip() for l in body.splitlines()):
            if not line or line == "---" or line in old_lines:
                continue  # unchanged lines were already checked when written
            cited = CITATION.findall(line)
            if not cited:
                problems.append(f'"{line[:60]}" has no provenance: end it with (sess_<id>: source)')
            elif not set(cited) <= known_sessions:
                problems.append(f'"{line[:60]}" cites an unknown session {set(cited) - known_sessions}')
            for f in CODE_PATH.findall(line):
                if tracked and f not in tracked and f.split("/")[-1] not in {t.split("/")[-1] for t in tracked}:
                    problems.append(f'"{line[:60]}" names {f}, which is not in the repo '
                                    f'(repo-facts lists the real files). Notes must match the repo.')
        if problems:
            return "write refused by harness verification:\n- " + "\n- ".join(problems[:6])
        return None

    return check


class DreamState(TypedDict, total=False):
    transcripts: list[str]
    results: Annotated[list, operator.add]  # each subagent appends its outcome


class SubagentInput(TypedDict):
    transcript: str


def build_dream_graph(llm, out_store: MemoryStore, run_id: str, known_sessions: set[str]):
    facts = out_store.read("team/repo-facts.md")[0] if out_store.file("team/repo-facts.md").exists() else "(none)"
    org = "\n\n".join(out_store.read(p)[0] for p, _ in out_store.files() if p.startswith("org/")) or "(none)"
    system = SUBAGENT.format(org=org, facts=facts)
    write_check = make_write_check(out_store, known_sessions)

    def orchestrator(state: DreamState):
        print(f"[dreaming] orchestrator: {len(state['transcripts'])} session(s) -> one subagent each")
        return {}

    def fan_out(state: DreamState):
        return [Send("subagent", {"transcript": t}) for t in state["transcripts"]]

    def subagent(inp: SubagentInput):
        path = Path(inp["transcript"])
        session = path.stem
        # Each subagent's writes are attributed to it, so $MEM_OUT's history shows who changed what.
        tools = make_memory_tools(out_store, author=f"dream:{session}", session=run_id, can_delete=True,
                                  write_check=write_check)
        agent = make_agent(llm, tools, system, ("updated", "nothing"))
        msgs = run_agent(agent, [HumanMessage(condense(path))], f"dream {session}")
        verdict, summary = get_verdict(msgs)
        return {"results": [{"session": session, "verdict": verdict, "summary": summary}]}

    def organizer(state: DreamState):
        """One final pass over the whole merged copy. Parallel subagents each see one session,
        so nobody folds together near-duplicates written by different subagents. The organizer
        may only reorganize: the harness lets it cite only sessions already cited in memory,
        so it can merge and reword but can't introduce new claims."""
        cited = set()
        for p, _ in out_store.files():
            cited |= set(CITATION.findall(out_store.read(p)[0]))
        tools = make_memory_tools(out_store, author="dream:organizer", session=run_id, can_delete=True,
                                  write_check=make_write_check(out_store, cited))
        agent = make_agent(llm, tools, ORGANIZER.format(org=org, facts=facts), ("organized", "nothing"))
        msgs = run_agent(agent, [HumanMessage(
            "The per-session subagents are done. Organize the whole team/ memory now.")], "dream organizer")
        verdict, summary = get_verdict(msgs)
        return {"results": [{"session": "organizer", "verdict": verdict, "summary": summary}]}

    g = StateGraph(DreamState)
    g.add_node("orchestrator", orchestrator)
    g.add_node("subagent", subagent)
    g.add_node("organizer", organizer)
    g.add_edge(START, "orchestrator")
    g.add_conditional_edges("orchestrator", fan_out, ["subagent"])
    g.add_edge("subagent", "organizer")   # runs once, after every subagent has finished
    g.add_edge("organizer", END)
    return g.compile()


# ---------- diff / report ----------
def team_files(root: Path, repo: str) -> dict[str, str]:
    folder = root / "team" / repo
    return {f"team/{f.name}": f.read_text(encoding="utf-8") for f in sorted(folder.glob("*.md"))}


def memory_diff(repo: str, out_root: Path) -> str:
    live, out = team_files(MEMORY_DIR, repo), team_files(out_root, repo)
    chunks = []
    for path in sorted(set(live) | set(out)):
        a, b = live.get(path, ""), out.get(path, "")
        if a != b:
            chunks.append("".join(difflib.unified_diff(
                a.splitlines(keepends=True), b.splitlines(keepends=True),
                fromfile=f"live/{path}", tofile=f"dreamed/{path}")))
    return "\n".join(chunks)


def dream(llm, repo: str, include_dreamed: bool = False, include_evals: bool = False) -> Path | None:
    transcripts = pending_transcripts(repo, include_dreamed, include_evals)
    if not transcripts:
        print("[dreaming] no new sessions to dream about")
        return None
    run_id = datetime.now().strftime("dream_%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / run_id
    out_root = run_dir / "memory"
    clone_memory(repo, out_root)
    out_store = MemoryStore(out_root, repo)

    known = {p.stem for p in TRANSCRIPT_DIR.glob("sess_*.jsonl")}
    graph = build_dream_graph(llm, out_store, run_id, known)
    state = graph.invoke({"transcripts": [str(t) for t in transcripts], "results": []},
                         config={"max_concurrency": MAX_PARALLEL, "recursion_limit": 100})

    diff = memory_diff(repo, out_root)
    sessions = [t.stem for t in transcripts]
    (run_dir / "run.json").write_text(json.dumps(
        {"run_id": run_id, "repo": repo, "sessions": sessions, "results": state["results"]},
        indent=2), encoding="utf-8")
    (run_dir / "memory.diff").write_text(diff, encoding="utf-8")
    report = [f"# Dreaming run {run_id}", f"Repo: {repo}", f"Sessions: {len(sessions)}", ""]
    for r in state["results"]:
        report.append(f"- {r['session']}: {r['verdict']} - {r['summary']}")
    report += ["", "## Memory diff (live -> dreamed)", "```diff", diff or "(no changes)", "```"]
    (run_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return run_dir


# ---------- promote / rollback (versioned, attributed to "dreaming") ----------
def promote(run_id: str):
    run_dir = RUNS_DIR / run_id
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    repo = meta["repo"]
    live = MemoryStore(MEMORY_DIR, repo)
    before, after = team_files(MEMORY_DIR, repo), team_files(run_dir / "memory", repo)
    changes = []
    for path in sorted(set(before) | set(after)):
        if path == "team/repo-facts.md" or before.get(path) == after.get(path):
            continue
        current_sha = live.read(path)[1] if path in before else "new"
        if path in after:
            live.write(path, after[path], current_sha, "dreaming", run_id, "promoted dreaming run")
        else:
            live.delete(path, current_sha, "dreaming", run_id, "promoted dreaming run")
        changes.append({"path": path, "before": before.get(path)})
    (run_dir / "promoted.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")
    state = load_state()
    state["dreamed"] = sorted(set(state["dreamed"]) | set(meta["sessions"]))
    save_state(state)
    print(f"[dreaming] promoted {len(changes)} file change(s) from {run_id}")


def rollback(run_id: str):
    run_dir = RUNS_DIR / run_id
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    live = MemoryStore(MEMORY_DIR, meta["repo"])
    changes = json.loads((run_dir / "promoted.json").read_text(encoding="utf-8"))
    for c in changes:
        path, old = c["path"], c["before"]
        exists = live.file(path).exists()
        sha = live.read(path)[1] if exists else "new"
        if old is None and exists:
            live.delete(path, sha, "rollback", run_id, f"rolled back {run_id}")
        elif old is not None:
            live.write(path, old, sha, "rollback", run_id, f"rolled back {run_id}")
    state = load_state()
    state["dreamed"] = sorted(set(state["dreamed"]) - set(meta["sessions"]))
    save_state(state)
    print(f"[dreaming] rolled back {len(changes)} file change(s) from {run_id}")
