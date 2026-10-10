#!/usr/bin/env python3
"""kopi: create and manage GitHub Projects (v2) from the command line.

Shells out to the `gh` CLI, which handles authentication.
Requires the `project` scope: gh auth refresh -s project

Copyright (c) Richard Spindler. Licensed under CC BY-NC-SA 4.0:
https://creativecommons.org/licenses/by-nc-sa/4.0/ (see LICENSE.md)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from types import SimpleNamespace

__version__ = "0.3.9"


class GhError(RuntimeError):
    pass


def run(*args):
    """Run `gh <args>` and return stdout. Raises GhError on failure."""
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    except FileNotFoundError:
        raise GhError("gh CLI not found; install it from https://cli.github.com")
    if proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip()
        if "scope" in msg:
            msg += "\nHint: run `gh auth refresh -s project`"
        raise GhError(msg)
    return proc.stdout


AUTH_HELP = """Not authorised. Run:
  gh auth login
  gh auth refresh -s project"""


def check_auth():
    """Raise GhError with setup instructions unless gh is logged in with the project scope."""
    try:
        proc = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    except FileNotFoundError:
        raise GhError("gh CLI not found; install it from https://cli.github.com")
    if proc.returncode != 0:
        raise GhError(AUTH_HELP)
    scopes = re.search(r"Token scopes: (.*)", proc.stdout + proc.stderr)
    # Scopes are not reported for some token types (e.g. GH_TOKEN); only fail when listed without it.
    if scopes and "'project'" not in scopes.group(1):
        raise GhError("Token is missing the 'project' scope. Run:\n  gh auth refresh -s project")


def git_repo():
    """(owner, repo) of the current git repo's github.com `origin` remote, or None."""
    try:
        proc = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", proc.stdout.strip())
    return (m.group(1), m.group(2)) if m else None


def git_owner():
    repo = git_repo()
    return repo[0] if repo else None


def run_json(*args):
    out = run(*args, "--format", "json")
    return json.loads(out) if out.strip() else None


def graphql(query, **variables):
    """Run a GraphQL query for anything `gh project` doesn't cover."""
    cmd = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        cmd += ["-F", f"{k}={v}"]
    return json.loads(run(*cmd))


def _owner(args):
    return ["--owner", args.owner] if args.owner else []


def linked_repos(projects):
    """{project id: [owner/repo, ...]} via one batched GraphQL query; empty if the lookup fails."""
    ids = [p["id"] for p in projects if re.fullmatch(r"[\w-]+", p.get("id") or "")]
    if not ids:
        return {}
    query = "query { " + " ".join(
        f'p{n}: node(id: "{i}") {{ ... on ProjectV2 {{ repositories(first: 5) {{ nodes {{ nameWithOwner }} }} }} }}'
        for n, i in enumerate(ids)) + " }"
    try:
        data = graphql(query)["data"]
    except (GhError, ValueError, KeyError) as e:
        print(f"warning: could not look up linked repos: {e}", file=sys.stderr)
        return {}
    return {i: [r["nameWithOwner"] for r in ((data.get(f"p{n}") or {}).get("repositories") or {}).get("nodes", [])]
            for n, i in enumerate(ids)}


def emit(args, data):
    """Under --json, print `data` as the command's single JSON result."""
    if args.json:
        print(json.dumps(data, indent=2))


def say(args, msg):
    """Human-readable output; suppressed under --json so stdout holds only the JSON result."""
    if not args.json:
        print(msg)


def _item(status, title, url=None):
    return {"status": status, "title": title, **({"url": url} if url else {})}


def cmd_list(args):
    cmd = ["project", "list", *_owner(args), "--limit", str(args.limit)]
    if args.closed:
        cmd.append("--closed")
    projects = run_json(*cmd)["projects"]
    repos = linked_repos(projects)
    rows = [{"number": p["number"], "title": p["title"], "state": "closed" if p.get("closed") else "open",
             "repos": repos.get(p.get("id"), []), "url": p["url"]} for p in projects]
    if args.json:
        emit(args, rows)
        return
    print("NUMBER\tTITLE\tSTATE\tREPO\tURL")
    for r in rows:
        print(f"{r['number']}\t{r['title']}\t{r['state']}\t{','.join(r['repos']) or '-'}\t{r['url']}")


def _create(args):
    """Create the project and link --repo if given. Returns (project, linked repo or None)."""
    p = run_json("project", "create", "--title", args.title, *_owner(args))
    say(args, f"Created #{p['number']}: {p['url']}")
    linked = None
    if args.repo:
        try:
            run("project", "link", str(p["number"]), *_owner(args), "--repo", args.repo)
            say(args, f"Linked to {args.repo}")
            linked = args.repo
        except GhError as e:
            print(f"warning: project #{p['number']} was created but linking to {args.repo} failed: {e}", file=sys.stderr)
    return p, linked


def cmd_create(args):
    p, linked = _create(args)
    emit(args, {"number": p["number"], "url": p["url"], "linked_repo": linked})


def find_project(args):
    """Return (project or None, (repo_owner, repo_name)) for the project named after the current repo."""
    repo = git_repo()
    if not repo:
        raise GhError("--init/--import/--update must be run inside a git repo with a github.com origin remote")
    data = run_json("project", "list", *_owner(args), "--limit", "100")
    return next((p for p in data["projects"] if p["title"] == repo[1]), None), repo


def ensure_project(args):
    """Find the project named after the current repo, creating and linking it if missing.

    Returns (number, created).
    """
    p, (repo_owner, name) = find_project(args)
    if p:
        say(args, f"Project exists: #{p['number']} {p['title']} {p['url']}")
        return p["number"], False
    args.title = name
    # gh only links a project to repos under the same owner
    args.repo = f"{repo_owner}/{name}" if args.owner == repo_owner else None
    if not args.repo:
        print(f"Not linking: project owner {args.owner} differs from repo owner {repo_owner}", file=sys.stderr)
    return _create(args)[0]["number"], True


def cmd_init(args):
    number, created = ensure_project(args)
    emit(args, {"number": number, "created": created})


STOPWORDS = {"a", "an", "and", "the", "of", "to", "in", "on", "for", "with", "from", "by", "is", "as", "at", "or"}


def _words(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words if w not in STOPWORDS and len(w) > 1}


def overlap(a, b):
    """Share of the shorter text's words that appear in the other (0..1)."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def similar(a, b, threshold=0.4):
    return overlap(a, b) >= threshold


def parse_changelog(text):
    """[(version, entry)] for every bullet under a `## [version]` heading, in file order."""
    items, version = [], None
    for line in text.splitlines():
        m = re.match(r"## \[(.+?)\]", line)
        if m:
            version = m.group(1)
        elif version and line.startswith("- "):
            items.append((version, line[2:].strip()))
    return items


def parse_todo(text):
    """[(status, text)] for every checkbox line; [ ] Todo, [~] In Progress, [x] Done."""
    names = {" ": "Todo", "~": "In Progress", "x": "Done"}
    return [(names[m.group(1).lower()], m.group(2).strip())
            for m in re.finditer(r"^- \[([ ~xX])\] (.+)$", text, re.M)]


def _title(text, limit=100):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def build_plan(changelog, todo, linked=()):
    """Return ([(status, title, body)], [skipped TODO done texts]).

    Changelog entries become Done items (oldest first). TODO items follow, except
    done ones that duplicate a changelog entry and any that duplicate a linked
    issue/PR (`linked` is [(status, title, url)]; those are added separately).
    """
    entries = parse_changelog(changelog)
    plan, skipped = [], []
    for version, text in reversed(entries):
        title = _title(f"[{version}] {text}")
        plan.append(("Done", title, text if title != f"[{version}] {text}" else ""))
    for status, text in parse_todo(todo):
        if any(similar(text, t) for _, t, _ in linked) or \
                (status == "Done" and any(similar(text, e) for _, e in entries)):
            skipped.append(text)
            continue
        title = _title(text)
        plan.append((status, title, text if title != text else ""))
    return plan, skipped


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


SOURCE_FILES = ("CHANGELOG.md", "TODO.md")


def load_sources(args):
    """(changelog, todo, linked) for --import/--update. With args.only (one file given), only that file is read
    and issues/PRs are skipped."""
    only = getattr(args, "only", None)
    changelog = "" if only == "TODO.md" else _read("CHANGELOG.md")
    todo = "" if only == "CHANGELOG.md" else _read("TODO.md")
    return changelog, todo, [] if only else fetch_linked(git_repo())


def fetch_linked(repo):
    """[(status, title, url)] for the repo's issues and PRs, mapped to project statuses.

    Open issue -> Todo, closed issue -> Done, open PR -> In Progress, merged PR -> Done.
    Closed-unmerged PRs are skipped. A failed lookup is a warning, not an error.
    """
    if not repo:
        return []
    slug = "/".join(repo)
    states = {"issue": {"OPEN": "Todo", "CLOSED": "Done"},
              "pr": {"OPEN": "In Progress", "MERGED": "Done"}}
    linked = []
    for kind, mapping in states.items():
        try:
            rows = json.loads(run(kind, "list", "--repo", slug, "--state", "all", "--limit", "200",
                                  "--json", "number,title,state,url"))
        except GhError as e:
            print(f"warning: could not list {kind}s for {slug}: {e}", file=sys.stderr)
            continue
        linked += [(mapping[r["state"]], r["title"], r["url"]) for r in rows if r["state"] in mapping]
    return linked


def _status_setter(args, number, needed):
    """Return set_status(item_id, status) for the project's Status field; error if an option is missing."""
    project_id = run_json("project", "view", str(number), *_owner(args))["id"]
    fields = run_json("project", "field-list", str(number), *_owner(args))["fields"]
    status_field = next((f for f in fields if f["name"] == "Status"), None)
    if not status_field:
        raise GhError("project has no Status field")
    options = {o["name"].lower(): o["id"] for o in status_field.get("options", [])}
    missing = {st for st in needed if st.lower() not in options}
    if missing:
        raise GhError(f"Status field has no option(s): {', '.join(sorted(missing))}")

    def set_status(item_id, status):
        run("project", "item-edit", "--id", item_id, "--project-id", project_id,
            "--field-id", status_field["id"], "--single-select-option-id", options[status.lower()])
    return set_status


SETTLE_TIMEOUT, SETTLE_INTERVAL = 30, 2


def wait_for_items(args, number, ids):
    """Poll item-list until every new item id is listed, so an immediate re-run sees them.

    GitHub's listing lags after writes; without this a second --update can re-add the same items.
    Gives up after SETTLE_TIMEOUT seconds with a warning.
    """
    pending = set(ids)
    if not pending:
        return
    waited = 0
    while True:
        listed = {i["id"] for i in run_json("project", "item-list", str(number), *_owner(args),
                                            "--limit", "500")["items"]}
        pending -= listed
        if not pending:
            return
        if waited >= SETTLE_TIMEOUT:
            print(f"warning: {len(pending)} new items not yet listed by GitHub; wait before running --update again",
                  file=sys.stderr)
            return
        time.sleep(SETTLE_INTERVAL)
        waited += SETTLE_INTERVAL


def cmd_import(args):
    """Seed an empty project from CHANGELOG.md and TODO.md (draft items) plus the repo's issues and PRs."""
    changelog, todo, linked = load_sources(args)
    if not changelog and not todo and not linked:
        raise GhError("nothing to import: no CHANGELOG.md or TODO.md in the current directory, and no issues or PRs")
    plan, skipped = build_plan(changelog, todo, linked)
    total = len(plan) + len(linked)
    if args.dry_run:
        items = [_item(st, t) for st, t, _ in plan] + [_item(st, _title(t), u) for st, t, u in linked]
        if args.json:
            emit(args, {"dry_run": True, "items": items, "skipped": [_title(t) for t in skipped]})
            return
        for status, title, _ in plan:
            print(f"{status}\t{title}")
        for status, title, url in linked:
            print(f"{status}\t{_title(title)}\t{url}")
        for text in skipped:
            print(f"skipped (duplicate)\t{_title(text)}")
        print(f"Dry run: {total} items would be imported")
        return
    number, _ = ensure_project(args)
    if run_json("project", "item-list", str(number), *_owner(args), "--limit", "1")["items"] and not args.force:
        say(args, f"Project #{number} already has items; nothing imported (use --force to import anyway)")
        emit(args, {"project": number, "dry_run": False, "imported": 0, "reason": "project already has items"})
        return
    set_status = _status_setter(args, number, [st for st, *_ in plan + linked])

    created = []
    for status, title, body in plan:
        created.append(run_json("project", "item-create", str(number), *_owner(args),
                                "--title", title, "--body", body)["id"])
        set_status(created[-1], status)
    for status, _, url in linked:
        created.append(run_json("project", "item-add", str(number), *_owner(args), "--url", url)["id"])
        set_status(created[-1], status)
    wait_for_items(args, number, created)
    say(args, f"Imported {total} items into project #{number}")
    emit(args, {"project": number, "dry_run": False, "imported": total})


RANK = {"Todo": 0, "In Progress": 1, "Done": 2}
CHANGELOG_TITLE = re.compile(r"^\[(\d+\.\d+\.\d+|Unreleased)\] ")


def _item_url(item):
    """URL of a linked issue/PR item, or None for draft items."""
    content = item.get("content") or {}
    if content.get("url"):
        return content["url"]
    if content.get("number") and content.get("repository"):
        kind = "pull" if content.get("type") == "PullRequest" else "issues"
        return f"https://github.com/{content['repository']}/{kind}/{content['number']}"
    return None


def sync_plan(args):
    """Compare CHANGELOG.md, TODO.md and the repo's issues/PRs with the project's items.

    Returns a namespace with: number; adds [(status, title, body)]; link_adds [(status, title, url)];
    moves [(item, from, to, title)] where the board is behind; ahead [(item, board, file, title)] where
    the board is ahead (never acted on); orphans, the board's draft items that match no file entry.
    """
    changelog, todo, linked = load_sources(args)
    if not changelog and not todo and not linked:
        raise GhError("nothing to compare: no CHANGELOG.md or TODO.md in the current directory, and no issues or PRs")
    plan, skipped = build_plan(changelog, todo)
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    number = project["number"]
    existing = run_json("project", "item-list", str(number), *_owner(args), "--limit", "500")["items"]

    # (status, title, body, is_changelog, can_add); skipped TODO items only advance a stale board copy
    entries = [(st, t, body, bool(CHANGELOG_TITLE.match(t)), True) for st, t, body in plan]
    entries += [("Done", _title(text), "", False, False) for text in skipped]

    # An exact title match wins; otherwise a TODO item may fuzzy-match an unclaimed item at 0.75+
    # overlap. Each board item matches at most one entry, so near-identical items are not conflated.
    # Linked issue/PR items are matched by URL only; they never take part in title matching.
    by_url = {u: i for i in existing if (u := _item_url(i))}
    linked_ids = {i["id"] for i in by_url.values()}
    matched, claimed = {}, set(linked_ids)
    for idx, (_, title, _, _, _) in enumerate(entries):
        item = next((i for i in existing if i["id"] not in claimed and i["title"] == title), None)
        if item:
            matched[idx] = item
            claimed.add(item["id"])
    for idx, (_, title, body, is_changelog, _) in enumerate(entries):
        if idx in matched or is_changelog:
            continue
        scored = [(overlap(body or title, i["title"]), i) for i in existing if i["id"] not in claimed]
        score, item = max(scored, key=lambda p: p[0], default=(0, None))
        if item and score >= 0.75:
            matched[idx] = item
            claimed.add(item["id"])

    # Completion sweep: a completed entry (changelog or [x] TODO) that matches no board item exactly may be
    # a reworded version of an item still open on the board. Match those at 0.6+ overlap, best pairs first,
    # and move the board item to Done instead of adding a duplicate.
    open_items = [i for i in existing if i["id"] not in claimed and RANK.get(i.get("status"), -1) < RANK["Done"]]
    pairs = sorted(((overlap(CHANGELOG_TITLE.sub("", body or title), i["title"]), idx, i)
                    for idx, (status, title, body, _, _) in enumerate(entries)
                    if idx not in matched and status == "Done" for i in open_items),
                   key=lambda p: p[0], reverse=True)
    for score, idx, item in pairs:
        if score >= 0.6 and idx not in matched and item["id"] not in claimed:
            matched[idx] = item
            claimed.add(item["id"])

    adds, moves, ahead, link_adds = [], [], [], []

    def compare(item, status):
        board = RANK.get(item.get("status"), -1)
        if RANK[status] > board:
            moves.append((item, item.get("status") or "none", status, item["title"]))
        elif RANK[status] < board:
            ahead.append((item, item.get("status"), status, item["title"]))

    for idx, (status, title, body, is_changelog, can_add) in enumerate(entries):
        item = matched.get(idx)
        if item:
            compare(item, status)
        elif can_add and (is_changelog or not any(similar(body or title, t) for _, t, _ in linked)):
            adds.append((status, title, body))  # a TODO item duplicating an issue/PR title is left to the issue/PR
    for status, title, url in linked:
        item = by_url.get(url)
        if item:
            compare(item, status)
        else:
            link_adds.append((status, title, url))

    matched_ids = {i["id"] for i in matched.values()}
    orphans = [i for i in existing if i["id"] not in matched_ids and i["id"] not in linked_ids]
    return SimpleNamespace(number=number, adds=adds, link_adds=link_adds, moves=moves, ahead=ahead, orphans=orphans, items=existing)


def cmd_update(args):
    """Sync CHANGELOG.md, TODO.md and the repo's issues/PRs into the existing project.

    Adds items the project lacks and moves items forward (Todo -> In Progress -> Done)
    when the files or GitHub are ahead of the board. Never moves an item backwards.
    """
    p = sync_plan(args)
    adds, link_adds, moves, number = p.adds, p.link_adds, p.moves, p.number
    report = {"project": number, "dry_run": bool(args.dry_run),
              "added": [_item(st, t) for st, t, _ in adds] + [_item(st, _title(t), u) for st, t, u in link_adds],
              "moved": [{"title": t, "from": old, "to": new} for _, old, new, t in moves]}
    if args.dry_run:
        if args.json:
            emit(args, report)
            return
        for status, title, _ in adds:
            print(f"add\t{status}\t{title}")
        for status, title, url in link_adds:
            print(f"add\t{status}\t{_title(title)}\t{url}")
        for _, old, new, title in moves:
            print(f"move\t{old} -> {new}\t{title}")
        print(f"Dry run: {len(adds) + len(link_adds)} to add, {len(moves)} to move in project #{number}")
        return
    if not adds and not link_adds and not moves:
        say(args, f"Project #{number} is up to date")
        emit(args, report)
        return
    set_status = _status_setter(args, number,
                                [st for st, *_ in adds + link_adds] + [new for _, _, new, _ in moves])
    created = []
    for status, title, body in adds:
        created.append(run_json("project", "item-create", str(number), *_owner(args),
                                "--title", title, "--body", body)["id"])
        set_status(created[-1], status)
    for status, _, url in link_adds:
        created.append(run_json("project", "item-add", str(number), *_owner(args), "--url", url)["id"])
        set_status(created[-1], status)
    for item, _, new, _ in moves:
        set_status(item["id"], new)
    wait_for_items(args, number, created)
    say(args, f"Updated project #{number}: {len(adds) + len(link_adds)} added, {len(moves)} moved")
    emit(args, report)


def cmd_diff(args):
    """Show every difference between the project's items and CHANGELOG.md / TODO.md / issues / PRs. Read-only."""
    p = sync_plan(args)
    dupes = find_duplicates(p.items)
    extra_ids = {extra["id"] for _, extra in dupes}
    orphans = [i for i in p.orphans if i["id"] not in extra_ids]  # a duplicate is reported once, as a duplicate
    if args.json:
        emit(args, {"project": p.number,
                    "files_only": [_item(st, t) for st, t, _ in p.adds],
                    "github_only": [_item(st, _title(t), u) for st, t, u in p.link_adds],
                    "board_only": [_item(i.get("status") or "none", i["title"]) for i in orphans],
                    "duplicates": [_item(extra.get("status") or "none", extra["title"]) for _, extra in dupes],
                    "behind": [{"title": t, "from": old, "to": new} for _, old, new, t in p.moves],
                    "ahead": [{"title": t, "board": b or "none", "file": f} for _, b, f, t in p.ahead]})
        return
    for status, title, _ in p.adds:
        print(f"files-only\t{status}\t{title}")
    for status, title, url in p.link_adds:
        print(f"github-only\t{status}\t{_title(title)}\t{url}")
    for item in orphans:
        print(f"board-only\t{item.get('status') or 'none'}\t{item['title']}")
    for keep, extra in dupes:
        print(f"duplicate\t{extra.get('status') or 'none'}\t{extra['title']}\t(kept copy: {keep.get('status') or 'none'})")
    for _, old, new, title in p.moves:
        print(f"behind\t{old} -> {new}\t{title}")
    for _, board, filed, title in p.ahead:
        print(f"ahead\t{board or 'none'} (file: {filed})\t{title}")
    counts = (len(p.adds), len(p.link_adds), len(orphans), len(p.moves), len(p.ahead), len(dupes))
    if not any(counts):
        print(f"No differences in project #{p.number}")
        return
    print(f"Project #{p.number}: {counts[0]} only in files, {counts[1]} only on GitHub, {counts[2]} only on board, "
          f"{counts[3]} behind, {counts[4]} ahead, {counts[5]} duplicates (see --squash)")


def cmd_archive(args):
    """Archive open board drafts that no longer match any CHANGELOG.md / TODO.md entry.

    Done items and linked issues/PRs are never archived. An orphan that resembles a pending new item
    (50%+ shared words) is probably a reworded one, so it is skipped unless --force is given.
    """
    p = sync_plan(args)
    pending = [body or title for _, title, body in p.adds] + [title for _, title, _ in p.link_adds]
    to_archive, reworded = [], []
    for item in p.orphans:
        if RANK.get(item.get("status"), -1) >= RANK["Done"]:
            continue
        looks_reworded = any(overlap(text, item["title"]) >= 0.5 for text in pending)
        (reworded if looks_reworded and not args.force else to_archive).append(item)
    report = {"project": p.number, "dry_run": bool(args.dry_run),
              "archived": [_item(i.get("status") or "none", i["title"]) for i in to_archive],
              "skipped": [_item(i.get("status") or "none", i["title"]) for i in reworded]}
    if args.json and args.dry_run:
        emit(args, report)
        return
    if args.dry_run:
        for item in to_archive:
            print(f"archive\t{item.get('status') or 'none'}\t{item['title']}")
        for item in reworded:
            print(f"skip (looks reworded; use --force)\t{item.get('status') or 'none'}\t{item['title']}")
        print(f"Dry run: {len(to_archive)} to archive, {len(reworded)} skipped in project #{p.number}")
        return
    for item in to_archive:
        run("project", "item-archive", str(p.number), *_owner(args), "--id", item["id"])
    msg = f"Archived {len(to_archive)} items in project #{p.number}"
    say(args, msg + (f" ({len(reworded)} skipped as possibly reworded; use --force)" if reworded else ""))
    emit(args, report)


def find_duplicates(items):
    """[(kept, extra)] for each board item that has another copy: same URL (linked) or same title (drafts).

    The kept copy is the one furthest along (Done over In Progress over Todo), the first listed on a tie.
    """
    groups = {}
    for item in items:
        key = _item_url(item) or " ".join(item["title"].lower().split())
        groups.setdefault(key, []).append(item)
    dupes = []
    for group in groups.values():
        keep = max(group, key=lambda i: RANK.get(i.get("status"), -1))  # max() keeps the first of equals
        dupes += [(keep, i) for i in group if i is not keep]
    return dupes


def cmd_squash(args):
    """Delete duplicate board items: same URL (linked) or same title (drafts).

    Keeps the copy furthest along (Done over In Progress over Todo), the earliest listed on a tie. Works
    on the board only; the files and GitHub issues/PRs are not consulted.
    """
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    number = project["number"]
    items = run_json("project", "item-list", str(number), *_owner(args), "--limit", "500")["items"]
    doomed = [extra for _, extra in find_duplicates(items)]
    report = {"project": number, "dry_run": bool(args.dry_run),
              "deleted": [_item(i.get("status") or "none", i["title"]) for i in doomed]}
    if args.json and args.dry_run:
        emit(args, report)
        return
    if args.dry_run:
        for item in doomed:
            print(f"delete\t{item.get('status') or 'none'}\t{item['title']}")
        print(f"Dry run: {len(doomed)} duplicates to delete in project #{number}")
        return
    for item in doomed:
        run("project", "item-delete", str(number), *_owner(args), "--id", item["id"])
    say(args, f"Deleted {len(doomed)} duplicate items in project #{number}")
    emit(args, report)


def cmd_view(args):
    print(json.dumps(run_json("project", "view", str(args.number), *_owner(args)), indent=2))


def cmd_delete(args):
    run("project", "delete", str(args.number), *_owner(args))
    say(args, f"Deleted #{args.number}")
    emit(args, {"deleted": args.number})


def cmd_fields(args):
    data = run_json("project", "field-list", str(args.number), *_owner(args))
    if args.json:
        emit(args, data["fields"])
        return
    for f in data["fields"]:
        print(f"{f['name']}\t{f['type']}")


def cmd_items(args):
    data = run_json("project", "item-list", str(args.number), *_owner(args), "--limit", str(args.limit))
    if args.json:
        emit(args, data["items"])
        return
    for i in data["items"]:
        print(f"{i['id']}\t{i.get('status', '')}\t{i['title']}")


def cmd_add(args):
    """Add an existing issue/PR by URL, or create a draft item with --title."""
    if args.url:
        run("project", "item-add", str(args.number), *_owner(args), "--url", args.url)
    else:
        run("project", "item-create", str(args.number), *_owner(args), "--title", args.title)
    say(args, "Added")
    emit(args, {"added": args.url or args.title})


def build_parser():
    p = argparse.ArgumentParser(prog="kopi", description="Manage GitHub Projects (v2)")
    p.add_argument("--version", action="version", version=f"kopi {__version__}")
    p.add_argument("--owner", help="user or org (default: owner of the current git repo's origin, else @me)")
    p.add_argument("--init", nargs="?", const=".", metavar="PATH",
                   help="create a project named after the repo (linked to it) if none exists; "
                        "uses the repo in PATH, default the current directory")
    p.add_argument("--import", dest="do_import", nargs="?", const=".", metavar="PATH",
                   help="seed an empty project from CHANGELOG.md, TODO.md and the repo's issues/PRs (creates the project if missing); "
                        "uses the repo in PATH, default the current directory; "
                        "give CHANGELOG.md or TODO.md (optionally with a directory, e.g. dir/TODO.md) to import only that file")
    p.add_argument("--update", nargs="?", const=".", metavar="PATH",
                   help="sync CHANGELOG.md, TODO.md and the repo's issues/PRs into the existing project: add missing items, move items forward; "
                        "uses the repo in PATH, default the current directory; "
                        "give CHANGELOG.md or TODO.md (optionally with a directory) to update from only that file")
    p.add_argument("--diff", nargs="?", const=".", metavar="PATH",
                   help="show differences between the project's items and CHANGELOG.md, TODO.md and issues/PRs; "
                        "uses the repo in PATH, default the current directory")
    p.add_argument("--archive", nargs="?", const=".", metavar="PATH",
                   help="archive open board drafts that no longer match any CHANGELOG.md/TODO.md entry; "
                        "uses the repo in PATH, default the current directory")
    p.add_argument("--squash", nargs="?", const=".", metavar="PATH",
                   help="delete duplicate board items (same title or URL), keeping the one furthest along; "
                        "uses the repo in PATH, default the current directory")
    p.add_argument("--json", action="store_true",
                   help="print the result as JSON on stdout (status messages are suppressed; warnings stay on stderr)")
    p.add_argument("--dry-run", action="store_true", help="with --import/--update/--archive/--squash: print what would change")
    p.add_argument("--force", action="store_true",
                   help="with --import: import even if the project has items; with --archive: also archive items that look reworded")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("list", help="list projects")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--closed", action="store_true", help="include closed projects")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("create", help="create a project")
    s.add_argument("title")
    s.add_argument("--repo", help="OWNER/REPO to link the project to")
    s.set_defaults(fn=cmd_create)

    for name, fn, help_ in [("view", cmd_view, "show project JSON"),
                            ("delete", cmd_delete, "delete a project"),
                            ("fields", cmd_fields, "list project fields")]:
        s = sub.add_parser(name, help=help_)
        s.add_argument("number", type=int)
        s.set_defaults(fn=fn)

    s = sub.add_parser("items", help="list project items")
    s.add_argument("number", type=int)
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_items)

    s = sub.add_parser("add", help="add an issue/PR (--url) or draft item (--title)")
    s.add_argument("number", type=int)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--url")
    g.add_argument("--title")
    s.set_defaults(fn=cmd_add)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    modes = [args.init, args.do_import, args.update, args.diff, args.archive, args.squash]
    if not (any(modes) or args.cmd):
        parser.error("a command is required (or use --init / --import / --update / --diff / --archive / --squash)")
    if sum(map(bool, modes)) > 1:
        parser.error("--init, --import, --update, --diff, --archive and --squash are mutually exclusive")
    try:
        path = next((m for m in modes if m), None)
        args.only = None
        if path:
            if os.path.isfile(path) or os.path.basename(path) in SOURCE_FILES:
                # a single source file: only --import/--update take one; work in its directory
                if not (args.do_import or args.update):
                    raise GhError(f"expected a directory, not a file: {path}")
                if os.path.basename(path) not in SOURCE_FILES:
                    raise GhError(f"only {' or '.join(SOURCE_FILES)} can be given as a file, not {path}")
                if not os.path.isfile(path):
                    raise GhError(f"no such file: {path}")
                args.only = os.path.basename(path)
                path = os.path.dirname(path) or "."
            elif not os.path.isdir(path):
                raise GhError(f"not a directory: {path}")
            os.chdir(path)
        check_auth()
        if not args.owner:
            args.owner = git_owner()
            if args.owner:
                print(f"Using owner: {args.owner} (from git origin)", file=sys.stderr)
            else:
                args.owner = "@me"
        handler = next((fn for on, fn in ((args.do_import, cmd_import), (args.update, cmd_update),
                                          (args.diff, cmd_diff), (args.archive, cmd_archive),
                                          (args.squash, cmd_squash),
                                          (args.init, cmd_init)) if on), getattr(args, "fn", None))
        handler(args)
    except GhError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
