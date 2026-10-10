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

__version__ = "0.1.8"


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


def ensure_project(args):
    """Find the project named after the current repo, creating and linking it if missing.

    Returns (number, created).
    """
    repo = git_repo()
    if not repo:
        raise GhError("--init/--import must be run inside a git repo with a github.com origin remote")
    repo_owner, name = repo
    data = run_json("project", "list", *_owner(args), "--limit", "100")
    for p in data["projects"]:
        if p["title"] == name:
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


def similar(a, b, threshold=0.4):
    """True when most of the shorter text's words appear in the other."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= threshold


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


def build_plan(changelog, todo):
    """Return ([(status, title, body)], [skipped TODO done texts]).

    Changelog entries become Done items (oldest first). TODO items follow, except
    done ones that duplicate a changelog entry.
    """
    entries = parse_changelog(changelog)
    plan, skipped = [], []
    for version, text in reversed(entries):
        title = _title(f"[{version}] {text}")
        plan.append(("Done", title, text if title != f"[{version}] {text}" else ""))
    for status, text in parse_todo(todo):
        if status == "Done" and any(similar(text, e) for _, e in entries):
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


def cmd_import(args):
    """Seed an empty project from CHANGELOG.md (Done) and TODO.md (Todo / In Progress / Done)."""
    changelog, todo = _read("CHANGELOG.md"), _read("TODO.md")
    if not changelog and not todo:
        raise GhError("neither CHANGELOG.md nor TODO.md found in the current directory")
    plan, skipped = build_plan(changelog, todo)
    if args.dry_run:
        for status, title, _ in plan:
            print(f"{status}\t{title}")
        for text in skipped:
            print(f"skipped (duplicate of changelog)\t{_title(text)}")
        print(f"Dry run: {len(plan)} items would be imported")
        return
    number, _ = ensure_project(args)
    if run_json("project", "item-list", str(number), *_owner(args), "--limit", "1")["items"] and not args.force:
        print(f"Project #{number} already has items; nothing imported (use --force to import anyway)")
        return
    project_id = run_json("project", "view", str(number), *_owner(args))["id"]
    fields = run_json("project", "field-list", str(number), *_owner(args))["fields"]
    status_field = next((f for f in fields if f["name"] == "Status"), None)
    if not status_field:
        raise GhError("project has no Status field")
    options = {o["name"].lower(): o["id"] for o in status_field.get("options", [])}
    missing = {st for st, _, _ in plan if st.lower() not in options}
    if missing:
        raise GhError(f"Status field has no option(s): {', '.join(sorted(missing))}")
    for status, title, body in plan:
        item = run_json("project", "item-create", str(number), *_owner(args), "--title", title, "--body", body)
        run("project", "item-edit", "--id", item["id"], "--project-id", project_id,
            "--field-id", status_field["id"], "--single-select-option-id", options[status.lower()])
    print(f"Imported {len(plan)} items into project #{number}")


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
                   help="seed an empty project from CHANGELOG.md and TODO.md (creates the project if missing)")
    p.add_argument("--dry-run", action="store_true", help="with --import: print what would be imported")
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
    if not (args.init or args.do_import or args.cmd):
        parser.error("a command is required (or use --init / --import)")
    try:
        check_auth()
        if not args.owner:
            args.owner = git_owner()
            if args.owner:
                print(f"Using owner: {args.owner} (from git origin)", file=sys.stderr)
            else:
                args.owner = "@me"
        (cmd_import if args.do_import else cmd_init if args.init else args.fn)(args)
    except GhError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
