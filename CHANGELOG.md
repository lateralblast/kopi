# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are `MAJOR.MINOR.PATCH` with no digit above 9, so `0.0.9` is followed by `0.1.0`.

## [Unreleased]

## [0.2.1] - 2026-10-10

### Added
- `--update` flag (with `--dry-run`): sync `CHANGELOG.md` and `TODO.md` into an existing project by adding missing items and moving items forward (never backwards), without duplicating. `--init`, `--import` and `--update` are now mutually exclusive. Documented in `README.md`.

## [0.2.0] - 2026-10-10

### Changed
- Verified `--import` against the real `kopi` project: 31 items created (20 Done, 10 Todo, 1 In Progress) with statuses applied correctly.

## [0.1.9] - 2026-10-10

### Added
- `--import` now also adds the repo's issues and pull requests as linked items (open issue Todo, closed issue Done, open PR In Progress, merged PR Done), skipping TODO items that duplicate them. Documented in `README.md`.

## [0.1.8] - 2026-10-10

### Added
- `--import` flag (with `--dry-run` and `--force`): seed an empty project from `CHANGELOG.md` (Done) and `TODO.md` (Todo / In Progress / Done), skipping TODO done items that duplicate changelog entries, creating the project first if missing. Documented in `README.md`.

## [0.1.7] - 2026-10-10

### Added
- `--init` flag: if the current repo (from git `origin`) has no project titled after it, create one and link it. Documented in `README.md`.

## [0.1.6] - 2026-10-10

### Changed
- `--owner` now defaults to the owner of the current git repo's github.com `origin` (with a notice on stderr), falling back to `@me`. Documented in `README.md`.

## [0.1.5] - 2026-10-10

### Added
- Auth check before every command: prints `gh auth login` / `gh auth refresh -s project` instructions when `gh` is not logged in or the token lacks the `project` scope.

## [0.1.4] - 2026-10-10

### Added
- `CLAUDE.md` with commands and architecture notes.

## [0.1.3] - 2026-10-10

### Added
- `TODO.md` tracking outstanding items and their status.

## [0.1.2] - 2026-10-10

### Added
- `requirements.txt` listing the test dependency (pytest); there are no runtime dependencies.

## [0.1.1] - 2026-10-10

### Added
- "Help Support Development" section with a Ko-fi link in `README.md`.

## [0.1.0] - 2026-10-10

### Added
- CC BY-NC-SA 4.0 license (`LICENSE.md`), referenced from `README.md` and `kopi.py`.

## [0.0.9] - 2026-10-10

### Added
- `README.md` with usage.

## [0.0.8] - 2026-10-10

### Added
- `--version` flag, and a version tracked in `kopi.py`, `pyproject.toml` and `README.md` (checked by a test).

## [0.0.7] - 2026-10-10

### Added
- Mock-based tests in `tests/test_cli.py`.

## [0.0.6] - 2026-10-10

### Added
- `pyproject.toml` exposing a `kopi` console command.

## [0.0.5] - 2026-10-10

### Added
- Hint to run `gh auth refresh -s project` when the `project` scope is missing.

## [0.0.4] - 2026-10-10

### Added
- `graphql()` helper for operations `gh project` doesn't support.

## [0.0.3] - 2026-10-10

### Added
- Global `--owner` flag to target an organisation or user.

## [0.0.2] - 2026-10-10

### Added
- Subcommands: `list`, `create` (with optional `--repo` link), `view`, `delete`, `fields`, `items`, and `add` (issue/PR via `--url`, or draft item via `--title`).

## [0.0.1] - 2026-10-10

### Added
- Single-file `kopi.py` CLI for managing GitHub Projects (v2), built on the `gh` CLI with no runtime dependencies.
