"""Skills: reusable procedures in skills/<name>/SKILL.md, loaded progressively.

Each agent's system prompt lists only the name and description of the skills for its role
(and for the repo's languages). The agent calls skill_read(name) for the full procedure when
its situation matches. Skills are written by Emmie; agents can't change them.
"""
from pathlib import Path

from langchain_core.tools import tool

from .memory_store import parse_frontmatter

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _as_list(value: str) -> list[str]:
    """'[builder, test-writer]' or 'builder' -> ['builder', 'test-writer']."""
    value = value.strip().strip("[]")
    return [v.strip().strip("'\"") for v in value.split(",") if v.strip()]


def load_skills(skills_dir: Path = SKILLS_DIR) -> dict[str, dict]:
    skills = {}
    for f in sorted(skills_dir.glob("*/SKILL.md")):
        text = f.read_text(encoding="utf-8")
        meta = parse_frontmatter(text)
        name = meta.get("name") or f.parent.name
        skills[name] = {
            "description": meta.get("description", ""),
            "roles": _as_list(meta.get("roles", "")),
            "langs": _as_list(meta.get("langs", "")),
            "text": text,
        }
    return skills


def skills_for(skills: dict, role: str, langs: list[str]) -> dict:
    """Skills listed for this role; language-specific ones only when the repo has that language."""
    return {n: s for n, s in skills.items()
            if (not s["roles"] or role in s["roles"])
            and (not s["langs"] or set(s["langs"]) & set(langs))}


def skills_index(mine: dict) -> str:
    if not mine:
        return ""
    lines = "\n".join(f"- {n}: {s['description']}" for n, s in mine.items())
    return ("\n\n## Skills (procedures for common situations)\n"
            "When your situation matches one, call skill_read(name) and follow it. "
            "Read each one at most once per session.\n" + lines)


def make_skill_tool(mine: dict):
    @tool
    def skill_read(name: str) -> str:
        """Read the full procedure of a skill listed in your instructions."""
        s = mine.get(name.strip())
        if s is None:
            return f"error: no skill named {name!r}. Your skills: {', '.join(mine) or 'none'}"
        return s["text"]

    return skill_read
