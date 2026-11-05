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

## Step 3: the builder (one agent, no memory)
- `agents/builder.py` – LangGraph loop: model -> tools -> model ... until it answers without a tool call
- `run_builder.py` – runs the builder on a request in a fresh sandbox, prints each step
- `transcripts.py` – saves every session to `transcripts/<session_id>.jsonl` (input for dreaming)

Run: `pip install -r requirements.txt`, then `python run_builder.py "add a greet() function in hello.py"`

## Step 4: memory
- `tools/memory.py` – memory_list (frontmatter only), memory_grep, memory_read, memory_write
- `memory/org/` – your rules, read-only for agents
- `memory/team/<repo>/` – agents' notes, one folder per target repo
- The builder reads memory at the start of a run and updates it at the end.

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
