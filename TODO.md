# TODO

Status: `[ ]` open, `[~]` in progress, `[x]` done.

## Open

- [ ] Run `--update` for real against the `kopi` project (only `--dry-run` and mocks so far).
- [ ] Make `--update` handle issues/PRs, and optionally archive board items whose TODO entry was deleted.
- [ ] Verify issue/PR import live once the repo has an issue or PR (only mocks and an empty-result dry run so far).
- [ ] Consider opt-in import of TODO/FIXME code comments and GitHub releases/tags.
- [ ] Verify the `--init` create-and-link path against real GitHub (only the existing-project path was run live).
- [~] Run `kopi` against real GitHub: `list`, `view`, `create`, `--repo` link and `delete` verified; still to try `fields`, `items`, `add`.
- [ ] Treat a failed repo link after a successful `create` as a warning (currently exits 1 although the project was created).
- [ ] Make `list` output more useful: header row, open/closed state, linked repo.
- [ ] Set item field values (status, etc.) via the `graphql()` helper.
- [ ] Add a `--json` output flag.
- [ ] Cut the first release: tag the version in git (changelog and files are at `0.2.1`).
- [ ] Add `CHANGELOG.md` heading to the version-consistency test (it checks `kopi.py`, `pyproject.toml` and `README.md` only).

## Done

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
