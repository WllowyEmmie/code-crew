---
name: start-new-project-rust
description: Setting up a new Rust crate in this repo (Cargo.toml, layout, errors, what to commit)
roles: [builder]
langs: [rust]
---
# Start a new Rust crate

- `Cargo.toml`: `[package]` with `name` (the crate name; `-` becomes `_` in `use` paths),
  `version = "0.1.0"`, `edition = "2021"`, and an empty `[dependencies]`. No external
  crates: there's no network, so only `std`.
- Library: `src/lib.rs`. Start it with a `//!` crate doc comment; give every `pub` item a `///` doc comment.
- Errors: a `pub enum XxxError` deriving `Debug, Clone, PartialEq, Eq`, implementing
  `std::fmt::Display` and `std::error::Error`; functions return `Result<T, XxxError>`.
  No `unwrap()`/`expect()`/`panic!` outside tests.
- Tests: unit tests in `#[cfg(test)] mod tests { use super::*; ... }` at the bottom of the same
  file; integration tests in `tests/<name>.rs` (only files there are compiled by cargo test).
- Commit `Cargo.lock`, and a `.gitignore` containing `/target`.
- Before git_commit: `cargo fmt` (don't hand-format), then
  `cargo clippy --offline --all-targets -- -D warnings`, then `cargo test --offline`.
- Unsigned arithmetic: subtracting `u32`s underflows (panics in debug). Compare first or use
  `checked_sub` / `saturating_sub`.
