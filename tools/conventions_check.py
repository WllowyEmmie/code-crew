"""Conventions must say which language they're for.

team/conventions.md once said "functions are snake_case" and "strings use f-strings" with no
language, so agents applied Python habits to Rust. Every convention line must now start with a
language tag, e.g. "- python: functions are snake_case" or "- all: no secrets in code".
"""
CONVENTIONS = "team/conventions.md"
LANG_TAGS = {
    "all", "python", "javascript", "typescript", "node", "bun", "deno", "go", "rust", "java",
    "kotlin", "scala", "csharp", "dotnet", "fsharp", "php", "ruby", "elixir", "swift", "dart",
    "flutter", "c", "cpp", "sql", "shell", "html", "css",
}


def untagged_conventions(path: str, content: str) -> list[str]:
    if path != CONVENTIONS:
        return []
    body = content.replace("\r\n", "\n").split("\n---", 1)[-1]
    bad = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        tag = line[2:].split(":", 1)[0].strip().lower() if ":" in line else ""
        if tag not in LANG_TAGS:
            bad.append(line[:70])
    return bad


def conventions_error(path: str, content: str) -> str | None:
    bad = untagged_conventions(path, content)
    if bad:
        return ("every line in team/conventions.md must start with the language it applies to, "
                "e.g. '- python: functions are snake_case' or '- rust: types are CamelCase' "
                "('- all: ...' for every language). Untagged: " + " | ".join(bad[:4]))
    return None
