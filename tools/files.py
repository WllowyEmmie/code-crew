import re
import shlex
from pathlib import Path

from langchain_core.tools import tool

MAX_READ_CHARS = 20000


TEST_DIRS = {"test", "tests", "spec", "specs", "__tests__", "testdata"}
# test_x.py, x_test.go, x.test.ts, x.spec.js, x_spec.rb, FooTest.java, FooTests.cs ...
# The CamelCase "FooTest.java" rule is case-sensitive so "latest.py" / "contest.py" don't match.
TEST_NAME = re.compile(r"(?i:^test_|_test\.|\.test\.|\.spec\.|_spec\.)|[a-z0-9]Tests?\.[A-Za-z]+$")


def is_test_path(path: str) -> bool:
    parts = Path(path).parts
    return any(d.lower() in TEST_DIRS for d in parts[:-1]) or bool(TEST_NAME.search(parts[-1]))


def make_file_tools(sb, write_guard=None):
    """File tools for one sandbox session. Paths are relative to the repo root.
    write_guard(path) -> error message or None: the harness's chance to refuse a write
    (e.g. "test files only", or "read memory first")."""
    root = Path(sb.workdir).resolve()
    edits: list[tuple[str, str, str]] = []  # (path, old, new) of every edit made with these tools

    def check_write(path: str):
        err = write_guard(path) if write_guard else None
        if err:
            raise ValueError(err)

    def safe_path(rel: str) -> Path:
        # resolve() follows "..", and symlinks, so this blocks escaping the repo.
        p = (root / rel).resolve()
        if p != root and not p.is_relative_to(root):
            raise ValueError(f"path escapes the repo: {rel}")
        if ".git" in p.relative_to(root).parts:
            raise ValueError("the .git folder is off limits; use the git tools")
        return p

    @tool
    def read_file(path: str) -> str:
        """Read a text file in the repo. `path` is relative to the repo root."""
        try:
            text = safe_path(path).read_text(encoding="utf-8")
        except Exception as e:
            return f"error: {e}"
        if len(text) > MAX_READ_CHARS:
            return text[:MAX_READ_CHARS] + f"\n... [truncated, {len(text)} chars total]"
        return text

    @tool
    def list_dir(path: str = ".") -> str:
        """List files and folders at `path` (relative to the repo root). Folders end with '/'."""
        try:
            p = safe_path(path)
            entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
        except Exception as e:
            return f"error: {e}"
        names = [e.name + ("/" if e.is_dir() else "") for e in entries if e.name != ".git"]
        return "\n".join(names) or "(empty)"

    @tool
    def grep(pattern: str, path: str = ".") -> str:
        """Search file contents for a regex `pattern` under `path`. Returns file:line:text matches."""
        try:
            safe_path(path)
        except Exception as e:
            return f"error: {e}"
        cmd = f"grep -rnE --exclude-dir=.git -- {shlex.quote(pattern)} {shlex.quote(path)} | head -200"
        r = sb.run(cmd, timeout=30)
        return r["stdout"] or "(no matches)"

    @tool
    def write_file(path: str, content: str) -> str:
        """Create a NEW file in the repo with `content`. Creates parent folders.
        Refuses if the file already exists: use edit_file to change existing files."""
        try:
            check_write(path)
            p = safe_path(path)
            if p.exists():
                return f"error: {path} already exists. Use edit_file to change it (read_file it first)."
            p.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n" keeps LF endings even though this runs on Windows.
            p.write_text(content, encoding="utf-8", newline="\n")
        except Exception as e:
            return f"error: {e}"
        return f"wrote {path} ({len(content)} chars)"

    @tool
    def edit_file(path: str, old_str: str, new_str: str) -> str:
        """Replace `old_str` with `new_str` in a file. `old_str` must appear exactly once.
        To append, use the last lines of the file as old_str and repeat them in new_str."""
        try:
            check_write(path)
            p = safe_path(path)
            # newline="" reads the file exactly as stored, so CRLF files stay CRLF.
            with open(p, encoding="utf-8", newline="") as f:
                text = f.read()
        except Exception as e:
            return f"error: {e}"
        if "\r\n" in text:
            # The model writes "\n"; match the file's CRLF style so lines we don't touch stay identical.
            old_str = old_str.replace("\r\n", "\n").replace("\n", "\r\n")
            new_str = new_str.replace("\r\n", "\n").replace("\n", "\r\n")
        if old_str == new_str:
            return ("error: old_str and new_str are identical, so this edit changes nothing. If a "
                    "fix keeps failing, the problem is probably elsewhere: re-read the full error "
                    "(the line and the exact symbol it points at) before editing again.")
        count = text.count(old_str)
        if count != 1:
            return f"error: old_str found {count} times; it must match exactly once. read_file and copy the exact lines."
        # Going back and forth between versions means the agent is stuck, usually because two
        # tests can't both pass or a test expects something the request never asked for.
        # Stop the loop and make it step back, instead of letting it burn every remaining step.
        if (path, new_str, old_str) in edits or (path, old_str, new_str) in edits:
            return ("error: this edit repeats or undoes one you already made: you're going back and "
                    "forth. Stop editing. Re-read the failing test assertions and the request. If two "
                    "tests can't both pass, or a test expects something the request doesn't say, the "
                    "TEST is wrong: don't special-case the code to satisfy it. Keep the version that "
                    "matches the request and call finish('done'), naming the wrong test, what it "
                    "expects, and why the request says otherwise.")
        edits.append((path, old_str, new_str))
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(old_str, new_str))
        return f"edited {path}"

    return [read_file, list_dir, grep, write_file, edit_file]
