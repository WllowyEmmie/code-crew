"""Loads language profiles from profiles.toml and checks them against a repo."""
import fnmatch
import json
import os
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROFILES_FILE = Path(__file__).with_name("profiles.toml")

# Folders that hold dependencies or build output, never source worth scanning.
SKIP_DIRS = {
    ".git", "node_modules", "target", "build", "dist", "vendor", "venv", ".venv", "__pycache__",
    ".gradle", "obj", ".dart_tool", "_build", ".build", ".next",
}
NO_TEST_SCRIPT = "no test specified"  # npm's placeholder test script


def find_files(repo: Path, patterns: list[str], limit: int = 1) -> list[Path]:
    """Find files matching any pattern anywhere in the repo, skipping dependency/build folders."""
    found = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if any(fnmatch.fnmatch(f, p) for p in patterns):
                found.append(Path(root) / f)
                if len(found) >= limit:
                    return found
    return found


def _at_root(repo: Path, patterns: list[str]) -> bool:
    return any(any(repo.glob(p)) for p in patterns)


def _file_contains(repo: Path, file: str, text: str) -> bool:
    p = repo / file
    # Leading "\n" lets a pattern like "\ntest:" also match on the file's first line.
    return p.is_file() and text in "\n" + p.read_text(encoding="utf-8", errors="ignore")


def _npm_scripts(repo: Path) -> dict:
    try:
        return json.loads((repo / "package.json").read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, ValueError):
        return {}


def check(repo: Path, cond: dict | None) -> bool:
    """True if every condition in the set passes (an empty/missing set always passes)."""
    for key, val in (cond or {}).items():
        if key == "file":
            ok = _at_root(repo, val)
        elif key == "no_file":
            ok = not _at_root(repo, val)
        elif key == "glob":
            ok = bool(find_files(repo, val))
        elif key == "dir":
            ok = any((repo / d).is_dir() for d in val)
        elif key == "contains":
            ok = _file_contains(repo, *val)
        elif key == "not_contains":
            ok = not _file_contains(repo, *val)
        elif key == "script":
            scripts = _npm_scripts(repo)
            ok = any(n in scripts and NO_TEST_SCRIPT not in scripts[n] for n in val)
        elif key == "no_script":
            ok = not any(n in _npm_scripts(repo) for n in val)
        else:
            raise ValueError(f"unknown condition '{key}' in profiles.toml")
        if not ok:
            return False
    return True


@dataclass
class Profile:
    name: str
    image: str
    detect: list[dict]
    setup: list[dict]
    test: list[dict]
    allowed: tuple[str, ...]
    artifacts: tuple[str, ...]
    lint: list[dict]
    tests_go: str = ""  # where this language's tests conventionally live

    def matches(self, repo: Path) -> bool:
        return any(check(repo, c) for c in self.detect)

    def setup_commands(self, repo: Path) -> list[str]:
        return [s["run"] for s in self.setup if check(repo, s.get("when"))]

    def test_commands(self, repo: Path) -> list[str]:
        return [t["run"] for t in self.test if check(repo, t.get("when"))]

    def lint_steps(self, repo: Path) -> list[dict]:
        return [s for s in self.lint if check(repo, s.get("when"))]

    @staticmethod
    def file_scoped(step: dict) -> bool:
        """Steps with {files} run only on files the agent changed; others check the whole project."""
        return "{files}" in step["run"]

    @staticmethod
    def render(step: dict, changed: list[str]) -> str | None:
        """Fill {files} with the changed files this step applies to (None if there are none)."""
        if not Profile.file_scoped(step):
            return step["run"]
        pats = step.get("files", ["*"])
        mine = [f for f in changed if any(fnmatch.fnmatch(f.split("/")[-1], p) for p in pats)]
        return step["run"].replace("{files}", " ".join(shlex.quote(f) for f in mine)) if mine else None


def load_profiles(path: Path = PROFILES_FILE) -> list[Profile]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [
        Profile(
            name=name,
            image=d["image"],
            detect=d["detect"],
            setup=d.get("setup", []),
            test=d.get("test", []),
            allowed=tuple(d.get("allowed", [])),
            artifacts=tuple(d.get("artifacts", [])),
            lint=d.get("lint", []),
            tests_go=d.get("tests_go", ""),
        )
        for name, d in data.items()
    ]


def detect_profiles(repo: Path) -> list[Profile]:
    return [p for p in load_profiles() if p.matches(repo)]
