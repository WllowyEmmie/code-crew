from langchain_core.tools import tool

# Read-only commands every repo gets. Language-specific ones (go test, cargo test, npm run ...)
# come from the sandbox's language profiles.
BASE_PREFIXES = ("ls", "cat", "head", "tail", "wc", "find")

# Chaining or redirecting would let a command sneak past the prefix check
# (e.g. "ls && rm -rf ."), so these are refused outright.
BLOCKED_TOKENS = ("&&", "||", ";", "|", "`", "$(", ">", "<", "\n")


def check_command(cmd: str, allowed: tuple[str, ...]) -> str | None:
    """Return an error message if the command isn't allowed, else None."""
    c = cmd.strip()
    if any(tok in c for tok in BLOCKED_TOKENS):
        return "error: chaining, pipes and redirects aren't allowed; run one command at a time"
    if not any(c == p or c.startswith(p + " ") for p in allowed):
        return f"error: command not allowed. Allowed: {', '.join(allowed)}"
    return None


def make_terminal_tools(sb):
    allowed = BASE_PREFIXES + sb.allowed_prefixes()

    @tool
    def run_command(cmd: str, timeout: int = 120) -> str:
        """Run one allowlisted shell command in the repo (tests, linters, builds).
        Returns the exit code, stdout and stderr."""
        err = check_command(cmd, allowed)
        if err:
            return err
        r = sb.run(cmd, timeout=min(timeout, 600), container=sb.container_for(cmd.strip()))
        return f"exit_code: {r['exit_code']}\n--- stdout ---\n{r['stdout']}\n--- stderr ---\n{r['stderr']}"

    return [run_command]
