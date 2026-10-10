# kopi

Version: 0.1.8

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
./kopi.py --import [--dry-run] [--force]   # seed the project from CHANGELOG.md / TODO.md
./kopi.py --version
```

## Initialising a project for a repo

Run `kopi --init` inside a git repo with a github.com `origin`. It looks for a project (under the resolved owner, see below) whose title matches the repo name. If one exists it prints it and does nothing; otherwise it creates a project with that title and links it to the repo. The link is skipped, with a note, if `--owner` differs from the repo's owner, because `gh` only links repos under the same owner. Only the first 100 projects are searched.

## Importing CHANGELOG.md and TODO.md

`kopi --import` seeds the repo's project (creating and linking it first if missing, as `--init` does) with draft items built from `CHANGELOG.md` and `TODO.md` in the current directory. It does nothing if the project already has items, unless you pass `--force`. Use `--dry-run` to print what would be imported without touching GitHub.

- Each `CHANGELOG.md` bullet becomes a **Done** item titled `[version] text` (oldest first).
- Each `TODO.md` checkbox becomes an item: `[ ]` is **Todo**, `[~]` is **In Progress**, `[x]` is **Done**.
- `[x]` TODO items that duplicate a changelog entry (matched by shared words) are skipped.
- Titles are cut to 100 characters; the full text goes in the item body.

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
