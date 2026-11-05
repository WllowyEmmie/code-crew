"""Markdown memory the agents share between sessions.

Layout on disk:
  memory/org/                 read-only for agents (rules you write yourself)
  memory/team/<repo>/         read-write, one folder per target repo

Agents see two virtual folders: org/ and team/. "team/" maps to the current repo's folder,
so notes about one repo never leak into another.
"""
import re
from pathlib import Path

from langchain_core.tools import tool

MAX_READ_CHARS = 20000
NAME_RE = re.compile(r"^(org|team)/[A-Za-z0-9_-]+\.md$")


def parse_frontmatter(text: str) -> dict:
    """Read `key: value` lines between the opening and closing '---'."""
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def make_memory_tools(memory_root: str | Path, repo_name: str):
    memory_root = Path(memory_root)
    scopes = {
        "org": memory_root / "org",
        "team": memory_root / "team" / repo_name,
    }
    scopes["team"].mkdir(parents=True, exist_ok=True)

    def resolve(path: str) -> Path:
        if not NAME_RE.fullmatch(path):
            raise ValueError("path must look like team/<name>.md or org/<name>.md")
        scope, name = path.split("/", 1)
        return scopes[scope] / name

    def all_files():
        for scope, folder in scopes.items():
            if folder.exists():
                for f in sorted(folder.glob("*.md")):
                    yield f"{scope}/{f.name}", f

    @tool
    def memory_list() -> str:
        """List every memory file with its description (frontmatter only, not the body).
        Call this first to see what's known, then memory_read the files you need."""
        lines = []
        for path, f in all_files():
            meta = parse_frontmatter(f.read_text(encoding="utf-8"))
            lines.append(f"{path} - {meta.get('description', '(no description)')}")
        return "\n".join(lines) or "(memory is empty)"

    @tool
    def memory_grep(pattern: str) -> str:
        """Search all memory files for a regex `pattern` (case-insensitive). Returns path:line:text."""
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"error: bad regex: {e}"
        hits = []
        for path, f in all_files():
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{path}:{i}:{line}")
        return "\n".join(hits[:100]) or "(no matches)"

    @tool
    def memory_read(path: str) -> str:
        """Read one memory file, e.g. team/repo-layout.md."""
        try:
            text = resolve(path).read_text(encoding="utf-8")
        except Exception as e:
            return f"error: {e}"
        return text[:MAX_READ_CHARS]

    @tool
    def memory_write(path: str, content: str) -> str:
        """Create or replace a memory file in team/ (org/ is read-only).
        `content` is the WHOLE file: frontmatter with name and description, then one fact per line.
        To update a file, memory_read it first and write back the full new version.
        Example:
        ---
        name: testing
        description: How to run tests in this repo and known flaky tests
        ---
        - Tests run with `go test ./...`
        """
        if path.startswith("org/"):
            return "error: org/ is read-only. Write to team/ instead."
        try:
            p = resolve(path)
        except Exception as e:
            return f"error: {e}"
        meta = parse_frontmatter(content)
        if not meta.get("name") or not meta.get("description"):
            return "error: content must start with frontmatter containing name and description (see the example)"
        p.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")
        return f"saved {path}"

    return [memory_list, memory_grep, memory_read, memory_write]
