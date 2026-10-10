# kopi

Version: 0.2.1

Create and manage GitHub Projects (v2) from the command line. Wraps the `gh` CLI, so it needs no dependencies beyond Python 3.9+ and an authenticated `gh` with the `project` scope (`gh auth refresh -s project`).

## Usage

```
./kopi.py list
./kopi.py create "Title" --repo owner/repo
./kopi.py view N
./kopi.py delete N
./kopi.py fields N
./kopi.py items N
./kopi.py add N --url <issue-or-pr-url>    # or --title "draft item"
./kopi.py --init                           # ensure the current repo has a project
./kopi.py --import [--dry-run] [--force]   # seed the project from CHANGELOG.md, TODO.md, issues and PRs
./kopi.py --update [--dry-run]            # sync CHANGELOG.md / TODO.md changes into the project
./kopi.py --version
```

## Initialising a project for a repo

Run `kopi --init` inside a git repo with a github.com `origin`. It looks for a project (under the resolved owner, see below) whose title matches the repo name. If one exists it prints it and does nothing; otherwise it creates a project with that title and links it to the repo. The link is skipped, with a note, if `--owner` differs from the repo's owner, because `gh` only links repos under the same owner. Only the first 100 projects are searched.

## Importing CHANGELOG.md, TODO.md, issues and PRs

`kopi --import` seeds the repo's project (creating and linking it first if missing, as `--init` does) with draft items built from `CHANGELOG.md` and `TODO.md` in the current directory, plus the repo's issues and pull requests. It does nothing if the project already has items, unless you pass `--force`. Use `--dry-run` to print what would be imported without touching GitHub.

- Each `CHANGELOG.md` bullet becomes a **Done** item titled `[version] text` (oldest first).
- Each `TODO.md` checkbox becomes an item: `[ ]` is **Todo**, `[~]` is **In Progress**, `[x]` is **Done**.
- `[x]` TODO items that duplicate a changelog entry (matched by shared words) are skipped.
- Titles are cut to 100 characters; the full text goes in the item body.
- The repo's issues and PRs (from `origin`) are added as real linked items, not drafts: open issue is **Todo**, closed issue **Done**, open PR **In Progress**, merged PR **Done**. Closed-unmerged PRs are skipped. If listing them fails, `kopi` warns and imports the files only.
- `TODO.md` items that duplicate an issue or PR title are skipped, so the linked item wins.

## Updating the project from CHANGELOG.md and TODO.md

`kopi --update` syncs an existing project with the files, so you can run it after changing them. It does not create the project (use `--init` or `--import` first). Add `--dry-run` to see the changes first.

- A `CHANGELOG.md` entry with no matching item (same `[version] text` title) is added as **Done**.
- A `TODO.md` item with no matching item is added as **Todo**, **In Progress** or **Done** according to its checkbox. Items match by identical title, or by very similar wording (75%+ shared words); each board item matches at most one file entry.
- If an item already exists but is behind the file (for example Todo on the board, now `[x]` in `TODO.md`), it is moved forward rather than duplicated. Keep an item's text unchanged when you tick it: a heavily reworded item is treated as new, and the old board item stays where it was. Items are never moved backwards, and nothing is deleted, so items removed from the files stay on the board.
- Issues and PRs are not touched by `--update`.

## Choosing the owner

`gh` needs to know which user or organisation owns the project. `kopi` picks it in this order:

1. `--owner NAME` if given.
2. Otherwise, if run inside a git repo whose `origin` remote is on github.com, the owner of that repo (the organisation or user in `github.com/OWNER/REPO`). `kopi` prints `Using owner: OWNER (from git origin)` to stderr when it does this.
3. Otherwise `@me`, the authenticated user.

Note that `gh` only links a project to repos owned by the same owner (`create --repo`).

## Help Support Development

If you find this software useful and would like to support its development, please consider buying me a coffee:

https://ko-fi.com/richardatlateralblast

## License

[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). See [LICENSE.md](LICENSE.md).

See [CHANGELOG.md](CHANGELOG.md) for release history.
