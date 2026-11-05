from .files import make_file_tools
from .git import make_git_tools
from .terminal import make_terminal_tools


def make_tools(sb):
    """All tools for one sandbox session."""
    return make_file_tools(sb) + make_terminal_tools(sb) + make_git_tools(sb)
