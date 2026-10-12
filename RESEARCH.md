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

## Grouping a long changelog by minor version

**Idea.** Collapse the versions of one `MAJOR.MINOR` into a single board item (`[16.5] ...`) instead of one item per version.

**Why it might help.** A repo like `lunar` has 1,571 versions, most with a single bullet, so its board is mostly noise. `[changelog] last` / `since` (built, 1.1.3) already keep a board to the recent releases.

**Status:** built in 1.1.8 as `[changelog] group = "minor"` (see the README). The answers to the open questions are after the list.

**Open questions.**
- What is the group's title? Most versions have one bullet, so any single bullet is an arbitrary label for the group, and a count ("16.5.x: 10 releases") loses the content.
- Item identity: titles are matched to the file by exact title, and `CHANGELOG_TITLE`, the Gantt and the report all parse `[x.y.z]`; a 2-part key touches all of them.
- Dates: a group needs Started as its earliest release date and Completed as its latest, which no code does yet.

**Answers (1.1.8).** The title is a stable label, `[16.5] Releases 16.5.x`, and the content goes in the body (a bullet list, newest first), so a count or an arbitrary bullet never has to name the group. Identity: `CHANGELOG_TITLE` accepts a two-part version, `resolve_dates` and `gantt_tasks` handle it, and callers that need the individual bullets (duplicate checks, tag skipping, the context warning) read them ungrouped (`parse_changelog(raw=True)`). Dates: Completed is the group's latest release date and Started the release before its first, so the bars are contiguous. Left open: a group's body is written once, so a minor that keeps growing is not refreshed on the board.

## Creating items in parallel

**Idea.** Run `--import` / `--update`'s item creation (`addProjectV2DraftIssue`, 50 per request) several requests at once, the way the Status, date and section writes already are.

**Why it might help.** Creation is now most of a big import's time (about 0.115 s per item on the server; lunar's 1,631 items take around 3 minutes of it).

**Result: measured, not built (1.2.7).** 300 drafts on a scratch board, two trials each:

| Workers | Time | Failed requests | Items that landed |
|---|---|---|---|
| 1 | 37.5 s, 36.7 s | 0, 0 | 300, 300 |
| 2 | 21.1 s, 28.0 s | 0, 2 | 300, 288 |
| 3 | 17.5 s, 17.1 s | 2, 2 | 246, 272 |

At best about 40% off the create phase, but parallel requests fail even at 2 workers, and a failed request can still create part of its items. A blind retry would then duplicate items (a repeated field write is harmless, a repeated create is not) and no retry loses them. A correct recovery has to list the board after a failure, match the new drafts to the failed request by title and body, and wait out GitHub's listing lag first, which is a lot of machinery and risk for a saving that matters only on very large imports. Sequential creates never failed. The idempotent passes (statuses, dates, sections, bodies) stay parallel with a retry, since repeating them is safe.

**Open questions if it is ever revisited.** Whether GitHub's partial application is a server-side timeout that a smaller batch (say 10) avoids; whether a per-item token in the body could make a retry detectable without listing the board.

