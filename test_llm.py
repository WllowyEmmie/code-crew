"""Checks the model on OpenRouter can be reached and makes a tool call."""
from langchain_core.tools import tool

from llm import get_llm


@tool
def list_dir(path: str = ".") -> str:
    """List files and folders at `path`."""
    return "README.md\nmain.go\n"


llm = get_llm().bind_tools([list_dir])
msg = llm.invoke("What files are in the root of the repo? Use your tools.")
print("tool_calls:", msg.tool_calls)
print("content:", msg.content)
