#!/usr/bin/env python3
"""kopi: create and manage GitHub Projects (v2) from the command line.

Shells out to the `gh` CLI, which handles authentication.
Requires the `project` scope: gh auth refresh -s project

Copyright (c) Richard Spindler. Licensed under CC BY-NC-SA 4.0:
https://creativecommons.org/licenses/by-nc-sa/4.0/ (see LICENSE.md)
"""
import argparse
import json
import re
import subprocess
import sys

__version__ = "0.2.1"


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


def cmd_list(args):
    data = run_json("project", "list", *_owner(args), "--limit", str(args.limit))
    for p in data["projects"]:
        print(f"{p['number']}\t{p['title']}\t{p['url']}")


def cmd_create(args):
    p = run_json("project", "create", "--title", args.title, *_owner(args))
    print(f"Created #{p['number']}: {p['url']}")
    if args.repo:
        run("project", "link", str(p["number"]), *_owner(args), "--repo", args.repo)
        print(f"Linked to {args.repo}")
    return p


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
        print(f"Project exists: #{p['number']} {p['title']} {p['url']}")
        return p["number"], False
    args.title = name
    # gh only links a project to repos under the same owner
    args.repo = f"{repo_owner}/{name}" if args.owner == repo_owner else None
    if not args.repo:
        print(f"Not linking: project owner {args.owner} differs from repo owner {repo_owner}", file=sys.stderr)
    return cmd_create(args)["number"], True


def cmd_init(args):
    ensure_project(args)


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


def cmd_import(args):
    """Seed an empty project from CHANGELOG.md and TODO.md (draft items) plus the repo's issues and PRs."""
    changelog, todo = _read("CHANGELOG.md"), _read("TODO.md")
    linked = fetch_linked(git_repo())
    if not changelog and not todo and not linked:
        raise GhError("nothing to import: no CHANGELOG.md or TODO.md in the current directory, and no issues or PRs")
    plan, skipped = build_plan(changelog, todo, linked)
    total = len(plan) + len(linked)
    if args.dry_run:
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
        print(f"Project #{number} already has items; nothing imported (use --force to import anyway)")
        return
    set_status = _status_setter(args, number, [st for st, *_ in plan + linked])

    for status, title, body in plan:
        set_status(run_json("project", "item-create", str(number), *_owner(args),
                            "--title", title, "--body", body)["id"], status)
    for status, _, url in linked:
        set_status(run_json("project", "item-add", str(number), *_owner(args), "--url", url)["id"], status)
    print(f"Imported {total} items into project #{number}")


RANK = {"Todo": 0, "In Progress": 1, "Done": 2}
CHANGELOG_TITLE = re.compile(r"^\[(\d+\.\d+\.\d+|Unreleased)\] ")


def cmd_update(args):
    """Sync CHANGELOG.md and TODO.md into the existing project.

    Adds items the project lacks and moves items forward (Todo -> In Progress -> Done)
    when the files are ahead of the board. Never moves an item backwards.
    """
    changelog, todo = _read("CHANGELOG.md"), _read("TODO.md")
    if not changelog and not todo:
        raise GhError("nothing to update from: no CHANGELOG.md or TODO.md in the current directory")
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
    matched, claimed = {}, set()
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

    adds, moves = [], []
    for idx, (status, title, body, _, can_add) in enumerate(entries):
        item = matched.get(idx)
        if item:
            if RANK[status] > RANK.get(item.get("status"), -1):
                moves.append((item, item.get("status") or "none", status, title))
        elif can_add:
            adds.append((status, title, body))

    if args.dry_run:
        for status, title, _ in adds:
            print(f"add\t{status}\t{title}")
        for _, old, new, title in moves:
            print(f"move\t{old} -> {new}\t{title}")
        print(f"Dry run: {len(adds)} to add, {len(moves)} to move in project #{number}")
        return
    if not adds and not moves:
        print(f"Project #{number} is up to date")
        return
    set_status = _status_setter(args, number, [st for st, *_ in adds] + [new for _, _, new, _ in moves])
    for status, title, body in adds:
        set_status(run_json("project", "item-create", str(number), *_owner(args),
                            "--title", title, "--body", body)["id"], status)
    for item, _, new, _ in moves:
        set_status(item["id"], new)
    print(f"Updated project #{number}: {len(adds)} added, {len(moves)} moved")


def cmd_view(args):
    print(json.dumps(run_json("project", "view", str(args.number), *_owner(args)), indent=2))


def cmd_delete(args):
    run("project", "delete", str(args.number), *_owner(args))
    print(f"Deleted #{args.number}")


def cmd_fields(args):
    data = run_json("project", "field-list", str(args.number), *_owner(args))
    for f in data["fields"]:
        print(f"{f['name']}\t{f['type']}")


def cmd_items(args):
    data = run_json("project", "item-list", str(args.number), *_owner(args), "--limit", str(args.limit))
    for i in data["items"]:
        print(f"{i['id']}\t{i.get('status', '')}\t{i['title']}")


def cmd_add(args):
    """Add an existing issue/PR by URL, or create a draft item with --title."""
    if args.url:
        run("project", "item-add", str(args.number), *_owner(args), "--url", args.url)
    else:
        run("project", "item-create", str(args.number), *_owner(args), "--title", args.title)
    print("Added")


def build_parser():
    p = argparse.ArgumentParser(prog="kopi", description="Manage GitHub Projects (v2)")
    p.add_argument("--version", action="version", version=f"kopi {__version__}")
    p.add_argument("--owner", help="user or org (default: owner of the current git repo's origin, else @me)")
    p.add_argument("--init", action="store_true",
                   help="create a project named after the current repo (linked to it) if none exists")
    p.add_argument("--import", dest="do_import", action="store_true",
                   help="seed an empty project from CHANGELOG.md, TODO.md and the repo's issues/PRs (creates the project if missing)")
    p.add_argument("--update", action="store_true",
                   help="sync CHANGELOG.md and TODO.md into the existing project: add missing items, move items forward")
    p.add_argument("--dry-run", action="store_true", help="with --import/--update: print what would change")
    p.add_argument("--force", action="store_true", help="with --import: import even if the project has items")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("list", help="list projects")
    s.add_argument("--limit", type=int, default=30)
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
    if not (args.init or args.do_import or args.update or args.cmd):
        parser.error("a command is required (or use --init / --import / --update)")
    if args.init + args.do_import + args.update > 1:
        parser.error("--init, --import and --update are mutually exclusive")
    try:
        check_auth()
        if not args.owner:
            args.owner = git_owner()
            if args.owner:
                print(f"Using owner: {args.owner} (from git origin)", file=sys.stderr)
            else:
                args.owner = "@me"
        (cmd_import if args.do_import else cmd_update if args.update else cmd_init if args.init else args.fn)(args)
    except GhError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
