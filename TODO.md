# TODO

Status: `[ ]` open, `[~]` in progress, `[x]` done.

## Open

- [ ] Run `kopi` against real GitHub (only mock-based tests so far); confirm `gh project` JSON shapes match what `cmd_*` expects.
- [ ] Set item field values (status, etc.) via the `graphql()` helper.
- [ ] Add a `--json` output flag.
- [ ] Cut the first release: date the `[Unreleased]` changelog entry and tag the version (currently `0.1.0`).
- [ ] Add `CHANGELOG.md` heading to the version-consistency test (it checks `kopi.py`, `pyproject.toml` and `README.md` only).

## Done

- [x] Single-file CLI (`kopi.py`) with list/create/view/delete/fields/items/add.
- [x] Version tracked in `kopi.py`, `pyproject.toml`, `README.md` with a consistency test.
- [x] CC BY-NC-SA 4.0 license and references.
- [x] `CHANGELOG.md`, `requirements.txt`, `.gitignore`.
