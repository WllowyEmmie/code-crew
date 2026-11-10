import re
import shlex

from langchain_core.tools import tool

PROTECTED_BRANCHES = {"main", "master"}
BRANCH_PREFIX = "code-crew/"
# Identity for agent commits, so they're easy to tell apart from yours.
GIT = "git -c user.name=code-crew -c user.email=code-crew@users.noreply.github.com"


def crew_message(message: str, agent: str, session: str) -> str:
    """Label every commit so anyone reading the history can see code-crew made it:
    a [code-crew] subject prefix, plus trailers naming the agent and the session."""
    message = message.strip() or "update"
    if not message.startswith("[code-crew]"):
        message = f"[code-crew] {message}"
    return (f"{message}\n\nGenerated-by: code-crew\n"
            f"Code-crew-agent: {agent}\nCode-crew-session: {session}")


# Source-file extension -> language profiles that can build, test and lint it.
LANGUAGE_OF = {
    ".py": {"python"}, ".js": {"node", "bun", "deno"}, ".jsx": {"node", "bun", "deno"},
    ".ts": {"node", "bun", "deno"}, ".tsx": {"node", "bun", "deno"}, ".mjs": {"node", "bun", "deno"},
    ".cjs": {"node", "bun", "deno"}, ".go": {"go"}, ".rs": {"rust"}, ".java": {"maven", "gradle"},
    ".kt": {"gradle", "maven"}, ".scala": {"scala"}, ".cs": {"dotnet"}, ".fs": {"dotnet"},
    ".php": {"php"}, ".rb": {"ruby"}, ".ex": {"elixir"}, ".exs": {"elixir"}, ".swift": {"swift"},
    ".dart": {"dart", "flutter"}, ".c": {"cpp"}, ".cc": {"cpp"}, ".cpp": {"cpp"}, ".cxx": {"cpp"},
    ".h": {"cpp"}, ".hpp": {"cpp"},
}


# Ways to make a linter/compiler stop complaining without fixing anything.
SUPPRESSION = re.compile(
    r"#!?\[allow\(|#\s*noqa|#\s*type:\s*ignore|#\s*pylint:\s*disable|//\s*eslint-disable|"
    r"/\*\s*eslint-disable|@ts-ignore|@ts-nocheck|@SuppressWarnings|//\s*nolint|//\s*NOLINT|"
    r"rubocop:disable|phpcs:ignore|#\s*fmt:\s*off|//\s*@ts-expect-error")


def added_suppressions(sb) -> list[str]:
    """Lines added on this branch that silence a checker instead of fixing what it found."""
    diff = sb.run(f"git add -A && git diff --cached -U0 {sb.base_branch}", timeout=60)["stdout"]
    return [l[1:].strip()[:100] for l in diff.splitlines()
            if l.startswith("+") and not l.startswith("+++") and SUPPRESSION.search(l)]


def unverifiable(sb) -> list[str]:
    """Changed source files in a language this sandbox has no toolchain for. Nothing could
    compile, test or lint them, so committing them would pass every check without being checked."""
    active = {p.name for p in sb.profiles}
    out = []
    for f in sb.changed_files():
        ext = "." + f.rsplit(".", 1)[-1] if "." in f else ""
        langs = LANGUAGE_OF.get(ext)
        if langs and not langs & active:
            out.append(f"{f} (needs {'/'.join(sorted(langs))})")
    return out


def make_git_tools(sb, agent: str = "agent"):
    def current_branch() -> str:
        return sb.run("git branch --show-current", timeout=15)["stdout"].strip()

    @tool
    def git_status() -> str:
        """Show the current branch and which files are changed, staged or untracked."""
        r = sb.run("git status --short --branch", timeout=30)
        return r["stdout"] or r["stderr"]

    @tool
    def git_diff(staged: bool = False) -> str:
        """Show the diff of uncommitted changes. Set staged=True to see staged changes only."""
        # Plain `git diff` skips untracked (brand-new) files. --intent-to-add records new files
        # as "will be added" without staging their content, so they show up in the diff.
        sb.run("git add --intent-to-add .", timeout=30)
        r = sb.run("git diff --staged" if staged else "git diff", timeout=30)
        return r["stdout"] or "(no changes)"

    @tool
    def git_diff_base() -> str:
        """Show everything this branch changes compared to the branch it started from
        (all commits so far plus uncommitted changes). Use this to review the whole piece of work."""
        sb.run("git add --intent-to-add .", timeout=30)
        stat = sb.run(f"git diff --stat {sb.base_branch}", timeout=30)["stdout"]
        diff = sb.run(f"git diff {sb.base_branch}", timeout=30)["stdout"]
        return (stat + "\n" + diff[:15000]) if diff else f"(no changes compared to {sb.base_branch})"

    @tool
    def create_branch(name: str) -> str:
        """Create and switch to a new branch. The name must start with 'code-crew/',
        e.g. code-crew/fix-issue-42."""
        if getattr(sb, "continuing", None):
            return (f"error: you're continuing the existing branch {sb.continuing} (an open pull "
                    f"request). Don't create a new branch: commit your changes here.")
        if not re.fullmatch(r"code-crew/[A-Za-z0-9._/-]+", name):
            return "error: branch name must look like code-crew/<short-description>"
        r = sb.run(f"git checkout -b {shlex.quote(name)}", timeout=30)
        return r["stderr"].strip() or r["stdout"].strip()

    @tool
    def git_commit(message: str) -> str:
        """Run the repo's tests and the language's standard formatter/linter checks, then stage
        all changes and commit them with `message`. Refuses to commit on main/master, if tests
        fail, or if the changed files don't meet the standard style."""
        branch = current_branch()
        if branch in PROTECTED_BRANCHES or not branch.startswith(BRANCH_PREFIX):
            return f"error: on '{branch}'. Call create_branch with a {BRANCH_PREFIX} name first."
        # Never commit code nothing can check (e.g. a .rs file in a Python-only sandbox).
        cant_check = unverifiable(sb)
        if cant_check:
            active = ", ".join(p.name for p in sb.profiles) or "none"
            return ("error: nothing committed. These files are in a language this sandbox can't "
                    f"build, test or lint (its toolchains: {active}): {'; '.join(cant_check)}. "
                    "Write the change in a language the repo already uses, or finish('done') and say "
                    "the request needs a different toolchain.")
        silenced = added_suppressions(sb)
        if silenced:
            return ("error: nothing committed. These lines silence the linter/compiler instead of "
                    "fixing what it found: " + " | ".join(silenced[:5]) + ". Warnings usually point "
                    "at a real bug (e.g. 'value assigned is never read' = your change isn't stored). "
                    "Remove the suppression and fix the cause.")
        # The harness runs the tests itself instead of trusting the model to remember.
        test_report = []
        for container, cmd in sb.test_commands():
            t = sb.run(cmd, timeout=900, container=container)
            if t["exit_code"] != 0:
                return (f"error: tests failed, nothing committed. Fix them and call git_commit again.\n"
                        f"$ {cmd}\n{t['stdout'][-3000:]}{t['stderr'][-2000:]}")
            test_report.append(f"{cmd}: passed")
        # Then the language's standard formatter/linter checks, on the files the agent changed.
        lint_report = []
        for container, cmd, label in sb.lint_commands():
            t = sb.run(cmd, timeout=900, container=container)
            if t["exit_code"] != 0:
                return (f"error: code doesn't meet the standard style, nothing committed. Fix these, "
                        f"then call git_commit again. Most are auto-fixable with run_command, e.g. "
                        f"ruff format FILE + ruff check --fix FILE (Python), gofmt -w FILE (Go), "
                        f"cargo fmt (Rust), npx --no-install prettier --write FILE (JS/TS), "
                        f"rubocop -a FILE (Ruby), phpcbf FILE (PHP), dart format FILE, mix format FILE, "
                        f"clang-format -i FILE (C/C++), dotnet format --no-restore (C#).\n"
                        f"$ {cmd}\n{t['stdout'][-3000:]}{t['stderr'][-2000:]}")
            lint_report.append(f"{label}: passed")
        full = crew_message(message, agent, sb.session_id)
        r = sb.run(f"git add -A && {GIT} commit -m {shlex.quote(full)}", timeout=60)
        if r["exit_code"] != 0:
            return f"error: {r['stdout']}{r['stderr']}"
        tests = "\n".join(test_report) or "no tests found in repo"
        lint = "\n".join(lint_report) or "no lint checks apply"
        return f"{r['stdout']}\ntests: {tests}\nlint: {lint}"

    return [git_status, git_diff, git_diff_base, create_branch, git_commit]
