import subprocess
import tempfile
import uuid
from pathlib import Path

from .profiles import detect_profiles

IMAGES_DIR = Path(__file__).parent / "images"
IMAGE_PREFIX = "code-crew-"
BASE_IMAGE = "base"  # used when no language is detected: just git + shell tools

# Ignored in every repo; each language profile adds its own (node_modules/, target/, ...).
# They go in .git/info/exclude, a local-only .gitignore, so `git add -A` never commits them.
COMMON_ARTIFACTS = ["__pycache__/", "*.pyc", ".DS_Store"]


def ensure_image(name: str) -> str:
    """Build the sandbox image for `name` the first time it's needed."""
    tag = IMAGE_PREFIX + name
    exists = subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0
    if not exists:
        print(f"[sandbox] building image {tag} (first time only, can take a while)...")
        subprocess.run(["docker", "build", "-t", tag, str(IMAGES_DIR / name)], check=True)
    return tag


class Sandbox:
    """Docker containers for one agent session, all mounting the same repo at /workspace.

    One container per language image, so a Go + Next.js repo gets a Go container and a
    Node container instead of one huge image with every toolchain.

    Lifecycle:
      1. clone the repo on the host and detect its languages (sandbox/profiles.toml)
      2. start one container per language WITH network
      3. the harness (never the agent) installs dependencies
      4. disconnect every container's network and verify it's gone
      5. the agent works with no network
    """

    def __init__(self, repo_url: str, setup_timeout: int = 900):
        self.session_id = f"sess_{uuid.uuid4().hex[:12]}"
        self.workdir = tempfile.mkdtemp(prefix=self.session_id)
        self.containers: dict[str, str] = {}  # image name -> container name
        repo = Path(self.workdir)

        # core.autocrlf=false keeps line endings exactly as committed; otherwise Windows git
        # converts files to CRLF and git inside the Linux container sees every file as modified.
        subprocess.run(
            ["git", "clone", "-c", "core.autocrlf=false", repo_url, self.workdir],
            check=True,
        )

        # The branch the agents start from; reviewers diff against it.
        self.base_branch = subprocess.run(
            ["git", "-C", self.workdir, "branch", "--show-current"],
            capture_output=True, text=True,
        ).stdout.strip() or "main"

        self.profiles = detect_profiles(repo)
        artifacts = COMMON_ARTIFACTS + [a for p in self.profiles for a in p.artifacts]
        with open(repo / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
            f.write("\n# code-crew: never commit dependencies or build output\n")
            f.write("\n".join(dict.fromkeys(artifacts)) + "\n")

        images = list(dict.fromkeys(p.image for p in self.profiles)) or [BASE_IMAGE]
        names = ", ".join(p.name for p in self.profiles) or "none detected"
        print(f"[sandbox] languages: {names} -> containers: {', '.join(images)}")

        try:
            for image in images:
                self.containers[image] = self._start(image)
            self.primary = self.containers[images[0]]  # runs git and the base commands
            self._setup(repo, setup_timeout)
            self._cut_network()
        except Exception:
            self.close()
            raise

    def _start(self, image: str) -> str:
        tag = ensure_image(image)
        name = f"{self.session_id}-{image}"
        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", name,
                # Starts on Docker's default "bridge" network so setup can download
                # dependencies; _cut_network() removes it before the agent runs.
                # Stop Python/pytest leaving __pycache__ and .pytest_cache in the repo.
                "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-e", "PYTEST_ADDOPTS=-p no:cacheprovider",
                # CI=true stops Jest/Vitest/CRA test runners from starting in watch mode.
                "-e", "CI=true",
                "-v", f"{self.workdir}:/workspace",
                "-w", "/workspace",
                tag,
                "sleep", "infinity",
            ],
            check=True,
            capture_output=True,
        )
        return name

    def _setup(self, repo: Path, timeout: int):
        for p in self.profiles:
            for cmd in p.setup_commands(repo):
                print(f"[sandbox] setup ({p.name}): {cmd}")
                r = self.run(cmd, timeout=timeout, container=self.containers[p.image])
                if r["exit_code"] != 0:
                    raise RuntimeError(f"setup failed: {cmd}\n{r['stdout'][-2000:]}\n{r['stderr'][-3000:]}")

    def _cut_network(self):
        for name in self.containers.values():
            subprocess.run(["docker", "network", "disconnect", "bridge", name], check=True)
            # Verify instead of trusting: only the loopback interface should be left.
            interfaces = self.run("ls /sys/class/net", timeout=15, container=name)["stdout"].split()
            if interfaces != ["lo"]:
                raise RuntimeError(f"network still attached to {name}: {interfaces}")
        print("[sandbox] network disconnected")

    def test_commands(self) -> list[tuple[str, str]]:
        """(container, command) pairs. Worked out fresh each time, since the agent may
        add the repo's first test file."""
        repo = Path(self.workdir)
        return [(self.containers[p.image], cmd) for p in self.profiles for cmd in p.test_commands(repo)]

    def allowed_prefixes(self) -> tuple[str, ...]:
        return tuple(a for p in self.profiles for a in p.allowed)

    def container_for(self, cmd: str) -> str:
        """Send a command to the container of the language it belongs to (longest matching
        prefix wins); anything else (ls, cat, git) runs in the primary container."""
        best, best_len = self.primary, 0
        for p in self.profiles:
            for prefix in p.allowed:
                if (cmd == prefix or cmd.startswith(prefix + " ")) and len(prefix) > best_len:
                    best, best_len = self.containers[p.image], len(prefix)
        return best

    def run(self, cmd: str, timeout: int = 120, container: str | None = None) -> dict:
        """Run a shell command in a container (the primary one by default)."""
        try:
            r = subprocess.run(
                ["docker", "exec", container or self.primary, "bash", "-lc", cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            return {
                "exit_code": r.returncode,
                "stdout": r.stdout[-10000:],
                "stderr": r.stderr[-10000:],
            }
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": f"timed out after {timeout}s"}

    def close(self):
        """Remove the containers (the repo copy in workdir is kept)."""
        for name in self.containers.values():
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
