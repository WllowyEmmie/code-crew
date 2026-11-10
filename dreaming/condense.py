"""Turn one session transcript (JSONL) into the evidence a dreaming subagent needs:
what was asked, how it ended, the feedback given, errors hit, commands that failed,
and what the agents wrote to memory during the session. Raw logs are mostly noise."""
import json
import re
from pathlib import Path

MAX_CHARS = 12000


def load(path: Path) -> tuple[dict, list[dict]]:
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return lines[0], lines[1:]


def condense(path: Path) -> str:
    header, msgs = load(path)
    out = [
        f"# Session {header.get('session_id')}",
        f"Request: {header.get('request')}",
        f"Outcome: {header.get('status')}",
    ]
    for fb in header.get("feedback_log") or []:
        out.append(f"Feedback sent back to the builder: {fb}")

    current = None
    nudges = 0
    for m in msgs:
        agent = m.get("agent", "agent")
        if agent != current:
            current = agent
            out.append(f"\n## {agent}")
        if m["type"] == "human" and str(m["content"]).startswith("[harness]"):
            nudges += 1
            out.append("- harness nudge: stopped without calling finish")
        for tc in m.get("tool_calls") or []:
            name, args = tc.get("name"), tc.get("args", {})
            if name == "finish":
                out.append(f"- FINISH {args.get('verdict')}: {args.get('summary')}")
            elif name == "memory_write":
                # Content deliberately left out: notes written mid-session can be wrong, and a
                # subagent must not copy them back into memory as if they were evidence.
                out.append(f"- wrote memory {args.get('path')} (reason: {args.get('reason')}) "
                           f"[content not shown: unverified]")
            elif name == "memory_delete":
                out.append(f"- deleted memory {args.get('path')} (reason: {args.get('reason')})")
            elif name in ("create_branch", "git_commit"):
                out.append(f"- {name}: {json.dumps(args)[:200]}")
        if m["type"] == "tool":
            content, tool = str(m["content"]), m.get("tool_name")
            if tool == "finish" and content != "recorded":
                out.append(f"- finish rejected by harness: {content[:300]}")
            elif content.startswith("error:"):
                out.append(f"- {tool} error: {content[:400]}")
            elif tool == "run_command":
                code = re.search(r"exit_code: (-?\d+)", content)
                if code and code.group(1) != "0":
                    out.append(f"- command failed (exit {code.group(1)}): {content[:500]}")
    # Harness-detected events, listed up front: each one is a candidate lesson.
    events = []
    for m in msgs:
        c, tool = str(m.get("content", "")), m.get("tool_name")
        if m["type"] == "tool" and c.startswith("error: tests failed"):
            events.append(f"{m.get('agent')}: commit refused because tests failed")
        elif m["type"] == "tool" and tool == "finish" and c != "recorded":
            events.append(f"{m.get('agent')}: finish rejected by harness: {c[7:200]}")
        elif m["type"] == "tool" and c.startswith("error: read memory first"):
            events.append(f"{m.get('agent')}: tried to edit files before reading memory")
    if nudges:
        events.append(f"{nudges} harness nudge(s): an agent stopped without calling finish")
    for fb in header.get("feedback_log") or []:
        events.append("feedback loop: " + fb.split("\n")[0])
    if events:
        out[3:3] = ["\n## Notable events (detected by the harness)"] + [f"- {e}" for e in events]
    text = "\n".join(out)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n...[truncated]"
