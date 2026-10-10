#!/usr/bin/env python3
"""kopi (Keep Our Projects In-sync): create and manage GitHub Projects (v2) from the command line, and keep them
in step with CHANGELOG.md, TODO.md, issues and PRs.

Shells out to the `gh` CLI, which handles authentication.
Requires the `project` scope: gh auth refresh -s project

Copyright (c) Richard Spindler. Licensed under CC BY-NC-SA 4.0:
https://creativecommons.org/licenses/by-nc-sa/4.0/ (see LICENSE.md)
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from types import SimpleNamespace

__version__ = "0.6.1"


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


ITEM_LIMIT = 5000      # `gh` pages through results up to --limit, so this is "everything" for any realistic board
PROJECT_LIMIT = 1000
LINKED_LIMIT = 5000    # issues / PRs per repo


def _warn_if_full(what, count, limit):
    if count >= limit:
        print(f"warning: {what} stopped at the limit of {limit}; results may be incomplete", file=sys.stderr)


# The tool speaks three canonical statuses. Boards may name their Status options differently: these common
# names are recognised, and --status-map adds or overrides pairs.
STATUS_SYNONYMS = {"Todo": ("todo", "to do", "backlog", "open", "ready", "not started"),
                   "In Progress": ("in progress", "inprogress", "doing", "wip", "started", "active", "in development"),
                   "Done": ("done", "complete", "completed", "closed", "finished", "shipped")}


def parse_status_map(text):
    """{canonical status: board option name} from "Todo=Backlog,In Progress=Doing"; GhError on a bad pair."""
    mapping = {}
    for pair in filter(None, (p.strip() for p in (text or "").split(","))):
        name, sep, option = (x.strip() for x in pair.partition("="))
        canon = next((c for c in STATUS_SYNONYMS if c.lower() == name.lower()), None)
        if not sep or not option or not canon:
            raise GhError(f"bad --status-map entry {pair!r}: expected Todo|In Progress|Done=OPTION")
        mapping[canon] = option
    return mapping


def _status_map_arg(text):
    try:
        return parse_status_map(text)
    except GhError as e:
        raise argparse.ArgumentTypeError(str(e))


def canonical_status(name, args):
    """Todo / In Progress / Done for a board option name (via --status-map, then the built-in synonyms); other names unchanged."""
    low = name.strip().lower()
    for canon, option in (getattr(args, "status_map", None) or {}).items():
        if option.lower() == low:
            return canon
    for canon, names in STATUS_SYNONYMS.items():
        if low == canon.lower() or low in names:
            return canon
    return name


def status_option(status, options, args):
    """Id of the Status option for canonical `status` (--status-map, exact name, then synonyms), or None.

    `options` maps lower-cased option names to ids."""
    mapped = (getattr(args, "status_map", None) or {}).get(status)
    candidates = [mapped.lower()] if mapped else []
    candidates += [status.lower(), *STATUS_SYNONYMS.get(status, ())]
    return next((options[c] for c in candidates if c in options), None)


def board_items(args, number):
    """The project's (non-archived) items, all of them, with each item's status normalised to Todo / In Progress / Done."""
    items = run_json("project", "item-list", str(number), *_owner(args), "--limit", str(ITEM_LIMIT))["items"]
    _warn_if_full("the project's item list", len(items), ITEM_LIMIT)
    for i in items:
        if i.get("status"):
            i["status"] = canonical_status(i["status"], args)
    return items


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
        sink = getattr(args, "sink", None)  # --sync collects its steps' results into one document
        if sink is not None:
            sink.append(data)
        else:
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
    data = run_json("project", "list", *_owner(args), "--limit", str(PROJECT_LIMIT))
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
            rows = json.loads(run(kind, "list", "--repo", slug, "--state", "all", "--limit", str(LINKED_LIMIT),
                                  "--json", "number,title,state,url"))
        except GhError as e:
            print(f"warning: could not list {kind}s for {slug}: {e}", file=sys.stderr)
            continue
        _warn_if_full(f"the {kind} list for {slug}", len(rows), LINKED_LIMIT)
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
    needed = {canonical_status(st, args) for st in needed}
    missing = {st for st in needed if not status_option(st, options, args) and st.lower() not in options}
    if missing:
        raise GhError(f"Status field has no option(s): {', '.join(sorted(missing))}"
                      " (map them with --status-map, e.g. --status-map \"Todo=Backlog,Done=Shipped\")")

    def set_status(item_id, status):
        status = canonical_status(status, args)
        option = status_option(status, options, args) or options[status.lower()]
        run("project", "item-edit", "--id", item_id, "--project-id", project_id,
            "--field-id", status_field["id"], "--single-select-option-id", option)
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
        listed = {i["id"] for i in board_items(args, number)}
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
    the board is ahead (never acted on); orphans, the board's draft items that match no file entry; ticks
    [(item, todo text, todo status)] for TODO entries still open although their board item is Done.
    """
    changelog, todo, linked = load_sources(args)
    if not changelog and not todo and not linked:
        raise GhError("nothing to compare: no CHANGELOG.md or TODO.md in the current directory, and no issues or PRs")
    plan, skipped = build_plan(changelog, todo)
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    number = project["number"]
    existing = board_items(args, number)

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

    adds, moves, ahead, link_adds, ticks = [], [], [], [], []

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
            if not is_changelog and status != "Done" and RANK.get(item.get("status"), -1) >= RANK["Done"]:
                ticks.append((item, body or title, status))  # Done on the board, still open in TODO.md
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
    return SimpleNamespace(number=number, adds=adds, link_adds=link_adds, moves=moves, ahead=ahead, orphans=orphans, items=existing, ticks=ticks)


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


CHECK_FAILED = 2  # --diff --check exit status when the board has drifted (1 is reserved for errors)


def cmd_diff(args):
    """Show every difference between the project's items and CHANGELOG.md / TODO.md / issues / PRs. Read-only.

    Returns CHECK_FAILED under --check when there is any difference, else 0 (for CI and pre-commit hooks).
    """
    p = sync_plan(args)
    dupes = find_duplicates(p.items)
    extra_ids = {extra["id"] for _, extra in dupes}
    orphans = [i for i in p.orphans if i["id"] not in extra_ids]  # a duplicate is reported once, as a duplicate
    counts = (len(p.adds), len(p.link_adds), len(orphans), len(p.moves), len(p.ahead), len(dupes))
    drift = any(counts)
    if args.json:
        emit(args, {"project": p.number,
                    "files_only": [_item(st, t) for st, t, _ in p.adds],
                    "github_only": [_item(st, _title(t), u) for st, t, u in p.link_adds],
                    "board_only": [_item(i.get("status") or "none", i["title"]) for i in orphans],
                    "duplicates": [_item(extra.get("status") or "none", extra["title"]) for _, extra in dupes],
                    "behind": [{"title": t, "from": old, "to": new} for _, old, new, t in p.moves],
                    "ahead": [{"title": t, "board": b or "none", "file": f} for _, b, f, t in p.ahead],
                    "in_sync": not drift})
        return CHECK_FAILED if args.check and drift else 0
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
    if not drift:
        print(f"No differences in project #{p.number}")
        return 0
    print(f"Project #{p.number}: {counts[0]} only in files, {counts[1]} only on GitHub, {counts[2]} only on board, "
          f"{counts[3]} behind, {counts[4]} ahead, {counts[5]} duplicates (see --dedupe)")
    return CHECK_FAILED if args.check else 0


def _state_path():
    """Where --archive records what it archived (GitHub's API cannot list archived items). KOPI_STATE overrides."""
    return os.environ.get("KOPI_STATE") or os.path.join(os.path.expanduser("~"), ".config", "kopi", "archived.json")


def load_archive_log():
    try:
        with open(_state_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save_archive_log(entries):
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(entries, f, indent=2)


def _log_archived(args, number, item):
    save_archive_log(load_archive_log() + [{
        "owner": args.owner, "project": number, "id": item["id"], "title": item["title"],
        "status": item.get("status") or "none", "archived": datetime.date.today().isoformat()}])


def _match_one(items, text, number, flag):
    """The one item whose title contains `text` (case-insensitive); GhError listing candidates otherwise."""
    hits = [i for i in items if text.lower() in i["title"].lower()]
    if len(hits) != 1:
        lines = "".join(f"\n  {i['id']}\t{i['title']}" for i in hits[:10])
        raise GhError(f"{flag} {text!r} matched {len(hits)} items in project #{number}"
                      + (", expected exactly one; use the item id for one of:" + lines if hits else ""))
    return hits[0]


LIST_KINDS = {"todo": "Todo", "inprogress": "In Progress", "in-progress": "In Progress", "done": "Done"}  # --list KIND -> board status (and the TODO.md checkbox it corresponds to)


LINKED_LISTS = {"issues": "issues", "prs": "prs"}  # --list issues|prs: the same kinds --export writes to ISSUES.md / PRS.md


def _list_linked(args):
    """--list issues|prs: the project's issue (or pull request) items, any status; with --local, the entries in
    ISSUES.md (or PRS.md) instead."""
    filename, ctype, *_ = LINKED_EXPORTS[args.list]
    if args.local:
        rows = [{"status": section, "title": title, **({"url": url} if url else {})}
                for title, url, section, _labels in parse_issues_file(_read(filename))]
        if args.json:
            emit(args, rows)
            return
        for r in rows:
            print(f"{r['title']}\t{r['url']}" if r.get("url") else r["title"])
        return
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    rows = [{"id": i["id"], "status": i.get("status"), "title": i["title"], "url": _item_url(i)}
            for i in board_items(args, project["number"]) if (i.get("content") or {}).get("type") == ctype]
    if args.json:
        emit(args, rows)
        return
    for r in rows:
        print(f"{r['id']}\t{r['title']}\t{r['url']}")


def cmd_list_kind(args):
    """--list KIND: the project's items with that status; with --local, the matching entries in TODO.md.

    `todo` is the Todo status (`[ ]` in TODO.md, not `[~]` in progress or `[x]` done); `inprogress` (or `in-progress`) is In Progress
    (`[~]`); `done` is the Done status (`[x]` in TODO.md). The board's Done items include the changelog entries; TODO.md's do not.
    """
    if args.list in LINKED_LISTS:
        return _list_linked(args)
    status = LIST_KINDS[args.list]
    if args.local:
        rows = [{"status": st, "title": text} for st, text in parse_todo(_read("TODO.md")) if st == status]
        if args.json:
            emit(args, rows)
            return
        for r in rows:
            print(r["title"])
        return
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    items = board_items(args, project["number"])
    rows = [{"id": i["id"], "status": i.get("status"), "title": i["title"],
             **({"url": u} if (u := _item_url(i)) else {})} for i in items if i.get("status") == status]
    if args.json:
        emit(args, rows)
        return
    for r in rows:
        print(f"{r['id']}\t{r['title']}")


TODO_TEMPLATE = "# TODO\n\nStatus: `[ ]` open, `[~]` in progress, `[x]` done.\n\n## Open\n\n## Done\n"

# --export KIND -> (board status, checkbox mark, TODO.md section, put at the top of the section?)
EXPORT_KINDS = {"todo": ("Todo", " ", "Open", False), "inprogress": ("In Progress", "~", "Open", False),
                "in-progress": ("In Progress", "~", "Open", False), "done": ("Done", "x", "Done", True)}


def _insert_entries(text, section, lines, top=False):
    """Add `lines` to the `## section` of TODO.md `text`: at the end of the section, or at the top of its
    entries when `top` (newest first, as the Done list is kept). The section is created if absent."""
    src = text.splitlines()
    head = next((n for n, l in enumerate(src) if re.match(rf"##\s+{section}\b", l, re.I)), None)
    if head is None:
        base = text.rstrip("\n")
        return (base + "\n\n" if base else "# TODO\n\n") + f"## {section}\n\n" + "\n".join(lines) + "\n"
    end = next((n for n in range(head + 1, len(src)) if src[n].startswith("## ")), len(src))
    filled = [n for n in range(head + 1, end) if src[n].strip()]
    if top and filled:
        src[filled[0]:filled[0]] = lines
    elif filled:
        src[filled[-1] + 1:filled[-1] + 1] = lines + ([""] if filled[-1] + 1 == end and end < len(src) else [])
    else:  # empty section: a blank line after the heading, then the entries, then a blank before any next heading
        pos = head + 1
        gap = [] if pos < len(src) and not src[pos].strip() else [""]
        pos += 1 - len(gap)
        src[pos:pos] = gap + lines + ([""] if end < len(src) else [])
    return "\n".join(src) + "\n"


ISSUES_FILE = "ISSUES.md"
ISSUES_MARKER = "Generated by kopi --export issues"
ISSUE_SECTIONS = ("Todo", "In Progress", "Done")
# --export issues|prs: kind -> (file, board content type, heading, plural noun)
LINKED_EXPORTS = {"issues": (ISSUES_FILE, "Issue", "Issues", "issues"),
                  "prs": ("PRS.md", "PullRequest", "Pull requests", "pull requests")}

ISSUE_LINE = re.compile(r"^\s*[-*]\s+(?:\[[ x~]\]\s+)?(?:(\[[^\]]*\])\(([^)]*)\)\s+)?(\S.*?)\s*$")


def render_issues(rows, project, owner, kind="issues"):
    """ISSUES.md (or PRS.md) text for `rows` ([{status, number, repository, title, url, labels}]), grouped by board status."""
    _, _, heading, noun = LINKED_EXPORTS[kind]
    multi = len({r["repository"] for r in rows}) > 1
    out = [f"<!-- Generated by kopi --export {kind} from project #{project} ({owner}). Edits are overwritten by the next "
           "export; delete this line to stop kopi overwriting the file. -->", "", f"# {heading}", ""]
    if not rows:
        return "\n".join(out + [f"_No {noun} in the project._", ""])
    for status in (*ISSUE_SECTIONS, None):
        group = [r for r in rows if (r["status"] if r["status"] in ISSUE_SECTIONS else None) == status]
        if not group:
            continue
        out += [f"## {status or 'No status'}", ""]
        for r in group:
            ref = f"{r['repository']}#{r['number']}" if multi else f"#{r['number']}"
            labels = f" ({', '.join(r['labels'])})" if r["labels"] else ""
            out.append(f"- [{ref}]({r['url']}) {r['title']}{labels}")
        out.append("")
    return "\n".join(out)


def _export_issues(args):
    """--export issues|prs: write the project's issue (or pull request) items, not the other or drafts, to
    ISSUES.md (or PRS.md), replacing it.

    A snapshot grouped by board status. An existing file that kopi did not generate (no marker line) is
    left alone unless --force is given.
    """
    kind = args.export
    filename, ctype, _, noun = LINKED_EXPORTS[kind]
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    items = board_items(args, project["number"])
    rows = [{"status": i.get("status"), "number": c.get("number"), "repository": c.get("repository", ""),
             "title": i["title"], "url": _item_url(i),
             "labels": [l if isinstance(l, str) else l.get("name", "") for l in (i.get("labels") or [])]}
            for i in items if (c := i.get("content") or {}).get("type") == ctype]
    text = render_issues(rows, project["number"], args.owner, kind)
    old = _read(filename)
    if old and f"Generated by kopi --export {kind}" not in old.splitlines()[0] and not args.force:
        raise GhError(f"{filename} exists and was not generated by kopi; use --force to overwrite it")
    changed = text != old
    if changed and not args.dry_run:
        with open(filename, "w") as f:
            f.write(text)
    if args.json:
        emit(args, {"project": project["number"], "dry_run": bool(args.dry_run), "file": filename,
                    "changed": changed, kind: rows})
        return
    if args.dry_run:
        for r in rows:
            print(f"export\t{r['status'] or 'none'}\t#{r['number']}\t{r['title']}")
    verb = "would be written" if args.dry_run else "written"
    print(("Dry run: " if args.dry_run else "") + (f"{len(rows)} {noun} {verb} to {filename}" if changed
                                                  else f"{filename} is up to date ({len(rows)} {noun})"))


LABELS_SUFFIX = re.compile(r"\s+\(labels?:\s*([^()]*)\)$", re.I)


def parse_issues_file(text):
    """[(title, url, section, labels)] for each bullet in ISSUES.md text.

    Accepts kopi's own export lines (`- [#3](url) Title (bug, docs)`, whose trailing parenthesis is the label
    list) and hand-written ones (`- Title`, `- [ ] Title`, `- Title (labels: bug, docs)`). `section` is the
    nearest `## ` heading, or None.
    """
    entries, section, seen = [], None, set()
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        m = ISSUE_LINE.match(line)
        if not m:
            continue
        url, title = m.group(2), m.group(3)
        labels = []
        found = re.search(r"\s+\(([^()]*)\)$", title) if url else LABELS_SUFFIX.search(title)
        if found:
            labels = [l.strip() for l in found.group(1).split(",") if l.strip()]
            title = title[:found.start()]
        if title.casefold() in seen:
            continue
        seen.add(title.casefold())
        entries.append((title, url, section, labels))
    return entries


def cmd_import_issues(args):
    """--import issues: create a GitHub issue for each ISSUES.md entry the repo does not already have.

    An entry exists if its link points at an existing issue (or at another repo), or its title matches an issue's
    title (case-insensitive). Entries under a `## Done` heading are closed after creation. Labels the repo
    already has are applied; unknown ones are reported on stderr, not created.
    """
    repo = git_repo()
    if not repo:
        raise GhError("no github.com origin remote; --import issues needs a repo to create issues in")
    slug = "/".join(repo)
    text = _read(ISSUES_FILE)
    if not text:
        raise GhError(f"no {ISSUES_FILE} in the current directory")
    entries = parse_issues_file(text)
    existing = json.loads(run("issue", "list", "--repo", slug, "--state", "all", "--limit", str(LINKED_LIMIT),
                              "--json", "number,title,url") or "[]")
    urls = {i["url"] for i in existing}
    titles = {i["title"].casefold() for i in existing}
    todo, present = [], []
    for title, url, section, labels in entries:
        other_repo = url and not url.startswith(f"https://github.com/{slug}/")
        if url in urls or other_repo or title.casefold() in titles:
            present.append(title)
        else:
            todo.append((title, section, labels))
    repo_labels = {}
    if any(labels for *_, labels in todo):
        rows = json.loads(run("label", "list", "--repo", slug, "--json", "name", "--limit", str(LINKED_LIMIT)) or "[]")
        repo_labels = {r["name"].lower(): r["name"] for r in rows}
    created, unknown = [], set()
    for title, section, wanted in todo:
        done = section == "Done"
        labels = [repo_labels[l.lower()] for l in wanted if l.lower() in repo_labels]
        unknown.update(l for l in wanted if l.lower() not in repo_labels)
        entry = {"title": title, "closed": done, "labels": labels}
        if not args.dry_run:
            cmd = ["issue", "create", "--repo", slug, "--title", _title(title), "--body", title if len(title) > 100 else ""]
            for label in labels:
                cmd += ["--label", label]
            entry["url"] = run(*cmd).strip()
            if done:
                run("issue", "close", entry["url"])
        created.append(entry)
    if unknown:
        print(f"warning: labels not in {slug}, so not applied: {', '.join(sorted(unknown))}", file=sys.stderr)
    if args.json:
        emit(args, {"repo": slug, "dry_run": bool(args.dry_run), "created": created, "existing": present,
                    "unknown_labels": sorted(unknown)})
        return
    for c in created:
        print(f"{'would create' if args.dry_run else 'created'}\t{'closed' if c['closed'] else 'open'}\t{c['title']}"
              + (f"\t[{', '.join(c['labels'])}]" if c["labels"] else "") + (f"\t{c['url']}" if c.get("url") else ""))
    print(("Dry run: " if args.dry_run else "") + f"{len(created)} issues {'would be ' if args.dry_run else ''}"
          f"created in {slug}; {len(present)} already exist")


def cmd_export(args):
    """--export KIND: write the project's items with that status into the local TODO.md.

    todo -> `- [ ]` and inprogress -> `- [~]` at the end of `## Open`; done -> `- [x]` at the top of `## Done`.
    An item is skipped when TODO.md already has a matching entry in any state (same text, or 75%+ shared
    words, as --update matches), so a ticked entry is not re-opened. For `done`, the board's changelog items
    (`[version] ...`) and anything resembling a CHANGELOG.md entry are skipped too: those belong in the
    changelog. Linked issues/PRs are written as `title (url)`. TODO.md is created if missing; the board is
    not changed.
    """
    if args.export in LINKED_EXPORTS:
        return _export_issues(args)
    status, mark, section, top = EXPORT_KINDS[args.export]
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    items = board_items(args, project["number"])
    created = not os.path.exists("TODO.md")  # a missing TODO.md is created from the template, even with nothing to add
    text = _read("TODO.md")
    if not text.strip():
        text = TODO_TEMPLATE
    known = [t for _, t in parse_todo(text)]
    changelog = [e for _, e in parse_changelog(_read("CHANGELOG.md"))] if status == "Done" else []
    new, skipped = [], 0  # [(entry text, url or None)]
    for item in items:
        if item.get("status") != status:
            continue
        body = ((item.get("content") or {}).get("body") or "").strip()
        entry = body if item["title"].endswith("…") and body and "\n" not in body else item["title"]
        if (status == "Done" and CHANGELOG_TITLE.match(item["title"])) or any(similar(entry, e) for e in changelog) \
                or any(entry == k or similar(entry, k, 0.75) for k in known + [e for e, _ in new]):
            skipped += 1
        else:
            new.append((entry, _item_url(item)))
    added = [e for e, _ in new]
    lines = [f"- [{mark}] {e}" + (f" ({u})" if u else "") for e, u in new]
    if top:
        lines.reverse()  # board order is oldest first; the Done list is kept newest first
    if (added or created) and not args.dry_run:
        with open("TODO.md", "w") as f:
            f.write(_insert_entries(text, section, lines, top) if lines else text)
    if args.json:
        emit(args, {"project": project["number"], "dry_run": bool(args.dry_run), "file": "TODO.md",
                    "created": created, "added": added, "already_present": skipped})
        return
    if created:
        print("Would create TODO.md" if args.dry_run else "Created TODO.md")
    for line in lines:
        print(f"{'export' if args.dry_run else 'added'}\t{line}")
    print(("Dry run: " if args.dry_run else "") + f"{len(added)} {args.export} items {'to export' if args.dry_run else 'added'} "
          f"to TODO.md ({skipped} already present)")


STATUS_ALIASES = {"todo": "Todo", "inprogress": "In Progress", "in-progress": "In Progress",
                  "in progress": "In Progress", "done": "Done"}


def _move_item(args, text, target, flag):
    """Move one board item to the Status `target`. `text` is an item id (PVTI_...) or text matching exactly one title.

    Any direction: an explicit move is allowed to go backwards (reopening a Done item). An item already in
    `target` is left alone. Board only: the files and any linked issue/PR are not changed.
    """
    target = canonical_status(STATUS_ALIASES.get(target.strip().lower(), target.strip()), args)
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    number = project["number"]
    items = board_items(args, number)
    item = next((i for i in items if i["id"] == text), None) or _match_one(items, text, number, flag)
    old = item.get("status") or "none"
    report = {"project": number, "dry_run": bool(args.dry_run), "moved": []}
    if old.lower() == target.lower():
        say(args, f"Already {target}: {item['title']}")
    elif args.dry_run:
        report["moved"].append({"title": item["title"], "from": old, "to": target})
        say(args, f"move\t{old} -> {target}\t{item['title']}")
    else:
        _status_setter(args, number, [target])(item["id"], target)
        report["moved"].append({"title": item["title"], "from": old, "to": target})
        say(args, f"Moved to {target}: {item['title']} (was {old})")
    emit(args, report)


def cmd_done(args):
    """--done ITEM: move one board item to Done (see _move_item)."""
    _move_item(args, args.done, "Done", "--done")


def cmd_start(args):
    """--start ITEM: move one board item to In Progress (see _move_item)."""
    _move_item(args, args.start, "In Progress", "--start")


def cmd_move(args):
    """--move ITEM STATUS: move one board item to any Status option, e.g. Todo, In Progress, Done (see _move_item)."""
    _move_item(args, args.move[0], args.move[1], "--move")


def cmd_sync(args):
    """--sync: bring the board and the files level in both directions: --update (files and GitHub -> board), then
    --squash (board -> TODO.md ticks). The --export modes stay separate because they write new files/entries.

    With --json the two results are one document, {project, dry_run, update, squash}. Under --dry-run the squash
    step is planned from the board as it is now, before --update's changes would land.
    """
    args.sink = []
    try:
        cmd_update(args)
        cmd_squash(args)
        steps = args.sink
    finally:
        args.sink = None
    if not args.json:
        return
    update, squash = steps
    emit(args, {"project": update["project"], "dry_run": bool(args.dry_run), "update": update, "squash": squash})


def _archive_selected(args):
    """Archive the individual items named by --id (repeatable) and/or --match, whatever their status or kind."""
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    number = project["number"]
    items = board_items(args, number)
    chosen = []
    for item_id in args.id or []:
        item = next((i for i in items if i["id"] == item_id), None)
        if not item:
            raise GhError(f"no item with id {item_id} in project #{number} (see `kopi items {number}`)")
        chosen.append(item)
    if args.match:
        chosen.append(_match_one(items, args.match, number, "--match"))
    chosen = list({i["id"]: i for i in chosen}.values())
    report = {"project": number, "dry_run": bool(args.dry_run),
              "archived": [_item(i.get("status") or "none", i["title"]) for i in chosen], "skipped": []}
    for item in chosen:
        if args.dry_run:
            say(args, f"archive\t{item.get('status') or 'none'}\t{item['title']}")
        else:
            run("project", "item-archive", str(number), *_owner(args), "--id", item["id"])
            _log_archived(args, number, item)
    say(args, f"Dry run: {len(chosen)} to archive in project #{number}" if args.dry_run
        else f"Archived {len(chosen)} items in project #{number}")
    emit(args, report)


def cmd_archive(args):
    """Archive open board drafts that no longer match any CHANGELOG.md / TODO.md entry.

    With --id / --match, archive just those individual items instead (any status or kind).

    In bulk mode, Done items and linked issues/PRs are never archived. An orphan that resembles a pending new item
    (50%+ shared words) is probably a reworded one, so it is skipped unless --force is given.
    """
    if args.id or args.match:
        return _archive_selected(args)
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
        _log_archived(args, p.number, item)
    msg = f"Archived {len(to_archive)} items in project #{p.number}"
    say(args, msg + (f" ({len(reworded)} skipped as possibly reworded; use --force)" if reworded else ""))
    emit(args, report)


def cmd_squash(args):
    """Tick TODO.md entries that are Done on the board but still `[ ]` or `[~]` in the file.

    The reverse of --update: it edits the local TODO.md only (the board is untouched) and does not move the
    line to the Done section. Matching is the same as --update's; only TODO.md is read, issues/PRs and
    the changelog are not consulted.
    """
    args.only = "TODO.md"
    p = sync_plan(args)
    text = _read("TODO.md")
    ticked = []
    for item, entry, status in p.ticks:
        pattern = re.compile(r"^(- \[)[ ~](\] " + re.escape(entry) + r")[ \t]*$", re.M)
        text, n = pattern.subn(r"\g<1>x\g<2>", text, count=1)
        if n:
            ticked.append({"title": entry, "from": status})
    if ticked and not args.dry_run:
        with open("TODO.md", "w") as f:
            f.write(text)
    if args.json:
        emit(args, {"project": p.number, "dry_run": bool(args.dry_run), "ticked": ticked})
        return
    for t in ticked:
        print(f"{'tick' if args.dry_run else 'ticked'}\t{t['from']}\t{t['title']}")
    verb = "to tick" if args.dry_run else "ticked"
    print(("Dry run: " if args.dry_run else "") + f"{len(ticked)} TODO.md items {verb} (Done in project #{p.number})")


ARCHIVED_QUERY = """query($id: ID!, $after: String) { node(id: $id) { ... on ProjectV2 {
  items(first: 100, after: $after) { pageInfo { hasNextPage endCursor } nodes { id isArchived
    fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
    content { __typename ... on DraftIssue { title } ... on Issue { title } ... on PullRequest { title } } } } } } }"""


def list_archived(args, number):
    """[{id, title, status}] for the project's archived items, however they were archived.

    `gh project item-list` hides archived items, but the GraphQL items connection returns them with
    isArchived set (its `query:` filter does not narrow by archive state), so page through and filter here.
    """
    project_id = run_json("project", "view", str(number), *_owner(args))["id"]
    found, after = [], None
    while True:
        variables = {"id": project_id, **({"after": after} if after else {})}
        page = graphql(ARCHIVED_QUERY, **variables)["data"]["node"]["items"]
        for n in page["nodes"]:
            if n["isArchived"]:
                found.append({"id": n["id"], "title": (n.get("content") or {}).get("title", ""),
                              "status": canonical_status((n.get("fieldValueByName") or {}).get("name") or "none", args)})
        if not page["pageInfo"]["hasNextPage"]:
            return found
        after = page["pageInfo"]["endCursor"]


def cmd_restore(args):
    """Unarchive items. By default every item `kopi --archive` archived in this project (from its local log);
    --id restores specific item ids instead, including ones archived by hand; --all restores every archived
    item GitHub reports for the project, however it was archived (found with list_archived).
    """
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    number = project["number"]
    log = load_archive_log()
    mine = [e for e in log if e.get("owner") == args.owner and e.get("project") == number]
    if args.all:
        if args.id:
            raise GhError("--all and --id cannot be combined")
        targets = list_archived(args, number)
    elif args.id:
        known = {e["id"]: e for e in mine}
        targets = [known.get(i, {"id": i, "title": "(not in kopi's log)", "status": "none"}) for i in args.id]
    else:
        targets = mine
    report = {"project": number, "dry_run": bool(args.dry_run), "restored": [], "failed": []}
    for e in targets:
        entry = {"id": e["id"], "title": e.get("title", ""), "status": e.get("status", "none")}
        if args.dry_run:
            report["restored"].append(entry)
            say(args, f"restore\t{entry['status']}\t{entry['title']}\t{entry['id']}")
            continue
        try:
            run("project", "item-archive", str(number), *_owner(args), "--id", e["id"], "--undo")
        except GhError as err:
            report["failed"].append({**entry, "error": str(err)})
            print(f"warning: could not restore {e['id']} ({entry['title']}): {err}", file=sys.stderr)
            continue
        report["restored"].append(entry)
        log = [x for x in log if x.get("id") != e["id"]]
        save_archive_log(log)
    n = len(report["restored"])
    if args.dry_run:
        say(args, f"Dry run: {n} to restore in project #{number}")
    elif not targets:
        say(args, f"Nothing to restore in project #{number}" + (" (no archived items)" if args.all
                                                              else " (kopi has no record of archived items; use --all or --id)"))
    else:
        say(args, f"Restored {n} items in project #{number}" + (f" ({len(report['failed'])} failed)" if report["failed"] else ""))
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


def cmd_dedupe(args):
    """Delete duplicate board items: same URL (linked) or same title (drafts).

    Keeps the copy furthest along (Done over In Progress over Todo), the earliest listed on a tie. Works
    on the board only; the files and GitHub issues/PRs are not consulted.
    """
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    number = project["number"]
    items = board_items(args, number)
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


FIELDS_QUERY = """query($id: ID!) { node(id: $id) { ... on ProjectV2 { fields(first: 100) { nodes {
  ... on ProjectV2FieldCommon { id name dataType }
  ... on ProjectV2SingleSelectField { options { id name } }
  ... on ProjectV2IterationField { configuration { iterations { id title } } } } } } } }"""
SETTABLE = ("TEXT", "NUMBER", "DATE", "SINGLE_SELECT", "ITERATION")


def _field_value_literal(field, value):
    """GraphQL ProjectV2FieldValue literal for `value` typed by the field's dataType; GhError if it doesn't fit."""
    kind, name = field["dataType"], field["name"]
    if kind == "TEXT":
        return "{text: %s}" % json.dumps(value)
    if kind == "NUMBER":
        try:
            return "{number: %s}" % float(value)
        except ValueError:
            raise GhError(f"{name} is a number field; {value!r} is not a number")
    if kind == "DATE":
        try:
            return '{date: "%s"}' % datetime.date.fromisoformat(value).isoformat()
        except ValueError:
            raise GhError(f"{name} is a date field; use YYYY-MM-DD, not {value!r}")
    options = ([(o["id"], o["name"]) for o in field.get("options", [])] if kind == "SINGLE_SELECT"
               else [(i["id"], i["title"]) for i in field["configuration"]["iterations"]])
    chosen = next((i for i, n in options if n.lower() == value.lower()), None)
    if not chosen:
        raise GhError(f"{name} has no option {value!r}; choose from: " + ", ".join(n for _, n in options))
    return '{%s: "%s"}' % ("singleSelectOptionId" if kind == "SINGLE_SELECT" else "iterationId", chosen)


def cmd_set(args):
    """Set (or --clear) one field's value on one item through GraphQL. ITEM is an item id or matching title text.

    Supports text, number, date, single-select and iteration fields; built-in fields such as Title or Labels
    are not settable here.
    """
    project_id = run_json("project", "view", str(args.number), *_owner(args))["id"]
    items = board_items(args, args.number)
    item = next((i for i in items if i["id"] == args.item), None) or _match_one(items, args.item, args.number, "item")
    nodes = (graphql(FIELDS_QUERY, id=project_id).get("data", {}).get("node") or {}).get("fields", {}).get("nodes", [])
    fields = [f for f in nodes if f.get("name")]
    field = next((f for f in fields if f["name"].lower() == args.field.lower()), None)
    if not field:
        raise GhError(f"no field {args.field!r} in project #{args.number}; settable fields: "
                      + ", ".join(f["name"] for f in fields if f.get("dataType") in SETTABLE))
    if field["dataType"] not in SETTABLE:
        raise GhError(f"field {field['name']} ({field['dataType']}) can't be set here; "
                      "settable types: " + ", ".join(SETTABLE))
    if args.clear == (args.value is not None):
        raise GhError("give exactly one of --value or --clear")
    ids = f'projectId: "{project_id}", itemId: "{item["id"]}", fieldId: "{field["id"]}"'
    if args.dry_run:
        say(args, f"{'clear' if args.clear else 'set'}\t{field['name']}\t{args.value if not args.clear else ''}\t{item['title']}")
    else:
        if args.clear:
            query = f"mutation {{ clearProjectV2ItemFieldValue(input: {{{ids}}}) {{ projectV2Item {{ id }} }} }}"
        else:
            literal = _field_value_literal(field, args.value)
            query = (f"mutation {{ updateProjectV2ItemFieldValue(input: {{{ids}, value: {literal}}}) "
                     "{ projectV2Item { id } } }")
        result = graphql(query)
        if result.get("errors"):
            raise GhError("; ".join(e.get("message", "?") for e in result["errors"]))
        say(args, f"Cleared {field['name']} on {item['title']}" if args.clear
            else f"Set {field['name']} = {args.value} on {item['title']}")
    emit(args, {"project": args.number, "dry_run": bool(args.dry_run), "item": {"id": item["id"], "title": item["title"]},
                "field": field["name"], "value": None if args.clear else args.value})


def cmd_add(args):
    """Add an existing issue/PR by URL, or create a draft item with --title."""
    if args.url:
        run("project", "item-add", str(args.number), *_owner(args), "--url", args.url)
    else:
        run("project", "item-create", str(args.number), *_owner(args), "--title", args.title)
    say(args, "Added")
    emit(args, {"added": args.url or args.title})


def build_parser():
    p = argparse.ArgumentParser(prog="kopi", description="KOPI: Keep Our Projects In-sync. Manage GitHub Projects (v2) and keep them in step with "
                                "CHANGELOG.md, TODO.md, issues and PRs")
    p.add_argument("--version", action="version", version=f"kopi {__version__}")
    p.add_argument("--owner", help="user or org (default: owner of the current git repo's origin, else @me)")
    p.add_argument("--init", nargs="?", const=".", metavar="PATH",
                   help="create a project named after the repo (linked to it) if none exists; "
                        "uses the repo in PATH, default the current directory")
    p.add_argument("--import", dest="do_import", nargs="?", const=".", metavar="PATH",
                   help="seed an empty project from CHANGELOG.md, TODO.md and the repo's issues/PRs (creates the project if missing); "
                        "`--import issues` instead creates a GitHub issue for each ISSUES.md entry the repo lacks; "
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
                   help="tick TODO.md entries that are Done in the project but still open in the file; "
                        "uses the repo in PATH, default the current directory")
    p.add_argument("--dedupe", nargs="?", const=".", metavar="PATH",
                   help="delete duplicate board items (same title or URL), keeping the one furthest along; "
                        "uses the repo in PATH, default the current directory")
    p.add_argument("--status-map", metavar="MAP", type=_status_map_arg,
                   help="name the board's Status options when they differ from Todo / In Progress / Done, e.g. "
                        "\"Todo=Backlog,In Progress=Doing,Done=Shipped\" (common names such as Backlog, Doing and "
                        "Shipped are recognised without it)")
    p.add_argument("--json", action="store_true",
                   help="print the result as JSON on stdout (status messages are suppressed; warnings stay on stderr)")
    p.add_argument("--list", choices=sorted({*LIST_KINDS, *LINKED_LISTS}), metavar="KIND",
                   help="list the project's items of one kind (todo, inprogress, done, issues, prs); with --local, list the matching "
                        "entries in TODO.md (ISSUES.md / PRS.md for issues / prs) instead")
    p.add_argument("--export", choices=sorted({*EXPORT_KINDS, *LINKED_EXPORTS}), metavar="KIND",
                   help="write the project's items of one kind into local files: todo, inprogress or done into TODO.md "
                        "as [ ] / [~] / [x] entries (skipping any already there); issues into ISSUES.md, prs into PRS.md")
    p.add_argument("--local", action="store_true", help="with --list: read the local TODO.md instead of the project")
    p.add_argument("--done", metavar="ITEM",
                   help="move one board item to Done: ITEM is an item id or text matching exactly one title "
                        "(run from the repo directory)")
    p.add_argument("--sync", nargs="?", const=".", metavar="PATH",
                   help="--update then --squash: sync files and GitHub into the board, then tick TODO.md entries "
                        "that are Done on it; uses the repo in PATH, default the current directory")
    p.add_argument("--start", metavar="ITEM",
                   help="move one board item to In Progress: ITEM is an item id or text matching exactly one title "
                        "(run from the repo directory)")
    p.add_argument("--move", nargs=2, metavar=("ITEM", "STATUS"),
                   help="move one board item to STATUS (Todo, In Progress, Done, or another Status option; "
                        "case-insensitive): ITEM is an item id or text matching exactly one title")
    p.add_argument("--check", action="store_true",
                   help="with --diff: exit with status 2 if the board and files differ (for CI / pre-commit)")
    p.add_argument("--restore", nargs="?", const=".", metavar="PATH",
                   help="unarchive the items kopi --archive archived in this project (or those given with --id); "
                        "uses the repo in PATH, default the current directory")
    p.add_argument("--id", action="append", metavar="ITEM_ID",
                   help="with --archive: archive just this item id (repeatable); "
                        "with --restore: restore this item id (repeatable) instead of the logged ones")
    p.add_argument("--all", action="store_true",
                   help="with --restore: restore every archived item GitHub reports, not just those kopi archived")
    p.add_argument("--match", metavar="TEXT",
                   help="with --archive: archive the one item whose title contains TEXT (case-insensitive; error if not exactly one)")
    p.add_argument("--dry-run", action="store_true", help="with --import/--update/--sync/--archive/--dedupe/--squash/--restore/--done/--start/--move: print what would change")
    p.add_argument("--force", action="store_true",
                   help="with --import: import even if the project has items; with --archive: also archive items that look reworded; "
                        "with --export issues/prs: overwrite an ISSUES.md/PRS.md kopi did not generate")
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

    s = sub.add_parser("set", help="set or clear a field value on one item (text, number, date, single-select, iteration)")
    s.add_argument("number", type=int)
    s.add_argument("item", help="item id, or text matching exactly one item title")
    s.add_argument("--field", required=True, help="field name (e.g. Status)")
    s.add_argument("--value", help="new value; a select/iteration option name, a number, YYYY-MM-DD, or text")
    s.add_argument("--clear", action="store_true", help="clear the field instead of setting it")
    s.set_defaults(fn=cmd_set)

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
    # `--import issues` is a mode, not a path (unless a directory named "issues" exists)
    args.import_issues = args.do_import == "issues" and not os.path.isdir("issues")
    if args.import_issues:
        args.do_import = "."
    modes = [args.init, args.do_import, args.update, args.diff, args.archive, args.dedupe, args.squash, args.restore, args.done, args.list, args.export,
             args.sync, args.start, args.move]
    if not (any(modes) or args.cmd):
        parser.error("a command is required (or use --init / --import / --update / --diff / --archive / --dedupe / --squash / --restore / --done / --list / --export / --sync / --start / --move)")
    if sum(map(bool, modes)) > 1:
        parser.error("--init, --import, --update, --diff, --archive, --dedupe, --squash, --restore, --done, --list, --export, --sync, --start and --move are mutually exclusive")
    if args.check and not args.diff:
        parser.error("--check only applies to --diff")
    if args.all and not args.restore:
        parser.error("--all only applies to --restore")
    if args.local and not args.list:
        parser.error("--local only applies to --list")
    try:
        # only these modes take a directory/file; --done's, --list's and --export's values are not paths
        path = next((m for m in [*modes[:8], args.sync] if m), None)
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
        if args.local:  # --list KIND --local reads TODO.md only: no GitHub needed
            cmd_list_kind(args)
            return 0
        check_auth()
        if not args.owner:
            args.owner = git_owner()
            if args.owner:
                print(f"Using owner: {args.owner} (from git origin)", file=sys.stderr)
            else:
                args.owner = "@me"
        handler = next((fn for on, fn in ((args.do_import, cmd_import_issues if args.import_issues else cmd_import), (args.update, cmd_update),
                                          (args.diff, cmd_diff), (args.archive, cmd_archive),
                                          (args.dedupe, cmd_dedupe), (args.squash, cmd_squash), (args.restore, cmd_restore), (args.done, cmd_done), (args.list, cmd_list_kind), (args.export, cmd_export),
                                          (args.init, cmd_init), (args.sync, cmd_sync), (args.start, cmd_start),
                                          (args.move, cmd_move)) if on), getattr(args, "fn", None))
        rc = handler(args)
        return rc if isinstance(rc, int) else 0
    except GhError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
