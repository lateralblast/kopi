# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are `MAJOR.MINOR.PATCH` with no digit above 9, so `0.0.9` is followed by `0.1.0`.

## [Unreleased]

## [0.4.5] - 2026-10-10

### Added
- `--done ITEM` flag (with `--dry-run`): move one board item from Todo or In Progress to Done. `ITEM` is an item id or text matching exactly one title (an error lists the candidates otherwise). Board only; verified live. Documented in `README.md`.

## [0.4.4] - 2026-10-10

### Added
- `--archive --id ITEM_ID` (repeatable) and `--archive --match TEXT` archive individual items instead of the stale-orphan sweep. Any status or kind of item can be archived this way; `--match` must match exactly one title. The items are recorded in the archive log, so `--restore` can undo them. Documented in `README.md`.

## [0.4.3] - 2026-10-10

### Added
- `--restore` flag (with `--dry-run` and `--id`): unarchive the items `--archive` archived in this project. GitHub's API cannot list archived items, so `--archive` now records what it archives in `~/.config/kopi/archived.json` (or `$KOPI_STATE`) and `--restore` reads that log; `--id ITEM_ID` restores specific items not in the log. Verified live. Documented in `README.md`.

## [0.4.2] - 2026-10-10

### Changed
- First release tagged in git as `v0.4.1` (the tag and its remote copy point at the 0.4.1 commit). Later changes are numbered from 0.4.2.

## [0.4.1] - 2026-10-10

### Changed
- Verified `fields`, `items` and both `add` forms (`--title` draft and `--url` issue) live on a scratch project; the temporary items were deleted afterwards. `fields` and `items` also work with `--json`.

## [0.4.0] - 2026-10-10

### Changed
- Verified `--init` live on the create-and-link path: `kopi --init ~/code/ocicat` created project #3 `ocicat` under `lateralblast` and linked it to `lateralblast/ocicat`, and `list` shows the link. A second `--init` finds the existing project. This also exercised the optional `PATH` argument.

## [0.3.9] - 2026-10-10

### Added
- `--import` and `--update` accept `CHANGELOG.md` or `TODO.md` (optionally with a directory) to process only that file; issues and PRs are skipped in that mode. Without it they process both files as before. Documented in `README.md`.

## [0.3.8] - 2026-10-10

### Changed
- `--diff` now reports duplicate board items explicitly as `duplicate` lines (and a `duplicates` list under `--json`) instead of leaving them to show up as `board-only`, and counts them in the summary. The duplicate detection is shared with `--squash`. Documented in `README.md`.

## [0.3.7] - 2026-10-10

### Changed
- `test_version_consistent` now also checks that the newest `CHANGELOG.md` release heading matches the version in `kopi.py`, `pyproject.toml` and `README.md`.

## [0.3.6] - 2026-10-10

### Added
- `--json` flag: every command and mode prints its result as a single JSON document on stdout, with human status messages suppressed and warnings left on stderr. Result shapes are documented in `README.md`.

## [0.3.5] - 2026-10-10

### Changed
- `list` now prints a header row and shows each project's open/closed state and linked repo(s), and takes `--closed` to include closed projects. If the linked-repo lookup fails it warns and shows `-`. The output has a new column order (number, title, state, repo, URL). Documented in `README.md`.

## [0.3.4] - 2026-10-10

### Fixed
- A failed repo link after a successful project create (`create --repo`, `--init`, `--import`) is now a warning on stderr and exits 0, instead of an error that hid the fact the project was created.

## [0.3.3] - 2026-10-10

### Added
- `--init`, `--diff`, `--archive` and `--squash` take an optional `PATH` like `--import` and `--update`: they work on the repo in that directory, or the current one without it. Documented in `README.md`.

## [0.3.2] - 2026-10-10

### Added
- `--import` and `--update` take an optional `PATH`: with it they work on the repo in that directory, without it on the current directory. Documented in `README.md`.

## [0.3.1] - 2026-10-10

### Changed
- Verified `--archive` live: a temporary orphaned Todo draft was skipped as possibly reworded, then archived with `--force`, and no longer appears in the next run.

## [0.3.0] - 2026-10-10

### Fixed
- `--update` and `--import` now wait (up to 30 seconds) for GitHub's item listing to show the items they just created, so an immediate second `--update` no longer re-adds them. A warning is printed if the listing is still behind.

## [0.2.9] - 2026-10-10

### Changed
- Verified `--squash` live: it deleted the two duplicate Done items (`[0.2.3]`, `[0.2.4]`) from the `kopi` board, and a following `--diff` no longer lists them.

## [0.2.8] - 2026-10-10

### Added
- `--squash` flag (with `--dry-run`): delete duplicate board items (same title or same issue/PR URL), keeping the one furthest along. Documented in `README.md`.

## [0.2.7] - 2026-10-10

### Added
- `--archive` flag (with `--dry-run` and `--force`): archive open board drafts that no longer match any `CHANGELOG.md` or `TODO.md` entry. Never touches Done items or linked issues/PRs, and skips orphans that look like a reworded pending item unless forced. Documented in `README.md`.

## [0.2.6] - 2026-10-10

### Added
- `--diff` flag: read-only report of every difference between the project's items and `CHANGELOG.md`, `TODO.md` and issues/PRs (`files-only`, `github-only`, `board-only`, `behind`, `ahead`). The matching logic behind `--update` now lives in a shared `sync_plan()` used by `--update`, `--diff` and `--archive`. Documented in `README.md`.

## [0.2.5] - 2026-10-10

### Changed
- Verified issue/PR handling live with a real issue: `--update` adds it as a linked Todo item, `item-list` exposes its `url` (plus `repository` and `number`), and a later run matches it by URL instead of re-adding it.
- Documented that GitHub's item listing lags after writes, so an immediate second `--update` can add items twice.

## [0.2.4] - 2026-10-10

### Added
- `--update` now also imports the repo's issues and PRs: new ones are added as linked items, existing ones (matched by URL) move forward when their GitHub state does, and linked items are excluded from title matching. Documented in `README.md`.

## [0.2.3] - 2026-10-10

### Changed
- Verified `--update` against the real `kopi` project: 5 items added and 1 moved to Done (board now 24 Done, 11 Todo, 1 In Progress); a second run reported nothing to do.

## [0.2.2] - 2026-10-10

### Changed
- `--update` now moves board items that are still Todo or In Progress to Done when a completed `TODO.md` item or `CHANGELOG.md` entry matches them (60%+ shared words, one-to-one, best pairs first), rather than adding a duplicate. Documented in `README.md`.

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
