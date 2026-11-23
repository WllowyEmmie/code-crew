---
name: write-tests-from-spec
description: How to choose test cases from the request and work out their expected values, independently of the builder's code
roles: [test-writer]
---
# Write tests from the spec

Your tests check the REQUEST, not the builder's code. Write down the cases before you read
the implementation closely, so its bugs can't leak into your expectations.

1. One test per requirement the request states, named after the behaviour
   (`test_get_counts_as_a_use`, `touching_busy_intervals_leave_no_gap`).
2. For each requirement add the edge cases it implies:
   - empty / zero / one item; the largest and smallest allowed values
   - exactly at a boundary, one below, one above (a slot exactly == duration; 3999 and 4000)
   - ties and ordering; unsorted, duplicate or overlapping input
   - every error the request names, each in its own test, checked for the right error type
3. Work out every expected value by hand from the request, step by step. Never copy an
   expected value from running the code.
4. Put the tests where repo-facts says this language's tests go.
5. Run them. If one fails, use the debug-failing-test skill: decide whether your expectation
   or the code is wrong. Never weaken a failing test to make it pass.
6. Where the request doesn't decide a behaviour, don't test a guess as if it were required:
   name it as an open question in your finish summary.
7. git_commit your tests, then finish("pass") / finish("fail") (record_finding first).
