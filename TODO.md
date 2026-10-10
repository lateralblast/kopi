# TODO

Status: `[ ]` open, `[~]` in progress, `[x]` done.

## Open

- [ ] Consider opt-in import of TODO/FIXME code comments and GitHub releases/tags (notes in [RESEARCH.md](RESEARCH.md)).

## Done

- [x] `--restore --all` finds items archived outside kopi via GraphQL (`isArchived`), with tests; probed live.
- [x] `--export prs` to a local `PRS.md`, with tests.
- [x] `--import issues` creates GitHub issues from `ISSUES.md` where none exist, with tests.
- [x] `--export todo|inprogress|done` always creates a missing `TODO.md` from a template, with tests (dry run checked live).
- [x] `--export issues` to a local `ISSUES.md`, with tests (dry run checked live).
- [x] `--export inprogress` and `--export done`, with tests (dry runs checked live).
- [x] `--export todo` flag: add the board's Todo items to TODO.md as unchecked entries, with tests (dry run checked live).
- [x] `--list inprogress` (project) and `--list inprogress --local` (TODO.md), with tests.
- [x] `--list done` (project) and `--list done --local` (TODO.md), with tests.
- [x] `--list todo` (project) and `--list todo --local` (TODO.md) flags, with tests; run live.
- [x] Set item field values via the `graphql()` helper (`kopi set`), with tests; verified live.
- [x] `--squash` flag: tick `TODO.md` entries that are Done on the board, with tests (live dry run found nothing to tick).
- [x] Renamed `--squash` to `--dedupe` (older entries below keep the old name).
- [x] `--done` flag to move a single item to Done by id or matching text, with tests; verified live.
- [x] Archive an individual item (`--archive --id` / `--match`), with tests.
- [x] `--restore` flag to unarchive items, with an archive log and tests; verified live.
- [x] Cut the first release: `v0.4.1` tagged in git and pushed.
- [x] Ran every `kopi` command against real GitHub (`list`, `view`, `create`, `--repo` link, `delete`, `fields`, `items`, `add`).
- [x] Verified `--init` create-and-link live on `lateralblast/ocicat` (project #3), including the `PATH` argument.
- [x] `--import` / `--update` can process a single file (`CHANGELOG.md` or `TODO.md`), with tests.
- [x] `--diff` flags duplicate board items explicitly, with a test.
- [x] Added the `CHANGELOG.md` heading to the version-consistency test.
- [x] `--json` output flag for every command and mode, with tests.
- [x] Make `list` output more useful: header row, open/closed state, linked repo; verified live.
- [x] Treat a failed repo link after a successful `create` as a warning, with a test.
- [x] Optional repo path for `--init`, `--diff`, `--archive` and `--squash`, with tests.
- [x] `--import` / `--update` accept an optional repo path, with tests.
- [x] Ran `--archive` for real on the `kopi` board using a temporary orphaned draft; archived and gone from the next run.
- [x] Guard against GitHub's delayed `item-list`: `--update`/`--import` poll until new items are listed, with tests (mock-based; not re-run live).
- [x] Ran `--squash` for real on the `kopi` board; the duplicate `[0.2.3]` and `[0.2.4]` items are gone.
- [x] `--squash` flag: delete duplicate board items, with tests.
- [x] Archive board items whose TODO entry was deleted (optional `--update` behaviour).
- [x] `--diff` flag: show differences between the board and CHANGELOG.md / TODO.md / issues / PRs, with tests; run live.
- [x] Verify issue/PR import and `--update` live once the repo has an issue or PR (only mocks and an empty-result dry run so far); confirm `item-list` exposes a URL (or repository and number) for linked items.
- [x] Make `--update` handle issues/PRs.
- [x] Run `--update` for real against the `kopi` project (only `--dry-run` and mocks so far).
- [x] `--update` completion sweep: move still-open board items to Done when a completed TODO/CHANGELOG entry matches them, with tests.
- [x] `--update` flag: add missing changelog/TODO items to the project and move items forward, with tests.
- [x] Ran `--import` for real against the `kopi` project; 31 items created with correct statuses.
- [x] `--import` also imports issues and PRs as linked items, with tests (mock-based; this repo has none yet).
- [x] `--import` flag (CHANGELOG.md -> Done; TODO.md -> Todo / In Progress / Done, deduped), with tests; dry run verified live.
- [x] `--init` flag (create a project named after the current repo if missing), with tests; existing-project path verified live.
- [x] Default `--owner` from the git `origin` owner, else `@me`; documented in the README (including that `gh` only links repos under the same owner).
- [x] Single-file CLI (`kopi.py`) with list/create/view/delete/fields/items/add.
- [x] Version tracked in `kopi.py`, `pyproject.toml`, `README.md` with a consistency test.
- [x] CC BY-NC-SA 4.0 license and references.
- [x] `CHANGELOG.md`, `requirements.txt`, `.gitignore`.
- [x] Auth check before every command (not logged in / missing `project` scope), with tests.
- [x] Created the `kopi` project under `lateralblast` and linked it to `lateralblast/kopi`.
