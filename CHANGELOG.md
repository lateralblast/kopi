# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are `MAJOR.MINOR.PATCH` with no digit above 9, so `0.0.9` is followed by `0.1.0`.

## [Unreleased]

### Added
- Single-file `kopi.py` CLI for managing GitHub Projects (v2), built on the `gh` CLI with no runtime dependencies.
- Subcommands: `list`, `create` (with optional `--repo` link), `view`, `delete`, `fields`, `items`, and `add` (issue/PR via `--url`, or draft item via `--title`).
- Global `--owner` flag to target an organisation or user.
- `graphql()` helper for operations `gh project` doesn't support.
- Hint to run `gh auth refresh -s project` when the `project` scope is missing.
- `pyproject.toml` exposing a `kopi` console command.
- Mock-based tests in `tests/test_cli.py`.
- `--version` flag, and a version tracked in `kopi.py`, `pyproject.toml` and `README.md` (checked by a test).
- `README.md` with usage.
- CC BY-NC-SA 4.0 license (`LICENSE.md`), referenced from `README.md` and `kopi.py`.
- "Help Support Development" section with a Ko-fi link in `README.md`.
- `requirements.txt` listing the test dependency (pytest); there are no runtime dependencies.
- `TODO.md` tracking outstanding items and their status.
- `CLAUDE.md` with commands and architecture notes.
