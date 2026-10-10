# Research

Notes on ideas we have considered but not built. Each section says what the idea is, why it might be worth doing and what would need deciding first. Open work is tracked in [TODO.md](TODO.md).

## Importing more sources

`--import` and `--update` currently read `CHANGELOG.md`, `TODO.md` and the repo's issues and PRs. Two further sources were considered and built. Both are opt-in, so existing behaviour does not change and boards do not fill with noise.

### TODO/FIXME code comments

**Idea:** scan the source files for comments such as `# TODO: handle rate limits` or `# FIXME: breaks on empty input`, and add each one to the board as a draft **Todo** item, with the file and line in the body. A flag such as `--code-comments` would turn it on.

**Why it might help:** work that lives only in a code comment never reaches the board or `TODO.md`.

**Open questions:**
- Which files to scan, and how to skip `.venv`, vendored code, generated files and build output.
- Comments are often stale, trivial or duplicated, so the board could fill with low-value items.
- Titles need a stable identity. A line number changes whenever the file is edited, so matching on it would re-add the same comment. The comment text is a better key.
- A deleted comment leaves an orphaned item on the board, the same problem `--archive` solves for deleted `TODO.md` entries, so it would need to take part in `--archive` and `--diff`.
- `kopi.py` has no TODO/FIXME comments today, and this project tracks work in `TODO.md`, so it would add nothing here yet.

### GitHub releases and tags

**Idea:** turn each release or tag (for example `v0.3.8`) into a **Done** item, for example titled `[release] v0.3.8`.

**Why it might help:** repos that publish releases but keep no changelog would still get their history on the board.

**Open questions:**
- It overlaps heavily with `CHANGELOG.md`, whose entries already become Done items titled `[version] text`. For a repo with a good changelog it would mostly add duplicates, so it needs to be opt-in and ideally skip releases whose version already appears in a changelog entry.
- Release notes may differ from the changelog wording, so the existing fuzzy matching would not reliably tell them apart.
- It only becomes useful once this project tags a release. Cutting the first release is a separate TODO item.

**Status:** built in 0.6.4 as `--code-comments` and `--releases` (see the README). The answers to the open questions: files come from `git ls-files` (so ignored and vendored files are skipped) minus docs and data globs, with `[comments]` in `.kopi.toml` to tune; the text is the key; removed comments become `--archive` orphans, which are protected while the source is off; releases are read from git tags and skip versions already in the changelog.
