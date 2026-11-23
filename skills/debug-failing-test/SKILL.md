---
name: debug-failing-test
description: A test failed (run_command or git_commit refused) - find out whether the code or the test is wrong before changing either
roles: [builder, test-writer]
---
# Debug a failing test

1. Read the failure itself, not just "failed": the test name, the assertion, expected vs actual.
   If the output is cut off, rerun only that test:
   `python3 -m pytest test_x.py::test_name -q`, `cargo test --offline test_name`, `go test -run TestName ./...`
2. Read the test. From the REQUEST (not the code), work out by hand what the right answer is
   for that input. Write the steps down in your head: inputs -> each step -> result.
3. Decide who is wrong:
   - The code's actual value is the right answer -> the test is wrong.
     Builder: don't edit the test; say so in your finish summary and record_finding.
     Test-writer: fix your test only if your hand calculation proves it wrong. Never change an
     expectation just to make a test pass.
   - The test's expected value is the right answer -> the code is wrong. Go to step 4.
   - The request doesn't decide it -> don't guess: say so in your finish summary as an open question.
4. Trace that input through the code line by line until a value differs from your hand
   calculation. That line is the bug. Fix the cause, not the symptom: no special-casing the
   test's input, no catching and ignoring the error, no lint/warning suppression.
5. Rerun that one test, then the whole suite.
6. Same failure twice after a fix = your picture of the code is wrong. Stop editing. Re-read the
   code path from the entry point; check your hand calculation again.

Common causes, check these first:
- Boundaries: `<` vs `<=`, inclusive vs exclusive end, empty input, exactly-equal values.
- Mutating a copy instead of the original (a `for x in list` copy, a Rust value moved/cloned
  instead of `&mut`), or two names pointing at the same list.
- Input assumed sorted / unique / in range when the request says it may not be.
- Formatting: zero-padding ("09:05"), case, whitespace, trailing punctuation.
- Integer underflow/overflow (Rust `u32` subtraction), integer vs float division.
