"""record_finding: the easy way for agents to write memory while they work.

Agents found the full memory_write dance (read, pass the sha256, write the whole file) hard, so
findings are a one-call append to team/findings.md. The harness formats the line, adds the
session citation dreaming needs, and retries if another write got there first.
team/findings.md is an inbox: dreaming turns its raw lines into lessons and clears them.
"""
import random
import time

from langchain_core.tools import tool

from .memory_store import NEW, MemoryStore, WriteRefused

FINDINGS = "team/findings.md"
HEADER = """---
name: findings
description: Inbox of raw problems agents found during sessions (dreaming turns these into lessons)
---
"""


def make_finding_tool(store: MemoryStore, role: str, session: str, counter: dict):
    @tool
    def record_finding(what_went_wrong: str, how_to_avoid: str) -> str:
        """Record a problem you found (in your own work or someone else's) so future sessions
        avoid it. One concrete problem per call, e.g.
        what_went_wrong: "free_slots returned slots ending after day_end for meetings after the day"
        how_to_avoid: "clip or skip busy intervals outside [day_start, day_end] before merging"
        """
        what, how = " ".join(what_went_wrong.split()), " ".join(how_to_avoid.split())
        if len(what) < 10 or len(how) < 5:
            return "error: describe what went wrong and how to avoid it in a few concrete words each"
        line = f"- {role} found: {what}. Avoid: {how} ({session}: {role} finding)"
        for attempt in range(20):  # another agent may have written in between: re-read and retry
            try:
                current, digest = store.read(FINDINGS)
            except FileNotFoundError:
                current, digest = HEADER, NEW
            try:
                store.write(FINDINGS, current.rstrip("\n") + "\n" + line, digest, role, session, "finding")
                counter[role] = counter.get(role, 0) + 1
                return f"recorded finding in {FINDINGS}"
            except WriteRefused:
                time.sleep(random.uniform(0.01, 0.05) * (attempt + 1))  # spread out competing writers
                continue
        return "error: couldn't save the finding (memory kept changing); try again"

    return record_finding
