"""Detect placeholder implementations, so "done" can't mean "I wrote an empty function".

Python is checked precisely with its own parser (ast): a function whose body is only a docstring,
`pass`, `...` or `raise NotImplementedError` is a stub. Other languages get a text check for
the usual placeholders (todo!(), unimplemented!(), panic("not implemented"),
throw new UnsupportedOperationException / NotImplementedException).
"""
import ast
import re
from pathlib import Path

from .files import is_test_path

TEXT_STUBS = re.compile(r"todo!\(|unimplemented!\(|panic\(\"not implemented|"
                        r"UnsupportedOperationException\(\"?not implemented|NotImplementedException|"
                        r"NotImplementedError", re.IGNORECASE)


def _rust_ignored_input_fns(text: str) -> list[tuple[str, list[str]]]:
    """pub fns with `_`-prefixed parameters: Rust's way of marking an input "unused", so the
    function ignores it. Seen in practice: a stub that renamed 3 of its 4 params to silence
    clippy and returned an empty result. A public API that ignores an input is unfinished."""
    found = []
    for m in re.finditer(r"pub\s+fn\s+(\w+)\s*(?:<[^>]*>)?\s*\(", text):
        depth, i = 1, m.end()
        while i < len(text) and depth:  # find the matching ")" (types can contain parentheses)
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        params, depth, cur = [], 0, ""
        for ch in text[m.end():i - 1]:
            if ch in "(<[":
                depth += 1
            elif ch in ")>]":
                depth -= 1
            if ch == "," and depth == 0:
                params.append(cur)
                cur = ""
            else:
                cur += ch
        params.append(cur)
        names = [p.split(":")[0].replace("mut ", "").strip() for p in params if ":" in p]
        names = [n for n in names if n not in ("self", "&self", "&mut self")]
        ignored = [n for n in names if n.startswith("_")]
        if ignored:
            found.append((m.group(1), ignored))
    return found


def _is_stub_body(body: list[ast.stmt]) -> bool:
    stmts = list(body)
    if stmts and isinstance(stmts[0], ast.Expr) and isinstance(getattr(stmts[0], "value", None), ast.Constant) \
            and isinstance(stmts[0].value.value, str):
        stmts = stmts[1:]  # skip the docstring
    if not stmts:
        return True
    for s in stmts:
        if isinstance(s, ast.Pass):
            continue
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis:
            continue
        if isinstance(s, ast.Raise) and s.exc is not None and "NotImplementedError" in ast.unparse(s.exc):
            continue
        return False
    return True


def find_stubs(workdir: str, changed: list[str]) -> list[str]:
    found = []
    for rel in changed:
        if is_test_path(rel):
            continue
        p = Path(workdir) / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if rel.endswith(".py"):
            try:
                tree = ast.parse(text)
            except SyntaxError as e:
                found.append(f"{rel} doesn't parse: {e.msg} (line {e.lineno})")
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_stub_body(node.body):
                    # Abstract methods and Protocol stubs are legitimately empty.
                    decos = {ast.unparse(d) for d in node.decorator_list}
                    if not any("abstractmethod" in d or "overload" in d for d in decos):
                        found.append(f"{node.name}() in {rel} (line {node.lineno}) has no implementation")
        elif TEXT_STUBS.search(text):
            found.append(f"{rel} contains a not-implemented placeholder")
        elif rel.endswith(".rs"):
            for name, ignored in _rust_ignored_input_fns(text):
                found.append(f"{name}() in {rel} ignores its input(s) {', '.join(ignored)} "
                             f"(_-prefixed = unused)")
    return found
