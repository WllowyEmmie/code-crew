---
name: start-new-project-go
description: Setting up a new Go module or package in this repo (go.mod, layout, errors, tests)
roles: [builder]
langs: [go]
---
# Start a new Go module or package

- No `go.mod` yet: create one with `module <name>` and a `go 1.22` line. No external modules:
  there's no network, so only the standard library.
- Package names: short, lowercase, one word, no underscores. File names: lowercase, `_` allowed.
- Exported names are CamelCase and each gets a doc comment starting with its name
  (`// FreeSlots returns ...`). Unexported names are camelCase.
- Errors: return `error` as the last result; `errors.New` / `fmt.Errorf("...: %w", err)`;
  sentinel errors as `var ErrX = errors.New(...)`. No panics for bad input.
- Tests: `<file>_test.go` in the same directory and package; table-driven tests with
  `t.Run(tc.name, ...)`.
- Before git_commit: `gofmt -w` the files, `go vet ./...`, `go test ./...`.
