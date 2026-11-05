import re
import shlex

from langchain_core.tools import tool

PROTECTED_BRANCHES = {"main", "master"}
# Identity for agent commits, so they're easy to tell apart from yours.
GIT = "git -c user.name=code-crew -c user.email=code-crew@users.noreply.github.com"


def make_git_tools(sb):
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
        """Create and switch to a new branch. The name must start with 'agent/', e.g. agent/fix-issue-42."""
        if not re.fullmatch(r"agent/[A-Za-z0-9._/-]+", name):
            return "error: branch name must look like agent/<short-description>"
        r = sb.run(f"git checkout -b {shlex.quote(name)}", timeout=30)
        return r["stderr"].strip() or r["stdout"].strip()

    @tool
    def git_commit(message: str) -> str:
        """Run the repo's tests, then stage all changes and commit them with `message`.
        Refuses to commit on main/master or if tests fail."""
        branch = current_branch()
        if branch in PROTECTED_BRANCHES or not branch.startswith("agent/"):
            return f"error: on '{branch}'. Call create_branch with an agent/ name first."
        # The harness runs the tests itself instead of trusting the model to remember.
        test_report = []
        for container, cmd in sb.test_commands():
            t = sb.run(cmd, timeout=900, container=container)
            if t["exit_code"] != 0:
                return (f"error: tests failed, nothing committed. Fix them and call git_commit again.\n"
                        f"$ {cmd}\n{t['stdout'][-3000:]}{t['stderr'][-2000:]}")
            test_report.append(f"{cmd}: passed")
        r = sb.run(f"git add -A && {GIT} commit -m {shlex.quote(message)}", timeout=60)
        if r["exit_code"] != 0:
            return f"error: {r['stdout']}{r['stderr']}"
        tests = "\n".join(test_report) or "no tests found in repo"
        return f"{r['stdout']}\ntests: {tests}"

    return [git_status, git_diff, git_diff_base, create_branch, git_commit]
