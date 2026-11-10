"""Dreaming CLI.

  python run_dream.py                  dream over sessions not dreamed yet -> report + diff
  python run_dream.py --all            include sessions already dreamed
  python run_dream.py --auto-promote   dream, then promote straight away (for a nightly schedule)
  python run_dream.py --promote RUN    make a reviewed run's memory live (versioned)
  python run_dream.py --rollback RUN   undo a promotion
  python run_dream.py --list           show past runs
"""
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)                      # so a scheduled task works from any working directory
load_dotenv(ROOT / ".env")

from dreaming.dream import RUNS_DIR, dream, promote, rollback  # noqa: E402
from llm import get_llm  # noqa: E402
from run_crew import repo_name  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--include-evals", action="store_true")
    ap.add_argument("--auto-promote", action="store_true")
    ap.add_argument("--promote", metavar="RUN")
    ap.add_argument("--rollback", metavar="RUN")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.promote:
        return promote(args.promote)
    if args.rollback:
        return rollback(args.rollback)
    if args.list:
        for run in sorted(RUNS_DIR.glob("dream_*")):
            meta = json.loads((run / "run.json").read_text(encoding="utf-8")) if (run / "run.json").exists() else {}
            status = "promoted" if (run / "promoted.json").exists() else "not promoted"
            print(f"{run.name}  {len(meta.get('sessions', []))} session(s)  {status}")
        return

    repo = repo_name(os.environ["REPO_URL"])
    run_dir = dream(get_llm(), repo, include_dreamed=args.all, include_evals=args.include_evals)
    if run_dir is None:
        return
    print(f"\n[dreaming] report: {run_dir / 'report.md'}")
    print((run_dir / "memory.diff").read_text(encoding="utf-8") or "(no memory changes)")
    if args.auto_promote:
        promote(run_dir.name)
    else:
        print(f"\nReview it, then: python run_dream.py --promote {run_dir.name}")


if __name__ == "__main__":
    main()
