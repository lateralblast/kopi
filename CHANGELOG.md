# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are `MAJOR.MINOR.PATCH` with no digit above 9, so `0.0.9` is followed by `0.1.0`.

## [Unreleased]

## [1.2.8] - 2026-10-12

### Fixed
- Both example workflows (`docs/kopi-check.yml`, `docs/kopi-update.yml`) installed kopi with a plain `pip install git+https://github.com/lateralblast/kopi.git`, which fails on a runner while the kopi repo is private (there are no credentials for it). They now put the `KOPI_TOKEN` secret in the install URL (`x-access-token:${GH_TOKEN}@github.com/...`; GitHub masks it in the log). Checked in a credential-free environment (new venv, empty `HOME`, no system git config, no prompt): the plain URL fails to clone, the token form installs kopi. The workflows themselves have still not run on a real Actions runner.

## [1.2.7] - 2026-10-12

### Changed
- Parallel item creation was measured and rejected, and `RESEARCH.md` records why. With 2 or 3 requests at once, creating 300 drafts took 17 to 28 s against 37 s one at a time, but requests failed and the failed ones still created part of their items (246 to 288 of 300 landed), so a retry would duplicate items and no retry would lose them. Sequential creation never failed. Item creation therefore stays one request at a time (the README says why); the idempotent writes (statuses, dates, sections, bodies) stay parallel. No code change.

## [1.2.6] - 2026-10-12

### Added
- `--report --to FILE` writes the report to a file (Markdown for a `.md` name or `markdown`), and `--report --post ISSUE` posts it as a Markdown comment on an issue of this repo. The comment carries a hidden `<!-- kopi-report -->` marker, so the next run edits it in place instead of adding another, and an unchanged report writes nothing. `--dry-run` writes no file and posts nothing (for `--post` it reads the comments and prints what it would do), and `--json` gains `file` and `posted: {issue, url, action}`. Verified on a temporary issue: dry run, first post, unchanged repeat, and an edit of a stale comment, always leaving one comment.

## [1.2.5] - 2026-10-12

### Added
- `docs/kopi-update.yml`: an example GitHub Actions workflow that runs `kopi --update` when `CHANGELOG.md` or `TODO.md` change on the default branch (and on demand), so the board follows without a manual run. Board only (a runner cannot commit the files `--sync` edits), with a `concurrency` group that never cancels a run, since two overlapping runs could add the same item. The README has a section on it and the token it needs. Checked from a simulated runner (`--dry-run`, new venv, only `GH_TOKEN`), and a smoke test confirms a repeated `--update` adds nothing.

## [1.2.4] - 2026-10-12

### Added
- `--update --refresh-bodies` (also with `--sync`) rewrites a draft item's body when the files give one and the board's differs: a grouped minor (`[changelog] group = "minor"`) that has grown since it was created, or a long entry's full text. It only touches exact-title drafts whose file side has a body (so notes you keep on a short item are safe), skips issues, PRs and items without a draft id, overwrites a body edited on the board (hence opt-in), and runs the writes in parallel like the other field updates. `--dry-run` lists them, `--json` adds `refreshed`. Verified live: a minor grown by one bullet was refreshed, and a second run found nothing.

### Changed
- Boards read through the slim query now carry each draft's own id (`content.id`), as `gh project item-list` does.

## [1.2.3] - 2026-10-12

### Added
- `tests/test_smoke.py` and `tests/fakegh.py`: an end-to-end smoke suite that runs the real `kopi.py` as a subprocess against a stand-in `gh` on `PATH` (a small fake GitHub with projects, items, date fields, issues and PRs), so the subprocess layer is finally exercised: argument quoting, GraphQL string literals, JSON parsing of what `gh` prints, exit codes and the date-field path of a default `--import`. It covers import, `--diff --check`, `--update`, `--report --json`, `--checkquota`, linked issues and PRs, awkward titles (quotes, backslashes, unicode), a `gh` failure and a missing `gh`. It fails if kopi stops asking `gh` for JSON, which no mocked test noticed. No change to kopi itself.

## [1.2.2] - 2026-10-12

### Added
- `--import` warns on stderr when a changelog with 300 or more entries would be imported whole (no `[changelog] last`, `since` or `group` in `.kopi.toml`): it says how many entries, releases and items there are, roughly how long the writes take, and points at those settings. `--import --dry-run` ends with the estimate (`about 2 minutes to write`) for 300 or more items, and its `--json` gains `estimated_seconds` (0.4 s per item, from the measured speed). The import itself is unchanged.

## [1.2.1] - 2026-10-12

### Changed
- `--apply-labels` was checked live on a temporary issue: `--dry-run` reports and warns without touching GitHub, the real run adds the label and rewrites the file, and a label deleted from the file stays on GitHub. A label the repo does not have is warned about and then drops out of the rewritten file; the README now says so.

## [1.2.0] - 2026-10-12

### Changed
- Milestone, assignee and label output was checked against a real issue that has all three: the slim read and `gh project item-list` return the same `labels`, `assignees` and `milestone` (`gh` adds the milestone's `description`, which kopi does not use), `--report` lists the milestone with its due date, and `ISSUES.md` shows the assignee. No code change.

## [1.1.9] - 2026-10-12

### Changed
- The CI recipe (`docs/kopi-check.yml`) was checked from a simulated runner: kopi pip-installed into a new venv, an empty `HOME`, only `GH_TOKEN` set. `kopi --diff --check` reads the board and exits 2 on drift, or exits 1 with the "Not authorised" message when there is no token. No code change.

## [1.1.8] - 2026-10-12

### Added
- `[changelog] group = "minor"` in `.kopi.toml` turns each `MAJOR.MINOR` of a long changelog into one board item (`[16.5] Releases 16.5.x`) instead of one per bullet. The body lists that minor's bullets, Started / Completed span its releases (from the release before it to its last), and `--gantt` draws `v16.5.x` bars. The title is stable, so `--update` does not re-add it. It works with `last` / `since` (the window applies first), leaves `Unreleased` and non-`X.Y.Z` versions per-bullet, and leaves existing per-version items on the board alone.

### Changed
- Board titles of the form `[16.5]` (no patch number) are recognised as changelog entries everywhere (dates, `--gantt`, `--report`, `--archive`, `--export`, `--promote`).

## [1.1.7] - 2026-10-12

### Added
- `--report` lists the milestones of the board's issues and PRs with how many of their items are Done and the due date (`Milestones:  - v2.0: 3 of 5 done (due 2026-12-01)`), soonest first, and `--json` gains `milestones: [{title, due, done, total}]`. Boards read through the slim query now carry each item's `milestone`. Read-only: the board's native Milestone field already shows them.

## [1.1.6] - 2026-10-12

### Added
- `--report` shows trends: for each of the last 12 months the changelog versions released and the work items completed, the cycle time (Started to Completed: median, mean, longest) of work items that have both dates, and the gap between release days (`Trends (last 12 months):`, `Cycle time ...`, `Between releases ...`). It uses the board's date fields (named by `[dates]` when set) and the changelog's release dates, costs no extra API reads, and is left out when there is nothing to measure. `--json` gains `trends`.

## [1.1.5] - 2026-10-12

### Added
- A CI recipe for `kopi --diff --check`: `docs/kopi-check.yml` is an example GitHub Actions workflow (pull requests touching `CHANGELOG.md` / `TODO.md`, plus weekly) and the README has a "Gating CI" section with the token it needs (the built-in `GITHUB_TOKEN` cannot read Projects) and a pre-commit hook.

## [1.1.4] - 2026-10-12

### Added
- `ISSUES.md` and `PRS.md` show each issue's or PR's assignees as `@login`s after its labels (`- [#3](url) Title (bug, docs) @alice`), and the `--export issues|prs` JSON rows gain `assignees`. Boards read through the slim query now carry assignees, and the file still reads back (the `@names` are ignored when matching).
- `--apply-labels` with `--export issues|prs`, `--squash` or `--sync`: labels listed in `ISSUES.md` / `PRS.md` for an issue or PR that lacks them are added on GitHub before the file is rewritten, so a label typed into the file is kept. It never removes labels, warns about (and does not create) labels the repo lacks, and `--dry-run` only reports (`labels_added` in `--json`).

## [1.1.3] - 2026-10-12

### Added
- `[changelog]` in `.kopi.toml` tracks only part of a very long `CHANGELOG.md`: `last = 50` takes the newest 50 releases, `since = "16.0.0"` takes releases from that version up (both may be set; `Unreleased` entries are always kept). A repo like `lunar` (1,571 versions) can then have a board of the recent ones. Board items for older versions that are already there are left alone, and `--diff` / `--archive` no longer report them as `board-only` or orphans. Invalid values are rejected with a message.

## [1.1.2] - 2026-10-12

### Changed
- `--import` and `--update` write faster. Mutations go 50 to a request instead of 20, and the Status, date and section writes (which are safe to repeat) run three requests at once; a request that fails is retried on its own. Item creation stays one request at a time, because repeating a failed create could duplicate items. Measured on GitHub: 300 status writes took 45 s before and 11-18 s now, and importing `ocicat` (210 items) went from 2:12 to 1:19. Four parallel requests made GitHub answer "something went wrong", so three is the limit.

## [1.1.1] - 2026-10-12

### Changed
- Boards are read with kopi's own slim GraphQL query instead of `gh project item-list`: about 0.02 API points per item, where `gh` costs about 1 (32 points against 1,520 for the 1,631-item `lunar` board, in 21 seconds). The items come back in the same shape (status, `Started` / `Completed` and other custom fields, issue and PR links, labels), archived items stay hidden, and kopi falls back to `gh project item-list` with a warning if the query fails or an item has more than 12 field values. `--update`, `--diff`, `--report`, `--sync`, `--gantt`, `--archive` and the other read commands all use it.

## [1.1.0] - 2026-10-12

### Fixed
- The rate-limit error now gives the reset time (`resets at 13:01 UTC (in 38 min)`) even when a few points are left, which is what a read that needs more than the remainder reports; otherwise it points to `kopi --checkquota`, not a raw `gh api` query.
- The documented cost of reading a board was too low: measured at about 1 point per item with the Started / Completed fields (1,520 points for the 1,631-item `lunar` board, 0.7 without them). Every read-only command (`--update --dry-run`, `--gantt`, `--report`) pays it, so the README, the error hint and the `--checkquota` warning say "about 1 point per item".

## [1.0.9] - 2026-10-11

### Added
- `--checkquota` shows how much of the hourly GraphQL quota is left and when it resets (`GraphQL quota: 755 of 5000 points left (4245 used); resets at 10:39 UTC (in 12 min)`), with a warning once less than a tenth is left, since reading a board costs about 0.7 points per item. It costs one point and needs no project or repo (so no owner lookup). `--json` prints `{limit, remaining, used, reset_at, reset_in_minutes, low}`. Documented in `README.md`.

## [1.0.8] - 2026-10-11

### Changed
- A version with more than one release heading in `CHANGELOG.md` (found importing `manx`: `0.5.1` and `0.5.2` each have an Added and a Fixed heading, and `0.5.2`'s two headings carry different dates) is reported by `--import` and `--update` (`2 versions listed under more than one heading`), and its release date is now the latest of its headings. Before, the heading that happened to come last in the file decided silently. Every entry is still imported. Documented in `README.md`.

## [1.0.7] - 2026-10-11

### Fixed
- `--report` read the board twice (for its counts, then again for the comparison with the files). It now reads it once, which halves its API cost on a big board.

## [1.0.6] - 2026-10-11

### Changed
- When GitHub's API limit is spent, the error now says when it resets (`resets at 10:39 UTC (in 4 min)`) and that reading a board costs about 0.7 points per item. `gh` reports a spent budget as `unknown owner type`; that is recognised too (only when the budget really is 0). Documented in `README.md`.

## [1.0.5] - 2026-10-11

### Fixed
- `--import` and `--update` used up the hourly GraphQL budget on a big board: waiting for GitHub to list the new items re-listed the whole board every two seconds, about 400 points for a 573-item board (found importing `guige`, whose 573-entry changelog hit the limit; the writes had all been made, but the run ended with a misleading `unknown owner type`). The wait now polls the board's item count, one point a poll, which catches up no earlier than the listing does (measured). If the wait itself fails, for a rate limit or anything else, it is a warning and the import is still reported, since the writes are done. `--open` and `--reopen` use the same wait. Documented in `README.md`.

## [1.0.4] - 2026-10-11

### Changed
- `--report` on a board with no tracked work (a changelog-only history, such as `ussuri`) says `Tracked work: none yet` instead of `no work items yet (0 done, 0 in progress, 0 todo)`; the counts are shown only when there is something to count.

## [1.0.3] - 2026-10-11

### Fixed
- `--gantt` ordered the releases of one day as text, so a repo with a version component above 9 got a backwards range such as `v1.0.10 to v1.0.9 (8 releases)` (found importing `ussuri`). They are now ordered by version number: `v1.0.9 to v1.0.10`.

## [1.0.2] - 2026-10-11

### Changed
- Checked the Mermaid Gantt chart (the README block and a generated `ROADMAP.md`, including the `∶` colon look-alike) and the SVG in GitHub's renderer: both display correctly. No code change.

## [1.0.1] - 2026-10-11

### Changed
- Created the Roadmap view (layout Roadmap, Started and Completed as the date fields) for the `kopi` and `druid` projects in the web UI, which the API cannot do; it displays correctly. No code change.

## [1.0.0] - 2026-10-11

### Changed
- `--delete release --tag TAG --cleanup-tag` now also works when the release is already gone and only its tag is left (as after deleting the release by hand): it deletes just the tag. It is still an error when there is neither a release nor a tag, and without `--cleanup-tag` a missing release is still an error that touches nothing. `--json` reports `url: null`, `deleted: false` and `tag_deleted: true` for a tag-only delete. Documented in `README.md`.

## [0.9.9] - 2026-10-11

### Added
- `--delete release --tag TAG` deletes a GitHub release (`--delete release --release TAG` works too, and `--release TAG` is now the same as `--release --tag TAG`). The tag must be named, the release is looked up first (a wrong tag is an error and deletes nothing), `--dry-run` shows what would go, and the git tag stays on GitHub unless `--cleanup-tag` is given (kopi says when it is left behind). `--json` prints `{repo, tag, url, draft, dry_run, deleted, tag_deleted}`. Documented in `README.md`.

## [0.9.8] - 2026-10-11

### Added
- Opt-in **Section** field. With `[sections] enabled = true` in `.kopi.toml`, `--import` and `--update` create a text field (`[sections] field` renames it) and fill it with the `##` heading each `TODO.md` entry sits under, so a board can be grouped by it. A value already on an item is never overwritten, changelog entries get none, items imported by an older kopi (cut at their first line) are still matched, and `--json` reports `sections: [{title, section}]`. Tried live on the `birman` board (30 values). Documented in `README.md`.

## [0.9.7] - 2026-10-11

### Changed
- A repo with issues turned off (`the 'o/r' repository has disabled issues`) no longer gets a warning from every command. `--import` says so once (`note: issues are turned off for o/r; importing the files and pull requests only`) and the others stay quiet; any other failure to list issues still warns.

## [0.9.6] - 2026-10-11

### Changed
- Markdown bold (`**text**`, `__text__`) is stripped from the titles of imported items, so `**The functions have no callers.** ...` is no longer shown with asterisks. Code spans are left alone and the body keeps the original text. Boards imported before this still match (`--update` on `birman` adds nothing).

## [0.9.5] - 2026-10-11

### Added
- `--import` and `--update` warn on stderr about changelog and `TODO.md` entries that read as a continuation of the one before (they start with `Neither`, `Both`, `It`, `They`, `These`, `Also`, `Then`, ... or say `as above`), since as board items on their own they mean nothing (`[1.0.0] Neither is run by --status ...` on `havana`). Advice only; the entries are still imported. Documented in `README.md`.

## [0.9.4] - 2026-10-11

### Changed
- `--import` and `--update` write the dates before waiting for GitHub's item listing to catch up, instead of after, since the date writes need only the new items' ids. Timed on a 143-item import (91 seconds): the wait is 3 seconds of that, and the rest is the writes themselves at about 0.13 seconds per mutation (dates 38 s, creating drafts 21 s, statuses 19 s), so this is a small change, not a speed-up, and further gains would need concurrent requests (not done: GitHub asks clients to avoid them for mutations).

## [0.9.3] - 2026-10-11

### Added
- `[release] tag_prefix` in `.kopi.toml` (for example `"v"`): `--release` names the tag prefix + the changelog version (`v1.2.3`) without `--tag v1.2.3` every time. An explicit `--tag` is used as typed, and the release notes are still found. Documented in `README.md`.

## [0.9.2] - 2026-10-11

### Changed
- `--report` no longer reads as "7% done" for a repo whose record is a changelog. Progress is now `Tracked work: 7% done (2 done, 0 in progress, 28 todo)` and a separate `Shipped: 110 changelog entries in 66 releases` line (omitted when nothing has shipped). `--json` adds `history: {entries, releases}`; `counts` and `percent_done` are unchanged.

## [0.9.1] - 2026-10-11

### Fixed
- The `possible duplicate TODO.md entries` warning (0.8.5) was also printed by `--diff`, `--report`, `--archive` and `--squash`, because they share the matcher. It now appears only for `--import` and `--update` (so `--sync` too), as documented. It also compares only the first eight words of each entry, and only entries starting with the same word, because long wrapped entries (see 0.9.0) shared enough common words to flag unrelated pairs (six pairs on `birman`, down to the one `TL1` pair).

## [0.9.0] - 2026-10-11

### Fixed
- A `CHANGELOG.md` or `TODO.md` bullet wrapped onto indented lines was read as its first line only, so the board item (and its body) stopped mid-sentence. The lines are now joined into one entry (found importing `birman`, where 49 continuation lines across 30 items were dropped). `--squash` still ticks the entry (on its first line), `--annotate` adds the issue reference to its last line, and the commit-date lookup for a ticked entry searches only its first 60 characters, since the file wraps it. Documented in `README.md`.

## [0.8.9] - 2026-10-11

### Fixed
- Changelog release headings without square brackets (`## 0.4.0 - 2026-10-07`, `## v0.4.0 (2026-10-07)`) are now recognised, as well as `## [0.4.0] - 2026-10-07`. Before, such a changelog imported no Done items and had no release dates, so `--gantt` had nothing to draw and `--release` and `--list releases --local` found no versions (found importing `birman`). Documented in `README.md`.

## [0.8.8] - 2026-10-11

### Changed
- In Mermaid Gantt output (`--gantt`, `.mmd` and `ROADMAP.md`) a colon in a task or section name is now written as the look-alike `∶` (U+2236) instead of the entity code `#58;`, so the raw file reads cleanly and still renders as a colon. `#` and `;` are still entity-encoded. Documented in `README.md`.

## [0.8.7] - 2026-10-11

### Fixed
- `--gantt FILE` (a file name where the view goes) now says `'FILE' is not a view (releases or items); to choose the output file use --to FILE`, instead of only "invalid choice".

## [0.8.6] - 2026-10-11

### Fixed
- `--gantt` in the releases view no longer collapses to one or two bars when every release falls on one or two days (a repo imported from a changelog written in a day or two, such as `havana`'s 66 releases on two dates). It draws a bar per version then; three or more release days still merge same-day releases. Documented in `README.md`.

## [0.8.5] - 2026-10-11

### Added
- `--import` and `--update` warn on stderr about open `TODO.md` entries that share at least half their words (`possible duplicate TODO.md entries`), such as "Start Ollama and the gateway at boot" written twice in different words. The warning is advice only: nothing is merged or skipped. Documented in `README.md`.

## [0.8.4] - 2026-10-11

### Added
- `--list releases` (also `--list release`): list the repo's GitHub releases, newest first, one per line as tag, date, flags (`latest`, `draft`, `pre-release`) and name. `--json` prints `{tag, name, date, latest, draft, prerelease, url}` for each. With `--local` it lists the dated versions in `CHANGELOG.md` instead (version, date, number of entries), with no GitHub access or login, so comparing the two shows which versions have not been released yet. Documented in `README.md`.

## [0.8.3] - 2026-10-11

### Changed
- Ran `kopi --sync` on the `kopi` project itself: 61 items added (the changelog entries from 0.5.0 onwards and recent `TODO.md` items), 1 moved to Done and 108 Started / Completed dates set in 57 seconds. The board now has 125 items with no duplicate titles, and `ISSUES.md` was created. `--diff` shows three old drafts that exist only on the board, which `--archive` can retire.

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
