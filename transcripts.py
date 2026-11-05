import json
from datetime import datetime, timezone
from pathlib import Path

TRANSCRIPT_DIR = Path(__file__).parent / "transcripts"


def save_transcript(session_id: str, agent: str, request: str, messages: list) -> Path:
    """Save one session as JSONL: a header line, then one line per message.
    Dreaming will read these later."""
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    path = TRANSCRIPT_DIR / f"{session_id}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        header = {
            "session_id": session_id,
            "agent": agent,
            "request": request,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        f.write(json.dumps(header) + "\n")
        for m in messages:
            f.write(json.dumps({
                "type": m.type,  # human / ai / tool
                "content": m.content,
                "tool_calls": getattr(m, "tool_calls", None) or None,
                "tool_name": getattr(m, "name", None) if m.type == "tool" else None,
            }) + "\n")
    return path
