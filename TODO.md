# TODO

Status: `[ ]` open, `[~]` in progress, `[x]` done.

## Open

- [ ] Run `--import` for real against the `kopi` project and confirm item statuses (only `--dry-run` was run live).
- [ ] Verify the `--init` create-and-link path against real GitHub (only the existing-project path was run live).
- [~] Run `kopi` against real GitHub: `list`, `view`, `create`, `--repo` link and `delete` verified; still to try `fields`, `items`, `add`.
- [ ] Treat a failed repo link after a successful `create` as a warning (currently exits 1 although the project was created).
- [ ] Make `list` output more useful: header row, open/closed state, linked repo.
- [ ] Set item field values (status, etc.) via the `graphql()` helper.
- [ ] Add a `--json` output flag.
- [ ] Cut the first release: tag the version in git (changelog and files are at `0.1.8`).
- [ ] Add `CHANGELOG.md` heading to the version-consistency test (it checks `kopi.py`, `pyproject.toml` and `README.md` only).

## Done

- [x] `--import` flag (CHANGELOG.md -> Done; TODO.md -> Todo / In Progress / Done, deduped), with tests; dry run verified live.
- [x] `--init` flag (create a project named after the current repo if missing), with tests; existing-project path verified live.
- [x] Default `--owner` from the git `origin` owner, else `@me`; documented in the README (including that `gh` only links repos under the same owner).
- [x] Single-file CLI (`kopi.py`) with list/create/view/delete/fields/items/add.
- [x] Version tracked in `kopi.py`, `pyproject.toml`, `README.md` with a consistency test.
- [x] CC BY-NC-SA 4.0 license and references.
- [x] `CHANGELOG.md`, `requirements.txt`, `.gitignore`.
- [x] Auth check before every command (not logged in / missing `project` scope), with tests.
- [x] Created the `kopi` project under `lateralblast` and linked it to `lateralblast/kopi`.
