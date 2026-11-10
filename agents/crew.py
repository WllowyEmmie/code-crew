"""The crew pipeline: builder -> test-writer -> reviewer.

Agents write memory themselves while they work (in-band). Consolidating it (verify, organize,
enrich) is dreaming's job, out of band: see dreaming/.

The order is fixed edges in the graph, not decided by an LLM. Failures loop back to the
builder with the feedback, up to MAX_ROUNDS times.
"""
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from tools.files import is_test_path, make_file_tools
from tools.git import make_git_tools
from tools.conventions_check import conventions_error
from tools.findings import make_finding_tool
from tools.memory import make_memory_tools
from tools.stubs import find_stubs
from tools.terminal import make_terminal_tools

from . import prompts
from .base import get_verdict, make_agent, run_agent

MAX_ROUNDS = 3


class CrewState(TypedDict, total=False):
    request: str
    round: int
    status: str            # running / approved / failed: <reason>
    feedback: str          # what the builder must fix next round
    builder_summary: str
    test_summary: str
    review_summary: str
    feedback_log: list     # every piece of feedback sent back to the builder this session
    test_changes_after_fail: list  # test edits made after tests failed (shown to the reviewer)
    conversations: dict    # agent name -> list of messages (for the transcript)


def edits_after_failure(msgs) -> list[str]:
    """Test edits the test-writer made AFTER its tests failed against the builder's code.
    Read from its own transcript, so it can't be hidden: a test changed to match buggy output
    is the classic way a real bug slips through (it once swapped a failing '25:00' case for
    one the code already handled)."""
    calls = {}
    for m in msgs:
        for tc in getattr(m, "tool_calls", None) or []:
            calls[tc["id"]] = tc
    failed, changes = False, []
    for m in msgs:
        if m.type != "tool":
            continue
        tc = calls.get(getattr(m, "tool_call_id", None), {})
        name, args = tc.get("name"), tc.get("args", {})
        if name in ("run_command", "git_commit") and "tests failed" in m.content or (
                name == "run_command" and "pytest" in str(args.get("cmd", "")) + m.content
                and "exit_code: 0" not in m.content and m.content.startswith("exit_code")):
            failed = True
        elif failed and name == "edit_file" and m.content.startswith("edited"):
            changes.append(f"{args.get('path')}:\n- before: {str(args.get('old_str'))[:400]}\n"
                           f"+ after:  {str(args.get('new_str'))[:400]}")
        elif failed and name == "write_file" and m.content.startswith("wrote"):
            changes.append(f"{args.get('path')}: rewritten after the tests failed")
    return changes


def pick(tools, *names):
    by_name = {t.name: t for t in tools}
    return [by_name[n] for n in names]


class ReadGate:
    """Progressive reading, enforced: before an agent may change any file it must have looked at
    the memory index (memory_list) and pulled in something relevant (memory_grep / memory_read).
    The talk's rule is "frontmatter + grep, never the whole store", so we never paste team notes
    into the prompt; instead the harness makes sure the agent goes and reads them."""

    def __init__(self, store):
        self.store = store
        self.reset()

    def reset(self):
        self.listed = self.consulted = False

    def mark(self, kind: str):
        if kind == "list":
            self.listed = True
        else:
            self.consulted = True

    def guard(self, path: str) -> str | None:
        notes = [p for p, _ in self.store.files() if p.startswith("team/") and p != "team/repo-facts.md"]
        if self.listed and (self.consulted or not notes):
            return None
        return ("read memory first: call memory_list, then memory_read or memory_grep the notes "
                "relevant to this task before changing files.")


def build_crew(llm, sb, store, context: str):
    rules = prompts.SHARED_RULES
    fill = lambda p: p.format(rules=rules, context=context)

    findings = {}  # role -> findings recorded in the current agent run (reset by each node)

    def memory_for(agent: str):
        """Each agent gets its own memory tools: writes are attributed to it, reads open its gate.
        Agents decide for themselves what's worth recording ("autonomy wins"); record_finding
        makes the common case (a problem found) a single easy call."""
        gate = ReadGate(store)
        tools = make_memory_tools(store, author=agent, session=sb.session_id, on_read=gate.mark,
                                  write_check=conventions_error)
        tools.append(make_finding_tool(store, agent, sb.session_id, findings))
        return gate, tools

    def require_finding(role: str, verdict: str, negative: str):
        """A test-writer 'fail' or reviewer 'changes' means a problem was found: it must be recorded."""
        if verdict == negative and not findings.get(role):
            return (f"before finish('{negative}'), call record_finding for each problem you found "
                    "(what went wrong, how to avoid it). That's how the crew learns from it: "
                    "dreaming turns findings into lessons for future builders.")
        return None

    builder_gate, builder_mem = memory_for("builder")
    tw_gate, tw_mem = memory_for("test-writer")
    reviewer_gate, reviewer_mem = memory_for("reviewer")

    terminal = make_terminal_tools(sb)
    # Each agent gets its own git tools so its commits are labelled with its name.
    git = make_git_tools(sb, "builder")
    tw_git = make_git_tools(sb, "test-writer")
    reviewer_git = make_git_tools(sb, "reviewer")
    files_all = make_file_tools(sb, write_guard=builder_gate.guard)

    def tests_only_guard(path):
        return tw_gate.guard(path) or (
            None if is_test_path(path) else f"you may not change {path}; you can only write test files")

    files_tests_only = make_file_tools(sb, write_guard=tests_only_guard)
    files_read = pick(files_all, "read_file", "list_dir", "grep")

    # Each agent only gets the tools its job needs; the harness enforces the roles.
    # Builder and test-writer must say which memory they applied (or "none relevant"),
    # so using memory is part of the deliverable, not optional.
    def check_builder(verdict, summary):
        """'done' must mean implemented: refuse it while any changed function is still a stub."""
        stubs = find_stubs(sb.workdir, sb.changed_files())
        if stubs:
            return ("the work isn't done, these are placeholders without an implementation: "
                    + "; ".join(stubs[:5]) + ". Implement them fully, commit, then finish.")
        return check_memory_section(verdict, summary)

    def check_memory_section(verdict, summary):
        if "memory:" not in summary.lower():
            return ('add a "Memory:" line to your summary: which notes from memory (lessons, '
                    'conventions, repo-facts) you applied, or "none relevant".')
        return None

    builder = make_agent(llm, files_all + terminal + git + builder_mem,
                         fill(prompts.BUILDER), ("done",), validate=check_builder,
                         learn_from_failures=True)
    # The test-writer's "pass" is only accepted if it actually committed test files this round.
    # (Without this check it once reported "added tests" after only re-running the builder's.)
    tw_round = {"start": None}

    def check_test_writer(verdict, summary):
        err = require_finding("test-writer", verdict, "fail") or check_memory_section(verdict, summary)
        if err or verdict != "pass":
            return err
        changed = sb.run(f"git diff --name-only {tw_round['start']} HEAD", timeout=30)["stdout"].split()
        if not any(is_test_path(f) for f in changed):
            return ("you haven't committed any tests of your own. Tests the builder wrote don't count. "
                    "Write tests for the change and git_commit them, or use verdict nothing_to_test "
                    "if the change has no testable behavior.")
        return None

    test_writer = make_agent(llm, files_tests_only + terminal
                             + pick(tw_git, "git_status", "git_diff", "git_diff_base", "git_commit") + tw_mem,
                             fill(prompts.TEST_WRITER), ("pass", "fail", "nothing_to_test"),
                             validate=check_test_writer, learn_from_failures=True)
    # The reviewer's summary must answer every checklist item, so it can't approve on a glance.
    def check_reviewer(verdict, summary):
        err = require_finding("reviewer", verdict, "changes")
        if err:
            return err
        missing = [h for h in prompts.REVIEW_CHECKLIST if h.lower() not in summary.lower()]
        if missing:
            return ("your summary must answer every checklist item. Missing: "
                    + ", ".join(missing) + ". Use each one as a heading.")
        return None

    reviewer = make_agent(llm, files_read + terminal + pick(reviewer_git, "git_status", "git_diff_base") + reviewer_mem,
                          fill(prompts.REVIEWER), ("approve", "changes"), validate=check_reviewer,
                          learn_from_failures=True)
    def convo(state, name, msgs):
        c = dict(state.get("conversations") or {})
        c[f"{name}#{state.get('round', 1)}"] = msgs
        return c

    def has_commits() -> bool:
        """New commits in THIS session (when continuing a branch, its old commits don't count)."""
        r = sb.run(f"git log --oneline {sb.start_commit}..HEAD", timeout=30)
        return r["exit_code"] == 0 and bool(r["stdout"].strip())

    # ---------- nodes ----------
    def builder_node(state: CrewState):
        builder_gate.reset()
        findings.clear()
        rnd = state.get("round", 0) + 1
        if rnd == 1:
            msgs = [HumanMessage(state["request"])]
        else:
            msgs = [HumanMessage(
                f"Original request: {state['request']}\n\n"
                f"Round {rnd}. You're on your branch with your earlier commits. "
                f"Fix this feedback, then commit:\n\n{state['feedback']}"
            )]
        msgs = run_agent(builder, msgs, f"builder (round {rnd})")
        verdict, summary = get_verdict(msgs)
        update = {"round": rnd, "builder_summary": summary,
                  "conversations": convo({**state, "round": rnd}, "builder", msgs)}
        if verdict != "done":
            update["status"] = "failed: builder didn't finish"
        elif not has_commits():
            update["status"] = "failed: builder made no commit"
        else:
            update["status"] = "running"
        return update

    def test_writer_node(state: CrewState):
        tw_gate.reset()
        findings.clear()
        tw_round["start"] = sb.run("git rev-parse HEAD", timeout=15)["stdout"].strip()
        msgs = run_agent(test_writer, [HumanMessage(
            f"Request the builder worked on: {state['request']}\n\n"
            f"Builder's summary: {state.get('builder_summary', '')}"
        )], f"test-writer (round {state['round']})")
        verdict, summary = get_verdict(msgs)
        update = {"test_summary": summary, "conversations": convo(state, "test-writer", msgs),
                  "test_changes_after_fail": edits_after_failure(msgs)}
        if verdict == "fail":
            update["feedback"] = f"The test-writer's tests fail:\n{summary}"
            update["feedback_log"] = (state.get("feedback_log") or []) + [update["feedback"]]
        elif verdict not in ("pass", "nothing_to_test"):
            update["status"] = "failed: test-writer didn't finish"
        return update

    def reviewer_node(state: CrewState):
        reviewer_gate.reset()
        findings.clear()
        # Hand the reviewer the whole diff up front instead of hoping it fetches and reads it.
        diff = sb.run(f"git diff {sb.base_branch}", timeout=30)["stdout"]
        msgs = run_agent(reviewer, [HumanMessage(
            f"Request: {state['request']}\n\n"
            f"Builder's summary: {state.get('builder_summary', '')}\n\n"
            f"Test-writer's summary: {state.get('test_summary', '')}\n\n"
            + (("WARNING (detected by the harness): the test-writer changed these tests AFTER "
                "they failed against the builder's code. For each one, check the new expectation "
                "against the request. If a correct test was weakened or replaced to match buggy "
                "output, that's a hidden bug: request changes and name it.\n\n"
                + "\n\n".join(state["test_changes_after_fail"]) + "\n\n")
               if state.get("test_changes_after_fail") else "")
            + f"Full diff against {sb.base_branch}:\n```diff\n{diff[:20000]}\n```"
        )], f"reviewer (round {state['round']})")
        verdict, summary = get_verdict(msgs)
        update = {"review_summary": summary, "conversations": convo(state, "reviewer", msgs)}
        if verdict == "approve":
            update["status"] = "approved"
        elif verdict == "changes":
            update["feedback"] = f"The reviewer requested changes:\n{summary}"
            update["feedback_log"] = (state.get("feedback_log") or []) + [update["feedback"]]
        else:
            update["status"] = "failed: reviewer didn't finish"
        return update

    # ---------- routing ----------
    def after_builder(state):
        return END if state["status"].startswith("failed") else "test_writer"

    def after_tests(state):
        if state["status"].startswith("failed"):
            return END
        if state.get("feedback") and state["feedback"].startswith("The test-writer"):
            return "give_up" if state["round"] >= MAX_ROUNDS else "builder"
        return "reviewer"

    def after_review(state):
        if state["status"] == "approved" or state["status"].startswith("failed"):
            return END
        return "give_up" if state["round"] >= MAX_ROUNDS else "builder"

    def give_up(state):
        return {"status": f"failed: still not passing after {MAX_ROUNDS} rounds"}

    def clear_feedback_then(node_fn):
        """Feedback is consumed by the builder; clear it so old feedback can't re-route."""
        def wrapped(state):
            update = node_fn(state)
            update.setdefault("feedback", "")
            return update
        return wrapped

    g = StateGraph(CrewState)
    g.add_node("builder", clear_feedback_then(builder_node))
    g.add_node("test_writer", test_writer_node)
    g.add_node("reviewer", reviewer_node)
    g.add_node("give_up", give_up)
    g.add_edge(START, "builder")
    g.add_conditional_edges("builder", after_builder, ["test_writer", END])
    g.add_conditional_edges("test_writer", after_tests, ["builder", "reviewer", "give_up", END])
    g.add_conditional_edges("reviewer", after_review, ["builder", "give_up", END])
    g.add_edge("give_up", END)
    return g.compile()
