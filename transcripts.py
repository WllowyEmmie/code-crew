import json
from datetime import datetime, timezone
from pathlib import Path

TRANSCRIPT_DIR = Path(__file__).parent / "transcripts"


def save_transcript(session_id: str, repo: str, request: str, status: str,
                    feedback_log: list, conversations: dict, kind: str = "work") -> Path:
    """Save one session as JSONL: a header line, then one line per message, tagged with
    the agent that produced it (e.g. "builder#1", "reviewer#2"). Dreaming reads these."""
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    path = TRANSCRIPT_DIR / f"{session_id}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "session_id": session_id,
            "kind": kind,  # "work" or "eval"
            "repo": repo,
            "request": request,
            "status": status,
            "feedback_log": feedback_log,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }) + "\n")
        for agent, messages in conversations.items():
            for m in messages:
                f.write(json.dumps({
                    "agent": agent,
                    "type": m.type,  # human / ai / tool
                    "content": m.content,
                    "tool_calls": getattr(m, "tool_calls", None) or None,
                    "tool_name": getattr(m, "name", None) if m.type == "tool" else None,
                }) + "\n")
    return path
