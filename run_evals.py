"""Evals: proof that the crew improves.

Runs a fixed set of tasks (evals/tasks.json) through the full crew, then runs HIDDEN tests the
agents never see (evals/hidden/) against what they built. Each run is saved, so you can
re-run after every dreaming pass and compare: is the code more correct, and did it take
fewer rounds, refused commits and harness corrections?

  python run_evals.py                  run every task once
  python run_evals.py --tasks roman lru
  python run_evals.py --repeat 2       run each task twice (models vary run to run)
  python run_evals.py --compare        show every saved run side by side
"""
import argparse
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
load_dotenv(ROOT / ".env")

from evals.metrics import session_metrics  # noqa: E402
from run_crew import MEMORY_DIR, repo_name, run_session  # noqa: E402

EVALS = ROOT / "evals"
RESULTS = EVALS / "results"
COLUMNS = ["hidden_pass", "rounds", "tool_calls", "refused_commits", "finish_rejected",
           "nudges", "gate_blocks", "memory_reads", "memory_writes", "skill_reads"]


def memory_fingerprint(repo: str) -> str:
    """Short hash of the team notes, so each result records which memory it ran with."""
    folder = MEMORY_DIR / "team" / repo
    h = hashlib.sha256()
    for f in sorted(folder.glob("*.md")):
        if f.name != "repo-facts.md":
            h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()[:10]


def hidden_check(task: dict):
    """Runs after the crew finishes and before the sandbox closes: copy in the hidden tests
    and run them where the agents worked. The agents never saw these files."""
    def after(sb, state):
        test = task["hidden_test"]
        shutil.copy(EVALS / "hidden" / test, Path(sb.workdir) / test)
        r = sb.run(f"python3 -m pytest -q {test}", timeout=300)
        out = r["stdout"] + r["stderr"]
        passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
        failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
        errors = int(m.group(1)) if (m := re.search(r"(\d+) error", out)) else 0
        total = passed + failed + errors
        print(f"\n[eval] hidden tests for {task['id']}: {passed}/{total} passed")
        return {"hidden_passed": passed, "hidden_total": total,
                "hidden_failures": [l for l in out.splitlines() if l.startswith(("FAILED", "ERROR"))][:20]}
    return after


def run(task_ids: list[str] | None, repeat: int):
    repo_url = os.environ["REPO_URL"]
    repo = repo_name(repo_url)
    tasks = json.loads((EVALS / "tasks.json").read_text(encoding="utf-8"))
    tasks = [t for t in tasks if not task_ids or t["id"] in task_ids]
    run_id = datetime.now().strftime("eval_%Y%m%d_%H%M%S")
    results = []
    for task in tasks:
        for i in range(repeat):
            print(f"\n##################### eval {task['id']} ({i + 1}/{repeat}) #####################")
            out = run_session(task["request"], repo_url, after=hidden_check(task), kind="eval")
            total = out.get("hidden_total", 0)
            results.append({
                "task": task["id"],
                "status": out.get("status"),
                "hidden_pass": round(out.get("hidden_passed", 0) / total, 3) if total else 0.0,
                "hidden_failures": out.get("hidden_failures", []),
                "session": out["session_id"],
                **session_metrics(Path(out["transcript"])),
            })
    RESULTS.mkdir(parents=True, exist_ok=True)
    record = {"run_id": run_id, "memory": memory_fingerprint(repo), "results": results}
    (RESULTS / f"{run_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\n[eval] saved {RESULTS / f'{run_id}.json'}")
    compare()


def summarize(record: dict) -> dict:
    rows = record["results"]
    return {c: round(sum(r[c] for r in rows) / len(rows), 2) for c in COLUMNS} if rows else {}


def compare():
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RESULTS.glob("eval_*.json"))]
    if not runs:
        print("no eval runs yet")
        return
    print("\nAverages per task run (hidden_pass: 1.0 = every hidden test passed; lower is better for the rest)")
    print(f"{'run':22} {'memory':10} " + " ".join(f"{c[:12]:>12}" for c in COLUMNS))
    for rec in runs:
        s = summarize(rec)
        print(f"{rec['run_id']:22} {rec['memory']:10} " + " ".join(f"{s.get(c, '-'):>12}" for c in COLUMNS))
    last = runs[-1]
    print(f"\nLatest run {last['run_id']}, per task:")
    for r in last["results"]:
        print(f"  {r['task']:12} {r['status']:10} hidden {r['hidden_pass']:.0%}  rounds {r['rounds']}  "
              f"refused {r['refused_commits']}  rejected {r['finish_rejected']}  nudges {r['nudges']}")
        for f in r["hidden_failures"][:5]:
            print(f"      {f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="*")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()
    if args.x:
        return compare()
    run(args.tasks, args.repeat)


if __name__ == "__main__":
    main()
