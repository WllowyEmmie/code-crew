# code-crew

A team of AI coding agents that works on a GitHub repo the way a small dev team would: a
**builder** writes the change, a **test-writer** independently tests it, a **reviewer** checks
it, and the approved work arrives as a **pull request** for a human to merge.

The agents share a markdown **memory** that they read before working and write to as they
learn. A separate **dreaming** pass later replays their session transcripts, verifies what
they wrote, and turns findings into cleaner lessons, so the crew makes fewer of the same
mistakes over time. **Evals** with hidden tests measure whether that's actually happening.

Built with LangGraph, an OpenRouter model (DeepSeek V4.1 Flash), and Docker sandboxes.

```
request ─▶ builder ─▶ test-writer ─▶ reviewer ─▶ approved? ─▶ push branch + open PR
              ▲            │              │
              └── fail ────┴── changes ───┘   (max 3 rounds)

every agent:  read memory ─▶ work in a sandbox ─▶ write memory / record findings
every 10 sessions:  dreaming ─▶ report + diff ─▶ you review ─▶ promote (or roll back)
```

## The idea: the harness enforces, the prompt guides

Anything that must always happen and can be checked mechanically is enforced in code, not
asked for in a prompt. The model can't skip it.

- **Tests and lint run inside `git_commit`.** A commit is refused if the tests fail or the
  code doesn't meet the language's standard style (ruff, rustfmt + clippy, gofmt + go vet, ...).
- **`finish(verdict, summary)` is validated.** Placeholder code (`pass`, `todo!()`, unused
  `_` parameters) is refused; the test-writer can't say "pass" without committing its own
  tests; the reviewer must answer every checklist heading; every summary names the memory it used.
- **Roles are enforced by tools.** The test-writer can only write test files, the reviewer
  can't edit anything, nobody can push or touch `main`.
- **Cheating is refused.** Lint suppressions (`# noqa`, `#[allow]`, `eslint-disable`, ...) are
  rejected; tests edited after they failed are flagged to the reviewer.
- **Model slips are repaired.** A tool call written as text is converted into a real call;
  an empty or prose reply is discarded and re-asked; the same failure three times in a row
  triggers a step-back hint; an agent near its step limit is told to finish.

Things that need judgment (what to test, who's wrong when a test fails, how to review) are
guided by prompts and **skills**.

## Quick start

Requirements: Windows/macOS/Linux, Python 3.12+, Docker Desktop (running), git, an
[OpenRouter](https://openrouter.ai/keys) API key, and a throwaway GitHub repo for the crew.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env              # then fill in REPO_URL, OPENROUTER_API_KEY, MODEL
python test_sandbox.py              # checks Docker + clone; images build on first use
```

## Usage

```powershell
# new work: a branch code-crew/<task> and, when approved, a pull request
python run_crew.py "add roman.py with to_roman(n) and from_roman(s) ..."

# a language the repo doesn't use yet (standard library only, the sandbox has no network)
python run_crew.py --lang rust "create a Rust crate with scheduler::free_slots ..."

# fix an existing crew branch without merging first (pushes onto its pull request)
python run_crew.py --branch code-crew/add-roman "from_roman should reject lowercase"

# dreaming (also starts automatically every DREAM_EVERY work sessions)
python run_dream.py                 # writes dreaming/runs/<run>/report.md and memory.diff
python run_dream.py --promote RUN   # apply it to the live memory (versioned)
python run_dream.py --rollback RUN  # undo a promotion
python run_dream.py --list

# evals: fixed tasks + hidden tests the agents never see
python run_evals.py --repeat 2
python run_evals.py --compare       # every saved run side by side
```

A failed run that made commits still pushes its branch as an unapproved draft (no pull
request), so `--branch` can continue it.

## Configuration (.env)

| Variable | What it does |
|---|---|
| `REPO_URL` | The repo the crew works on |
| `OPENROUTER_API_KEY`, `MODEL` | The model. It must support `tool_choice` (DeepSeek V4.1 Flash does) |
| `PUBLISH` | `false` keeps branches local |
| `GITHUB_TOKEN` | Fine-grained, this repo only: *Pull requests: Read and write* + *Contents: Read-only*. Opens PRs automatically; without it you get a link |
| `DREAM_EVERY` | Work sessions between automatic dreaming runs (0 = only by hand) |
| `DREAM_AUTO_PROMOTE` | `true` promotes without your review |
| `CREW_MAX_STEPS`, `CREW_MAX_TOKENS` | Steps per agent (default 100), tokens per reply (default 8000) |
| `CREW_TOOL_CHOICE` | `auto` for models without `tool_choice=required` |

## How it works

### Sandbox — `sandbox/`
Each session clones the repo into a temp folder and starts one Docker container per detected
language, all mounting the same copy. The harness installs dependencies, then **disconnects
the network** before any agent runs. 17 language profiles in `sandbox/profiles.toml` (Python,
Node/TypeScript, Bun, Deno, Go, Rust, Java/Kotlin with Maven or Gradle, Scala, C#/.NET, PHP,
Ruby, Elixir, Swift, Dart, Flutter, C/C++) say how to detect, set up, test, lint, and where tests
go. Adding a language is one profile entry plus one Dockerfile in `sandbox/images/<name>/`.

### Tools — `tools/`
20 tools, and each role only gets the ones its job needs:

| Group | Tools |
|---|---|
| Files | `read_file`, `list_dir`, `grep`, `write_file` (new files only), `edit_file` |
| Terminal | `run_command` (allowlisted commands, no chaining or redirects) |
| Git | `git_status`, `git_diff`, `git_diff_base`, `create_branch`, `git_commit` |
| Memory | `memory_list`, `memory_grep`, `memory_read`, `memory_history`, `memory_write`, `memory_delete` |
| Learning | `record_finding`, `skill_read` |
| Control | `finish` |

### The crew — `agents/`
A LangGraph graph: builder → test-writer → reviewer, with failures and requested changes
going back to the builder (up to 3 rounds). `agents/base.py` is the agent loop and its
safeguards; `agents/prompts.py` holds the instructions; `agents/crew.py` wires roles, tools
and validators. Every session saves a transcript in `transcripts/`.

### Memory — `memory/`
Markdown files with frontmatter, read **progressively**: the org rules and the
harness-measured `repo-facts.md` are always in the prompt; other notes appear as a one-line
index and are loaded with `memory_grep` / `memory_read` (an agent must read memory before it
can change files).

- `org/` — your rules and code standards. Read-only for agents.
- `team/<repo>/` — what the crew learned: `conventions.md` (every line tagged with its
  language), `lessons.md`, and `findings.md`, the inbox of problems agents found.
- `team/<repo>/repo-facts.md` — measured from the fresh clone each session (languages,
  test commands, where tests go per language, line endings, files). Never model-written.
- Every write is versioned in `memory/.history/` with author, session and reason, and
  carries the sha256 of the version it read; a stale write is refused, so concurrent agents
  re-read and merge instead of overwriting each other.

### Dreaming — `dreaming/`
Curates memory out of band: clone the memory → an orchestrator gives each new session
transcript to its own subagent (in parallel) → subagents **verify, organize and enrich** the
copy → an organizer merges duplicates and processes the findings inbox → you get a report and
a diff. Every new claim must cite the session it came from, and code paths it mentions must
exist; the organizer can only reuse existing citations. Nothing reaches the live memory until
you promote it, and every promotion can be rolled back. Eval sessions are skipped, so memory
never learns the exam answers.

### Skills — `skills/`
Step-by-step procedures you write, loaded the same progressive way (each agent sees the names
and descriptions for its role and calls `skill_read`). Memory is what this repo learned; a
skill is how to do something in any repo. See `skills/README.md`.

| Skill | Who | When |
|---|---|---|
| `debug-failing-test` | builder, test-writer | a test or commit failed |
| `write-tests-from-spec` | test-writer | choosing cases and expected values |
| `review-a-branch` | reviewer | reviewing within a step budget |
| `start-new-project-{python,rust,go}` | builder | a new module/crate in that language |

### Publishing — `tools/publish.py`
Only approved work gets a pull request. The harness (not an agent) pushes `code-crew/<task>`
with your git login. The PR title is the builder's first commit message, the body has the
request and every agent's summary. Commits are labelled `[code-crew] ...` with trailers
`Generated-by: code-crew`, `Code-crew-agent: <role>` and `Code-crew-session: <id>`, so
`git log` shows which agent wrote what. An existing remote branch is never overwritten.

### Evals — `evals/`, `run_evals.py`
Fixed tasks in `evals/tasks.json`, hidden pytest checks in `evals/hidden/`, and process
metrics from each transcript (`evals/metrics.py`): rounds, tool calls, refused commits,
rejected finishes, nudges, gate blocks, memory reads/writes, skill reads. Each result records
a fingerprint of the memory it ran with, so runs before and after a change compare directly.

## Results so far

- Baseline `eval_20261110_163412`: 4 tasks × 2 runs, **100% of hidden tests passed**, every task
  approved in one round with no refused commits.
- A Rust free-slots scheduler (overlapping, unsorted and out-of-day busy times plus validation)
  was built, tested with 23 unit + 29 independent integration tests, and approved in one round.
- The crew caught real mistakes along the way: a reviewer noticed a human edit to
  `conventions.md` had dropped the Rust lines, recorded a finding and restored them.

## Limitations

- The current eval tasks are easy (all at 100%), so they can't yet show improvement; harder
  tasks (the scheduler, a bug fix in existing code, a multi-file change) are the next step.
- Only the repo root is scanned for languages (no monorepo subfolders); no iOS/Android builds;
  no browser for visual UI checks.
- The sandbox has no network, so agents can only use the standard library and the repo's
  already-declared dependencies.
- A failed `--branch` continuation keeps its new commits local (in the temp folder printed
  at the end), so unreviewed commits never land in an open pull request.

## Layout

```
agents/        agent loop, prompts, crew graph
dreaming/      dreaming orchestrator, subagent prompts, transcript condenser
evals/         tasks, hidden tests, metrics, results
memory/        org/ (yours), team/<repo>/ (crew's), .history/ (every version)
sandbox/       Sandbox class, language profiles, Dockerfiles
skills/        SKILL.md procedures
tools/         file, terminal, git, memory, findings, skills, repo-facts, publish
run_crew.py    run the crew        run_dream.py   dreaming        run_evals.py   evals
llm.py         OpenRouter model + retries         transcripts.py session transcripts
```
