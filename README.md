# code-crew

A team of LangGraph coding agents (builder, test-writer, reviewer) that work on a GitHub repo,
share file-based markdown memory, and improve it with a nightly "dreaming" pass.

## Layout
- `sandbox/`  – Docker image + `Sandbox` class (one container per session, no network)
- `tools/`    – file, terminal and git tools (step 2)
- `agents/`   – builder, test-writer, reviewer (later)
- `dreaming/` – nightly job: orchestrator + one subagent per session transcript (step 6)
- `memory/org/`  – read-only memory (style guide, security rules)
- `memory/team/` – read-write memory (agents' working notes)

## Build order
1. Sandbox + file/terminal/git tools
2. One agent, no memory
3. Memory files + memory tools
4. Second and third agents, versioning, hash check
5. Read-only / read-write scopes
6. Dreaming
7. Evals

## Step 1: run the sandbox
1. Start Docker Desktop.
2. `docker build -t agent-sandbox ./sandbox`
3. `pip install -r requirements.txt`, copy `.env.example` to `.env` and set `REPO_URL` to a throwaway repo.
4. `python test_sandbox.py`

If it prints the repo's files and last three commits, step 1 works.

## Step 2: tools
- `llm.py` – DeepSeek via OpenRouter (set `OPENROUTER_API_KEY` and `MODEL` in `.env`)
- `tools/files.py` – read_file, list_dir, grep, write_file, edit_file (paths locked to the repo, `.git` off limits)
- `tools/terminal.py` – run_command with an allowlist; no chaining, pipes or redirects
- `tools/git.py` – git_status, git_diff, create_branch (`agent/` only), git_commit (refuses main/master)

Run: `pip install -r requirements.txt`, then `python test_tools.py` and `python test_llm.py`.

## Memory (tools/memory_store.py, tools/memory.py)
Markdown files with frontmatter, with a standalone API on top:
- `memory/org/` shared org knowledge, read-only for agents
- `memory/team/<repo>/` team working memory; `repo-facts.md` is measured by the harness
- Reading is progressive: org rules + repo-facts are always in the prompt; team notes via
  memory_list (descriptions) -> memory_grep / memory_read. Agents must read before editing.
- Writing is autonomous: every agent can memory_write what it judges worth keeping.
- Versioning: every write is attributed (author, session, time) and kept in `memory/.history/`.
- Concurrency: every write carries the sha256 of the version the writer read; stale writes
  are refused by the harness.

## Dreaming (dreaming/)
Out-of-band batch pass whose only job is curating memory:
clone memory -> orchestrator -> one subagent per session transcript (in parallel) ->
subagents verify, organize and enrich the copy -> you review the diff -> promote.
- `python run_dream.py` / `--promote RUN` / `--rollback RUN` / `--list` / `--auto-promote`
- After the per-session subagents, one organizer pass merges near-duplicates across sessions
  (it may only reuse citations already in memory, so it cannot add new claims).
- Reports and diffs: `dreaming/runs/<run>/report.md`

## Language profiles (sandbox/profiles.toml)
Python, Node/TypeScript (npm, yarn, pnpm), Bun, Deno, Go, Rust, Java/Kotlin (Maven, Gradle),
Scala, C#/.NET, PHP, Ruby, Elixir, Swift, Dart, Flutter, C/C++ (CMake, Make).
Add a language: one entry in `sandbox/profiles.toml` + one Dockerfile in `sandbox/images/<name>/`.

Per session: one container per detected language, all mounting the same repo (a Go + Next.js
repo gets a Go container and a Node container). Containers start with network -> harness
installs deps -> network disconnected and verified -> agent works. run_command is routed to
the matching container; git_commit runs every language's tests. Images build on first use.
Limits: only the repo root is checked (no monorepo subfolders yet); no iOS/Android builds;
no browser for visual UI checks.

## The crew (agents/)
`python run_crew.py "add a shout() function in hello.py"`

builder -> test-writer -> reviewer, as fixed edges in a LangGraph graph
(`agents/crew.py`). Test failures or requested changes go back to the builder (max 3 rounds).
- `agents/base.py`: the agent loop. Agents must end by calling `finish(verdict, summary)`.
- `agents/prompts.py`: each agent's instructions.
- Roles are enforced by tools: the test-writer can only write test files, the reviewer
  can't edit code.
- Every run saves `transcripts/<session>.jsonl` with each agent's messages.

## Evals (evals/, run_evals.py)
Fixed tasks (`evals/tasks.json`) run through the full crew; then HIDDEN tests the agents never
see (`evals/hidden/`) check what they built. Process metrics come from each transcript:
rounds, tool calls, refused commits, rejected finishes, nudges, gate blocks, memory reads/writes.
Each run records which memory it used, so runs before and after a dreaming pass compare directly.
- `python run_evals.py` / `--tasks roman lru` / `--repeat 2` / `--compare`
- Eval sessions are tagged and skipped by dreaming (else memory learns the exam answers);
  `run_dream.py --include-evals` overrides.

## Code standards (lint)
Each language profile has `lint` checks that git_commit runs after the tests (ruff, gofmt/go vet,
rustfmt/clippy, prettier/eslint, dotnet format, PHP_CodeSniffer PSR-12, RuboCop, dart format/analyze,
mix format, swift-format, clang-format; Java/Kotlin via the repo's Spotless/Checkstyle/ktlint).
- File-scoped checks only look at files the agent changed; whole-project checks that already fail
  on the base branch are recorded at session start and not enforced.
- The repo's own lint config always wins. `memory/org/code-standards.md` summarizes the standards.
- Images rebuild automatically when their Dockerfile changes (hash label on the image).

## Publishing (tools/publish.py)
Agents work offline on a `code-crew/<task>` branch and never push or touch main.
When the reviewer approves, the harness (on your machine) pushes the branch with your normal git
login and opens a pull request for you to merge or close.
- Every commit: `[code-crew] ...` subject, author `code-crew`, trailers `Generated-by: code-crew`,
  `Code-crew-agent: <builder|test-writer|reviewer>`, `Code-crew-session: <id>`.
- An existing remote branch is never overwritten (a session suffix is added).
- `GITHUB_TOKEN` (fine-grained, this repo, Pull requests: read/write) opens the PR automatically;
  without it you get a compare link. `PUBLISH=false` keeps everything local. Evals never publish.
- `python run_crew.py --branch code-crew/<task> "fix ..."` continues an existing crew branch
  instead of starting from main: the agents start from its code, commit on it (no new branch),
  and the push updates its open pull request, with a comment summarizing the round.
- `python run_crew.py --lang rust "..."` also starts toolchains for languages the repo doesn't use
  yet. The agents create the project files (e.g. Cargo.toml); standard library only (no network).
- git_commit refuses source files in a language the sandbox has no toolchain for (e.g. a .rs file
  without --lang rust), since nothing could build, test or lint them.
