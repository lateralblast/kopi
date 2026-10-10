# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are `MAJOR.MINOR.PATCH` with no digit above 9, so `0.0.9` is followed by `0.1.0`.

## [Unreleased]

## [0.8.2] - 2026-10-11

### Changed
- `--release` no longer takes a tag as its value: the new `--tag TAG` flag (only with `--release`) names it. By default the tag is now the plain version number from `CHANGELOG.md` (`1.2.3`), where `--release` used to add a `v` unless the repo's existing tags had none; use `--tag v1.2.3` for a `v` tag. A leading `v` on `--tag` is ignored when looking up the changelog section for the notes, and a tag name containing whitespace is rejected. Documented in `README.md`.

## [0.8.1] - 2026-10-11

### Added
- `--release [TAG]` (with `--draft`, `--dry-run`, `--json`): create a GitHub release at the last commit (`HEAD`). `TAG` defaults to the newest dated version in `CHANGELOG.md` (the latest date wins), written `v1.2.3`, or `1.2.3` if the repo's existing tags have no `v`. The notes are that version's `CHANGELOG.md` section, or GitHub's generated notes if there is none. The commit must already be on GitHub. A release that already exists is reported and left alone, a tag that already exists on another commit is an error (one already on the last commit is used), and uncommitted changes are warned about because they are not in the release. Documented in `README.md`.

## [0.8.0] - 2026-10-11

### Added
- `--gantt [releases|items]` (with `--to FILE`, `--dry-run`, `--force`, `--json`): draw the project's Roadmap as a Gantt chart from the board's Started and Completed dates. `releases` (the default) draws one bar per changelog release date, merging releases that share a day, and `items` draws one bar per work item in a section per release. In Progress items with a Started date appear as bars running to today. The format follows the file extension: `ROADMAP.md` (the default) is Markdown with a Mermaid block, which GitHub renders inline; `.mmd` is raw Mermaid; `.svg` is a standalone image drawn by kopi, well-formed XML in plain colours with a dark-mode variant; `--to -` prints the chart. A file kopi did not generate is not overwritten without `--force`, and an unchanged chart is left alone. Documented in `README.md`.
- Verified by rendering druid's imported history (30 release bars, 51 item bars) to SVG and viewing it. That caught two bugs before release: `--` is not allowed in an XML comment, so the SVG marker line made the file invalid, and the colours used CSS variables that some viewers ignore.

## [0.7.9] - 2026-10-11

### Changed
- `--import` and `--update` (and so `--sync`) are much faster. Draft items are created, linked issues and PRs are added and their Status is set in batched GraphQL requests (20 operations each), as the Started / Completed dates already were, instead of two `gh` calls per item. Importing a 51-item repo dropped from about three minutes to 52 seconds, and most of what remains is the deliberate wait for GitHub's item listing to catch up; the resulting board is identical.
- Long operations now report progress on stderr (never under `--json`): `Importing 51 items into project #7`, `Creating 50 draft items...`, `  created 20/50`, `Setting 51 statuses...`, `Waiting for GitHub to list the 51 new items...`, `Setting 102 dates...` and so on. Normal messages are flushed so they stay in order with it.
- A GraphQL error in a batch now stops the run with all of GitHub's messages, instead of failing on one item.
- Documented in `README.md`.

## [0.7.8] - 2026-10-11

### Changed
- `--import` and `--update` (and so `--sync`) now handle the Roadmap dates completely: if the board lacks the Started and Completed date fields they are created, and every item on the board that is missing a date gets it (not only the items just added or moved), including when the board is otherwise up to date. Existing values are still never overwritten. Added `--no-dates` to skip this for a run and `enabled = false` under `[dates]` in `.kopi.toml` to turn it off for a repo (which also stops `--start` / `--done` / `--move` stamping dates). `--dry-run` does not create fields or write dates. `--import` / `--update` `--json` output gains `date_fields_created` when fields were created. Documented in `README.md`.

## [0.7.7] - 2026-10-11

### Added
- Roadmap dates. kopi can now fill two date fields on the project, **Started** and **Completed** (names configurable under `[dates]` in `.kopi.toml`), so the board's Roadmap view shows history:
  - `--import` and `--update` set them on the items they add, and `--update` sets Completed on items it moves to Done. A changelog item gets the date in its `## [version] - date` heading as Completed, and the date of the previous changelog release as Started (the same day for the oldest release). A Done `TODO.md` draft gets Completed from the commit that ticked its `- [x]` entry. A linked issue or PR gets its created date as Started and its closed or merged date as Completed. A `[release]` item gets its tag's commit date.
  - `--start` stamps Started with today's date, and `--done` (or `--move ... Done`) stamps Completed with today's date and, if Started is empty, the previous changelog release's date.
  - A value already on an item is never overwritten, and a board without the fields is left alone.
  - `--setup-dates` creates the two date fields if missing, and `--backfill-dates` fills the gaps on existing items (both with `--dry-run` and `--json`).
  - `--import` / `--update` / `--done` / `--start` / `--move` `--json` output gains `dates: [{title, field, date}]` when any were set. Documented in `README.md`.
- Verified live on a temporary project (deleted afterwards): the fields were created, an import set dates from the changelog, a ticked `TODO.md` commit and an issue's created date, and `--start` / `--done` stamped as described.

## [0.7.6] - 2026-10-11

### Added
- When `gh` reports that GitHub's API rate limit was exceeded, the error now says how to see when it resets (`gh api graphql -f query='{ rateLimit { remaining resetAt } }'`, since `gh api rate_limit` can show stale numbers) and to retry after that time.

### Changed
- Verified live on the `kopi` project and a temporary project (both cleaned up afterwards): `--open` (created issue #3 and added it as Todo; a repeat with the same title was refused), `--close` and `--reopen` (including the already-closed and already-open cases and moving a Done item back to Todo), `--promote --annotate` and `--promote-all --yes` (two drafts became issues #6 and #7 a second apart and kept their statuses; a changelog draft and a Done draft were left alone). On this board GitHub's built-in "item closed" workflow moves a closed issue's item to Done straight away, so `--close` does not need `--update` here. The check found and fixed a bug in `--annotate` matching, now covered by a test: a short title could claim a long `TODO.md` entry that merely contained its words, ahead of the exact entry.

## [0.7.5] - 2026-10-11

### Changed
- Split the 165-test `tests/test_cli.py` into one file per feature area (`test_core.py`, `test_import_update.py`, `test_diff_sync.py`, `test_archive_restore.py`, `test_export.py`, `test_items.py`, `test_issues.py`, `test_config_sources.py`, `test_reports.py`), with the shared boards and mock runners in `tests/helpers.py` and the autouse fixtures in `tests/conftest.py`. No test was added or removed by the split.
- Tightened weak test assertions: the mode-exclusivity tests now check the error message and that nothing reached GitHub, `check_auth` is asserted to return quietly, the forced-import test counts the created items, and a helper that was defined after its use was replaced with `types.SimpleNamespace`.

## [0.7.4] - 2026-10-11

### Added
- `--annotate` with `--promote` and `--promote-all`: after promoting, add the new issue number to the matching `TODO.md` entry, for example `- [ ] Add dark mode` becomes `- [ ] Add dark mode (#12)`. The entry is the one with the same text (or starting with a title that was cut to 100 characters), else the best match at 75%+ shared words and similar length (a short title cannot claim a long entry that merely contains its words). An entry that already ends in an issue reference is left alone, so re-running is safe, and a similar longer entry is never annotated in its place. `--dry-run` shows the planned annotations (as `(#N)`) without writing, and `--json` reports `annotated`. Documented in `README.md`.

## [0.7.3] - 2026-10-11

### Added
- `--local` with `--open`, `--reopen` and `--close`: edit only the local `ISSUES.md`, with no GitHub access or login. `--open TITLE --local` adds `- TITLE` (with `(labels: ...)` for `--label`) to `## Todo`, creating the file if needed and refusing an open duplicate title unless `--force`; `--close ISSUE --local` moves the entry to `## Done`; `--reopen ISSUE --local` moves it back to `## Todo`. `ISSUE` is matched by number, link or title as usual, and `--dry-run` and `--json` work. `--body` cannot be used with `--local`. Documented in `README.md`.

### Changed
- `--export issues` and `--squash` (and so `--sync`) now keep `ISSUES.md` entries that have no link, such as those added with `--open --local`, until a board issue with the same title exists, instead of dropping them when the file is regenerated. A file created locally starts with the same kopi marker line, so it is treated as generated.

## [0.7.2] - 2026-10-11

### Added
- `--reopen ISSUE` (with `--dry-run`, `--json`): reopen a closed issue in the current repo and put it back on the project as Todo. `ISSUE` is a number, an issue URL, or text matching exactly one closed issue's title. On the board the issue's item is moved to Todo if it is Done or has no status, left alone if it is already Todo or In Progress, and added as Todo if it is missing; with no project the issue is still reopened. An already-open issue is reported and left alone. Documented in `README.md`.

### Changed
- `--close` and `--reopen` share their issue lookup. `--close` behaves as before.

## [0.7.1] - 2026-10-11

### Added
- `--open TITLE` (with `--body TEXT`, `--label NAME` repeatable, `--dry-run`, `--force`, `--json`): create a new issue in the current repo and add it to the project as Todo, the counterpart of `--close`. It refuses when an open issue already has the same title unless `--force` is given, and it checks the project and its Todo status first so a failure does not leave a stray issue behind. `TODO.md` is not touched. Documented in `README.md`.

## [0.7.0] - 2026-10-11

### Added
- `--squash` (and so `--sync`) now also refreshes `ISSUES.md` when the project has issue items, rewriting it exactly as `--export issues` would (and creating it if it is missing), so the local issue list follows the board. A project without issues leaves `ISSUES.md` alone, a hand-written `ISSUES.md` is skipped with a note unless `--force` is given, and `--dry-run` writes nothing. `--squash --json` reports `issues: {file, changed, skipped, count}` (or `null`). Documented in `README.md`.

## [0.6.9] - 2026-10-11

### Added
- `--promote-all [todo|inprogress]` (with `--dry-run`, `--yes`, `--json`): turn every open draft board item (or just the Todo or In Progress ones) into a real issue in the current repo, the bulk form of `--promote`. Changelog entries, `[code]` / `[release]` items, Done items, items with no status and anything already an issue or PR are skipped. Because it creates many public issues at once, a real run needs `--yes`; without it, or with `--dry-run`, it only lists them. Items are converted a second apart, a failure is reported and skipped (the exit status is 1 if any failed). Documented in `README.md`.

## [0.6.8] - 2026-10-11

### Changed
- Verified `--promote` live on the `kopi` project: a throwaway draft with a body and an In Progress status was converted to issue #2 in place. The board item kept its id, status and body, became a linked `Issue` item, and `--diff` recognised it. The test issue and item were deleted afterwards, leaving the board at its original 64 items.

## [0.6.7] - 2026-10-11

### Added
- `--promote ITEM` (with `--dry-run`, `--json`): turn one draft board item into a real issue in the current repo. `ITEM` is an item id or text matching exactly one title. The draft is converted in place with GitHub's `convertProjectV2DraftIssueItemToIssue`, so it keeps its place, status and fields and becomes a linked issue item with the same title. Only drafts can be promoted; an item that is already an issue or PR is an error. `TODO.md` is not touched. Documented in `README.md`.

## [0.6.6] - 2026-10-10

### Added
- `--rollup [DIR ...]` (with `--json`): a read-only status roll-up across several projects, one row each (owner, number, title, Todo, In Progress, Done, percent done, changelog history) plus a total. With directories (or `repos = [...]` in `.kopi.toml`) it finds each repo's project from that directory's git origin, using that repo's own `.kopi.toml` for its owner, project title and Status names unless `--owner` / `--status-map` override. Without either it covers every open project under the owner. Repos without a project are skipped with a warning (`skipped` in `--json`). Documented in `README.md`.

## [0.6.5] - 2026-10-10

### Added
- `--report [text|markdown]` (with `--json`): a read-only progress summary of the project: percent done and counts by status (changelog entries are counted separately as history, not work), drafts / issues / pull requests, the In Progress and Todo items, the latest releases from `CHANGELOG.md` with their dates and entry counts, and how many differences `--diff` would show. Documented in `README.md`.

### Changed
- `--diff` and `--report` share one `diff_summary` helper. `--diff` output is unchanged.

## [0.6.4] - 2026-10-10

### Added
- `--code-comments` (with `--import`, `--update`, `--diff`, `--archive`, `--sync`): track `TODO:` / `FIXME:` code comments as Todo items titled `[code] text`, with the file and line in the body. Only real comments count (a comment marker, the tag, then a colon); the files git tracks are scanned, minus docs and data files. The comment text is the item's identity, so moving a comment does not re-add it, and a removed comment leaves an orphan for `--archive`.
- `--releases` (same commands): track git tags as Done items titled `[release] tag`, skipping any tag whose version already has a `CHANGELOG.md` entry.
- `[sources]` (`code_comments`, `releases`) and `[comments]` (`tags`, `include`, `exclude`) settings in `.kopi.toml` to turn the sources on and tune the scan. Documented in `README.md`.

### Changed
- Board items titled `[code] ...` or `[release] ...` are not treated as orphans while their source is switched off, so `--archive` cannot sweep them away.

## [0.6.3] - 2026-10-10

### Added
- Per-repo settings in `.kopi.toml` (or `--config FILE`): `owner`, `project` (the board's title, if it is not the repo name) and a `[status]` table naming the board's Status options. Command-line flags win over the file, a missing default file is fine, and a missing `--config` file is an error. Uses `tomllib` where available (Python 3.11+) and a small built-in parser for the same subset on older Pythons, so there are still no dependencies. Documented in `README.md`.

## [0.6.2] - 2026-10-10

### Added
- `--close ISSUE` (with `--dry-run`, `--json`): close one issue in the current repo on GitHub. `ISSUE` is a number (`12` or `#12`), an issue URL, or text matching exactly one open issue's title (an exact title wins over partial matches). An already-closed issue is reported and left alone. The board catches up on the next `--update`. Documented in `README.md`.

## [0.6.1] - 2026-10-10

### Added
- `--status-map "Todo=Backlog,In Progress=Doing,Done=Shipped"`: name the board's Status options when they differ from Todo / In Progress / Done. Common names (Backlog, Doing, WIP, Complete, Shipped, ...) are recognised without it, so boards with other option names now work with `--import`, `--update`, `--list`, `--done`, `--move` and the rest. Statuses are shown under their Todo / In Progress / Done names, and writes use the board's own option.
- `--list issues` and `--list prs`: the project's issue or pull request items (any status) as `id`, title and URL; with `--local`, the entries in `ISSUES.md` / `PRS.md`.
- `--import issues` applies labels: the `(labels)` on exported lines, or `(labels: a, b)` on hand-written ones, for labels the repo already has. Unknown labels are reported on stderr (and as `unknown_labels` in `--json`), not created.

### Fixed
- Lookups no longer stop silently at fixed caps (100 projects, 500 board items, 200 issues/PRs per repo): they now go up to 1000 projects, 5000 items and 5000 issues/PRs, and warn on stderr if a cap is reached.

## [0.6.0] - 2026-10-10

### Added
- `--diff --check`: exit with status 2 when the board and the files differ (0 when in sync; 1 stays reserved for errors), for CI and pre-commit hooks. `--diff --json` now also reports `in_sync`.
- `--sync [PATH]` (with `--dry-run`): run `--update` then `--squash` in one step, so files and GitHub flow into the board and Done items are ticked back in `TODO.md`. With `--json` the two results are one document, `{project, dry_run, update, squash}`. The `--export` modes stay separate.
- `--start ITEM`: move one board item to In Progress, like `--done`.
- `--move ITEM STATUS`: move one board item to any Status option (Todo, In Progress, Done or a custom one; case-insensitive, `inprogress` and `in-progress` accepted). Moves may go backwards, to reopen an item.

### Changed
- `--done`, `--start` and `--move` share one implementation. `--done` behaves as before.

## [0.5.9] - 2026-10-10

### Changed
- kopi now describes itself as **KOPI: Keep Our Projects In-sync**: updated the `--help` description, the `kopi.py` docstring, the `pyproject.toml` description, the `README.md` intro and `CLAUDE.md`.

## [0.5.8] - 2026-10-10

### Added
- `--restore --all`: unarchive every archived item GitHub reports for the project, including ones archived in the web UI, on another machine, or before kopi kept a log. Found by paging through the GraphQL items connection, which (unlike `gh project item-list`) returns archived items. Plain `--restore` still restores only what `--archive` logged. Documented in `README.md`.

## [0.5.7] - 2026-10-10

### Added
- `--export prs` (with `--dry-run`, `--force`): write the project's pull request items to `PRS.md`, grouped by board status, the same way `--export issues` writes `ISSUES.md`. `--json` reports the rows under `prs`. Documented in `README.md`.

## [0.5.6] - 2026-10-10

### Added
- `--import issues` (with `--dry-run`, `--json`): read `ISSUES.md` and create a GitHub issue in the repo for each entry that has no matching issue (by link, or by case-insensitive title). Entries under a `## Done` heading are closed after creation. Accepts kopi's own `--export issues` format and hand-written bullets. Documented in `README.md`.

## [0.5.5] - 2026-10-10

### Changed
- `--export todo`, `--export inprogress` and `--export done` now always create `TODO.md` when it is missing or empty, from a template with the status legend and `## Open` / `## Done` sections, even if there is nothing to export. Before, the file was only created when an entry had to be added, and then without the legend or the other section. `--json` reports `created`. Documented in `README.md`.

## [0.5.4] - 2026-10-10

### Added
- `--export issues` (with `--dry-run`, `--force`): write the project's issue items to `ISSUES.md`, grouped by board status, replacing the file each time. A file kopi did not generate is not overwritten without `--force`. Documented in `README.md`.

## [0.5.3] - 2026-10-10

### Added
- `--export inprogress` (also `in-progress`) and `--export done`: write the project's In Progress items as `[~]` entries at the end of `## Open`, and Done items as `[x]` entries at the top of `## Done` (newest first), skipping anything TODO.md already has. For `done`, changelog-derived board items and anything resembling a `CHANGELOG.md` entry are skipped. Documented in `README.md`.

### Changed
- Inserting into an empty `TODO.md` section now leaves a blank line after the heading.

## [0.5.2] - 2026-10-10

### Added
- `--export todo` flag (with `--dry-run`): add the project's Todo items to the local `TODO.md` as unchecked entries, skipping any the file already has in any state, and writing them at the end of `## Open`. Never changes the board. Documented in `README.md`.

## [0.5.1] - 2026-10-10

### Added
- `--list inprogress` (also `in-progress`): list the project's In Progress items, and with `--local` the `[~]` entries in the local `TODO.md`. Documented in `README.md`.

## [0.5.0] - 2026-10-10

### Added
- `--list done`: list the project's Done items, and with `--local` the ticked (`[x]`) entries in the local `TODO.md`. Same output and `--json` shapes as `--list todo`. Documented in `README.md`.

## [0.4.9] - 2026-10-10

### Added
- `--list KIND` flag (currently `todo` only): list the project's Todo items as `id<TAB>title`. With `--local`, list the open (`[ ]`) entries in the local `TODO.md` instead, without needing GitHub or a login. Both support `--json`. Documented in `README.md`.

## [0.4.8] - 2026-10-10

### Added
- `set N ITEM --field NAME --value VALUE` (or `--clear`) command: set a text, number, date, single-select or iteration field on one item through the `graphql()` helper. Values are validated against the field's type before anything is sent; `--dry-run` and `--json` work. Verified live on scratch text, number, date and Status fields. Documented in `README.md`.

## [0.4.7] - 2026-10-10

### Added
- `--squash` flag (with `--dry-run`): tick `TODO.md` entries that are Done on the board but still `[ ]` or `[~]` in the file, the reverse of `--update`. Edits the local file only, in place, using the same matching as `--update`. (`--squash` previously meant the duplicate remover, now `--dedupe`.) Documented in `README.md`.

## [0.4.6] - 2026-10-10

### Changed
- Renamed `--squash` to `--dedupe` (behaviour unchanged). The old `--squash` flag is gone, so update any scripts. Earlier changelog entries keep the old name.

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
