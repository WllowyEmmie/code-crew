---
name: start-new-project-python
description: Adding a new Python module or package to this repo (where it goes, style, errors)
roles: [builder]
langs: [python]
---
# Start a new Python module

- Put the module where existing code lives (repo-facts lists the files). Don't add packaging
  (pyproject.toml, setup.py, __init__.py folders) unless the request asks for it.
- No new dependencies: there's no network, so only the standard library and what's installed.
- Names: snake_case functions/variables/modules, CamelCase classes, UPPER_CASE constants.
- Every function and class gets a docstring. Add type hints if the existing code uses them.
- Errors: raise the specific built-in the request names (usually ValueError/TypeError/KeyError)
  with a message that says what was wrong. Don't return None or -1 to signal an error.
- No imports inside functions, no print() debugging left behind.
- Before git_commit: `ruff format` the file, then `ruff check`, then `python3 -m pytest -q`.
