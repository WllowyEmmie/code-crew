"""Process metrics for one crew session, read from its transcript. Together with the hidden
tests they show HOW a result was reached, not just whether it was right: fewer rounds, fewer
refused commits and fewer harness corrections mean the agents are working better."""
import json
from pathlib import Path


def session_metrics(transcript: Path) -> dict:
    lines = [json.loads(l) for l in transcript.read_text(encoding="utf-8").splitlines() if l.strip()]
    msgs = lines[1:]
    tool_calls = [tc for m in msgs for tc in (m.get("tool_calls") or [])]
    tools = [m for m in msgs if m["type"] == "tool"]
    names = [tc["name"] for tc in tool_calls]
    rounds = len({m["agent"] for m in msgs if str(m.get("agent", "")).startswith("builder#")})
    return {
        "rounds": rounds,
        "tool_calls": len(tool_calls),
        "refused_commits": sum(str(t["content"]).startswith("error: tests failed") for t in tools),
        "finish_rejected": sum(t.get("tool_name") == "finish" and t["content"] != "recorded" for t in tools),
        "nudges": sum(m["type"] == "human" and str(m["content"]).startswith("[harness]") for m in msgs),
        "gate_blocks": sum(str(t["content"]).startswith("error: read memory first") for t in tools),
        "tool_errors": sum(str(t["content"]).startswith("error:") for t in tools),
        "memory_reads": names.count("memory_read") + names.count("memory_grep"),
        "memory_writes": names.count("memory_write"),
    }
