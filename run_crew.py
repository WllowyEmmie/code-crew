"""Usage: python run_crew.py "add a shout() function in hello.py" """
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from agents.crew import build_crew
from llm import get_llm
from sandbox.sandbox import Sandbox
from tools.facts import build_repo_facts
from tools.memory_store import MemoryStore
from tools.publish import publish
from tools.terminal import BASE_PREFIXES
from transcripts import save_transcript

MEMORY_DIR = Path(__file__).parent / "memory"


def repo_name(url: str) -> str:
    """https://github.com/me/my-repo.git -> my-repo (used as the memory folder name)."""
    return url.rstrip("/").split("/")[-1].removesuffix(".git")


def sandbox_context(sb) -> str:
    """Tell the agents what the harness already knows, so they don't guess or waste steps."""
    langs = ", ".join(p.name for p in sb.profiles) or "none detected"
    tests = ", ".join(cmd for _, cmd in sb.test_commands()) or "none (no tests found)"
    lint = "\n".join(f"  - {c}" for c in sb.lint_summary()) or "  (none for this repo)"
    allowed = ", ".join(BASE_PREFIXES + sb.allowed_prefixes())
    return (
        "## Sandbox\n"
        f"Languages: {langs}. Dependencies are already installed. Base branch: {sb.base_branch}.\n"
        + (f"You are CONTINUING the existing branch {sb.continuing}, an open pull request: its earlier "
           f"commits are already here. Don't create a branch; make the requested changes and commit "
           f"on this one. git_diff_base shows the whole pull request against {sb.base_branch}.\n"
           if sb.continuing else "")
        + (f"New languages for this task (not in the repo yet): {', '.join(sorted(sb.added_langs))}. "
           f"Create the project files the toolchain needs, at the repo root (e.g. Cargo.toml and "
           f"src/lib.rs for Rust, go.mod for Go, package.json for Node). There is no network: use "
           f"only the standard library, no external packages. The checks below apply to this code too.\n"
           if sb.added_langs else "")
        + f"git_commit runs these tests automatically: {tests}\n"
        f"git_commit also enforces the language's standard style with these checks, and refuses\n"
        f"to commit until they pass. Write code that follows the standard, and use the formatter\n"
        f"(run_command) to fix formatting rather than editing whitespace by hand:\n{lint}\n"
        f"run_command accepts commands starting with: {allowed}"
    )


def memory_context(store: MemoryStore) -> str:
    """What every agent always sees, like a CLAUDE.md: the org/ rules and the harness-measured
    repo-facts in full, plus the one-line index of team/ notes. The notes themselves are loaded
    progressively by the agents (frontmatter + grep, never the whole store)."""
    org = [store.read(p)[0] for p, _ in store.files() if p.startswith("org/")]
    facts = store.read("team/repo-facts.md")[0]
    index = "\n".join(f"{p} - {d}" for p, d in store.files()
                      if p.startswith("team/") and p != "team/repo-facts.md")
    return ("## org/ rules\n" + ("\n".join(org) or "(none)")
            + "\n\n## team/repo-facts.md\n" + facts
            + "\n\n## team/ notes (index only: memory_read what's relevant)\n" + (index or "(none yet)"))


def refresh_repo_facts(store: MemoryStore, sb):
    """Harness-measured facts, rewritten each session (as a new version only if they changed)."""
    new = build_repo_facts(sb)
    try:
        current, digest = store.read("team/repo-facts.md")
    except FileNotFoundError:
        current, digest = None, "new"
    if current is None or current.strip() != new.strip():
        store.write("team/repo-facts.md", new, digest, "harness", sb.session_id, "measured from fresh clone")


def start_session(repo_url: str, branch: str | None = None, langs: list[str] | None = None):
    """Fresh sandbox (clone, containers, network cut) plus memory and the agents' context."""
    sb = Sandbox(repo_url, branch=branch, langs=langs)
    try:
        store = MemoryStore(MEMORY_DIR, repo_name(repo_url))
        refresh_repo_facts(store, sb)
        context = sandbox_context(sb) + "\n\n" + memory_context(store)
    except BaseException:
        sb.close()
        raise
    return sb, store, context


def end_session(sb, state: dict, request: str, repo_url: str, kind: str = "work",
                extra: dict | None = None, publish_it: bool = True) -> dict:
    """Publish (work sessions only), print the result, save the transcript, close the sandbox."""
    extra = dict(extra or {})
    try:
        if kind == "work" and publish_it:  # eval runs are never pushed
            extra.update(publish(sb, state, repo_url))
    finally:
        print("\n========== result ==========")
        print(f"status: {state.get('status')} (rounds: {state.get('round', 0)})")
        commits = sb.run(f"git log --oneline {sb.base_branch}..HEAD", timeout=30)["stdout"]
        print(commits or "(no commits)")
        print(f"branch: {sb.run('git branch --show-current', timeout=15)['stdout'].strip()}")
        if extra.get("published"):
            print(f"pushed to GitHub as: {extra.get('branch')}")
        elif extra.get("draft"):
            print(f"pushed to GitHub as an unapproved draft: {extra.get('branch')} (no pull request)")
        if extra.get("pr_url"):
            print(f"pull request: {extra['pr_url']}")
        print(f"repo copy kept at: {sb.workdir}")
        path = save_transcript(sb.session_id, repo_name(repo_url), request, state.get("status", "?"),
                               state.get("feedback_log") or [], state.get("conversations") or {}, kind)
        print(f"transcript: {path}")
        sb.close()
    return {**state, **extra, "session_id": sb.session_id, "transcript": str(path),
            "commits": commits.strip()}


def run_session(request: str, repo_url: str, after=None, kind: str = "work", branch: str | None = None,
                langs: list[str] | None = None) -> dict:
    """Run the crew on one request in a fresh sandbox. `after(sb, state)` runs before the
    sandbox closes (evals use it to run hidden checks). Returns the final crew state plus
    the session id and transcript path."""
    sb, store, context = start_session(repo_url, branch, langs)
    state = {"request": request, "status": "running", "conversations": {}}
    extra, ok = {}, False
    try:
        crew = build_crew(get_llm(), sb, store, context)
        # Each agent prints its own steps; invoke returns the final crew state.
        state = crew.invoke(state, config={"recursion_limit": 50})
        if after:
            extra = after(sb, state) or {}
        ok = True
    finally:
        result = end_session(sb, state, request, repo_url, kind, extra, publish_it=ok)
    return result


def maybe_dream(repo_url: str):
    """Dreaming runs out of band, in batches: once DREAM_EVERY (default 10) new work sessions
    have built up, start a pass. Batches let it see patterns across sessions, which a pass
    after every single task can't. DREAM_AUTO_PROMOTE=true makes the result live without review."""
    from dreaming.dream import dream, new_sessions, promote

    every = int(os.getenv("DREAM_EVERY", "10"))
    if every <= 0:
        return
    repo = repo_name(repo_url)
    waiting = len(new_sessions(repo))
    if waiting < every:
        print(f"[dreaming] {waiting}/{every} new sessions; dreaming starts at {every}")
        return
    print(f"\n[dreaming] {waiting} new sessions: starting a dreaming pass")
    run_dir = dream(get_llm(), repo)
    if run_dir is None:
        return
    print(f"[dreaming] report: {run_dir / 'report.md'}")
    if os.getenv("DREAM_AUTO_PROMOTE", "false").lower() in ("true", "1", "yes"):
        promote(run_dir.name)
    else:
        print(f"[dreaming] review it, then: python run_dream.py --promote {run_dir.name}")


def main():
    import argparse

    load_dotenv()
    ap = argparse.ArgumentParser(description="Run the code-crew on one request.")
    ap.add_argument("request", help="what to build or fix")
    ap.add_argument("--branch", help="continue an existing code-crew/ branch (updates its open pull request)")
    # One value per flag ("rust" or "rust,go"), repeatable. Not nargs="+": that would swallow
    # the request text that follows as another language name.
    ap.add_argument("--lang", action="append", metavar="LANG",
                    help="also start this toolchain for a language the repo doesn't use yet, "
                         "e.g. --lang rust (or --lang rust,go)")
    args = ap.parse_args()
    langs = [l.strip() for v in (args.lang or []) for l in v.split(",") if l.strip()] or None
    repo_url = os.environ["REPO_URL"]
    run_session(args.request, repo_url, branch=args.branch, langs=langs)
    maybe_dream(repo_url)


if __name__ == "__main__":
    main()
