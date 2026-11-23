---
name: review-a-branch
description: The order to review a branch in, within a step budget - read this before you start reviewing
roles: [reviewer]
---
# Review a branch

Aim to finish in about 30 tool calls. Nothing changes while you review, so every file needs
reading once and every check needs running once. A reviewer that runs out of steps fails the
whole session, even when the code was good.

1. (no tool) From the request, list every requirement and the edge cases it implies.
2. Memory, 2-3 calls: memory_list, then memory_read team/conventions.md and team/lessons.md.
3. The diff is in your first message. read_file each CHANGED source and test file once.
   Don't read files the branch didn't touch unless the change depends on them.
4. Run each test command once, and each lint command once (repo-facts lists them).
   git_commit already ran them, so you're confirming. Don't run cargo build/check after
   cargo test passed: test already built it.
5. For each requirement: point to the code line that meets it and a test that checks it.
   For each edge case: what the code does with it. If a "tests changed after they failed"
   warning was shown, check those edits didn't weaken the tests.
6. Decide:
   - approve: every requirement met, tests check them, conventions followed, nothing out of scope.
   - changes: record_finding for each real problem first, then finish with a numbered list
     of fixes, each with the file and what to change. Not for personal taste.
7. finish, with the checklist headings (Requirements, Edge cases, Tests, Conventions, Scope).

Don't: rerun passing checks, re-read a file you already read, grep for things you've already
seen, or open memory history unless a note looks wrong.
