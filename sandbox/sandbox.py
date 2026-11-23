import hashlib
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from .profiles import Profile, detect_profiles, load_profiles

IMAGES_DIR = Path(__file__).parent / "images"
IMAGE_PREFIX = "code-crew-"
BASE_IMAGE = "base"  # used when no language is detected: just git + shell tools

# Ignored in every repo; each language profile adds its own (node_modules/, target/, ...).
# They go in .git/info/exclude, a local-only .gitignore, so `git add -A` never commits them.
COMMON_ARTIFACTS = ["__pycache__/", "*.pyc", ".DS_Store"]


def require_docker():
    """Fail early with a clear message when Docker isn't running, instead of a build error."""
    r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("Docker isn't running: start Docker Desktop, wait until it says "
                           "'Engine running', then try again.\n" + r.stderr.strip()[:300])


def ensure_image(name: str) -> str:
    """Build the sandbox image for `name` when it's missing, or when its Dockerfile changed
    since it was built (the Dockerfile's hash is stored as a label on the image)."""
    require_docker()
    tag = IMAGE_PREFIX + name
    dockerfile = (IMAGES_DIR / name / "Dockerfile").read_bytes()
    want = hashlib.sha256(dockerfile).hexdigest()[:16]
    r = subprocess.run(["docker", "image", "inspect", tag, "--format",
                        '{{ index .Config.Labels "code-crew.dockerfile" }}'],
                       capture_output=True, text=True)
    if r.returncode != 0 or r.stdout.strip() != want:
        why = "first time only" if r.returncode != 0 else "Dockerfile changed"
        print(f"[sandbox] building image {tag} ({why}, can take a while)...")
        subprocess.run(["docker", "build", "--label", f"code-crew.dockerfile={want}",
                        "-t", tag, str(IMAGES_DIR / name)], check=True)
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

    def __init__(self, repo_url: str, setup_timeout: int = 900, branch: str | None = None,
                 langs: list[str] | None = None):
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

        # The repo's main branch: reviewers diff against it, pull requests merge into it.
        self.base_branch = subprocess.run(
            ["git", "-C", self.workdir, "branch", "--show-current"],
            capture_output=True, text=True,
        ).stdout.strip() or "main"

        # Continuing an existing crew branch (an open pull request) instead of starting from main.
        self.continuing = branch
        if branch:
            if not branch.startswith("code-crew/"):
                self._cleanup_clone()
                raise ValueError(f"--branch must be a code-crew/ branch (got {branch!r})")
            r = subprocess.run(["git", "-C", self.workdir, "checkout", "-b", branch, "--track",
                                f"origin/{branch}"], capture_output=True, text=True)
            if r.returncode != 0:
                self._cleanup_clone()
                raise ValueError(f"branch {branch!r} isn't on GitHub: {r.stderr.strip()}")
            print(f"[sandbox] continuing branch {branch} (base: {self.base_branch})")
        self.start_branch = branch or self.base_branch
        # Where this session starts: "did the builder commit?" is measured from here.
        self.start_commit = subprocess.run(["git", "-C", self.workdir, "rev-parse", "HEAD"],
                                           capture_output=True, text=True).stdout.strip()

        self.profiles = detect_profiles(repo)
        # --lang: toolchains for languages the repo doesn't use yet (e.g. adding a Rust crate).
        self.added_langs: set[str] = set()
        if langs:
            known = {p.name: p for p in load_profiles()}
            unknown = [l for l in langs if l not in known]
            if unknown:
                self._cleanup_clone()
                raise ValueError(f"unknown --lang {unknown}; choose from: {', '.join(sorted(known))}")
            have = {p.name for p in self.profiles}
            for name in langs:
                if name not in have:
                    self.profiles.append(known[name])
                    self.added_langs.add(name)
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
            self._lint_baseline(repo)
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
            if p.name in self.added_langs:
                # New to this repo: no project files yet, so nothing to install.
                print(f"[sandbox] {p.name}: added with --lang (standard library only, no network)")
                continue
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

    def _cleanup_clone(self):
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _lint_baseline(self, repo: Path):
        """Run the whole-project lint checks once on the untouched base branch. A check that
        already fails there isn't the agent's fault, so it's recorded and not enforced
        (file-scoped checks only ever look at files the agent changed, so they need no baseline)."""
        self.lint_preexisting: set[str] = set()
        for p in self.profiles:
            if p.name in self.added_langs:
                continue  # the repo has no code in this language, so nothing is pre-existing
            for step in p.lint_steps(repo):
                if Profile.file_scoped(step):
                    continue
                r = self.run(step["run"], timeout=900, container=self.containers[p.image])
                if r["exit_code"] != 0:
                    self.lint_preexisting.add(step["run"])
        if self.lint_preexisting:
            print(f"[sandbox] lint already failing on {self.base_branch}, not enforced: "
                  + "; ".join(sorted(self.lint_preexisting)))

    def changed_files(self) -> list[str]:
        """Files added/modified compared to the base branch, committed or not (deleted excluded)."""
        r = self.run(f"git add -A && git diff --cached --name-only --diff-filter=ACMR {self.base_branch}",
                     timeout=60)
        return [f for f in r["stdout"].splitlines() if f]

    def lint_commands(self) -> list[tuple[str, str, str]]:
        """(container, command, label) for the formatter/linter checks that apply right now."""
        repo, changed = Path(self.workdir), self.changed_files()
        out = []
        for p in self.profiles:
            for step in p.lint_steps(repo):
                if step["run"] in getattr(self, "lint_preexisting", set()):
                    continue
                cmd = Profile.render(step, changed)
                if cmd:
                    label = step["run"].replace("{files}", "<changed files>")
                    out.append((self.containers[p.image], cmd, label if len(label) < 70 else label[:67] + "..."))
        return out

    def lint_summary(self) -> list[str]:
        """Human-readable list of lint checks, for the agents' prompt."""
        repo = Path(self.workdir)
        return [s["run"] + ("  (on files you change)" if Profile.file_scoped(s) else "")
                for p in self.profiles for s in p.lint_steps(repo)
                if s["run"] not in getattr(self, "lint_preexisting", set())]

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
                # Plain `bash -c`, not a login shell (-l): a login shell runs /etc/profile, which on
                # Debian-based images resets PATH and drops the toolchain folders the image added
                # (cargo in /usr/local/cargo/bin, go in /usr/local/go/bin, ...).
                ["docker", "exec", container or self.primary, "bash", "-c", cmd],
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
