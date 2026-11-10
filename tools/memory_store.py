"""Versioned markdown memory: "it's just files, with a standalone API on top".

Layout:
  memory/org/*.md                  shared org knowledge (read-only for agents)
  memory/team/<repo>/*.md          the team's working memory for one repo
  memory/.history/<scope>/<file>/  every version of every file, plus log.jsonl

Every write is attributed (author, session, time) and kept as a new version, so any file's
full history can be inspected and rolled back. Every write also carries a content-hash
precondition: the sha256 of the version the writer last read. If someone else changed the
file in between, the write is refused. That check lives here in the harness, not in the model.
"""
import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

NAME_RE = re.compile(r"^(org|team)/[A-Za-z0-9_-]+\.md$")
SHA_LEN = 12            # agents see/send a 12-char prefix; enough to detect any change
NEW = "new"             # if_sha256 value for creating a file that doesn't exist yet
_lock = threading.Lock()  # dreaming subagents write in parallel threads


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_frontmatter(text: str) -> dict:
    """`key: value` lines between the opening and closing '---'."""
    text = text.replace("\r\n", "\n")
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


class WriteRefused(Exception):
    pass


class MemoryStore:
    def __init__(self, root: str | Path, repo: str):
        self.root = Path(root)
        self.repo = repo
        self.dirs = {"org": self.root / "org", "team": self.root / "team" / repo}
        self.history_root = self.root / ".history"
        for d in self.dirs.values():
            d.mkdir(parents=True, exist_ok=True)

    # ---------- paths ----------
    def file(self, path: str) -> Path:
        if not NAME_RE.fullmatch(path):
            raise ValueError("path must look like team/<name>.md or org/<name>.md")
        scope, name = path.split("/", 1)
        return self.dirs[scope] / name

    def _history_dir(self, path: str) -> Path:
        scope, name = path.split("/", 1)
        owner = "org" if scope == "org" else f"team/{self.repo}"
        return self.history_root / owner / name

    # ---------- reading ----------
    def files(self) -> list[tuple[str, str]]:
        out = []
        for scope, folder in self.dirs.items():
            for f in sorted(folder.glob("*.md")):
                meta = parse_frontmatter(f.read_text(encoding="utf-8"))
                out.append((f"{scope}/{f.name}", meta.get("description", "(no description)")))
        return out

    def read(self, path: str) -> tuple[str, str]:
        """(content, sha256). Raises FileNotFoundError if missing."""
        text = self.file(path).read_text(encoding="utf-8")
        return text, sha(text)

    def grep(self, pattern: str) -> list[str]:
        rx = re.compile(pattern, re.IGNORECASE)
        hits = []
        for path, _ in self.files():
            for i, line in enumerate(self.read(path)[0].splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{path}:{i}:{line}")
        return hits

    def history(self, path: str) -> list[dict]:
        log = self._history_dir(path) / "log.jsonl"
        if not log.exists():
            return []
        return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l]

    def version(self, path: str, n: int) -> str | None:
        """Content of version n (None for a deletion)."""
        p = self._history_dir(path) / f"v{n}.md"
        return p.read_text(encoding="utf-8") if p.exists() else None

    # ---------- writing ----------
    def _check(self, path: str, if_sha: str):
        f = self.file(path)
        if not f.exists():
            if if_sha != NEW:
                raise WriteRefused(f"{path} doesn't exist; use if_sha256='{NEW}' to create it")
            return
        current = sha(f.read_text(encoding="utf-8"))
        if if_sha == NEW:
            raise WriteRefused(f"{path} already exists (sha256 {current[:SHA_LEN]}); read it first")
        if not current.startswith(if_sha.strip().lower()) or len(if_sha.strip()) < 8:
            raise WriteRefused(
                f"{path} changed since you read it (now sha256 {current[:SHA_LEN]}). "
                f"memory_read it again, merge your change into the new content, and retry")

    def _record(self, path: str, content: str | None, author: str, session: str, note: str = ""):
        """Append a version: the file content (or None = deleted) plus who/when."""
        hdir = self._history_dir(path)
        hdir.mkdir(parents=True, exist_ok=True)
        n = len(self.history(path)) + 1
        if content is not None:
            (hdir / f"v{n}.md").write_text(content, encoding="utf-8", newline="\n")
        entry = {"version": n, "sha256": sha(content)[:SHA_LEN] if content is not None else None,
                 "deleted": content is None, "author": author, "session": session,
                 "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "note": note}
        with open(hdir / "log.jsonl", "a", encoding="utf-8") as log:
            log.write(json.dumps(entry) + "\n")
        return n

    def _seed(self, path: str):
        """Files that existed before versioning get their current content saved as v1."""
        f = self.file(path)
        if f.exists() and not self.history(path):
            self._record(path, f.read_text(encoding="utf-8"), "unknown", "before-versioning")

    def write(self, path: str, content: str, if_sha: str, author: str, session: str, note: str = "") -> int:
        content = content.rstrip() + "\n"
        meta = parse_frontmatter(content)
        if not meta.get("name") or not meta.get("description"):
            raise WriteRefused("content must start with frontmatter containing name and description")
        with _lock:  # check-then-write must be atomic when subagents run in parallel
            self._check(path, if_sha)
            self._seed(path)
            self.file(path).write_text(content, encoding="utf-8", newline="\n")
            return self._record(path, content, author, session, note)

    def delete(self, path: str, if_sha: str, author: str, session: str, note: str = "") -> int:
        with _lock:
            if not self.file(path).exists():
                raise WriteRefused(f"{path} doesn't exist")
            self._check(path, if_sha)
            self._seed(path)
            self.file(path).unlink()
            return self._record(path, None, author, session, note)
