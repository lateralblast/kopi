# kopi

Version: 0.4.8

Create and manage GitHub Projects (v2) from the command line. Wraps the `gh` CLI, so it needs no dependencies beyond Python 3.9+ and an authenticated `gh` with the `project` scope (`gh auth refresh -s project`).

## Usage

```
./kopi.py list [--closed]                 # number, title, open/closed, linked repo, URL
./kopi.py create "Title" --repo owner/repo
./kopi.py view N
./kopi.py delete N
./kopi.py fields N
./kopi.py items N
./kopi.py set N ITEM --field NAME (--value V | --clear)   # set a field value on one item
./kopi.py add N --url <issue-or-pr-url>    # or --title "draft item"
./kopi.py --init [PATH]                    # ensure the current repo has a project
./kopi.py --import [PATH|FILE] [--dry-run] [--force]   # seed the project from CHANGELOG.md, TODO.md, issues and PRs
./kopi.py --update [PATH|FILE] [--dry-run]            # sync CHANGELOG.md / TODO.md changes into the project
./kopi.py --diff [PATH]                    # show how the project differs from the files/issues/PRs
./kopi.py --archive [PATH] [--id ID | --match TEXT]  # archive one item (see below)
./kopi.py --archive [PATH] [--dry-run] [--force]  # archive open board drafts no longer in TODO.md
./kopi.py --done ITEM [--dry-run]               # move one board item to Done (ITEM: id or title text)
./kopi.py --restore [PATH] [--dry-run] [--id ID]  # unarchive items that --archive archived
./kopi.py --squash [PATH] [--dry-run]             # tick TODO.md entries that are Done on the board
./kopi.py --dedupe [PATH] [--dry-run]             # delete duplicate board items
./kopi.py --json <command or mode>        # print the result as JSON (see below)
./kopi.py --version
```

## Listing projects

`kopi list` prints a header row, then one tab-separated line per project: number, title, state (`open` or `closed`), linked repo(s) and URL. Closed projects are hidden unless you pass `--closed`. Linked repos need an extra GraphQL lookup; if it fails, `kopi` warns and shows `-` for the repo.

## JSON output (`--json`)

Add `--json` to any command or mode to print its result as one JSON document on stdout, for scripts (`kopi --json list | jq`). Human status messages are suppressed, while warnings and the `Using owner` notice still go to stderr. Shapes:

- `list`: array of `{number, title, state, repos, url}`; `fields` and `items`: the `gh` arrays; `view`: the project object (always JSON).
- `create`: `{number, url, linked_repo}`; `--init`: `{number, created}`; `delete`: `{deleted}`; `add`: `{added}`.
- `--import` and `--update`: `{project, dry_run, added: [{status, title, url?}], moved: [{title, from, to}]}` (`--import` reports `items`, `skipped` on a dry run and `imported` otherwise).
- `--diff`: `{project, files_only, github_only, board_only, duplicates, behind, ahead}`.
- `--archive`: `{project, dry_run, archived, skipped}`; `--restore`: `{project, dry_run, restored, failed}`; `--done`: `{project, dry_run, moved: [{title, from, to}]}`; `--squash`: `{project, dry_run, ticked: [{title, from}]}`; `set`: `{project, dry_run, item, field, value}`; `--dedupe`: `{project, dry_run, deleted}`.

## Initialising a project for a repo

Run `kopi --init` inside a git repo with a github.com `origin`. It looks for a project (under the resolved owner, see below) whose title matches the repo name. If one exists it prints it and does nothing; otherwise it creates a project with that title and links it to the repo. If the link fails the project is still created and `kopi` prints a warning and exits 0. The link is skipped, with a note, if `--owner` differs from the repo's owner, because `gh` only links repos under the same owner. Only the first 100 projects are searched.

## Importing CHANGELOG.md, TODO.md, issues and PRs

`kopi --import` seeds the repo's project (creating and linking it first if missing, as `--init` does) with draft items built from `CHANGELOG.md` and `TODO.md` in the current directory, plus the repo's issues and pull requests. It does nothing if the project already has items, unless you pass `--force`. Use `--dry-run` to print what would be imported without touching GitHub.

- Each `CHANGELOG.md` bullet becomes a **Done** item titled `[version] text` (oldest first).
- Each `TODO.md` checkbox becomes an item: `[ ]` is **Todo**, `[~]` is **In Progress**, `[x]` is **Done**.
- `[x]` TODO items that duplicate a changelog entry (matched by shared words) are skipped.
- Titles are cut to 100 characters; the full text goes in the item body.
- The repo's issues and PRs (from `origin`) are added as real linked items, not drafts: open issue is **Todo**, closed issue **Done**, open PR **In Progress**, merged PR **Done**. Closed-unmerged PRs are skipped. If listing them fails, `kopi` warns and imports the files only.
- `TODO.md` items that duplicate an issue or PR title are skipped, so the linked item wins.

## Updating the project from CHANGELOG.md, TODO.md, issues and PRs

`kopi --update` syncs an existing project with the files and the repo's issues and PRs, so you can run it after changing them. It does not create the project (use `--init` or `--import` first). Add `--dry-run` to see the changes first.

- A `CHANGELOG.md` entry with no matching item (same `[version] text` title) is added as **Done**.
- A `TODO.md` item with no matching item is added as **Todo**, **In Progress** or **Done** according to its checkbox. Items match by identical title, or by very similar wording (75%+ shared words); each board item matches at most one file entry.
- If an item already exists but is behind the file (for example Todo on the board, now `[x]` in `TODO.md`), it is moved forward rather than duplicated. Items are never moved backwards, and nothing is deleted, so items removed from the files stay on the board.
- Completed entries (`[x]` in `TODO.md`, or any `CHANGELOG.md` entry) that match no board item exactly are also compared, at 60%+ shared words, with board items that are not yet Done (Todo or In Progress). A match moves that item to **Done** instead of adding a duplicate, so reworded items still get closed. Each board item is matched at most once, best pairs first.
- GitHub's item listing can lag for a short time after writes. After adding items, `--update` and `--import` poll the listing (up to 30 seconds) until every new item appears, so an immediate re-run won't add them twice. If GitHub is still slow they print a warning; wait a little before running `--update` again.
- Keeping an item's text unchanged when you tick it is still the most reliable way to match.
- The repo's issues and PRs are matched to board items by URL. New ones are added as linked items (open issue **Todo**, closed issue **Done**, open PR **In Progress**, merged PR **Done**), and existing ones move forward when their state on GitHub does (for example an issue closed since the last run). A reopened issue is not moved back. Linked items never take part in title matching, so a changelog entry can't close one, and a new `TODO.md` item that duplicates an issue or PR title is left to the issue or PR.

## Comparing the project with the files (`--diff`)

`kopi --diff` is read-only. It uses the same matching as `--update` and prints every difference, one per line:

- `files-only`: in `CHANGELOG.md` / `TODO.md` but not on the board (`--update` would add it).
- `github-only`: an issue or PR not on the board (`--update` would add it).
- `board-only`: a draft item on the board that matches no file entry.
- `duplicate`: an extra copy of a board item (same title, or same issue/PR URL), with the status of the copy `--dedupe` would keep. Each duplicate is listed once, here, not also as `board-only`. Run `kopi --dedupe` to remove them.
- `behind`: the board's status is behind the file or GitHub (`--update` would move it forward).
- `ahead`: the board's status is ahead of the file (for example Done on the board, still `[ ]` in `TODO.md`). `--update` leaves these alone.

## Archiving stale items (`--archive`)

`kopi --archive` archives open board drafts (Todo or In Progress) that no longer match any `CHANGELOG.md` or `TODO.md` entry, for example after you delete an idea from `TODO.md`. Archived items are hidden, not deleted, and can be restored from GitHub. It is separate from `--update` and does nothing unless you ask for it.

- Done items and linked issues/PRs are never archived.
- An orphan that shares 50%+ of its words with a pending new item is probably a reworded one, so it is skipped and reported. Pass `--force` to archive it anyway.
- Use `--dry-run` first. Items created by hand on the board count as orphans too.

## Removing duplicates (`--dedupe`)

`kopi --dedupe` deletes duplicate items from the board: drafts with the same title (ignoring case and spacing) and linked issues/PRs with the same URL. In each group it keeps the copy furthest along (Done over In Progress over Todo, the first listed on a tie) and **deletes** the rest, which cannot be undone, so run with `--dry-run` first. It looks only at the board, not the files. Duplicates usually come from running `--update` twice before GitHub's item listing catches up.

## Running against another repo

`--init`, `--import`, `--update`, `--diff`, `--archive` and `--dedupe` work on the current directory by default. Give them a path to work on the repo there instead, for example `kopi --update ~/code/other-repo`. The path's `CHANGELOG.md`, `TODO.md` and git `origin` are used, so the project and owner are resolved from that repo. It must be a directory. Put the path right after the flag, before other options (`kopi --update PATH --dry-run`).

## Importing or updating a single file

By default `--import` and `--update` process both `CHANGELOG.md` and `TODO.md`, plus the repo's issues and PRs. Give one of those file names instead of a directory to process only that file: `kopi --import CHANGELOG.md`, `kopi --update TODO.md`. The file may carry a directory (`kopi --update ~/code/other-repo/TODO.md`), which is then used as the repo.

- Issues and PRs are skipped when a single file is given.
- Only `CHANGELOG.md` and `TODO.md` are accepted, and the file must exist. `--init`, `--diff`, `--archive` and `--dedupe` take directories only.
- Matching works as usual but sees only that file. For example, `--import TODO.md` cannot dedupe `[x]` items against the changelog, so completed TODO items are imported as Done; and `--update CHANGELOG.md` will not move items based on `TODO.md`.

### Archiving a single item

Give `--archive` an item to archive just that one, ignoring the files and the orphan rules above:

- `kopi --archive --id PVTI_...` archives the item with that id (repeatable). Ids are the `id` field from `kopi items N` (`--limit` defaults to 30, so raise it for large boards).
- `kopi --archive --match "some text"` archives the one item whose title contains the text (case-insensitive). It is an error if zero or several items match; the error lists the candidates with their ids.
- Any status or kind of item can be archived this way, including Done items and linked issues/PRs, since you named it explicitly. `--dry-run` shows what would be archived.
- Single archives are recorded in the same log, so `--restore` can undo them.

## Ticking TODO.md from the board (`--squash`)

`kopi --squash` is the reverse of `--update`: it looks at the project's **Done** items and, for any whose `TODO.md` entry is still `[ ]` or `[~]`, ticks it to `[x]` in the local file. Use `--dry-run` to see which entries would change. It takes an optional directory like the other modes.

- It edits `TODO.md` only; the board is not touched. It does not commit, and it ticks the entry in place rather than moving it to a Done section, so move it yourself if you keep a separate Done list.
- Entries are matched to board items exactly as `--update` does (identical title, or 75%+ shared words). Only `TODO.md` is read: issues, PRs and the changelog are ignored.
- Open board items (Todo, In Progress) never cause a tick, and nothing is ever un-ticked.
- Pairs well with `--done`: mark an item done on the board, then `--squash` ticks it in the file.

## Setting field values (`set`)

`kopi set N ITEM --field NAME --value VALUE` sets one field on one item, using GraphQL. `ITEM` is an item id or text matching exactly one title (as for `--done`). `--clear` empties the field instead. Global flags go before the command: `kopi --dry-run set ...`, `kopi --json set ...`.

- Supported field types: **text**, **number**, **date** (`YYYY-MM-DD`), **single select** (an option name) and **iteration** (an iteration title). Names and option names are matched ignoring case. A value that doesn't fit the field type is rejected before anything is sent, with a message listing the valid choices.
- Built-in fields such as Title, Assignees, Labels, Milestone and Repository are not settable here. Status is a single select, so `kopi set N ITEM --field Status --value Done` works (though `--done` is the shortcut).
- To create the custom fields to set in the first place, use `gh project field-create`.

## Marking an item done (`--done`)

`kopi --done ITEM` moves one board item to **Done**. `ITEM` is either an item id (`PVTI_...`, from `kopi items N`) or text that matches exactly one item's title, ignoring case: `kopi --done "single archive"`. If the text matches no item, or several, it fails and lists the candidates with their ids.

- It moves items from Todo or In Progress. An item that is already Done is reported and left alone. Use `--dry-run` to preview.
- It changes the board only. `TODO.md`, `CHANGELOG.md` and any linked issue or PR are not touched, so tick the item in `TODO.md` too, or the next `--update` will see the board ahead of the file (shown as `ahead` by `--diff`, never moved back).
- Run it from the repo's directory (the project is found from the git `origin`); it takes no path argument because its value is the item.

## Restoring archived items (`--restore`)

`kopi --restore` unarchives the items that `kopi --archive` archived in this project, with their status intact. Use `--dry-run` to see them first.

GitHub's API cannot list archived items, so `--archive` records each item it archives (owner, project, item id, title, status, date) in a small local log, `~/.config/kopi/archived.json` (override the location with the `KOPI_STATE` environment variable), and `--restore` reads it. Restored items are removed from the log. If an item can't be restored (for example it was deleted), `kopi` warns and keeps its entry.

- Items archived by hand, on another machine, or before this feature existed are not in the log. Restore them by id with `kopi --restore --id ITEM_ID` (repeatable), using the `PVTI_...` item id (the `id` field from `kopi items`, which is how `--archive` identifies items).
- Only items from the current project are restored, even if the log holds others.
- `--restore` only undoes an archive. Run `--update` afterwards if you want the board brought back in line with the files, but note that `--archive` would archive a restored orphan again.

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
