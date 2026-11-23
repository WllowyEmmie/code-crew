# Skills

Reusable procedures for the crew, written by Emmie (agents can read them, never change them).

Memory holds facts this repo learned ("Rust integration tests go in tests/"). A skill is a
procedure that works in any repo ("how to debug a failing test"). Like memory, skills are
loaded progressively: each agent sees only the name and description of the skills for its
role (and the repo's languages), and calls skill_read(name) for the full text when needed.

Each skill is `skills/<name>/SKILL.md` with frontmatter:

    name: debug-failing-test
    description: one line saying WHEN to use it (that's what the agent decides from)
    roles: [builder, test-writer]      # who sees it in their list
    langs: [rust]                      # optional: only listed when the repo has this language

When dreaming notices the same lesson again and again, that's a sign it belongs in a skill:
edit the SKILL.md yourself.
