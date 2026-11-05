"""Calls each tool directly (no model) to check they work inside the sandbox."""
import os

from dotenv import load_dotenv

from sandbox.sandbox import Sandbox
from tools import make_tools

load_dotenv()
sb = Sandbox(os.environ["REPO_URL"])
t = {tool.name: tool for tool in make_tools(sb)}


def show(name, args=None):
    print(f"\n>>> {name}({args or ''})")
    print(t[name].invoke(args or {}))


try:
    show("create_branch", {"name": "agent/tools-smoke-test"})
    show("write_file", {"path": "hello.py", "content": "def greet():\n    return 'hi'\n"})
    show("read_file", {"path": "hello.py"})
    show("edit_file", {"path": "hello.py", "old_str": "'hi'", "new_str": "'hello'"})
    show("list_dir", {"path": "."})
    show("grep", {"pattern": "hello"})
    show("run_command", {"cmd": "cat hello.py"})
    show("git_status")
    show("git_diff")
    show("git_commit", {"message": "Add greet() (tools smoke test)"})
    show("git_status")

    print("\n--- these should all be refused ---")
    show("read_file", {"path": "../secret.txt"})
    show("read_file", {"path": ".git/config"})
    show("run_command", {"cmd": "ls && rm -rf ."})
    show("run_command", {"cmd": "curl https://example.com"})
    show("create_branch", {"name": "main"})
finally:
    sb.close()
