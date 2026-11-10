---
name: code-standards
description: Industry-standard style per language; git_commit enforces it with each language's formatter and linter
---
- Follow each language's standard style; git_commit runs its formatter/linter on changed files and refuses until they pass
- A repo's own config (pyproject/ruff.toml, .eslintrc, .prettierrc, .rubocop.yml, .clang-format, .editorconfig, phpcs.xml) overrides defaults
- Fix formatting with the formatter, never by hand: it's faster and exact
- Python: PEP 8 via ruff (lint) + ruff format; sorted imports, no unused imports or variables
- JS/TS: the repo's prettier + eslint; no unused vars, prefer const, strict equality
- Go: gofmt formatting and go vet clean; handle every returned error
- Rust: rustfmt + clippy with no warnings; avoid unwrap() outside tests
- Java/Kotlin: Google Java Style / Kotlin coding conventions (via the repo's Spotless, Checkstyle or ktlint)
- C#: .NET conventions via dotnet format and .editorconfig
- PHP: PSR-12 via phpcs; Ruby: community Ruby Style Guide via RuboCop
- Dart/Flutter: dart format + analyzer clean; Elixir: mix format (+ Credo if configured)
- Swift: swift-format; C/C++: clang-format (repo style, else Google)
- Naming is per language, never carried across: Python snake_case functions/variables, CamelCase classes; Rust snake_case functions/variables, CamelCase types, SCREAMING_SNAKE_CASE constants; Go camelCase, Exported names Capitalized, no underscores; JS/TS camelCase functions/variables, PascalCase classes/components; Java/Kotlin/C# camelCase methods (C#: PascalCase methods), PascalCase classes; Ruby/Elixir snake_case methods, CamelCase modules; PHP camelCase methods, PascalCase classes; C/C++: follow the repo
- Docs comments use each language's own form: Python docstrings, Rust ///, Go // FuncName ..., JS/TS JSDoc /** */
