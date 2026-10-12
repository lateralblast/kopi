#!/usr/bin/env python3
"""kopi (Keep Our Projects In-sync): create and manage GitHub Projects (v2) from the command line, and keep them
in step with CHANGELOG.md, TODO.md, issues and PRs.

Shells out to the `gh` CLI, which handles authentication.
Requires the `project` scope: gh auth refresh -s project

Copyright (c) Richard Spindler. Licensed under CC BY-NC-SA 4.0:
https://creativecommons.org/licenses/by-nc-sa/4.0/ (see LICENSE.md)
"""
import argparse
import copy
import datetime
import fnmatch
import json
import os
import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace

__version__ = "1.2.8"


class GhError(RuntimeError):
    pass


QUOTA_QUERY = "{ rateLimit { limit remaining used resetAt } }"


def parse_quota(stdout, now=None):
    """{limit, remaining, used, reset_at, reset_in_minutes} from the output of the rateLimit query; ValueError, KeyError
    or TypeError if it is not that."""
    limit = json.loads(stdout)["data"]["rateLimit"]
    reset = datetime.datetime.fromisoformat(limit["resetAt"].replace("Z", "+00:00"))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return {"limit": limit.get("limit"), "remaining": limit["remaining"], "used": limit.get("used"),
            "reset_at": reset.isoformat().replace("+00:00", "Z"),
            "reset_in_minutes": max(0, round((reset - now).total_seconds() / 60))}


def _reset_text(quota):
    return f"{quota['reset_at'][11:16]} UTC (in {max(1, quota['reset_in_minutes'])} min)"


def api_reset_time(only_if_spent=True):
    """When the GraphQL budget resets, as "HH:MM UTC (in N min)", or None if it cannot be read (or, with only_if_spent,
    if some budget is left). The rateLimit query itself is still answered when nothing is left."""
    try:
        proc = subprocess.run(["gh", "api", "graphql", "-f", f"query={QUOTA_QUERY}"], capture_output=True, text=True)
        quota = parse_quota(proc.stdout)
        return None if only_if_spent and quota["remaining"] > 0 else _reset_text(quota)
    except (OSError, ValueError, KeyError, TypeError):
        return None


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
        # gh reports "unknown owner type" when its owner lookup is refused, which is what a spent budget looks like
        limited = "rate limit" in msg.lower()
        reset = api_reset_time(only_if_spent=not limited) if limited or "unknown owner type" in msg else None
        if "rate limit" in msg.lower() or reset:
            msg += (f"\nHint: GitHub's API limit was reached; it resets at {reset}, so retry after that. Reading a board "
                    "through `gh project item-list` costs about 1 point per item (5000 an hour), so a big board uses it up quickly"
                    if reset else
                    "\nHint: GitHub's API limit was reached. See when it resets with `kopi --checkquota`, then retry after that time")
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


def git_repo(path=None):
    """(owner, repo) of the github.com `origin` remote of the git repo in `path` (default: the current one), or None."""
    try:
        proc = subprocess.run(["git", *(["-C", path] if path else []), "remote", "get-url", "origin"],
                              capture_output=True, text=True)
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


LEAN_READS = True      # read boards with our own slim query; tests switch it off (tests/conftest.py)
LEAN_PAGE = 100
LEAN_FIELD_VALUES = 12  # field values asked for per item; an item with more makes the read fall back to `gh`
LEAN_QUERY = """
query($id: ID!, $cursor: String) { node(id: $id) { ... on ProjectV2 { items(first: %d, after: $cursor) {
  pageInfo { hasNextPage endCursor }
  nodes { id isArchived
    fieldValues(first: %d) { pageInfo { hasNextPage } nodes { __typename
      ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
      ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { name } } }
      ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
      ... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2FieldCommon { name } } }
      ... on ProjectV2ItemFieldIterationValue { title field { ... on ProjectV2FieldCommon { name } } } } }
    content { __typename
      ... on DraftIssue { id title body }
      ... on Issue { title body url number repository { nameWithOwner } labels(first: 10) { nodes { name } } assignees(first: 5) { nodes { login } } milestone { title dueOn } }
      ... on PullRequest { title body url number repository { nameWithOwner } labels(first: 10) { nodes { name } } assignees(first: 5) { nodes { login } } milestone { title dueOn } } } } } } } }
""" % (LEAN_PAGE, LEAN_FIELD_VALUES)


def lean_item(node):
    """One GraphQL item node in the shape `gh project item-list` returns, or None for an archived item."""
    if node.get("isArchived"):
        return None
    content = node.get("content") or {}
    item = {"id": node["id"], "title": content.get("title", ""), "content": {}}
    if content:
        item["content"] = {"type": content["__typename"], "title": content.get("title", ""), "body": content.get("body", "")}
        if content.get("id"):     # a draft's own id, which is what editing its body needs (gh shows it for drafts too)
            item["content"]["id"] = content["id"]
        if content.get("url"):
            item["content"].update(url=content["url"], number=content["number"], repository=content["repository"]["nameWithOwner"])
            item["repository"] = f"https://github.com/{content['repository']['nameWithOwner']}"
            if labels := [n["name"] for n in (content.get("labels") or {}).get("nodes", [])]:
                item["labels"] = labels
            if assignees := [n["login"] for n in (content.get("assignees") or {}).get("nodes", [])]:
                item["assignees"] = assignees
            if content.get("milestone"):
                item["milestone"] = {"title": content["milestone"]["title"], "dueOn": content["milestone"].get("dueOn")}
    for value in node["fieldValues"]["nodes"]:
        name = ((value.get("field") or {}).get("name") or "").lower()
        raw = next((value[k] for k in ("name", "date", "text", "number", "title") if k in value), None)
        if name and raw is not None:
            item[name] = raw
    return item


def lean_items(args, number):
    """The project's non-archived items through a query that asks only for what kopi reads (about a third of the
    points `gh project item-list` costs). Raises GhError, KeyError, TypeError or ValueError when it cannot answer."""
    project_id = run_json("project", "view", str(number), *_owner(args))["id"]
    items, cursor = [], None
    while True:
        data = graphql(LEAN_QUERY, id=project_id, **({"cursor": cursor} if cursor else {}))["data"]["node"]["items"]
        for node in data["nodes"]:
            if node["fieldValues"]["pageInfo"]["hasNextPage"]:
                raise ValueError(f"an item has more than {LEAN_FIELD_VALUES} field values")
            if (item := lean_item(node)) is not None:
                items.append(item)
        if not data["pageInfo"]["hasNextPage"] or len(items) >= ITEM_LIMIT:
            return items
        cursor = data["pageInfo"]["endCursor"]


def board_items(args, number):
    """The project's (non-archived) items, all of them, with each item's status normalised to Todo / In Progress / Done."""
    items = None
    if LEAN_READS:
        try:
            items = lean_items(args, number)
        except (GhError, KeyError, TypeError, ValueError) as exc:
            print(f"warning: slim board read failed ({str(exc).splitlines()[0] if str(exc) else type(exc).__name__}); "
                  "using `gh project item-list`", file=sys.stderr)
    if items is None:
        items = run_json("project", "item-list", str(number), *_owner(args), "--limit", str(ITEM_LIMIT))["items"]
    _warn_if_full("the project's item list", len(items), ITEM_LIMIT)
    for i in items:
        if i.get("status"):
            i["status"] = canonical_status(i["status"], args)
    return items


CONFIG_FILE = ".kopi.toml"
CONFIG_KEYS = {"owner": str, "project": str, "status": dict, "sources": dict, "comments": dict, "repos": list, "dates": dict, "sections": dict, "release": dict, "changelog": dict}
SOURCES_KEYS = {"code_comments": bool, "releases": bool}
COMMENTS_KEYS = {"tags": list, "include": list, "exclude": list}
DATES_KEYS = {"started": str, "completed": str, "enabled": bool}
SECTIONS_KEYS = {"enabled": bool, "field": str}
RELEASE_KEYS = {"tag_prefix": str}
CHANGELOG_KEYS = {"last": int, "since": str, "group": str}


def _toml_value(raw):
    """One TOML value from the subset the fallback parser supports: strings, integers, booleans, flat arrays."""
    raw = raw.strip()
    if raw in ("true", "false"):
        return raw == "true"
    if re.fullmatch(r"[+-]?\d+", raw):
        return int(raw)
    if raw[:1] == '"' and raw[-1:] == '"' and len(raw) >= 2:
        return re.sub(r'\\(["\\nt])', lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), raw[1:-1])
    if raw[:1] == "'" and raw[-1:] == "'" and len(raw) >= 2:
        return raw[1:-1]
    if raw[:1] == "[" and raw[-1:] == "]":
        parts = re.findall(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'|[^,\s][^,]*', raw[1:-1])
        return [_toml_value(p) for p in parts]
    raise ValueError(f"unsupported value {raw!r}")


def _strip_toml_comment(line):
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote and line[i - 1] != "\\":
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def parse_toml_subset(text):
    """Parse the TOML subset .kopi.toml uses, for Pythons without tomllib (before 3.11): `[table]` headers
    (dotted ok), `key = value` with bare or quoted keys, and strings, integers, booleans and arrays of them
    (arrays may span lines). Inline tables and the rest of TOML raise ValueError."""
    data, table, pending = {}, None, ""
    table = data
    for n, line in enumerate(text.splitlines(), 1):
        line = (pending + " " + _strip_toml_comment(line).strip()).strip()
        pending = ""
        if not line:
            continue
        if line.startswith("[") and not line.startswith("[[") and line.endswith("]") and "=" not in line:
            table = data
            for part in (p.strip().strip('"\'') for p in line[1:-1].split(".")):
                table = table.setdefault(part, {})
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ValueError(f"line {n}: expected key = value")
        if value.strip().startswith("[") and value.count("[") > value.count("]"):
            pending = line  # an array continuing on the next line
            continue
        key = key.strip()
        if key[:1] in "\"'" and key[-1:] == key[:1] and len(key) >= 2:
            key = key[1:-1]
        try:
            table[key] = _toml_value(value)
        except ValueError as e:
            raise ValueError(f"line {n}: {e}")
    if pending:
        raise ValueError("unterminated array")
    return data


def load_config(path, required=False):
    """The settings in a .kopi.toml (see README), validated. {} when the file is missing and not `required`."""
    if not os.path.isfile(path):
        if required:
            raise GhError(f"no such config file: {path}")
        return {}
    try:
        with open(path, "rb") as f:
            raw = f.read()
        try:
            import tomllib
            cfg = tomllib.loads(raw.decode())
        except ImportError:
            cfg = parse_toml_subset(raw.decode())
    except (ValueError, OSError) as e:  # tomllib.TOMLDecodeError is a ValueError
        raise GhError(f"{path}: {e}")
    for key, value in cfg.items():
        if key not in CONFIG_KEYS:
            print(f"warning: {path}: unknown setting {key!r} ignored", file=sys.stderr)
        elif not isinstance(value, CONFIG_KEYS[key]):
            raise GhError(f"{path}: {key} must be a {CONFIG_KEYS[key].__name__}")
    for table, known in (("sources", SOURCES_KEYS), ("comments", COMMENTS_KEYS), ("dates", DATES_KEYS), ("sections", SECTIONS_KEYS),
                         ("release", RELEASE_KEYS), ("changelog", CHANGELOG_KEYS)):
        for key, value in (cfg.get(table) or {}).items():
            if key not in known:
                print(f"warning: {path}: unknown setting {table}.{key} ignored", file=sys.stderr)
            elif not isinstance(value, known[key]):
                raise GhError(f"{path}: {table}.{key} must be a {known[key].__name__}")
    window = cfg.get("changelog") or {}
    if isinstance(window.get("last"), int) and (isinstance(window["last"], bool) or window["last"] < 1):
        raise GhError(f"{path}: changelog.last must be a whole number of releases, 1 or more")
    if isinstance(window.get("since"), str) and not re.fullmatch(r"\d+(\.\d+){0,2}", window["since"]):
        raise GhError(f"{path}: changelog.since must be a version such as 16.0.0")
    if isinstance(window.get("group"), str) and window["group"] != "minor":
        raise GhError(f"{path}: changelog.group must be \"minor\"")
    return cfg


def apply_config(args, cfg):
    """Fill in args from a config dict wherever the command line left a setting unset (flags win)."""
    args.config_data = cfg
    if args.status_map is None and cfg.get("status"):
        args.status_map = parse_status_map(",".join(f"{k}={v}" for k, v in cfg["status"].items()))
    sources = cfg.get("sources") or {}
    args.code_comments = args.code_comments or bool(sources.get("code_comments"))
    args.releases = args.releases or bool(sources.get("releases"))
    CHANGELOG_WINDOW.clear()
    CHANGELOG_WINDOW.update(cfg.get("changelog") or {})


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
        print(msg, flush=True)  # flushed so it stays in order with the progress lines on stderr


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


def project_title(args, repo):
    """The title kopi looks for (and creates): `project` from .kopi.toml, else the repo name."""
    return (getattr(args, "config_data", None) or {}).get("project") or repo[1]


def find_project(args, repo=None):
    """Return (project or None, (repo_owner, repo_name)) for the project named after the repo (default: the
    current one), or titled by `project` in .kopi.toml."""
    repo = repo or git_repo()
    if not repo:
        raise GhError("--init/--import/--update must be run inside a git repo with a github.com origin remote")
    title = project_title(args, repo)
    data = run_json("project", "list", *_owner(args), "--limit", str(PROJECT_LIMIT))
    return next((p for p in data["projects"] if p["title"] == title), None), repo


def ensure_project(args):
    """Find the project named after the current repo, creating and linking it if missing.

    Returns (number, created).
    """
    p, (repo_owner, name) = find_project(args)
    if p:
        say(args, f"Project exists: #{p['number']} {p['title']} {p['url']}")
        return p["number"], False
    args.title = project_title(args, (repo_owner, name))
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


def similar_length(a, b, ratio=0.6):
    """True if the shorter text has at least `ratio` as many distinct words as the longer one."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and min(len(wa), len(wb)) / max(len(wa), len(wb)) >= ratio


def similar(a, b, threshold=0.4):
    return overlap(a, b) >= threshold


# `## [1.2.3] - 2026-01-31` (Keep a Changelog) or `## 1.2.3 - 2026-01-31` / `## v1.2.3 (2026-01-31)`; the date is optional
CHANGELOG_HEADING = re.compile(r"##[ \t]+(?:\[([^\]]+)\]|v?(\d+\.\d+\.\d+[^\s]*|Unreleased))"
                               r"(?:[ \t]*(?:[-\u2013\u2014(])[ \t]*(\d{4}-\d{2}-\d{2}))?")


def changelog_heading(line):
    """(version, date or None) if `line` is a changelog release heading, else None."""
    m = CHANGELOG_HEADING.match(line)
    return (m.group(1) or m.group(2), m.group(3)) if m else None


def bullet_blocks(lines):
    """[(first, last)] line indexes of each top-level `- ` bullet and the indented lines that continue (wrap) it."""
    blocks, i = [], 0
    while i < len(lines):
        if lines[i].startswith("- "):
            j = i
            while j + 1 < len(lines) and re.match(r"[ \t]+(?![-*+] |\d+[.)] )\S", lines[j + 1]):
                j += 1
            blocks.append((i, j))
            i = j + 1
        else:
            i += 1
    return blocks


def bullet_text(lines, first, last):
    """The bullet's text on one line: its first line without the `- `, then each wrapped line, joined by spaces."""
    return " ".join([lines[first][2:].strip()] + [l.strip() for l in lines[first + 1:last + 1]])


CHANGELOG_WINDOW = {}  # [changelog] last / since from .kopi.toml: which releases of a long CHANGELOG.md are tracked


def changelog_window(entries):
    """`entries` ([(version, entry)], newest release first) cut to the [changelog] window: the newest `last` releases
    that have entries and/or those at or above version `since`. `Unreleased` entries are always kept."""
    last, since = CHANGELOG_WINDOW.get("last"), CHANGELOG_WINDOW.get("since")
    if not (last or since):
        return entries
    releases = list(dict.fromkeys(v for v, _ in entries if v != "Unreleased"))
    keep = set(releases[:last] if last else releases)
    if since:
        keep = {v for v in keep if version_key(v) >= version_key(since)}
    return [(v, e) for v, e in entries if v == "Unreleased" or v in keep]


GROUP_VERSION = re.compile(r"\d+\.\d+")


def minor_of(version):
    """`16.5` for `16.5.3`; the version itself unless it is a plain MAJOR.MINOR.PATCH."""
    return ".".join(version.split(".")[:2]) if re.fullmatch(r"\d+\.\d+\.\d+", version) else version


def group_minor(entries):
    """`entries` ([(version, entry)], newest first) with each MAJOR.MINOR collapsed to one `("16.5", "Releases 16.5.x")`
    entry, at the place its newest release was. `Unreleased` and versions that are not MAJOR.MINOR.PATCH stay as they are."""
    out, seen = [], set()
    for version, entry in entries:
        group = minor_of(version)
        if group == version:
            out.append((version, entry))
        elif group not in seen:
            seen.add(group)
            out.append((group, f"Releases {group}.x"))
    return out


def group_bodies(entries):
    """{"16.5": "- 16.5.9: text\n- 16.5.8: text", ...}: what each minor version's grouped item holds, newest first."""
    bodies = {}
    for version, entry in entries:
        if minor_of(version) != version:
            bodies.setdefault(minor_of(version), []).append(f"- {version}: {entry}")
    return {group: "\n".join(lines) for group, lines in bodies.items()}


def parse_changelog(text, raw=False):
    """[(version, entry)] for every bullet under a release heading, in file order (a wrapped bullet is one entry),
    limited to the [changelog] window when .kopi.toml sets one and, with `group = "minor"`, collapsed to one entry
    per MAJOR.MINOR (`raw` returns the individual bullets either way)."""
    lines, items, version = text.splitlines(), [], None
    blocks = dict(bullet_blocks(lines))
    for n, line in enumerate(lines):
        heading = changelog_heading(line)
        if heading:
            version = heading[0]
        elif version and n in blocks:
            items.append((version, bullet_text(lines, n, blocks[n])))
    items = changelog_window(items)
    return group_minor(items) if CHANGELOG_WINDOW.get("group") == "minor" and not raw else items


def parse_todo(text):
    """[(status, text)] for every checkbox bullet; [ ] Todo, [~] In Progress, [x] Done (a wrapped bullet is one entry)."""
    names = {" ": "Todo", "~": "In Progress", "x": "Done"}
    lines, out = text.splitlines(), []
    for first, last in bullet_blocks(lines):
        m = re.match(r"\[([ ~xX])\] (.+)$", bullet_text(lines, first, last))
        if m:
            out.append((names[m.group(1).lower()], m.group(2).strip()))
    return out


def strip_emphasis(text):
    """`text` without Markdown bold markers (`**x**`, `__x__`), which show up as stray asterisks in a board title.
    Code spans are left alone, so `**kwargs` in backticks survives."""
    return re.sub(r"(`[^`]*`)|(\*\*|__)(.+?)\2", lambda m: m.group(1) or m.group(3), text)


def _title(text, limit=100):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def build_plan(changelog, todo, linked=()):
    """Return ([(status, title, body)], [skipped TODO done texts]).

    Changelog entries become Done items (oldest first). TODO items follow, except
    done ones that duplicate a changelog entry and any that duplicate a linked
    issue/PR (`linked` is [(status, title, url)]; those are added separately).
    """
    entries, raw = parse_changelog(changelog), parse_changelog(changelog, raw=True)
    bodies = group_bodies(raw) if CHANGELOG_WINDOW.get("group") == "minor" else {}
    plan, skipped = [], []
    for version, text in reversed(entries):
        title = _title(f"[{version}] {strip_emphasis(text)}")
        plan.append(("Done", title, bodies.get(version) or (text if title != f"[{version}] {text}" else "")))
    for status, text in parse_todo(todo):
        if any(similar(text, t) for _, t, _ in linked) or \
                (status == "Done" and any(similar(text, e) for _, e in raw)):
            skipped.append(text)
            continue
        title = _title(strip_emphasis(text))
        plan.append((status, title, text if title != text else ""))
    return plan, skipped


def _lead(text, n=8):
    """The first `n` significant words of `text`, in order (stop words dropped, plurals folded as in _words)."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    words = [w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words if w not in STOPWORDS and len(w) > 1]
    return words[:n]


def similar_todos(todo, threshold=0.5, min_words=4):
    """[(a, b)] pairs of open TODO.md entries that read like the same task written twice: they start with the same
    word and share at least `threshold` of their first eight significant words. Only the lead-in is compared, since
    long, wrapped entries share common words without being repeats. Advice only: nothing is merged or skipped.
    Entries under `min_words` words are ignored."""
    open_ = [(t, _lead(t)) for st, t in parse_todo(todo) if st != "Done" and len(_words(t)) >= min_words]
    return [(a, b) for i, (a, la) in enumerate(open_) for b, lb in open_[i + 1:]
            if la[0] == lb[0] and len(set(la) & set(lb)) / min(len(set(la)), len(set(lb))) >= threshold]


def warn_similar_todos(todo):
    for a, b in similar_todos(todo):
        print(f"warning: possible duplicate TODO.md entries:\n  {_title(a)}\n  {_title(b)}", file=sys.stderr)


CONTEXT_START = re.compile(r"(neither|both|it|its|they|them|their|these|those|also|then|and|but|which|the same)\b", re.I)
CONTEXT_WORDS = re.compile(r"\b(the above|as above|the previous (?:one|step|entry)|previously mentioned)\b", re.I)


def context_dependent(text):
    """True if an entry reads as a continuation of the one before it ("Neither is run by ...", "Both were run ...",
    "as above"), so as a board item on its own it would mean nothing. A heuristic, used only to warn."""
    return bool(CONTEXT_START.match(text) or CONTEXT_WORDS.search(text))


def warn_context_dependent(changelog, todo, limit=5):
    """Warn on stderr about changelog and TODO entries that probably only make sense next to the entry before them."""
    found = [f"[{v}] {e}" for v, e in parse_changelog(changelog, raw=True) if context_dependent(e)]
    found += [t for st, t in parse_todo(todo) if context_dependent(t)]
    if found:
        print(f"warning: {_n(len(found), 'entry').replace('entrys', 'entries')} may not make sense on their own as board items "
              "(they read as a continuation of the entry before):", file=sys.stderr)
        for text in found[:limit]:
            print(f"  {_title(text)}", file=sys.stderr)
        if len(found) > limit:
            print(f"  ... and {len(found) - limit} more", file=sys.stderr)


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


SOURCE_FILES = ("CHANGELOG.md", "TODO.md")


def load_sources(args, notice=False):
    """(changelog, todo, linked) for --import/--update. With args.only (one file given), only that file is read
    and issues/PRs are skipped."""
    only = getattr(args, "only", None)
    changelog = "" if only == "TODO.md" else _read("CHANGELOG.md")
    todo = "" if only == "CHANGELOG.md" else _read("TODO.md")
    return changelog, todo, [] if only else fetch_linked(git_repo(), notice)


def fetch_linked(repo, notice=False):
    """[(status, title, url)] for the repo's issues and PRs, mapped to project statuses.

    Open issue -> Todo, closed issue -> Done, open PR -> In Progress, merged PR -> Done.
    Closed-unmerged PRs are skipped. A failed lookup is a warning, not an error. A repo with issues turned off is
    an ordinary state, not a failure: it is noted once, by `notice` (--import), and otherwise passed over quietly.
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
            if "disabled issues" in str(e):
                if notice:
                    print(f"note: issues are turned off for {slug}; importing the files and pull requests only", file=sys.stderr)
            else:
                print(f"warning: could not list {kind}s for {slug}: {e}", file=sys.stderr)
            continue
        _warn_if_full(f"the {kind} list for {slug}", len(rows), LINKED_LIMIT)
        linked += [(mapping[r["state"]], r["title"], r["url"]) for r in rows if r["state"] in mapping]
    return linked


CODE_PREFIX, RELEASE_PREFIX = "[code] ", "[release] "
DEFAULT_COMMENT_TAGS = ("TODO", "FIXME")
# docs and data files are not scanned by default: their "# TODO" lines are headings, not code comments
DEFAULT_COMMENT_EXCLUDE = ("*.md", "*.txt", "*.rst", "*.json", "*.lock", "*.csv", "*.svg", "*.min.js", "*.min.css")
SCAN_MAX_BYTES = 1_000_000
SKIP_DIRS = {"node_modules", "venv", "__pycache__", "dist", "build", "vendor"}


def source_files():
    """Paths to scan for code comments: the files git tracks (so .gitignore, .venv and build output are skipped
    for free), or, outside a git repo, every file under the current directory except hidden and vendored dirs."""
    try:
        proc = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True)
        if proc.returncode == 0:
            return sorted(p for p in proc.stdout.split("\0") if p)
    except FileNotFoundError:
        pass
    found = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        found += [os.path.relpath(os.path.join(root, f)) for f in files if not f.startswith(".")]
    return sorted(found)


def _matches(path, patterns):
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(os.path.basename(path), p) for p in patterns)


def scan_code_comments(cfg=None):
    """[(path, line number, tag, text)] for each `TODO: text` / `FIXME(name): text` code comment.

    Only comments count: the tag must follow a comment marker (#, //, /*, *, --, <!--, ;) and be followed by a
    colon, which keeps prose and strings such as "# TODO" headings out. Tags and file globs come from the
    `[comments]` table in .kopi.toml (tags, include, exclude).
    """
    cfg = (cfg or {}).get("comments") or {}
    tags = cfg.get("tags") or DEFAULT_COMMENT_TAGS
    include, exclude = cfg.get("include") or [], [*DEFAULT_COMMENT_EXCLUDE, *(cfg.get("exclude") or [])]
    pattern = re.compile(r"(?:#|//|/\*|\*|<!--|--|;)\s*(" + "|".join(map(re.escape, tags)) + r")(?:\([^)]*\))?:\s*(\S.*?)\s*(?:\*/|-->)?\s*$")
    found = []
    for path in source_files():
        if (include and not _matches(path, include)) or _matches(path, exclude):
            continue
        try:
            if os.path.getsize(path) > SCAN_MAX_BYTES:
                continue
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError):  # unreadable, deleted but still listed, or binary
            continue
        for n, line in enumerate(lines, 1):
            m = pattern.search(line)
            if m:
                found.append((path, n, m.group(1), m.group(2)))
    return found


def code_comment_items(cfg=None):
    """[(status, title, body)] draft Todo items for the repo's code comments, one per distinct comment text.

    The title is `[code] text`, so the text is the item's identity: moving a comment to another line or file keeps
    its item, and the same comment in several places is one item whose body lists where.
    """
    by_text = {}
    for path, n, tag, text in scan_code_comments(cfg):
        by_text.setdefault(text, []).append((path, n, tag))
    items = []
    for text, places in by_text.items():
        where = "\n".join(f"{p}:{n}" for p, n, _ in places[:20]) + (f"\n(+{len(places) - 20} more)" if len(places) > 20 else "")
        items.append(("Todo", _title(CODE_PREFIX + text), f"{places[0][2]}: {text}\n\n{where}"))
    return items


def release_items(slug, changelog):
    """[(status, title, body)] Done items for the repo's git tags, skipping tags whose version already has a
    CHANGELOG.md entry (those are on the board as `[version] text`). A failed lookup is a warning."""
    try:
        tags = run("api", f"repos/{slug}/tags", "--paginate", "--jq", ".[].name").split()
    except GhError as e:
        print(f"warning: could not list tags for {slug}: {e}", file=sys.stderr)
        return []
    in_changelog = {v for v, _ in parse_changelog(changelog, raw=True)}
    return [("Done", _title(RELEASE_PREFIX + tag), "") for tag in tags if tag.lstrip("vV") not in in_changelog]


def load_extras(args, changelog):
    """[(status, title, body)] from the opt-in sources (--code-comments, --releases, or [sources] in .kopi.toml).
    Skipped when a single source file was given (args.only)."""
    if getattr(args, "only", None):
        return []
    extras = []
    if args.code_comments:
        extras += code_comment_items(getattr(args, "config_data", None))
    if args.releases:
        repo = git_repo()
        if repo:
            extras += release_items("/".join(repo), changelog)
    return extras


def progress(args, msg):
    """Progress feedback for long operations: a line on stderr (so stdout stays clean), but not under --json."""
    if not getattr(args, "json", False):
        print(msg, file=sys.stderr, flush=True)


def status_info(args, number, needed):
    """The project's Status field: namespace(project_id, field_id, option(status) -> option id). GhError if the
    project has no Status field or lacks an option for one of the `needed` statuses."""
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

    def option(status):
        status = canonical_status(status, args)
        return status_option(status, options, args) or options[status.lower()]
    return SimpleNamespace(project_id=project_id, field_id=status_field["id"], option=option)


def _status_setter(args, number, needed):
    """Return set_status(item_id, status) for the project's Status field; error if an option is missing."""
    info = status_info(args, number, needed)

    def set_status(item_id, status):
        run("project", "item-edit", "--id", item_id, "--project-id", info.project_id,
            "--field-id", info.field_id, "--single-select-option-id", info.option(status))
    return set_status


BATCH = 20  # GraphQL operations per request when looking up issue / PR ids
WRITE_BATCH = 50  # mutations per request: the server spends ~0.7 s per request and ~0.115 s per mutation, so 50 beats 20 by about a quarter
WRITE_WORKERS = 3  # parallel requests for writes that are safe to repeat; 4 made GitHub answer "something went wrong"


def _gql_str(text):
    """A GraphQL string literal for `text`."""
    return json.dumps(text, ensure_ascii=False)


def _send_mutations(chunk):
    """One request carrying the mutations in `chunk`; returns each one's result data, in order. GhError on any GraphQL error."""
    result = graphql("mutation { " + " ".join(f"a{n}: {p}" for n, p in enumerate(chunk)) + " }")
    if result.get("errors"):
        raise GhError("; ".join(e.get("message", "?") for e in result["errors"]))
    return [result["data"][f"a{n}"] for n in range(len(chunk))]


def mutate_batched(args, parts, label, repeatable=False):
    """Run GraphQL mutation `parts` (each `fieldName(input: {...}) { ... }`) WRITE_BATCH at a time, one request per batch,
    reporting progress as `label n/total`. Returns each part's result data, in order; GhError on any GraphQL error.

    `repeatable` says running a mutation twice is harmless (setting a field value, unlike creating an item): the requests
    then go WRITE_WORKERS at a time, and a request that fails is retried on its own once the others are done."""
    chunks = [parts[i:i + WRITE_BATCH] for i in range(0, len(parts), WRITE_BATCH)]
    results, done = [None] * len(chunks), 0

    def finished(n, data):
        nonlocal done
        results[n], done = data, done + len(chunks[n])
        progress(args, f"  {label} {done}/{len(parts)}")

    if not repeatable or len(chunks) < 2:
        for n, chunk in enumerate(chunks):
            finished(n, _send_mutations(chunk))
    else:
        failed = []
        with ThreadPoolExecutor(WRITE_WORKERS) as pool:
            futures = {pool.submit(_send_mutations, chunk): n for n, chunk in enumerate(chunks)}
            for future in as_completed(futures):
                try:
                    finished(futures[future], future.result())
                except GhError:
                    failed.append(futures[future])
        for n in sorted(failed):
            finished(n, _send_mutations(chunks[n]))
    return [r for data in results for r in data]


def content_ids(args, urls):
    """{url: node id} for issue / pull request URLs, looked up BATCH at a time."""
    found = {}
    for i in range(0, len(urls), BATCH):
        chunk = urls[i:i + BATCH]
        result = graphql("query { " + " ".join(
            f"a{n}: resource(url: {_gql_str(u)}) {{ ... on Issue {{ id }} ... on PullRequest {{ id }} }}"
            for n, u in enumerate(chunk)) + " }")
        if result.get("errors"):
            raise GhError("; ".join(e.get("message", "?") for e in result["errors"]))
        for n, u in enumerate(chunk):
            node = (result["data"].get(f"a{n}") or {})
            if not node.get("id"):
                raise GhError(f"could not find {u} on GitHub")
            found[u] = node["id"]
    return found


def create_items(args, info, drafts, urls):
    """Create `drafts` ([(title, body)]) as draft items and add `urls` (issues / PRs) as linked items, in batched
    requests. Returns the new item ids: the drafts' first, then the linked ones'."""
    ids = []
    if drafts:
        progress(args, f"Creating {len(drafts)} draft items...")
        ids += [r["projectItem"]["id"] for r in mutate_batched(args, [
            f"addProjectV2DraftIssue(input: {{projectId: {_gql_str(info.project_id)}, title: {_gql_str(t)}, body: {_gql_str(b)}}}) "
            "{ projectItem { id } }" for t, b in drafts], "created")]
    if urls:
        progress(args, f"Adding {len(urls)} issues and pull requests...")
        nodes = content_ids(args, urls)
        ids += [r["item"]["id"] for r in mutate_batched(args, [
            f"addProjectV2ItemById(input: {{projectId: {_gql_str(info.project_id)}, contentId: {_gql_str(nodes[u])}}}) "
            "{ item { id } }" for u in urls], "added")]
    return ids


def set_statuses(args, info, pairs):
    """Set the Status of many items at once: pairs is [(item id, status)], written WRITE_BATCH per request, several requests at once."""
    if pairs:
        progress(args, f"Setting {len(pairs)} statuses...")
        mutate_batched(args, [
            f"updateProjectV2ItemFieldValue(input: {{projectId: {_gql_str(info.project_id)}, itemId: {_gql_str(i)}, "
            f"fieldId: {_gql_str(info.field_id)}, value: {{singleSelectOptionId: {_gql_str(info.option(st))}}}}}) "
            "{ projectV2Item { id } }" for i, st in pairs], "set", repeatable=True)


SETTLE_TIMEOUT, SETTLE_INTERVAL = 30, 2


TOTAL_QUERY = "query($id: ID!) { node(id: $id) { ... on ProjectV2 { items(first: 1) { totalCount } } } }"


def board_total(args, number):
    """How many items GitHub counts on the project (archived ones too), or None if it cannot be read.

    One API point however big the board is, where listing the items through `gh project item-list` costs about 1 point each (400 for 573, 1,520 for 1,631 with date fields); `lean_items` reads them for about 0.02 each."""
    try:
        project_id = run_json("project", "view", str(number), *_owner(args))["id"]
        return graphql(TOTAL_QUERY, id=project_id)["data"]["node"]["items"]["totalCount"]
    except (GhError, KeyError, TypeError, ValueError):
        return None


def wait_for_items(args, number, ids, before=None):
    """Wait until GitHub lists the new items, so an immediate re-run sees them.

    GitHub's listing lags after writes; without this a second --update can re-add the same items. Callers run it
    after their other writes, so the lag has usually passed and the first poll is enough. `before` is the
    board_total taken before the writes: with it the wait polls that count (one API point a poll, and it catches up
    no earlier than the listing does); without it, or if the count cannot be read, it lists every item each poll,
    which on a big board spends the hourly API budget. Gives up after SETTLE_TIMEOUT seconds with a warning, and a
    failure while waiting (a rate limit, say) is a warning too: the writes have been made by then.
    """
    pending = set(ids)
    if not pending:
        return
    progress(args, f"Waiting for GitHub to list the {len(pending)} new items...")
    waited = 0
    try:
        while True:
            if before is not None:
                total = board_total(args, number)
                if total is None:
                    before = None   # cannot count: fall back to the full listing
                    continue
                if total >= before + len(pending):
                    return
            else:
                pending -= {i["id"] for i in board_items(args, number)}
                if not pending:
                    return
            if waited >= SETTLE_TIMEOUT:
                print(f"warning: {len(pending)} new items not yet listed by GitHub; wait before running --update again",
                      file=sys.stderr)
                return
            time.sleep(SETTLE_INTERVAL)
            waited += SETTLE_INTERVAL
    except GhError as e:
        print(f"warning: could not confirm that GitHub lists the new items ({e}); the writes were made, wait a little "
              "before running --update again", file=sys.stderr)


# --- Started / Completed dates, for the project's Roadmap view ---------------------------------------------------
DATE_KEYS = ("started", "completed")
DEFAULT_DATE_FIELDS = {"started": "Started", "completed": "Completed"}


def dates_enabled(args):
    """False under --no-dates or `[dates] enabled = false` in .kopi.toml: kopi then leaves the date fields alone."""
    cfg = (getattr(args, "config_data", None) or {}).get("dates") or {}
    return not getattr(args, "no_dates", False) and cfg.get("enabled", True) is not False


def date_field_names(args):
    """{"started": name, "completed": name}: the board's date field names (`[dates]` in .kopi.toml, else the defaults)."""
    cfg = (getattr(args, "config_data", None) or {}).get("dates") or {}
    return {k: cfg.get(k) or DEFAULT_DATE_FIELDS[k] for k in DATE_KEYS}


def changelog_release_dates(text):
    """{version: "YYYY-MM-DD"} for the release headings (`## [version] - date`, `## version - date`) that carry a date."""
    headings = (changelog_heading(line) for line in text.splitlines())
    dates = {}
    for version, date in (h for h in headings if h and h[1]):
        dates[version] = max(date, dates.get(version, date))   # a version listed twice takes the later date
    return dates


def duplicate_headings(text):
    """{version: [dates]} for versions with more than one release heading (the date is None where a heading has none)."""
    seen = {}
    for line in text.splitlines():
        heading = changelog_heading(line)
        if heading:
            seen.setdefault(heading[0], []).append(heading[1])
    return {v: d for v, d in seen.items() if len(d) > 1}


def warn_duplicate_headings(changelog):
    """Warn on stderr about a version that has several `## [version]` headings (for example one for Added and one for
    Fixed): their entries are all imported, and the version's date is the latest of its headings."""
    dupes = duplicate_headings(changelog)
    if dupes:
        shown = ", ".join(f"{v} ({', '.join(d or 'undated' for d in dates)})" for v, dates in list(dupes.items())[:5])
        print(f"warning: {_n(len(dupes), 'version')} listed under more than one heading in CHANGELOG.md: {shown}"
              + (", ..." if len(dupes) > 5 else "") + "; the latest date is used", file=sys.stderr)


def default_started(release_dates, completed):
    """The date of the newest release strictly before `completed`, else `completed` itself: with nothing better
    known, work is taken to have started when the previous changelog release shipped."""
    earlier = [d for d in release_dates if d < completed]
    return max(earlier) if earlier else completed


def _git_first_line(*argv):
    try:
        proc = subprocess.run(["git", *argv], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return (proc.stdout.split("\n", 1)[0].strip() or None) if proc.returncode == 0 else None


def tag_date(tag):
    """Commit date (YYYY-MM-DD) of a local git tag, or None."""
    return _git_first_line("log", "-1", "--format=%cs", tag)


TICK_PREFIX = 60


def tick_date(text):
    """Date (YYYY-MM-DD) of the first commit that put `- [x] text` into TODO.md, or None if no commit did.

    Only the first TICK_PREFIX characters are searched for: a wrapped entry is one line here but several in the file."""
    return _git_first_line("log", "--reverse", "--format=%cs", "-S- [x] " + text[:TICK_PREFIX], "--", "TODO.md")


def linked_dates(slug):
    """{url: (created, finished)} (YYYY-MM-DD) for the repo's issues and PRs; `finished` is closedAt / mergedAt."""
    out = {}
    for kind, done_key in (("issue", "closedAt"), ("pr", "mergedAt")):
        try:
            rows = json.loads(run(kind, "list", "--repo", slug, "--state", "all", "--limit", str(LINKED_LIMIT),
                                  "--json", f"url,createdAt,{done_key}") or "[]")
        except GhError as e:
            print(f"warning: could not list {kind} dates for {slug}: {e}", file=sys.stderr)
            continue
        for r in rows:
            out[r["url"]] = ((r.get("createdAt") or "")[:10] or None, (r.get(done_key) or "")[:10] or None)
    return out


def resolve_dates(ctx, item):
    """(started, completed) dates for a board item (`ctx` has `.releases` {version: date} and `.linked` {url: dates}).

    Completed (Done items only) comes from the item's own record: a changelog entry's release date, an issue's
    closedAt or PR's mergedAt, a `[release]` tag's commit date, or, for a `TODO.md` draft, the commit that ticked
    it. Started is an issue/PR's createdAt, else the previous changelog release's date (see default_started).
    Either may be None when nothing is known."""
    title, status = item["title"], item.get("status")
    content = item.get("content") or {}
    url = _item_url(item)
    started = completed = None
    m = CHANGELOG_TITLE.match(title)
    if url and url in ctx.linked:
        started, finished = ctx.linked[url]
        completed = finished
    elif m and m.group(1) in ctx.releases:
        completed = ctx.releases[m.group(1)]
    elif m and (group := [d for v, d in ctx.releases.items() if minor_of(v) == m.group(1) != v]):
        completed = max(group)    # a `[16.5]` item (changelog.group): from the release before the group to its last
        started = default_started(ctx.releases.values(), min(group)) if status == "Done" else None
    elif title.startswith(RELEASE_PREFIX):
        completed = tag_date(title[len(RELEASE_PREFIX):])
    elif status == "Done":
        text = (content.get("body") or "").strip() or title
        completed = tick_date(text[:-1] if text.endswith("…") else text)
    if status != "Done":
        completed = None
    if completed and not started:
        started = default_started(ctx.releases.values(), completed)
    return started, completed


class DateWriter:
    """Queue Started / Completed values for board items and write them in batched GraphQL requests.

    Only fields that exist on the board as DATE fields (named by date_field_names) are used, so a board without
    them is left alone (falsy writer), and a value is only written where the item has none."""

    def __init__(self, args, number, ignore_disabled=False):
        self.args, self.number, self.queue = args, number, []
        self.names = date_field_names(args)
        self.fields, self.missing, self.project_id = {}, [], None
        if not (ignore_disabled or dates_enabled(args)):
            return
        self.project_id = run_json("project", "view", str(number), *_owner(args))["id"]
        nodes = (graphql(FIELDS_QUERY, id=self.project_id).get("data", {}).get("node") or {}).get("fields", {}).get("nodes", [])
        for key, name in self.names.items():
            field = next((f for f in nodes if (f.get("name") or "").lower() == name.lower()), None)
            if field and field.get("dataType") == "DATE":
                self.fields[key] = field
            elif field:
                print(f"warning: field {field['name']!r} is {field.get('dataType')}, not a date field; skipped", file=sys.stderr)
            else:
                self.missing.append(name)

    def __bool__(self):
        return bool(self.fields)

    def create_missing(self, dry_run=False):
        """Create the date fields the board lacks (gh project field-create); returns their names. Say what happened."""
        for name in self.missing:
            if dry_run:
                say(self.args, f"create\t{name}\tDATE")
            else:
                run("project", "field-create", str(self.number), *_owner(self.args), "--name", name, "--data-type", "DATE")
                say(self.args, f"Created date field {name}")
        return list(self.missing)

    def want(self, item, started=None, completed=None):
        """Queue the dates for `item` (a board item dict, whose own values are honoured: nothing is overwritten)."""
        for key, date in (("started", started), ("completed", completed)):
            field = self.fields.get(key)
            if not (date and field) or item.get(self.names[key].lower()):
                continue
            datetime.date.fromisoformat(date)
            self.queue.append({"id": item["id"], "title": item["title"], "field": field["name"],
                               "field_id": field["id"], "date": date})

    def flush(self, dry_run=False):
        """Write the queue (unless dry_run); returns [{title, field, date}] for what was (or would be) set."""
        done = [{"title": q["title"], "field": q["field"], "date": q["date"]} for q in self.queue]
        if self.queue and not dry_run:
            progress(self.args, f"Setting {len(self.queue)} dates...")
            mutate_batched(self.args, [
                f'updateProjectV2ItemFieldValue(input: {{projectId: "{self.project_id}", itemId: "{q["id"]}", '
                f'fieldId: "{q["field_id"]}", value: {{date: "{q["date"]}"}}}}) {{ projectV2Item {{ id }} }}'
                for q in self.queue], "set", repeatable=True)
        self.queue = []
        return done


def write_dates(args, number, items, changelog, writer=None):
    """Set the Started / Completed dates of `items` (board-item dicts) from what is known about each; returns what was set.

    GitHub dates are fetched when some items are issues/PRs. A board without the date fields: no-op."""
    writer = writer or DateWriter(args, number)
    if not writer:
        return []
    repo = git_repo() if any(_item_url(i) for i in items) else None
    ctx = SimpleNamespace(releases=changelog_release_dates(changelog), linked=linked_dates("/".join(repo)) if repo else {})
    for item in items:
        writer.want(item, *resolve_dates(ctx, item))
    return writer.flush()


def sync_dates(args, number, items, changelog):
    """After --import / --update: create the date fields if the board lacks them, then fill in the missing dates of
    `items` (every item on the board, with its post-update status). Returns (dates set, fields created).
    Off under --no-dates or `[dates] enabled = false`."""
    if not dates_enabled(args):
        return [], []
    writer = DateWriter(args, number)
    created = writer.create_missing()
    if created:
        writer = DateWriter(args, number)  # re-read the board now that the fields exist
    return write_dates(args, number, items, changelog, writer=writer), created


# --- Section field: the TODO.md heading each item sat under (opt-in: `[sections] enabled = true`) -----------------------
DEFAULT_SECTION_FIELD = "Section"


def sections_settings(args):
    """(enabled, field name) from `[sections]` in .kopi.toml: off unless `enabled = true`; the field defaults to Section."""
    cfg = (getattr(args, "config_data", None) or {}).get("sections") or {}
    return bool(cfg.get("enabled")), cfg.get("field") or DEFAULT_SECTION_FIELD


def todo_sections(text):
    """{item title: heading} for every checkbox entry in TODO.md: the nearest `##`-or-deeper heading above it."""
    lines, heading, out = text.splitlines(), None, {}
    blocks = dict(bullet_blocks(lines))
    for n, line in enumerate(lines):
        m = re.match(r"#{2,6}\s+(.+?)\s*#*\s*$", line)
        if m:
            heading = m.group(1)
        elif n in blocks and heading:
            entry = re.match(r"\[[ ~xX]\] (.+)$", bullet_text(lines, n, blocks[n]))
            if entry:
                out[_title(strip_emphasis(entry.group(1).strip()))] = heading
    return out


def section_for(sections, title):
    """The section of the entry a board item's title stands for: the same title, or one that is the start of it
    (older boards cut a wrapped entry at its first line, and long titles end in an ellipsis)."""
    if title in sections:
        return sections[title]
    start = strip_emphasis(title).rstrip("\u2026").strip()
    if len(start) >= 20:
        return next((sec for key, sec in sections.items() if key.startswith(start)), None)
    return None


def sync_sections(args, number, items, todo):
    """After --import / --update, with `[sections] enabled = true`: create the Section text field if the board lacks it
    and fill it in for the items (board-item dicts) whose TODO.md entry sits under a heading. A value already on an item
    is never overwritten. Returns [{title, section}] for what was set."""
    enabled, name = sections_settings(args)
    sections = todo_sections(todo) if enabled else {}
    if not sections:
        return []
    project_id = run_json("project", "view", str(number), *_owner(args))["id"]

    def find():
        nodes = (graphql(FIELDS_QUERY, id=project_id).get("data", {}).get("node") or {}).get("fields", {}).get("nodes", [])
        return next((f for f in nodes if (f.get("name") or "").lower() == name.lower()), None)

    field = find()
    if not field:
        run("project", "field-create", str(number), *_owner(args), "--name", name, "--data-type", "TEXT")
        say(args, f"Created text field {name}")
        field = find()
    if not field or field.get("dataType") != "TEXT":
        print(f"warning: field {name!r} is not a text field; sections skipped", file=sys.stderr)
        return []
    queue = [(i, sec) for i in items if not i.get(name.lower()) and (sec := section_for(sections, i["title"]))]
    if queue:
        progress(args, f"Setting {len(queue)} sections...")
        mutate_batched(args, [
            f"updateProjectV2ItemFieldValue(input: {{projectId: {_gql_str(project_id)}, itemId: {_gql_str(i['id'])}, "
            f"fieldId: {_gql_str(field['id'])}, value: {{text: {_gql_str(sec)}}}}}) {{ projectV2Item {{ id }} }}"
            for i, sec in queue], "set", repeatable=True)
    return [{"title": i["title"], "section": sec} for i, sec in queue]


def stamp_dates(args, number, item, target):
    """After `--start` / `--done` / `--move`: Started today for In Progress; Completed today for Done, with Started
    defaulting to the previous changelog release's date if the item has none. Returns what was set."""
    writer = DateWriter(args, number)
    if not writer or target not in ("In Progress", "Done"):
        return []
    today = datetime.date.today().isoformat()
    if target == "In Progress":
        writer.want(item, started=today)
    else:
        writer.want(item, started=default_started(changelog_release_dates(_read("CHANGELOG.md")).values(), today), completed=today)
    return writer.flush()


def cmd_setup_dates(args):
    """--setup-dates: create the Started and Completed date fields on the project if they are missing."""
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    writer = DateWriter(args, project["number"], ignore_disabled=True)
    for key, field in writer.fields.items():
        say(args, f"exists\t{field['name']}\tDATE")
    created = writer.create_missing(dry_run=bool(args.dry_run))
    emit(args, {"project": project["number"], "dry_run": bool(args.dry_run), "created": [{"field": n} for n in created],
                "existing": [{"field": f["name"], "type": f["dataType"]} for f in writer.fields.values()]})


def cmd_backfill_dates(args):
    """--backfill-dates: set the missing Started / Completed dates on the project's items from what is known about
    each (see resolve_dates). Values already set are never changed. --dry-run lists them without writing."""
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    number = project["number"]
    writer = DateWriter(args, number, ignore_disabled=True)
    if not writer:
        raise GhError("the project has no Started / Completed date fields; run --setup-dates first")
    items = board_items(args, number)
    repo = git_repo() if any(_item_url(i) for i in items) else None
    ctx = SimpleNamespace(releases=changelog_release_dates(_read("CHANGELOG.md")),
                          linked=linked_dates("/".join(repo)) if repo else {})
    for item in items:
        writer.want(item, *resolve_dates(ctx, item))
    done = writer.flush(dry_run=bool(args.dry_run))
    for d in done:
        say(args, f"{'set' if args.dry_run else 'Set'}\t{d['field']}\t{d['date']}\t{d['title']}")
    say(args, ("Dry run: " if args.dry_run else "") + f"{len(done)} dates {'would be ' if args.dry_run else ''}set on "
        f"{len({d['title'] for d in done})} items in project #{number}")
    emit(args, {"project": number, "dry_run": bool(args.dry_run), "set": done})


BIG_IMPORT = 300         # items above which an import of a long, unwindowed changelog gets an advisory
SECONDS_PER_ITEM = 0.4   # measured: 210 items took 1:19, a 1,631-item import about 10 minutes


def estimate_seconds(total):
    """Roughly how long writing `total` items takes (items, statuses and dates), from the measured speed."""
    return round(total * SECONDS_PER_ITEM)


def _duration(seconds):
    return f"{seconds} seconds" if seconds < 90 else f"{round(seconds / 60)} minutes"


def warn_big_import(total, changelog):
    """Advise on stderr when a long CHANGELOG.md with no [changelog] setting makes a big import: it is the usual way
    a board ends up with over a thousand items nobody reads, and it takes minutes to write."""
    entries = parse_changelog(changelog)
    if total < BIG_IMPORT or len(entries) < BIG_IMPORT or any(CHANGELOG_WINDOW.get(k) for k in ("last", "since", "group")):
        return
    releases = len({v for v, _ in entries})
    print(f"warning: CHANGELOG.md has {len(entries)} entries in {releases} releases, so this import creates {total} items "
          f"and takes about {_duration(estimate_seconds(total))}; to track less, set in .kopi.toml `[changelog] last = 50` "
          "(the newest 50 releases) or `group = \"minor\"` (one item per MAJOR.MINOR), and run it again", file=sys.stderr)


def cmd_import(args):
    """Seed an empty project from CHANGELOG.md and TODO.md (draft items) plus the repo's issues and PRs."""
    changelog, todo, linked = load_sources(args, notice=True)
    extras = load_extras(args, changelog)
    if not changelog and not todo and not linked and not extras:
        raise GhError("nothing to import: no CHANGELOG.md or TODO.md in the current directory, and no issues or PRs")
    plan, skipped = build_plan(changelog, todo, linked)
    warn_similar_todos(todo)
    warn_context_dependent(changelog, todo)
    warn_duplicate_headings(changelog)
    plan += extras
    total = len(plan) + len(linked)
    warn_big_import(total, changelog)
    if args.dry_run:
        items = [_item(st, t) for st, t, _ in plan] + [_item(st, _title(t), u) for st, t, u in linked]
        if args.json:
            emit(args, {"dry_run": True, "items": items, "skipped": [_title(t) for t in skipped],
                        "estimated_seconds": estimate_seconds(total)})
            return
        for status, title, _ in plan:
            print(f"{status}\t{title}")
        for status, title, url in linked:
            print(f"{status}\t{_title(title)}\t{url}")
        for text in skipped:
            print(f"skipped (duplicate)\t{_title(text)}")
        print(f"Dry run: {total} items would be imported"
              + (f" (about {_duration(estimate_seconds(total))} to write)" if total >= BIG_IMPORT else ""))
        return
    number, _ = ensure_project(args)
    if run_json("project", "item-list", str(number), *_owner(args), "--limit", "1")["items"] and not args.force:
        say(args, f"Project #{number} already has items; nothing imported (use --force to import anyway)")
        emit(args, {"project": number, "dry_run": False, "imported": 0, "reason": "project already has items"})
        return
    info = status_info(args, number, [st for st, *_ in plan + linked])
    before = board_total(args, number)
    progress(args, f"Importing {total} items into project #{number}")
    created = create_items(args, info, [(t, b) for _, t, b in plan], [u for _, _, u in linked])
    set_statuses(args, info, list(zip(created, [st for st, *_ in plan + linked])))
    made = [{"id": i, "title": t, "status": st, "content": {"body": b}} for i, (st, t, b) in zip(created, plan)]
    made += [{"id": i, "title": t, "status": st, "content": {"url": u}} for i, (st, t, u) in zip(created[len(plan):], linked)]
    dates, fields = sync_dates(args, number, made, changelog)
    sections = sync_sections(args, number, made, todo)
    wait_for_items(args, number, created, before)   # last: the date writes need only the ids, and give GitHub's listing time to catch up
    say(args, f"Imported {total} items into project #{number}" + (f" ({len(dates)} dates set)" if dates else "")
        + (f" ({len(sections)} sections set)" if sections else ""))
    emit(args, {"project": number, "dry_run": False, "imported": total, **({"dates": dates} if dates else {}),
                **({"date_fields_created": fields} if fields else {}), **({"sections": sections} if sections else {})})


RANK = {"Todo": 0, "In Progress": 1, "Done": 2}
CHANGELOG_TITLE = re.compile(r"^\[(\d+\.\d+(?:\.\d+)?|Unreleased)\] ")


def _item_url(item):
    """URL of a linked issue/PR item, or None for draft items."""
    content = item.get("content") or {}
    if content.get("url"):
        return content["url"]
    if content.get("number") and content.get("repository"):
        kind = "pull" if content.get("type") == "PullRequest" else "issues"
        return f"https://github.com/{content['repository']}/{kind}/{content['number']}"
    return None


def sync_plan(args, items=None):
    """Compare CHANGELOG.md, TODO.md and the repo's issues/PRs with the project's items.

    Returns a namespace with: number; adds [(status, title, body)]; link_adds [(status, title, url)];
    moves [(item, from, to, title)] where the board is behind; ahead [(item, board, file, title)] where
    the board is ahead (never acted on); orphans, the board's draft items that match no file entry; ticks
    [(item, todo text, todo status)] for TODO entries still open although their board item is Done.
    `items` are the board's items when the caller has read them already (reading costs API points per item).
    """
    changelog, todo, linked = load_sources(args)
    extras = load_extras(args, changelog)
    if not changelog and not todo and not linked and not extras:
        raise GhError("nothing to compare: no CHANGELOG.md or TODO.md in the current directory, and no issues or PRs")
    plan, skipped = build_plan(changelog, todo)
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    number = project["number"]
    existing = items if items is not None else board_items(args, number)   # `items`: already read, so not read twice

    # (status, title, body, is_changelog, can_add); skipped TODO items only advance a stale board copy
    entries = [(st, t, body, bool(CHANGELOG_TITLE.match(t)), True) for st, t, body in plan]
    entries += [("Done", _title(text), "", False, False) for text in skipped]
    # opt-in sources (code comments, tags) match by exact title only, like changelog entries, and never feed the sweep
    extra_idx = set(range(len(entries), len(entries) + len(extras)))
    entries += [(st, t, body, True, True) for st, t, body in extras]

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
                    if idx not in matched and idx not in extra_idx and status == "Done" for i in open_items),
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

    refresh = []   # [(draft item, body)]: exact-title drafts whose body the files give and the board has otherwise
    for idx, (status, title, body, is_changelog, can_add) in enumerate(entries):
        item = matched.get(idx)
        if item:
            compare(item, status)
            content = item.get("content") or {}
            if body and item["title"] == title and content.get("type") == "DraftIssue" and content.get("id") \
                    and content.get("body", "").replace("\r\n", "\n").strip() != body.replace("\r\n", "\n").strip():
                refresh.append((item, body))
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
    # items from an opt-in source that is switched off this run are not orphans, or --archive would sweep them away
    off = tuple(p for p, on in ((CODE_PREFIX, args.code_comments), (RELEASE_PREFIX, args.releases)) if not on)
    # likewise, release entries the [changelog] window leaves out are not tracked, so they are not orphans either
    kept = {v for v, _ in parse_changelog(changelog)} | {"Unreleased"} if CHANGELOG_WINDOW else None

    def outside_window(item):
        m = CHANGELOG_TITLE.match(item["title"])
        return kept is not None and bool(m) and m.group(1) not in kept
    orphans = [i for i in existing if i["id"] not in matched_ids and i["id"] not in linked_ids
               and not i["title"].startswith(off) and not outside_window(i)]
    return SimpleNamespace(number=number, adds=adds, link_adds=link_adds, moves=moves, ahead=ahead, orphans=orphans, items=existing, ticks=ticks,
                           changelog=changelog, refresh=refresh)


def refresh_bodies(args, refresh):
    """Rewrite the body of each draft in `refresh` ([(item, body)]) to the file's: written WRITE_BATCH at a time, and
    safe to repeat, so the requests run in parallel like the other field writes."""
    if refresh:
        progress(args, f"Refreshing {len(refresh)} item bodies...")
        mutate_batched(args, [
            f"updateProjectV2DraftIssue(input: {{draftIssueId: {_gql_str(item['content']['id'])}, body: {_gql_str(body)}}}) "
            "{ draftIssue { id } }" for item, body in refresh], "refreshed", repeatable=True)


def cmd_update(args):
    """Sync CHANGELOG.md, TODO.md and the repo's issues/PRs into the existing project.

    Adds items the project lacks and moves items forward (Todo -> In Progress -> Done)
    when the files or GitHub are ahead of the board. Never moves an item backwards.
    """
    p = sync_plan(args)
    if getattr(args, "only", None) != "CHANGELOG.md":
        warn_similar_todos(_read("TODO.md"))
    if getattr(args, "only", None) != "TODO.md":
        warn_duplicate_headings(p.changelog)
    warn_context_dependent(p.changelog if getattr(args, "only", None) != "TODO.md" else "",
                           _read("TODO.md") if getattr(args, "only", None) != "CHANGELOG.md" else "")
    adds, link_adds, moves, number = p.adds, p.link_adds, p.moves, p.number
    refresh = p.refresh if args.refresh_bodies else []
    report = {"project": number, "dry_run": bool(args.dry_run),
              "added": [_item(st, t) for st, t, _ in adds] + [_item(st, _title(t), u) for st, t, u in link_adds],
              "moved": [{"title": t, "from": old, "to": new} for _, old, new, t in moves],
              **({"refreshed": [{"title": item["title"]} for item, _ in refresh]} if args.refresh_bodies else {})}
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
        for item, _ in refresh:
            print(f"refresh\tbody\t{item['title']}")
        print(f"Dry run: {len(adds) + len(link_adds)} to add, {len(moves)} to move"
              + (f", {len(refresh)} bodies to refresh" if args.refresh_bodies else "") + f" in project #{number}")
        return
    if not adds and not link_adds and not moves:
        refresh_bodies(args, refresh)
        dates, fields = sync_dates(args, number, p.items, p.changelog)
        sections = sync_sections(args, number, p.items, _read("TODO.md"))
        say(args, f"Project #{number} is up to date" + (f" ({len(dates)} dates set)" if dates else "")
            + (f" ({len(sections)} sections set)" if sections else "") + (f" ({len(refresh)} bodies refreshed)" if refresh else ""))
        emit(args, {**report, **({"dates": dates} if dates else {}), **({"date_fields_created": fields} if fields else {}),
                    **({"sections": sections} if sections else {})})
        return
    info = status_info(args, number, [st for st, *_ in adds + link_adds] + [new for _, _, new, _ in moves])
    before = board_total(args, number)
    progress(args, f"Updating project #{number}: {len(adds) + len(link_adds)} to add, {len(moves)} to move")
    created = create_items(args, info, [(t, b) for _, t, b in adds], [u for _, _, u in link_adds])
    set_statuses(args, info, list(zip(created, [st for st, *_ in adds + link_adds]))
                 + [(item["id"], new) for item, _, new, _ in moves])
    made = [{"id": i, "title": t, "status": st, "content": {"body": b}} for i, (st, t, b) in zip(created, adds)]
    made += [{"id": i, "title": t, "status": st, "content": {"url": u}} for i, (st, t, u) in zip(created[len(adds):], link_adds)]
    moved = {item["id"]: new for item, _, new, _ in moves}
    board = [{**i, "status": moved.get(i["id"], i.get("status"))} for i in p.items] + made   # every item, as it now stands
    refresh_bodies(args, refresh)
    dates, fields = sync_dates(args, number, board, p.changelog)
    sections = sync_sections(args, number, board, _read("TODO.md"))
    wait_for_items(args, number, created, before)
    say(args, f"Updated project #{number}: {len(adds) + len(link_adds)} added, {len(moves)} moved"
        + (f", {len(refresh)} bodies refreshed" if refresh else "")
        + (f", {len(dates)} dates set" if dates else "") + (f", {len(sections)} sections set" if sections else ""))
    emit(args, {**report, **({"dates": dates} if dates else {}), **({"date_fields_created": fields} if fields else {}),
                **({"sections": sections} if sections else {})})


def diff_summary(p):
    """(duplicates, orphans, counts) for a sync_plan result; counts is (files-only, github-only, board-only,
    behind, ahead, duplicates)."""
    dupes = find_duplicates(p.items)
    extra_ids = {extra["id"] for _, extra in dupes}
    orphans = [i for i in p.orphans if i["id"] not in extra_ids]  # a duplicate is reported once, as a duplicate
    return dupes, orphans, (len(p.adds), len(p.link_adds), len(orphans), len(p.moves), len(p.ahead), len(dupes))


CHECK_FAILED = 2  # --diff --check exit status when the board has drifted (1 is reserved for errors)


def cmd_diff(args):
    """Show every difference between the project's items and CHANGELOG.md / TODO.md / issues / PRs. Read-only.

    Returns CHECK_FAILED under --check when there is any difference, else 0 (for CI and pre-commit hooks).
    """
    p = sync_plan(args)
    dupes, orphans, counts = diff_summary(p)
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


RELEASE_LISTS = ("release", "releases")  # --list releases: GitHub's releases (or, with --local, CHANGELOG.md's versions)


def _list_releases(args):
    """--list releases: the repo's GitHub releases, newest first, one per line: tag, date, flags (latest, draft,
    pre-release) and name. With --local, the dated versions in CHANGELOG.md instead (version, date, entries): no
    GitHub, so comparing the two shows which versions have not been released."""
    if args.local:
        text = _read("CHANGELOG.md")
        rows = [{"version": v, "date": d, "entries": len([l for l in changelog_section(text, v).splitlines() if l.startswith("- ")])}
                for v, d in changelog_release_dates(text).items()]
        if args.json:
            emit(args, rows)
            return
        for r in rows:
            print(f"{r['version']}\t{r['date']}\t{r['entries']}")
        return
    repo = git_repo()
    if not repo:
        raise GhError("--list releases must be run inside a git repo with a github.com origin remote")
    slug = "/".join(repo)
    found = json.loads(run("release", "list", "--repo", slug, "--limit", str(LINKED_LIMIT), "--json",
                           "tagName,name,isDraft,isPrerelease,isLatest,publishedAt,createdAt") or "[]")
    _warn_if_full(f"the release list for {slug}", len(found), LINKED_LIMIT)
    rows = [{"tag": r["tagName"], "name": r.get("name") or r["tagName"], "date": (r.get("publishedAt") or r.get("createdAt") or "")[:10],
             "latest": bool(r.get("isLatest")), "draft": bool(r.get("isDraft")), "prerelease": bool(r.get("isPrerelease")),
             "url": f"https://github.com/{slug}/releases/tag/{r['tagName']}"} for r in found]
    if args.json:
        emit(args, rows)
        return
    for r in rows:
        flags = ",".join(f for f, on in (("latest", r["latest"]), ("draft", r["draft"]), ("pre-release", r["prerelease"])) if on)
        print(f"{r['tag']}\t{r['date']}\t{flags or '-'}\t{r['name']}")


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
    if args.list in RELEASE_LISTS:
        return _list_releases(args)
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


def render_issues(rows, project, owner, kind="issues", pending=()):
    """ISSUES.md (or PRS.md) text for `rows` ([{status, number, repository, title, url, labels, assignees}]), grouped by board status.

    `pending` is [(title, section, labels)] of local-only entries (no link yet, from `--open --local`), listed
    after the board's rows in their section. `project` None means the file is being created locally."""
    _, _, heading, noun = LINKED_EXPORTS[kind]
    multi = len({r["repository"] for r in rows}) > 1
    origin = f"from project #{project} ({owner})" if project else "locally"
    out = [f"<!-- Generated by kopi --export {kind} {origin}. Edits are overwritten by the next export, except "
           "entries without a link (added with --open --local), which stay until an issue with that title exists; "
           "delete this line to stop kopi overwriting the file. -->", "", f"# {heading}", ""]
    if not rows and not pending:
        return "\n".join(out + [f"_No {noun} in the project._", ""])
    for status in (*ISSUE_SECTIONS, None):
        group = [r for r in rows if (r["status"] if r["status"] in ISSUE_SECTIONS else None) == status]
        extra = [p for p in pending if (p[1] if p[1] in ISSUE_SECTIONS else "Todo") == status]
        if not group and not extra:
            continue
        out += [f"## {status or 'No status'}", ""]
        for r in group:
            ref = f"{r['repository']}#{r['number']}" if multi else f"#{r['number']}"
            labels = f" ({', '.join(r['labels'])})" if r["labels"] else ""
            people = "".join(f" @{a}" for a in r.get("assignees") or [])
            out.append(f"- [{ref}]({r['url']}) {r['title']}{labels}{people}")
        out += [local_issue_line(title, labels) for title, _, labels in extra]
        out.append("")
    return "\n".join(out)


def local_issue_line(title, labels):
    """The ISSUES.md bullet for an entry with no issue yet: `- Title` or `- Title (labels: a, b)`."""
    return f"- {title}" + (f" (labels: {', '.join(labels)})" if labels else "")


def linked_rows(items, kind):
    """[{status, number, repository, title, url, labels, assignees}] for the board items of one kind (issues or prs)."""
    ctype = LINKED_EXPORTS[kind][1]
    return [{"status": i.get("status"), "number": c.get("number"), "repository": c.get("repository", ""),
             "title": i["title"], "url": _item_url(i),
             "labels": [l if isinstance(l, str) else l.get("name", "") for l in (i.get("labels") or [])],
             "assignees": [a if isinstance(a, str) else a.get("login", "") for a in (i.get("assignees") or [])]}
            for i in items if (c := i.get("content") or {}).get("type") == ctype]


def apply_linked_labels(args, kind, rows, old):
    """Add to GitHub the labels the existing ISSUES.md / PRS.md text `old` lists for a linked entry that the board's
    row lacks, and put them in `rows` so the rewritten file shows both. Labels are only ever added, never removed (a
    label missing from the file may just mean it was added on GitHub since), and labels the repo does not have are
    warned about, not created. Returns [{number, repository, add}]; nothing is written under --dry-run."""
    by_url = {r["url"]: r for r in rows if r["url"]}
    repo_labels, changes, unknown = {}, [], set()
    for e in scan_issue_lines(old):
        row = by_url.get(e["url"])
        if not row:
            continue
        slug = row["repository"]
        have = {l.casefold() for l in row["labels"]}
        wanted = [l for l in dict.fromkeys(e["labels"]) if l.casefold() not in have]
        if not wanted:
            continue
        if slug not in repo_labels:
            found = json.loads(run("label", "list", "--repo", slug, "--json", "name", "--limit", str(LINKED_LIMIT)) or "[]")
            repo_labels[slug] = {r["name"].casefold(): r["name"] for r in found}
        add = [repo_labels[slug][l.casefold()] for l in wanted if l.casefold() in repo_labels[slug]]
        unknown.update(l for l in wanted if l.casefold() not in repo_labels[slug])
        if not add:
            continue
        if not args.dry_run:
            run("issue" if kind == "issues" else "pr", "edit", str(row["number"]), "--repo", slug, "--add-label", ",".join(add))
        row["labels"] = row["labels"] + add
        changes.append({"number": row["number"], "repository": slug, "add": add})
    if unknown:
        print(f"warning: labels not in the repo, so not applied: {', '.join(sorted(unknown))}", file=sys.stderr)
    return changes


def write_linked_file(args, kind, number, rows, strict):
    """Write ISSUES.md / PRS.md from `rows` (not under --dry-run). Returns {file, changed, skipped}.

    A file kopi did not generate (no marker line) is never overwritten without --force: that is an error when
    `strict` (an explicit --export), else it is skipped with a reason (a side effect of --squash / --sync)."""
    filename = LINKED_EXPORTS[kind][0]
    old = _read(filename)
    # entries added with --open --local have no link: keep them until a board row with that title exists
    have = {r["title"].casefold() for r in rows}
    pending = [(e["title"], e["section"], e["labels"]) for e in scan_issue_lines(old)
               if not e["url"] and e["title"].casefold() not in have] if old and kind == "issues" else []
    if old and f"Generated by kopi --export {kind}" not in old.splitlines()[0] and not args.force:
        if strict:
            raise GhError(f"{filename} exists and was not generated by kopi; use --force to overwrite it")
        return {"file": filename, "changed": False, "skipped": "not generated by kopi (use --force to overwrite it)"}
    added = apply_linked_labels(args, kind, rows, old) if getattr(args, "apply_labels", False) and old else None
    text = render_issues(rows, number, args.owner, kind, pending)
    changed = text != old
    if changed and not args.dry_run:
        with open(filename, "w") as f:
            f.write(text)
    return {"file": filename, "changed": changed, "skipped": None, **({"labels_added": added} if added is not None else {})}


def _export_issues(args):
    """--export issues|prs: write the project's issue (or pull request) items, not the other or drafts, to
    ISSUES.md (or PRS.md), replacing it.

    A snapshot grouped by board status. An existing file that kopi did not generate (no marker line) is
    left alone unless --force is given.
    """
    kind = args.export
    filename, _, _, noun = LINKED_EXPORTS[kind]
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    rows = linked_rows(board_items(args, project["number"]), kind)
    written = write_linked_file(args, kind, project["number"], rows, strict=True)
    changed = written["changed"]
    for c in written.get("labels_added", []):
        say(args, f"{'would add' if args.dry_run else 'added'} label(s) {', '.join(c['add'])} to #{c['number']}")
    if args.json:
        emit(args, {"project": project["number"], "dry_run": bool(args.dry_run), "file": filename,
                    "changed": changed, kind: rows, **({"labels_added": written["labels_added"]} if "labels_added" in written else {})})
        return
    if args.dry_run:
        for r in rows:
            print(f"export\t{r['status'] or 'none'}\t#{r['number']}\t{r['title']}")
    verb = "would be written" if args.dry_run else "written"
    print(("Dry run: " if args.dry_run else "") + (f"{len(rows)} {noun} {verb} to {filename}" if changed
                                                  else f"{filename} is up to date ({len(rows)} {noun})"))


LABELS_SUFFIX = re.compile(r"\s+\(labels?:\s*([^()]*)\)$", re.I)


def scan_issue_lines(text):
    """[{idx, title, url, number, section, labels}] for each bullet in ISSUES.md text, in file order.

    `idx` is the line index; `url` / `number` are None for entries with no link. Accepts kopi's own export lines
    (`- [#3](url) Title (bug, docs) @alice`, whose trailing parenthesis is the label list and `@names` the assignees) and hand-written ones
    (`- Title`, `- [ ] Title`, `- Title (labels: bug, docs)`). `section` is the nearest `## ` heading, or None.
    """
    entries, section = [], None
    for idx, line in enumerate(text.splitlines()):
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        m = ISSUE_LINE.match(line)
        if not m:
            continue
        ref, url, title = m.group(1), m.group(2), m.group(3)
        labels = []
        if url and (people := re.search(r"(\s+@[\w.-]+)+$", title)):   # kopi's export puts the assignees last
            title = title[:people.start()]
        found = re.search(r"\s+\(([^()]*)\)$", title) if url else LABELS_SUFFIX.search(title)
        if found:
            labels = [l.strip() for l in found.group(1).split(",") if l.strip()]
            title = title[:found.start()]
        number = int(n.group(1)) if url and ref and (n := re.search(r"#(\d+)", ref)) else None
        entries.append({"idx": idx, "title": title, "url": url or None, "number": number, "section": section, "labels": labels})
    return entries


def parse_issues_file(text):
    """[(title, url, section, labels)] for each distinct title in ISSUES.md text (see scan_issue_lines)."""
    entries, seen = [], set()
    for e in scan_issue_lines(text):
        if e["title"].casefold() in seen:
            continue
        seen.add(e["title"].casefold())
        entries.append((e["title"], e["url"] or "", e["section"], e["labels"]))
    return entries


# --open / --reopen / --close with --local: edit ISSUES.md only, no GitHub.
def _insert_in_section(lines, section, bullet):
    """Add `bullet` at the end of `## section` in `lines` (created in Todo / In Progress / Done order if missing)."""
    lines[:] = [l for l in lines if not re.fullmatch(r"_No \w[\w ]* in the project\._", l)]
    heading = f"## {section}"
    idx = next((i for i, l in enumerate(lines) if l.strip() == heading), None)
    if idx is None:
        order = [*ISSUE_SECTIONS, "No status"]
        later = order[order.index(section) + 1:] if section in order else []
        pos = next((i for i, l in enumerate(lines) if l.startswith("## ") and l[3:].strip() in later), len(lines))
        block = ([""] if pos and lines[pos - 1] != "" else []) + [heading, "", bullet, ""]
        lines[pos:pos] = block
        return
    end = next((i for i in range(idx + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    last = max((i for i in range(idx + 1, end) if ISSUE_LINE.match(lines[i])), default=None)
    pos = last + 1 if last is not None else idx + 1
    if last is None:
        if pos >= len(lines) or lines[pos] != "":
            lines.insert(pos, "")
        pos += 1
    lines.insert(pos, bullet)
    if last is None and (pos + 1 >= len(lines) or lines[pos + 1] != ""):
        lines.insert(pos + 1, "")


def _local_issue_rows(entries):
    """find_issue() rows for ISSUES.md entries: Done is closed, anything else open."""
    return [{"number": e["number"], "title": e["title"], "url": e["url"] or "", "idx": e["idx"], "section": e["section"],
             "state": "CLOSED" if e["section"] == "Done" else "OPEN"} for e in entries]


def _write_issues_file(args, lines):
    text = "\n".join(lines).rstrip("\n") + "\n"
    if not args.dry_run:
        with open(ISSUES_FILE, "w") as f:
            f.write(text)
    return text


def _local_move(args, flag, text, state, target, verb, past, key):
    """--close/--reopen --local: move the matching ISSUES.md entry between sections. `state` is the one the entry
    must be in to be changed; one already in the target is reported and left alone."""
    old = _read(ISSUES_FILE)
    if not old:
        raise GhError(f"no {ISSUES_FILE} in the current directory")
    lines = old.splitlines()
    row = find_issue(_local_issue_rows(scan_issue_lines(old)), text, state, ISSUES_FILE, flag)
    report = {"file": ISSUES_FILE, "dry_run": bool(args.dry_run), key: []}
    label = f"#{row['number']} {row['title']}" if row["number"] else row["title"]
    if row["state"] != state:
        say(args, f"Already {'open' if target != 'Done' else 'closed'}: {label}")
    else:
        bullet = lines[row["idx"]]
        del lines[row["idx"]]
        _insert_in_section(lines, target, bullet)
        _write_issues_file(args, lines)
        report[key].append({"title": row["title"], **({"number": row["number"]} if row["number"] else {}),
                            **({"url": row["url"]} if row["url"] else {})})
        say(args, f"{'Would ' + verb if args.dry_run else past} in {ISSUES_FILE}: {label} (local only)")
    emit(args, report)


def cmd_close_local(args):
    """--close ISSUE --local: move the ISSUES.md entry to `## Done`. GitHub and the board are not touched; the next
    --export issues / --squash rebuilds linked entries from the board."""
    _local_move(args, "--close", args.close, "OPEN", "Done", "close", "Closed", "closed")


def cmd_reopen_local(args):
    """--reopen ISSUE --local: move the ISSUES.md entry from `## Done` back to `## Todo`. Local only."""
    _local_move(args, "--reopen", args.reopen, "CLOSED", "Todo", "reopen", "Reopened", "reopened")


def cmd_open_local(args):
    """--open TITLE --local: add `- TITLE` (with `(labels: ...)` for --label) to `## Todo` in ISSUES.md, creating the
    file if needed. No issue is created: --import issues creates it on GitHub later, and --export issues / --squash
    keep the entry until an issue with that title exists. Refuses an open duplicate title unless --force."""
    title = args.open.strip()
    if not title:
        raise GhError("--open needs an issue title")
    old = _read(ISSUES_FILE)
    labels = args.label or []
    entries = scan_issue_lines(old) if old else []
    dup = next((e for e in entries if e["section"] != "Done" and e["title"].casefold() == title.casefold()), None)
    if dup and not args.force:
        raise GhError(f"{ISSUES_FILE} already has an open entry with that title (use --force to add another)")
    if old:
        lines = old.splitlines()
        _insert_in_section(lines, "Todo", local_issue_line(title, labels))
    else:
        lines = render_issues([], None, None, "issues", [(title, "Todo", labels)]).splitlines()
    _write_issues_file(args, lines)
    report = {"file": ISSUES_FILE, "dry_run": bool(args.dry_run), "created": {"title": title, "labels": labels}}
    say(args, (f"open\t{title}\t-> {ISSUES_FILE} (local only)" if args.dry_run
               else f"Added to {ISSUES_FILE}: {title} (local only; --import issues creates it on GitHub)"))
    emit(args, report)


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
    changelog = [e for _, e in parse_changelog(_read("CHANGELOG.md"), raw=True)] if status == "Done" else []
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
        dates = stamp_dates(args, number, item, target)
        for d in dates:
            say(args, f"Set {d['field']} {d['date']}")
        if dates:
            report["dates"] = dates
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


def list_issues(slug):
    """[{number, title, state, url}] for every issue in `slug` (open and closed)."""
    rows = json.loads(run("issue", "list", "--repo", slug, "--state", "all", "--limit", str(LINKED_LIMIT),
                          "--json", "number,title,state,url") or "[]")
    _warn_if_full(f"the issue list for {slug}", len(rows), LINKED_LIMIT)
    return rows


def find_issue(rows, text, state, slug, flag):
    """The one issue `text` names: a number (`12`, `#12`) or issue URL (any state), or text matching exactly one
    issue of `state` ("OPEN" / "CLOSED") by title, case-insensitive (an exact title wins over partial matches).
    GhError listing the candidates if it is not exactly one."""
    text = text.strip()
    if m := re.fullmatch(r"#?(\d+)", text):
        hits = [r for r in rows if r["number"] == int(m.group(1))]
    elif text.startswith(("http://", "https://")):
        hits = [r for r in rows if r["url"].rstrip("/") == text.rstrip("/")]
    else:
        hits = [r for r in rows if r["state"] == state and text.lower() in r["title"].lower()]
        exact = [r for r in hits if r["title"].lower() == text.lower()]
        hits = exact if len(hits) > 1 and len(exact) == 1 else hits
    if len(hits) != 1:
        lines = "".join(f"\n  #{r['number']}\t{r['title']}" for r in hits[:10])
        raise GhError(f"{flag} {text!r} matched {len(hits)} issues in {slug}"
                      + (", expected exactly one; use the number for one of:" + lines if hits else ""))
    return hits[0]


def cmd_reopen(args):
    """--reopen ISSUE: reopen a closed issue in the current repo and put it back on the project as Todo.

    ISSUE is a number, an issue URL, or text matching exactly one closed issue's title. An issue that is already
    open is left alone. On the board, the issue's item is moved to Todo if it is Done or has no status (one that is
    already Todo or In Progress stays put), and the issue is added as Todo if it is not on the board. With no
    project the issue is still reopened. TODO.md is not touched.
    """
    repo = git_repo()
    if not repo:
        raise GhError("--reopen must be run inside a git repo with a github.com origin remote")
    slug = "/".join(repo)
    issue = find_issue(list_issues(slug), args.reopen, "CLOSED", slug, "--reopen")
    info = {"number": issue["number"], "title": issue["title"], "url": issue["url"]}
    report = {"repo": slug, "dry_run": bool(args.dry_run), "reopened": [], "board": {"action": "skipped"}}
    if issue["state"] == "OPEN":
        say(args, f"Already open: #{issue['number']} {issue['title']}")
        emit(args, report)
        return
    project, _ = find_project(args, repo)
    item = None
    if project:
        item = next((i for i in board_items(args, project["number"]) if _item_url(i) == issue["url"]), None)
        if item and item.get("status") in ("Todo", "In Progress"):
            action = "unchanged"
        else:
            action = "moved" if item else "added"
        report["board"] = {"project": project["number"], "action": action}
    else:
        print(f"warning: no project for this repo under {args.owner}; the issue is reopened but not on a board", file=sys.stderr)
    report["reopened"].append(info)
    if args.dry_run:
        say(args, f"reopen\t#{issue['number']}\t{issue['title']}" + (f"\tboard: {report['board']['action']}" if project else ""))
        emit(args, report)
        return
    set_status = _status_setter(args, project["number"], ["Todo"]) if project and report["board"]["action"] != "unchanged" else None
    run("issue", "reopen", str(issue["number"]), "--repo", slug)
    if set_status and item:
        set_status(item["id"], "Todo")
    elif set_status:
        before = board_total(args, project["number"])
        item_id = run_json("project", "item-add", str(project["number"]), *_owner(args), "--url", issue["url"])["id"]
        set_status(item_id, "Todo")
        wait_for_items(args, project["number"], [item_id], before)
    say(args, f"Reopened #{issue['number']}: {issue['title']}"
        + (f" (board: {report['board']['action']})" if project else ""))
    emit(args, report)


def cmd_open(args):
    """--open TITLE: create a new issue in the current repo and add it to the project as Todo.

    The counterpart of --close. Optional --body and --label (repeatable; labels must already exist in the repo).
    Refuses when an open issue already has the same title (case-insensitive) unless --force. The project and its
    Todo status are checked first, so a missing project does not leave a stray issue behind. TODO.md is not
    touched.
    """
    title = args.open.strip()
    if not title:
        raise GhError("--open needs an issue title")
    repo = git_repo()
    if not repo:
        raise GhError("--open must be run inside a git repo with a github.com origin remote")
    slug = "/".join(repo)
    project, _ = find_project(args, repo)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    number = project["number"]
    report = {"project": number, "repo": slug, "dry_run": bool(args.dry_run), "created": None}
    if not args.force:
        rows = json.loads(run("issue", "list", "--repo", slug, "--state", "open", "--limit", str(LINKED_LIMIT),
                              "--json", "number,title,url") or "[]")
        dup = next((r for r in rows if r["title"].casefold() == title.casefold()), None)
        if dup:
            raise GhError(f"an open issue with that title already exists: #{dup['number']} {dup['url']} (use --force to create another)")
    if args.dry_run:
        say(args, f"open\t{title}\t-> new issue in {slug}, added to project #{number} as Todo")
        emit(args, report)
        return
    set_status = _status_setter(args, number, ["Todo"])  # fails early if the board has no Todo option
    cmd = ["issue", "create", "--repo", slug, "--title", title, "--body", args.body or ""]
    for label in args.label or []:
        cmd += ["--label", label]
    url = run(*cmd).strip().splitlines()[-1]
    before = board_total(args, number)
    item_id = run_json("project", "item-add", str(number), *_owner(args), "--url", url)["id"]
    set_status(item_id, "Todo")
    wait_for_items(args, number, [item_id], before)
    report["created"] = {"title": title, "url": url, "number": int(url.rstrip("/").rsplit("/", 1)[-1]), "item_id": item_id}
    say(args, f"Opened {slug}#{report['created']['number']}: {url} (added to project #{number} as Todo)")
    emit(args, report)


def cmd_close(args):
    """--close ISSUE: close one issue in the current repo on GitHub.

    ISSUE is a number (`12` or `#12`), an issue URL, or text matching exactly one open issue's title
    (case-insensitive; an exact title wins over partial matches). An issue that is already closed is left
    alone. Only the issue changes: the board catches up on the next --update, which moves a closed issue's
    item to Done.
    """
    repo = git_repo()
    if not repo:
        raise GhError("--close must be run inside a git repo with a github.com origin remote")
    slug = "/".join(repo)
    issue = find_issue(list_issues(slug), args.close, "OPEN", slug, "--close")
    report = {"repo": slug, "dry_run": bool(args.dry_run), "closed": []}
    if issue["state"] != "OPEN":
        say(args, f"Already closed: #{issue['number']} {issue['title']}")
    elif args.dry_run:
        report["closed"].append({"number": issue["number"], "title": issue["title"], "url": issue["url"]})
        say(args, f"close\t#{issue['number']}\t{issue['title']}")
    else:
        run("issue", "close", str(issue["number"]), "--repo", slug)
        report["closed"].append({"number": issue["number"], "title": issue["title"], "url": issue["url"]})
        say(args, f"Closed #{issue['number']}: {issue['title']}")
    emit(args, report)


def changelog_releases(text, limit=5):
    """[{version, date, entries}] for the newest `## [version] - date` sections of a changelog, in file order
    (the newest first, by Keep a Changelog convention). `date` is None when the heading has none."""
    out = []
    for line in text.splitlines():
        heading = changelog_heading(line)
        if heading:
            out.append({"version": heading[0], "date": heading[1], "entries": 0})
        elif out and line.startswith("- "):
            out[-1]["entries"] += 1
    return [r for r in out if r["entries"]][:limit]


def status_counts(items):
    """(work items, counts) for board items, with counts {todo, in_progress, done, other, history}.

    The board's `[version] ...` changelog entries are history, not work, so they are counted apart."""
    history = [i for i in items if CHANGELOG_TITLE.match(i["title"])]
    work = [i for i in items if i not in history]
    counts = {"todo": 0, "in_progress": 0, "done": 0, "other": 0, "history": len(history)}
    for i in work:
        counts[{"Todo": "todo", "In Progress": "in_progress", "Done": "done"}.get(i.get("status"), "other")] += 1
    return work, counts


TREND_MONTHS = 12


def _stats(values):
    """{n, median, mean, longest} of `values` (days), or None when there are none."""
    if not values:
        return None
    return {"n": len(values), "median": statistics.median(values), "mean": round(statistics.mean(values), 1), "longest": max(values)}


def _day(value):
    try:
        return datetime.date.fromisoformat(value[:10]) if value else None
    except (TypeError, ValueError):
        return None


def build_trends(items, release_dates, names, today, months=TREND_MONTHS):
    """How fast work moves, from the board's date fields and the changelog's release dates.

    {months: [{month, releases, completed}] (the last `months` calendar months, oldest first), cycle_days, release_interval_days}.
    `completed` counts work items (not changelog entries) by their Completed date, `releases` the changelog versions by
    release date (every version, even several on one day); `cycle_days` is Started to Completed for work items that have both, and `release_interval_days` the gap
    between consecutive release days (both {n, median, mean, longest}, or None when there is nothing to measure)."""
    keys = [f"{y:04d}-{m + 1:02d}" for y, m in (divmod(today.year * 12 + today.month - 1 - k, 12) for k in range(months - 1, -1, -1))]
    completed, releases, spans = dict.fromkeys(keys, 0), dict.fromkeys(keys, 0), []
    for item in items:
        if CHANGELOG_TITLE.match(item["title"]):
            continue
        end, start = _day(item.get(names["completed"].lower())), _day(item.get(names["started"].lower()))
        if end and end.strftime("%Y-%m") in completed:
            completed[end.strftime("%Y-%m")] += 1
        if end and start and end >= start:
            spans.append((end - start).days)
    versions = [d for d in map(_day, release_dates.values()) if d]
    for d in versions:
        if d.strftime("%Y-%m") in releases:
            releases[d.strftime("%Y-%m")] += 1
    days = sorted(set(versions))
    return {"months": [{"month": k, "releases": releases[k], "completed": completed[k]} for k in keys],
            "cycle_days": _stats(spans), "release_interval_days": _stats([(b - a).days for a, b in zip(days, days[1:])])}


def build_milestones(items):
    """[{title, due, done, total}] for the milestones of the board's issues and PRs, soonest due date first (no date last).

    `due` is YYYY-MM-DD or None; an item counts as done when its board status is Done."""
    found = {}
    for item in items:
        ms = item.get("milestone")
        title = ms if isinstance(ms, str) else (ms or {}).get("title")
        if not title:
            continue
        due = None if isinstance(ms, str) else (ms.get("dueOn") or "")[:10] or None
        row = found.setdefault(title, {"title": title, "due": due, "done": 0, "total": 0})
        row["total"] += 1
        row["done"] += item.get("status") == "Done"
    return sorted(found.values(), key=lambda m: (m["due"] is None, m["due"] or "", m["title"]))


def build_report(args):
    """The data behind --report: counts by status, by kind of item, open work, recent releases and how far the
    board is from the files."""
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    items = board_items(args, project["number"])
    work, counts = status_counts(items)
    tracked = counts["todo"] + counts["in_progress"] + counts["done"]
    versions = {m.group(1) for m in (CHANGELOG_TITLE.match(i["title"]) for i in items) if m}
    kinds = [(i.get("content") or {}).get("type") for i in work]
    try:
        differences = sum(diff_summary(sync_plan(args, items=items))[2])
    except GhError:  # no CHANGELOG.md / TODO.md to compare with
        differences = None
    return {"project": {"number": project["number"], "title": project["title"], "url": project.get("url")},
            "counts": counts, "percent_done": round(100 * counts["done"] / tracked) if tracked else None,
            "history": {"entries": counts["history"], "releases": len(versions)},
            "by_type": {"drafts": kinds.count("DraftIssue"), "issues": kinds.count("Issue"),
                        "prs": kinds.count("PullRequest")},
            "in_progress": [i["title"] for i in work if i.get("status") == "In Progress"],
            "todo": [i["title"] for i in work if i.get("status") == "Todo"],
            "milestones": build_milestones(items),
            "trends": build_trends(items, changelog_release_dates(_read("CHANGELOG.md")), date_field_names(args), datetime.date.today()),
            "releases": changelog_releases(_read("CHANGELOG.md")), "differences": differences}


def _n(count, noun):
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _days(value):
    return f"{value:g} day" + ("" if value == 1 else "s")


def render_report(r, markdown=False):
    """The --report text (plain or Markdown) for build_report data."""
    c, p = r["counts"], r["project"]
    pct = f"{r['percent_done']}% done" if r["percent_done"] is not None else "none yet"
    shipped = r["history"]["entries"]
    h = (lambda t: f"## {t}") if markdown else (lambda t: t + ":")
    bullet = "- " if markdown else "  - "
    out = [f"# {p['title']} (project #{p['number']})" if markdown else f"Project #{p['number']}: {p['title']}", p["url"] or "", ""]
    tracked = c["done"] + c["in_progress"] + c["todo"] + c["other"]
    out += [h("Progress"), f"Tracked work: {pct}" + (f" ({c['done']} done, {c['in_progress']} in progress, {c['todo']} todo"
            + (f", {c['other']} without a status" if c["other"] else "") + ")" if tracked else "")]
    if shipped:   # a changelog is the record of what shipped, so it is not folded into the percentage
        out.append(f"Shipped: {_n(shipped, 'changelog entry').replace('entrys', 'entries')} in {_n(r['history']['releases'], 'release')}")
    out.append("")
    t = r["by_type"]
    out += [h("Items"), f"{_n(t['drafts'], 'draft')}, {_n(t['issues'], 'issue')}, {_n(t['prs'], 'pull request')}", ""]
    if r.get("milestones"):
        out += [h("Milestones"), *(f"{bullet}{m['title']}: {m['done']} of {m['total']} done" + (f" (due {m['due']})" if m["due"] else "")
                                   for m in r["milestones"]), ""]
    for title, names in (("In progress", r["in_progress"]), ("Todo", r["todo"])):
        if names:
            out += [h(f"{title} ({len(names)})"), *(f"{bullet}{n}" for n in names[:10]),
                    *([f"{bullet}... and {len(names) - 10} more"] if len(names) > 10 else []), ""]
    if r["releases"]:
        out += [h("Recent releases"), *(f"{bullet}{x['version']}" + (f" ({x['date']})" if x["date"] else "")
                                        + f": {_n(x['entries'], 'entry').replace('entrys', 'entries')}" for x in r["releases"]), ""]
    tr = r.get("trends") or {}
    active = [m for m in tr.get("months", []) if m["releases"] or m["completed"]]
    if active or tr.get("cycle_days") or tr.get("release_interval_days"):
        out += [h(f"Trends (last {len(tr['months'])} months)"),
                *(f"{bullet}{m['month']}: {_n(m['releases'], 'release')}, {m['completed']} work item"
                  f"{'' if m['completed'] == 1 else 's'} completed" for m in active)]
        if tr.get("cycle_days"):
            s = tr["cycle_days"]
            out.append(f"Cycle time (Started to Completed): median {_days(s['median'])}, mean {_days(s['mean'])}, longest {_days(s['longest'])} ({_n(s['n'], 'item')})")
        if tr.get("release_interval_days"):
            s = tr["release_interval_days"]
            out.append(f"Between releases: median {_days(s['median'])}, mean {_days(s['mean'])}, longest {_days(s['longest'])} ({_n(s['n'], 'gap')})")
        out.append("")
    d = r["differences"]
    out += [h("Board vs files"), "not compared (no CHANGELOG.md or TODO.md)" if d is None
            else "in sync" if d == 0 else f"{_n(d, 'difference')} (see --diff)"]
    return "\n".join(out)


REPORT_MARKER = "<!-- kopi-report -->"


def post_report(args, issue, body):
    """Post `body` as a comment on issue `issue` of this repo, or edit in place the comment kopi posted there before
    (found by REPORT_MARKER), so a weekly run keeps one up-to-date status instead of piling up comments. Nothing is
    written under --dry-run. Returns {issue, url, action} with action "posted", "updated" or "unchanged"."""
    repo = git_repo()
    if not repo:
        raise GhError("--post needs a GitHub repo: no origin remote found in this directory")
    slug = "/".join(repo)
    found = run("api", f"repos/{slug}/issues/{issue}/comments", "--paginate", "--jq",
                f'.[] | select(.body | startswith("{REPORT_MARKER}")) | .id').split()
    url = f"https://github.com/{slug}/issues/{issue}"
    if found:
        path = f"repos/{slug}/issues/comments/{found[-1]}"
        if run("api", path, "--jq", ".body").rstrip("\n") == body.rstrip("\n"):
            return {"issue": issue, "url": url, "action": "unchanged"}
        if args.dry_run:
            return {"issue": issue, "url": url, "action": "updated"}
        url = run("api", "-X", "PATCH", path, "-f", f"body={body}", "--jq", ".html_url").strip() or url
        return {"issue": issue, "url": url, "action": "updated"}
    if args.dry_run:
        return {"issue": issue, "url": url, "action": "posted"}
    return {"issue": issue, "url": run("issue", "comment", str(issue), "--repo", slug, "--body", body).strip() or url, "action": "posted"}


def cmd_report(args):
    """--report [text|markdown] [--to FILE] [--post ISSUE]: a progress summary of the project. Read-only unless asked
    to write a file or post a comment; --json prints the data."""
    report = build_report(args)
    to_file = args.to not in (None, "-")
    out = {}
    if to_file:
        markdown = args.report == "markdown" or args.to.lower().endswith(".md")   # a .md file is Markdown
        if not args.dry_run:
            with open(args.to, "w") as f:
                f.write(render_report(report, markdown=markdown) + "\n")
        out["file"] = args.to
    if args.post:
        body = f"{REPORT_MARKER}\n{render_report(report, markdown=True)}\n\n_Kept up to date by kopi._"
        out["posted"] = post_report(args, args.post, body)
    if args.json:
        emit(args, {**report, **out})
        return
    if not out:
        print(render_report(report, markdown=args.report == "markdown"))
        return
    prefix = "Dry run: " if args.dry_run else ""
    if to_file:
        print(f"{prefix}report {'would be written' if args.dry_run else 'written'} to {args.to}")
    if args.post:
        p = out["posted"]
        verb = {"posted": "post", "updated": "update", "unchanged": "leave"}[p["action"]]
        done = {"posted": "Posted the report as a new comment", "updated": "Updated the report comment",
                "unchanged": "The report comment is already up to date"}[p["action"]]
        print(f"Dry run: would {verb} the report comment on #{p['issue']}" if args.dry_run and p["action"] != "unchanged"
              else f"{done} on #{p['issue']}: {p['url']}")
        if args.dry_run:
            print(body)


def _rollup_row(sub, project, owner, where):
    _, counts = status_counts(board_items(sub, project["number"]))
    return {"owner": owner, "number": project["number"], "title": project["title"], "url": project.get("url"),
            "where": where, "counts": counts}


def cmd_rollup(args):
    """--rollup [DIR ...]: status counts for several projects at once, with totals. Read-only.

    With directories (or `repos = [...]` in .kopi.toml) it looks up each repo's project from that directory's git
    origin, honouring that repo's own .kopi.toml (owner, project title, Status names) unless a flag overrides it.
    With neither, it covers every open project under the owner. Changelog entries are counted as history, not work.
    """
    dirs = args.rollup or (args.config_data.get("repos") or [])
    rows, skipped = [], []
    if dirs:
        for d in dirs:
            repo = git_repo(d) if os.path.isdir(d) else None
            if not repo:
                skipped.append({"where": d, "reason": "not a directory with a github.com origin"})
                continue
            sub = copy.copy(args)
            sub.owner = args.cli_owner or repo[0]  # an explicit --owner applies to every repo, else each repo's own
            sub.status_map = args.cli_status_map
            apply_config(sub, load_config(os.path.join(d, CONFIG_FILE)))
            if not args.cli_owner and sub.config_data.get("owner"):
                sub.owner = sub.config_data["owner"]
            project, _ = find_project(sub, repo)
            if not project:
                skipped.append({"where": d, "reason": f"no project for {repo[0]}/{repo[1]} under {sub.owner}"})
                continue
            rows.append(_rollup_row(sub, project, sub.owner, d))
    else:
        data = run_json("project", "list", *_owner(args), "--limit", str(PROJECT_LIMIT))
        rows = [_rollup_row(args, p, args.owner, "") for p in data["projects"] if not p.get("closed")]
    total = {k: sum(r["counts"][k] for r in rows) for k in ("todo", "in_progress", "done", "other", "history")}
    if args.json:
        emit(args, {"projects": rows, "total": total, "skipped": skipped})
        return
    for sk in skipped:
        print(f"warning: skipped {sk['where']}: {sk['reason']}", file=sys.stderr)

    def pct(c):
        tracked = c["todo"] + c["in_progress"] + c["done"]
        return f"{round(100 * c['done'] / tracked)}%" if tracked else "-"
    print("OWNER\tNUMBER\tTITLE\tTODO\tIN PROGRESS\tDONE\tDONE%\tHISTORY")
    for r in rows:
        c = r["counts"]
        print(f"{r['owner']}\t{r['number']}\t{r['title']}\t{c['todo']}\t{c['in_progress']}\t{c['done']}\t{pct(c)}\t{c['history']}")
    print(f"TOTAL\t{len(rows)} projects\t\t{total['todo']}\t{total['in_progress']}\t{total['done']}\t{pct(total)}\t{total['history']}")


REPO_ID_QUERY = "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { id } }"
PROMOTE_MUTATION = """mutation($item: ID!, $repo: ID!) {
  convertProjectV2DraftIssueItemToIssue(input: {itemId: $item, repositoryId: $repo}) {
    item { id content { ... on Issue { number title url } } } } }"""


def cmd_promote(args):
    """--promote ITEM: turn one draft board item into a real issue in the current repo.

    ITEM is an item id or text matching exactly one title. The draft is converted in place (GraphQL
    convertProjectV2DraftIssueItemToIssue), so it stays on the board with its status and fields and becomes a
    linked issue item with the same title, which `--update` and the TODO.md duplicate-title rule already
    understand. Only drafts can be promoted. Board and repo change; TODO.md is not touched.
    """
    repo = git_repo()
    if not repo:
        raise GhError("--promote must be run inside a git repo with a github.com origin remote")
    project, _ = find_project(args, repo)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    number = project["number"]
    items = board_items(args, number)
    item = next((i for i in items if i["id"] == args.promote), None) or _match_one(items, args.promote, number, "--promote")
    kind = (item.get("content") or {}).get("type") or "DraftIssue"
    if kind != "DraftIssue":
        url = _item_url(item)
        raise GhError(f"{item['title']!r} is already {'a pull request' if kind == 'PullRequest' else 'an issue'}"
                      + (f" ({url})" if url else "") + "; only draft items can be promoted")
    slug = "/".join(repo)
    report = {"project": number, "dry_run": bool(args.dry_run), "promoted": []}
    entry = {"title": item["title"], "id": item["id"]}
    if args.dry_run:
        report["promoted"].append(entry)
        say(args, f"promote\t{item['title']}\t-> new issue in {slug}")
    else:
        issue = _convert_draft(repo, _repo_node_id(repo), item)
        report["promoted"].append({**entry, "number": issue["number"], "url": issue["url"]})
        say(args, f"Promoted to {slug}#{issue['number']}: {issue['url']}")
    _annotate(args, report)
    emit(args, report)


def _annotate(args, report):
    """--annotate: add (#N) to the TODO.md entries of the items in report["promoted"]; adds report["annotated"]."""
    if not args.annotate:
        return
    report["annotated"] = annotate_todo([{"title": p["title"], "number": p.get("number")} for p in report["promoted"]],
                                        dry_run=args.dry_run)
    for a in report["annotated"]:
        say(args, f"{'annotate' if args.dry_run else 'Annotated'}\tTODO.md: {a['entry']}")


def annotate_todo(promoted, dry_run=False):
    """Append ` (#N)` to the TODO.md entry of each promoted item: [{title, number}] -> [{title, number, entry}] annotated.

    The entry is the checkbox line whose text equals the item's title (or starts with it, for a title cut to 100
    characters), else the best match at 75%+ shared words and similar length. An entry already ending in an
    issue reference is left alone. `number` None (a dry run) shows `#N`. Missing TODO.md: nothing to do."""
    text = _read("TODO.md")
    lines = text.splitlines()
    done = []
    ref = re.compile(r"\s*\(#(\d+|N)\)$")
    for item in promoted:
        title, best, exact = item["title"], (0.0, None, None), False
        for first, last in bullet_blocks(lines):
            m = re.match(r"\[[ ~xX]\] (.+?)\s*$", bullet_text(lines, first, last))
            if not m:
                continue
            idx = last   # the reference goes on the entry's last line
            entry, annotated = ref.sub("", m.group(1)), bool(ref.search(m.group(1)))
            is_exact = entry == title or (title.endswith("…") and entry.startswith(title[:-1]))
            if is_exact and annotated:
                exact, best = True, (0.0, None, None)  # already done: do not fall back to a similar, longer entry
                break
            # exact text wins outright; otherwise a similar entry must also be of similar length, so a short title
            # cannot claim a long entry that merely contains its words
            score = 2.0 if is_exact else (overlap(entry, title) if similar_length(entry, title) else 0.0)
            if not annotated and score >= 0.75 and score > best[0]:
                best = (score, idx, entry)
        if best[1] is not None and not exact:
            suffix = f" (#{item['number'] or 'N'})"
            lines[best[1]] += suffix
            done.append({"title": title, "number": item["number"], "entry": best[2] + suffix})
    if done and not dry_run:
        with open("TODO.md", "w") as f:
            f.write("\n".join(lines) + ("\n" if text.endswith("\n") else ""))
    return done


def _repo_node_id(repo):
    return graphql(REPO_ID_QUERY, owner=repo[0], name=repo[1])["data"]["repository"]["id"]


def _convert_draft(repo, repo_id, item):
    """Convert one draft item into an issue in `repo`; returns the new issue's {number, title, url}."""
    done = graphql(PROMOTE_MUTATION, item=item["id"], repo=repo_id)["data"]["convertProjectV2DraftIssueItemToIssue"]["item"]
    return done["content"]


PROMOTE_KINDS = {"todo": ("Todo",), "inprogress": ("In Progress",), "in-progress": ("In Progress",),
                 "all": ("Todo", "In Progress")}
PROMOTE_DELAY = 1  # seconds between issues: GitHub asks for a pause between content-creating requests


def promotable(items, kind="all"):
    """The open drafts --promote-all would convert: Todo / In Progress drafts, minus the board's changelog entries
    and the `[code]` / `[release]` items from the opt-in sources (history and noise, not work to track)."""
    statuses = PROMOTE_KINDS[kind]
    return [i for i in items
            if ((i.get("content") or {}).get("type") or "DraftIssue") == "DraftIssue" and i.get("status") in statuses
            and not CHANGELOG_TITLE.match(i["title"]) and not i["title"].startswith((CODE_PREFIX, RELEASE_PREFIX))]


def cmd_promote_all(args):
    """--promote-all [todo|inprogress]: turn every open draft board item into an issue in the current repo.

    See `promotable` for which drafts count. This creates many real, public issues at once, so a real run needs
    --yes; --dry-run (or omitting --yes) only lists them. Items are converted one at a time, a second apart; one
    that fails is reported and skipped, and the exit status is 1 if any failed.
    """
    repo = git_repo()
    if not repo:
        raise GhError("--promote-all must be run inside a git repo with a github.com origin remote")
    project, _ = find_project(args, repo)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    slug = "/".join(repo)
    targets = promotable(board_items(args, project["number"]), args.promote_all)
    report = {"project": project["number"], "dry_run": bool(args.dry_run), "promoted": [], "failed": []}
    if not targets:
        say(args, f"Nothing to promote in project #{project['number']}")
        emit(args, report)
        return 0
    if args.dry_run or not args.yes:
        for i in targets:
            report["promoted"].append({"title": i["title"], "id": i["id"]})
            say(args, f"promote\t{i['status']}\t{i['title']}\t-> new issue in {slug}")
        if args.dry_run:
            say(args, f"Dry run: {len(targets)} drafts would become issues in {slug}")
            _annotate(args, report)
            emit(args, report)
            return 0
        raise GhError(f"this would create {len(targets)} issues in {slug}; re-run with --yes to do it, or --dry-run to preview")
    repo_id = _repo_node_id(repo)
    for n, item in enumerate(targets):
        if n:
            time.sleep(PROMOTE_DELAY)
        try:
            issue = _convert_draft(repo, repo_id, item)
        except GhError as e:
            report["failed"].append({"title": item["title"], "id": item["id"], "error": str(e)})
            print(f"warning: could not promote {item['title']!r}: {e}", file=sys.stderr)
            continue
        report["promoted"].append({"title": item["title"], "id": item["id"], "number": issue["number"], "url": issue["url"]})
        say(args, f"Promoted to {slug}#{issue['number']}: {item['title']}")
    say(args, f"Promoted {len(report['promoted'])} drafts to issues in {slug}"
        + (f" ({len(report['failed'])} failed)" if report["failed"] else ""))
    _annotate(args, report)
    emit(args, report)
    return 1 if report["failed"] else 0


# --- --gantt: the Roadmap as a Gantt chart (Mermaid or SVG) --------------------------------------------------------
GANTT_MARKER = "Generated by kopi gantt"   # no "--": that is not allowed inside an XML (SVG) comment
GANTT_VIEWS = ("releases", "items")
GANTT_FILE = "ROADMAP.md"
GANTT_FEW_DATES = 2   # releases view: with this many release days or fewer, draw one bar per version


def gantt_view(value):
    """argparse type for --gantt: a file name here is a mistake, so point at --to instead of dumping the usage."""
    if value not in GANTT_VIEWS and ("/" in value or "." in value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a view (releases or items); to choose the output file use --to {value}")
    return value


def gantt_tasks(items, view, args, today):
    """([{section, name, start, end, state}], skipped) for a Gantt chart of the board, dates as `datetime.date`.

    Uses each item's Started / Completed dates. Done items become bars (a missing date is borrowed from the other);
    In Progress items with a Started date become `active` bars running to `today`. view "releases": one bar per
    changelog release date (releases on the same day share a bar: the earliest Started to the latest Completed), then the active items;
    view "items": one bar per item in a section per release, then "Other completed" and "In progress".
    `skipped` counts the items that had no usable date."""
    names = date_field_names(args)
    parse = lambda v: datetime.date.fromisoformat(v) if v else None
    releases, other, active, skipped = {}, [], [], 0
    for item in items:
        start, end = parse(item.get(names["started"].lower())), parse(item.get(names["completed"].lower()))
        title, status = item["title"], item.get("status")
        m = CHANGELOG_TITLE.match(title)
        if status == "Done" and (start or end):
            entry = {"name": CHANGELOG_TITLE.sub("", title), "start": start or end, "end": end or start, "state": "done"}
            if m:
                releases.setdefault(m.group(1), []).append(entry)
            else:
                other.append(entry)
        elif status == "In Progress" and start:
            active.append({"section": "In progress", "name": title, "start": start, "end": max(start, today), "state": "active"})
        else:
            skipped += 1
    ordered = sorted(releases.items(), key=lambda kv: (max(e["end"] for e in kv[1]), version_key(kv[0])))
    tasks = []
    if view == "releases":
        by_date = {}  # versions released on the same day share one bar
        for version, entries in ordered:
            by_date.setdefault(max(e["end"] for e in entries), []).append((version, entries))
        if len(by_date) <= GANTT_FEW_DATES < len(ordered):   # a few release days: grouping would hide the versions
            by_date = {i: [vg] for i, vg in enumerate(ordered)}
        for group in by_date.values():
            end = max(e["end"] for _, entries in group for e in entries)
            versions = [v for v, _ in group]
            first = versions[0] + (".x" if GROUP_VERSION.fullmatch(versions[0]) else "")   # a `[16.5]` group
            name = f"v{first}" if len(versions) == 1 else f"v{versions[0]} to v{versions[-1]} ({len(versions)} releases)"
            tasks.append({"section": "Releases", "name": name, "state": "done", "end": end,
                          "start": min(e["start"] for _, entries in group for e in entries)})
    else:
        for version, entries in ordered:
            tasks += [{**e, "section": f"v{version}"} for e in sorted(entries, key=lambda e: (e["end"], e["name"]))]
        tasks += [{**e, "section": "Other completed"} for e in sorted(other, key=lambda e: (e["end"], e["name"]))]
    return tasks + sorted(active, key=lambda t: (t["start"], t["name"])), skipped


def _mermaid_text(text, limit=70):
    """`text` made safe for a Mermaid task or section name: a colon becomes U+2236 (it would end the name), # and ;
    are entity-encoded, and it is cut to `limit` characters."""
    text = text.replace("\n", " ").strip()
    text = text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
    text = text.replace(":", "\u2236")   # looks the same, reads cleanly in the raw .mmd, and is not a separator
    return re.sub(r"[#;]", lambda m: f"#{ord(m.group())};", text)   # one pass: the entities contain # and ; themselves


def render_mermaid(title, tasks):
    """Mermaid `gantt` source for `tasks` (see gantt_tasks)."""
    first, last = min(t["start"] for t in tasks), max(t["end"] for t in tasks)
    span = (last - first).days
    out = ["gantt", f"    title {_mermaid_text(title)}", "    dateFormat YYYY-MM-DD",
           f"    axisFormat {'%Y' if span > 730 else '%b %Y' if span > 120 else '%d %b'}"]
    section = None
    for t in tasks:
        if t["section"] != section:
            section = t["section"]
            out.append(f"    section {_mermaid_text(section)}")
        until = t["end"].isoformat() if t["end"] > t["start"] else "1d"   # a one-day bar needs a duration
        out.append(f"    {_mermaid_text(t['name'])} :{t['state']}, {t['start'].isoformat()}, {until}")
    return "\n".join(out)


def _xml(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_svg(title, tasks, today, note=GANTT_MARKER + "."):
    """A standalone SVG Gantt chart for `tasks` (see gantt_tasks): section headings, one bar per task, a dated axis
    and a marker for today. Colours adapt to a dark colour scheme."""
    row, head, top, right = 22, 26, 56, 24
    label_w = min(380, max(150, 6.4 * max(len(t["name"][:60]) for t in tasks) + 24))
    width, plot_w = 1100, 1100 - label_w - right
    first, last = min(t["start"] for t in tasks), max(t["end"] for t in tasks)
    last = max(last, today) if any(t["state"] == "active" for t in tasks) else last
    days = max((last - first).days, 1)
    pad = max(days // 50, 1)
    first, days = first - datetime.timedelta(days=pad), days + 2 * pad
    x = lambda d: label_w + plot_w * (d - first).days / days
    rows, y, section = [], top, None
    for t in tasks:
        if t["section"] != section:
            section = t["section"]
            rows.append(("head", y, section, None))
            y += head
        rows.append(("task", y, t["name"], t))
        y += row
    height = y + 10
    if days > 3 * 365:
        ticks = [datetime.date(yr, 1, 1) for yr in range(first.year + 1, (first + datetime.timedelta(days=days)).year + 1)]
        step = max(1, len(ticks) // 12)
        ticks, fmt = ticks[::step], "%Y"
    elif days > 90:
        end_date = first + datetime.timedelta(days=days)
        months = [datetime.date(first.year + (first.month + k - 1) // 12, (first.month + k - 1) % 12 + 1, 1)
                  for k in range(1, (end_date.year - first.year) * 12 + end_date.month - first.month + 2)]
        step = max(1, len(months) // 12)
        ticks, fmt = [m for m in months[::step] if first <= m <= end_date], "%b %Y"
    else:
        ticks, fmt = [first + datetime.timedelta(days=k) for k in range(0, days + 1, max(1, days // 10))], "%d %b"
    out = [f"<!-- {note} -->",
           f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
           'font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="12" role="img" '
           f'aria-label="{_xml(title)}">',
           "<style>.bg{fill:#ffffff}.t{fill:#1f2328}.m{fill:#656d76}.g{stroke:#d0d7de;stroke-width:1}.d{fill:#2da44e}"
           ".a{fill:#bf8700}.td{stroke:#cf222e;stroke-width:1.5;stroke-dasharray:4 3}"
           "@media (prefers-color-scheme:dark){.bg{fill:#0d1117}.t{fill:#e6edf3}.m{fill:#8d96a0}.g{stroke:#30363d}"
           ".d{fill:#3fb950}.a{fill:#d29922}.td{stroke:#ff7b72}}</style>",
           f'<rect class="bg" width="{width}" height="{height}"/>',
           f'<text class="t" x="16" y="26" font-size="16" font-weight="600">{_xml(title)}</text>']
    for tick in ticks:
        out.append(f'<line class="g" x1="{x(tick):.1f}" y1="{top - 8}" x2="{x(tick):.1f}" y2="{height - 6}"/>'
                   f'<text class="m" x="{x(tick):.1f}" y="{top - 14}" text-anchor="middle">{_xml(tick.strftime(fmt))}</text>')
    for kind, y, label, t in rows:
        if kind == "head":
            out.append(f'<text class="t" x="16" y="{y + 16}" font-weight="600">{_xml(label)}</text>')
            continue
        shown = label if len(label) <= 60 else label[:59].rstrip() + "…"
        x0, x1 = x(t["start"]), max(x(t["end"]), x(t["start"]) + 4)
        out.append(f'<text class="t" x="28" y="{y + 15}">{_xml(shown)}</text>'
                   f'<rect class="{"a" if t["state"] == "active" else "d"}" x="{x0:.1f}" y="{y + 3}" width="{x1 - x0:.1f}" '
                   f'height="{row - 8}" rx="3"><title>{_xml(label)}: {t["start"]} to {t["end"]}</title></rect>')
    if first <= today <= first + datetime.timedelta(days=days):
        out.append(f'<line class="td" x1="{x(today):.1f}" y1="{top - 8}" x2="{x(today):.1f}" y2="{height - 6}"/>')
    return "\n".join(out + ["</svg>", ""])


def render_gantt(path, title, tasks, view, project, owner, today):
    """The text to write to `path`: SVG for .svg, raw Mermaid for .mmd, else Markdown with a ```mermaid block."""
    note = f"{GANTT_MARKER} {view} from project #{project} ({owner}). Edits are overwritten by the next run; delete this line to stop kopi overwriting the file."
    if path.lower().endswith(".svg"):
        return render_svg(title, tasks, today, note)
    chart = render_mermaid(title, tasks)
    if path.lower().endswith(".mmd"):
        return f"%% {note}\n{chart}\n"
    return f"<!-- {note} -->\n\n# {title}\n\n```mermaid\n{chart}\n```\n"


def cmd_gantt(args):
    """--gantt [releases|items] [--to FILE]: draw the project's Roadmap as a Gantt chart from the Started / Completed dates.

    FILE (default ROADMAP.md) picks the format by extension: .svg a standalone image, .mmd raw Mermaid, anything else
    Markdown with a Mermaid block (GitHub renders it). `--to -` prints it. An existing file that kopi did not generate
    (no marker line) is not overwritten without --force."""
    project, _ = find_project(args)
    if not project:
        raise GhError(f"no project for this repo under {args.owner}; run --init or --import first")
    today = datetime.date.today()
    tasks, skipped = gantt_tasks(board_items(args, project["number"]), args.gantt, args, today)
    if not tasks:
        raise GhError("no dated items to chart; run --backfill-dates (or --update) to fill in the Started / Completed dates")
    path = args.to or GANTT_FILE
    text = render_gantt(path, f"{project['title']} roadmap", tasks, args.gantt, project["number"], args.owner, today)
    changed = None
    if path == "-":
        if not args.json:
            print(text, end="")
    else:
        old = _read(path)
        if old and GANTT_MARKER not in old.splitlines()[0] and not args.force:
            raise GhError(f"{path} exists and was not generated by kopi; use --force to overwrite it")
        changed = text != old
        if changed and not args.dry_run:
            with open(path, "w") as f:
                f.write(text)
        say(args, ("Dry run: " if args.dry_run else "") + (f"{len(tasks)} bars {'would be ' if args.dry_run else ''}written to {path}"
            if changed else f"{path} is up to date ({len(tasks)} bars)") + (f"; {skipped} items without dates skipped" if skipped else ""))
    emit(args, {"project": project["number"], "view": args.gantt, "file": None if path == "-" else path, "dry_run": bool(args.dry_run),
                "changed": changed, "skipped": skipped,
                "tasks": [{**t, "start": t["start"].isoformat(), "end": t["end"].isoformat()} for t in tasks]})


# --- --release: a GitHub release at the last commit ----------------------------------------------------------------
def version_key(version):
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-+]", version))


def latest_release_version(changelog):
    """The newest dated `## [version] - date` in a changelog (latest date, then highest version), or None."""
    dated = changelog_release_dates(changelog)
    return max(dated, key=lambda v: (dated[v], version_key(v))) if dated else None


def changelog_section(changelog, version):
    """The body of a version's release heading in a changelog (up to the next release heading), stripped; "" if none."""
    body, inside = [], False
    for line in changelog.splitlines():
        heading = changelog_heading(line)
        if heading:
            if inside:
                break
            inside = heading[0] == version
        elif inside:
            body.append(line)
    return "\n".join(body).strip()


def head_commit():
    """(sha, subject) of the last commit in the current directory, or (None, None)."""
    return _git_first_line("rev-parse", "HEAD"), _git_first_line("log", "-1", "--format=%s")


def git_dirty():
    """True if the working tree has uncommitted changes."""
    try:
        proc = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def remote_tag_commit(slug, tag):
    """The commit sha a tag points at on GitHub (looking through an annotated tag), or None if it has no such tag."""
    try:
        kind, sha = run("api", f"repos/{slug}/git/ref/tags/{tag}", "--jq", '.object | "\\(.type) \\(.sha)"').split()
    except GhError:
        return None
    return run("api", f"repos/{slug}/git/tags/{sha}", "--jq", ".object.sha").strip() if kind == "tag" else sha


def cmd_checkquota(args):
    """--checkquota: how much of the hourly GraphQL quota is left, and when it resets. Costs one point. Read-only."""
    try:
        quota = parse_quota(run("api", "graphql", "-f", f"query={QUOTA_QUERY}"))
    except (ValueError, KeyError, TypeError) as e:
        raise GhError(f"could not read the GraphQL quota: {e}")
    low = quota["remaining"] < max(1, (quota["limit"] or 5000) // 10)
    say(args, f"GraphQL quota: {quota['remaining']} of {quota['limit']} points left ({quota['used']} used); "
        f"resets at {_reset_text(quota)}" + ("\nLow: a board read can cost up to 1 point per item, so a big board needs "
                                           "most of it. Wait for the reset before a large --import, --update or --report." if low else ""))
    emit(args, {**quota, "low": low})


def cmd_delete_release(args):
    """--delete release --tag TAG (or --release TAG): delete a GitHub release.

    The tag must be named: there is no "latest" default for a delete. The release is looked up first, so a wrong tag
    is an error and --dry-run shows what would go. The git tag stays on GitHub unless --cleanup-tag is given, which
    also works when the release is already gone and only its tag is left (as after deleting the release by hand)."""
    repo = git_repo()
    if not repo:
        raise GhError("--delete must be run inside a git repo with a github.com origin remote")
    slug = "/".join(repo)
    tag = (args.tag or "").strip()
    if not tag or re.search(r"\s", tag):
        raise GhError("name the release to delete: --delete release --tag TAG (or --release TAG)")
    try:
        found = json.loads(run("release", "view", tag, "--repo", slug, "--json", "url,isDraft"))
    except GhError:
        found = None
        if not (args.cleanup_tag and remote_tag_commit(slug, tag)):
            raise GhError(f"no release {tag} in {slug} (see --list releases)" + ("; no tag of that name either" if args.cleanup_tag else ""))
    report = {"repo": slug, "tag": tag, "url": found and found["url"], "draft": bool(found and found.get("isDraft")),
              "dry_run": bool(args.dry_run), "deleted": False, "tag_deleted": False}
    if args.dry_run:
        say(args, f"delete\t{'release' if found else 'tag'}\t{tag}\t{report['url'] or ''}" + ("\t+ tag" if found and args.cleanup_tag else ""))
        emit(args, report)
        return
    if found:
        run("release", "delete", tag, "--repo", slug, "--yes", *(["--cleanup-tag"] if args.cleanup_tag else []))
        report.update(deleted=True, tag_deleted=bool(args.cleanup_tag))
        say(args, f"Deleted release {tag} ({found['url']})" + (" and its tag" if args.cleanup_tag else ""))
    else:
        run("api", "-X", "DELETE", f"repos/{slug}/git/refs/tags/{tag}")
        report["tag_deleted"] = True
        say(args, f"No release {tag}; deleted its tag")
    if not args.cleanup_tag and remote_tag_commit(slug, tag):
        say(args, f"The tag {tag} is still on GitHub; --cleanup-tag removes it too")
    emit(args, report)


def release_tag_prefix(args):
    """`[release] tag_prefix` from .kopi.toml (for example "v"), put before the changelog version to name the tag."""
    return ((getattr(args, "config_data", None) or {}).get("release") or {}).get("tag_prefix", "")


def cmd_release(args):
    """--release [--tag TAG]: create a GitHub release at the last commit (HEAD).

    The tag is the newest changelog version number (`1.2.3`, with `[release] tag_prefix` in front) unless --tag names another. The release's notes are the
    CHANGELOG.md section for that version (a leading `v` on the tag is ignored), or GitHub's generated notes if there
    is none; --draft makes it a draft.
    The commit must already be on GitHub (it is pushed first, not by kopi). A release that already exists is reported
    and left alone; a tag that already exists on another commit is an error. Uncommitted changes are warned about
    and are not in the release."""
    repo = git_repo()
    if not repo:
        raise GhError("--release must be run inside a git repo with a github.com origin remote")
    slug = "/".join(repo)
    sha, subject = head_commit()
    if not sha:
        raise GhError("no commits to release")
    changelog = _read("CHANGELOG.md")
    if args.tag:
        tag = args.tag.strip()
        if not tag or re.search(r"\s", tag):
            raise GhError(f"{args.tag!r} is not a usable tag name")
        version = tag.lstrip("vV")
    else:
        version = latest_release_version(changelog)
        if not version:
            raise GhError("no dated `## [version] - date` heading in CHANGELOG.md to name the release after; give a tag: --tag TAG")
        tag = release_tag_prefix(args) + version
    notes = changelog_section(changelog, version)
    report = {"repo": slug, "tag": tag, "target": sha, "subject": subject, "dry_run": bool(args.dry_run), "draft": bool(args.draft),
              "notes": "changelog" if notes else "generated", "existing": False, "url": None}
    try:
        existing = json.loads(run("release", "view", tag, "--repo", slug, "--json", "url,isDraft"))
        report.update(existing=True, url=existing["url"])
        say(args, f"Already released: {tag} {existing['url']}")
        emit(args, report)
        return
    except GhError:
        pass  # no such release: the usual case
    try:
        run("api", f"repos/{slug}/commits/{sha}", "--jq", ".sha")
    except GhError:
        raise GhError(f"commit {sha[:7]} is not on GitHub yet; push it first")
    tagged = remote_tag_commit(slug, tag)
    if tagged and tagged != sha:
        raise GhError(f"tag {tag} already exists on GitHub at {tagged[:7]}, not the last commit {sha[:7]}; choose another with --tag TAG")
    if git_dirty():
        print("warning: uncommitted changes are not in the release (it is made from the last commit)", file=sys.stderr)
    if args.dry_run:
        say(args, f"release\t{tag}\t{sha[:7]} {subject}\tnotes: {report['notes']}" + ("\tdraft" if args.draft else ""))
        emit(args, report)
        return
    cmd = ["release", "create", tag, "--repo", slug, "--target", sha, "--title", tag]
    cmd += ["--notes", notes] if notes else ["--generate-notes"]
    if args.draft:
        cmd.append("--draft")
    report["url"] = run(*cmd).strip().splitlines()[-1]
    say(args, f"Released {tag} at {sha[:7]} ({subject}): {report['url']}")
    emit(args, report)


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
    """Tick TODO.md entries that are Done on the board but still `[ ]` or `[~]` in the file, and refresh ISSUES.md.

    The reverse of --update: it edits local files only (the board is untouched) and does not move the TODO.md
    line to the Done section. Matching is the same as --update's; only TODO.md is read, issues/PRs and
    the changelog are not consulted. When the project has issue items it also rewrites ISSUES.md as
    `--export issues` would (creating it if missing; a hand-written one is left alone without --force).
    """
    args.only = "TODO.md"
    p = sync_plan(args)
    lines = _read("TODO.md").split("\n")
    ticked = []
    for item, entry, status in p.ticks:
        for first, last in bullet_blocks(lines):
            m = re.match(r"\[[ ~]\] (.+)$", bullet_text(lines, first, last))
            if m and m.group(1).strip() == entry:
                lines[first] = "- [x]" + lines[first][5:]
                ticked.append({"title": entry, "from": status})
                break
    if ticked and not args.dry_run:
        with open("TODO.md", "w") as f:
            f.write("\n".join(lines))
    # ISSUES.md is a snapshot of the board's issues, so a board that has issues gets it refreshed (created if missing)
    rows = linked_rows(p.items, "issues")
    issues = write_linked_file(args, "issues", p.number, rows, strict=False) if rows else None
    if issues:
        issues["count"] = len(rows)
    if args.json:
        emit(args, {"project": p.number, "dry_run": bool(args.dry_run), "ticked": ticked, "issues": issues})
        return
    for t in ticked:
        print(f"{'tick' if args.dry_run else 'ticked'}\t{t['from']}\t{t['title']}")
    verb = "to tick" if args.dry_run else "ticked"
    print(("Dry run: " if args.dry_run else "") + f"{len(ticked)} TODO.md items {verb} (Done in project #{p.number})")
    for c in (issues or {}).get("labels_added", []):
        print(f"{'would add' if args.dry_run else 'added'} label(s) {', '.join(c['add'])} to #{c['number']}")
    if issues:
        f, n = issues["file"], issues["count"]
        print(("Dry run: " if args.dry_run else "")
              + (f"{f} skipped: {issues['skipped']}" if issues["skipped"]
                 else f"{f} {'would be updated' if args.dry_run else 'updated'} ({_n(n, 'issue')})" if issues["changed"]
                 else f"{f} is up to date ({_n(n, 'issue')})"))


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
    p.add_argument("--config", metavar="FILE",
                   help=f"read settings from FILE instead of ./{CONFIG_FILE} (owner, project title, Status names, "
                        "extra sources; command-line flags win)")
    p.add_argument("--status-map", metavar="MAP", type=_status_map_arg,
                   help="name the board's Status options when they differ from Todo / In Progress / Done, e.g. "
                        "\"Todo=Backlog,In Progress=Doing,Done=Shipped\" (common names such as Backlog, Doing and "
                        "Shipped are recognised without it)")
    p.add_argument("--code-comments", action="store_true",
                   help="with --import/--update/--diff/--archive/--sync: also track TODO:/FIXME: code comments as Todo items")
    p.add_argument("--releases", action="store_true",
                   help="with --import/--update/--diff/--archive/--sync: also track git tags as Done items")
    p.add_argument("--json", action="store_true",
                   help="print the result as JSON on stdout (status messages are suppressed; warnings stay on stderr)")
    p.add_argument("--list", choices=sorted({*LIST_KINDS, *LINKED_LISTS, *RELEASE_LISTS}), metavar="KIND",
                   help="list the project's items of one kind (todo, inprogress, done, issues, prs), or the repo's GitHub releases "
                        "(releases); with --local, list the matching entries in TODO.md (ISSUES.md / PRS.md for issues / prs, "
                        "the versions in CHANGELOG.md for releases) instead")
    p.add_argument("--export", choices=sorted({*EXPORT_KINDS, *LINKED_EXPORTS}), metavar="KIND",
                   help="write the project's items of one kind into local files: todo, inprogress or done into TODO.md "
                        "as [ ] / [~] / [x] entries (skipping any already there); issues into ISSUES.md, prs into PRS.md")
    p.add_argument("--local", action="store_true", help="with --list: read the local TODO.md (ISSUES.md / PRS.md for issues / prs) instead of the project; "
                        "with --open / --reopen / --close: only edit the local ISSUES.md, nothing on GitHub")
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
    p.add_argument("--rollup", nargs="*", metavar="DIR",
                   help="status counts across projects: those of the repos in DIR... (or `repos` in .kopi.toml), "
                        "else every open project under the owner")
    p.add_argument("--report", nargs="?", const="text", choices=("text", "markdown"), metavar="FORMAT",
                   help="print a progress summary of the project (text, or markdown); with --json, the data")
    p.add_argument("--promote", metavar="ITEM",
                   help="turn one draft board item into a real issue in the current repo: ITEM is an item id or "
                        "text matching exactly one title")
    p.add_argument("--promote-all", nargs="?", const="all", choices=sorted(PROMOTE_KINDS), metavar="KIND",
                   help="turn every open draft board item (or just the todo / inprogress ones) into a real issue in "
                        "the current repo; needs --yes unless --dry-run")
    p.add_argument("--yes", action="store_true", help="with --promote-all: really create the issues")
    p.add_argument("--annotate", action="store_true",
                   help="with --promote / --promote-all: add the new issue number, e.g. (#12), to the matching TODO.md entry")
    p.add_argument("--open", metavar="TITLE",
                   help="create a new issue titled TITLE in the current repo and add it to the project as Todo "
                        "(with --body / --label; --force to allow a duplicate open title)")
    p.add_argument("--body", metavar="TEXT", help="with --open: the issue body")
    p.add_argument("--label", action="append", metavar="NAME", help="with --open: a label to apply (repeatable; must exist)")
    p.add_argument("--reopen", metavar="ISSUE",
                   help="reopen a closed issue in the current repo and put it back on the project as Todo: ISSUE is a "
                        "number, an issue URL, or text matching exactly one closed issue's title")
    p.add_argument("--no-dates", action="store_true",
                   help="with --import / --update: do not create or fill the Started / Completed date fields this run")
    p.add_argument("--gantt", nargs="?", const="releases", type=gantt_view, choices=GANTT_VIEWS, metavar="VIEW",
                   help="draw the project's Roadmap as a Gantt chart from the Started / Completed dates: releases (one bar "
                        f"per changelog release, the default) or items; written to {GANTT_FILE} unless --to says otherwise")
    p.add_argument("--to", metavar="FILE",
                   help="with --gantt: where to write the chart; .svg for an image, .mmd for raw Mermaid, else Markdown "
                        "with a Mermaid block; - prints it. With --report: write the report to FILE (Markdown for a .md file)")
    p.add_argument("--post", type=int, metavar="ISSUE",
                   help="with --report: post the report (as Markdown) as a comment on issue ISSUE of this repo, or edit "
                        "in place the comment kopi posted there before")
    p.add_argument("--release", nargs="?", const=True, metavar="TAG",
                   help="create a GitHub release at the last commit: the tag is the newest changelog version number "
                        "(or TAG, the same as --tag) and the notes are its CHANGELOG.md section; with --delete, names "
                        "the release to delete")
    p.add_argument("--tag", metavar="TAG", help="with --release: the tag name, instead of the newest changelog version number; "
                                                "with --delete release: the release to delete")
    p.add_argument("--delete", choices=("release",), metavar="KIND",
                   help="delete a GitHub release (--delete release --tag TAG, or --delete release --release TAG); "
                        "the tag it was made from stays unless --cleanup-tag is given")
    p.add_argument("--checkquota", action="store_true",
                   help="show how much of the hourly GraphQL quota is left and when it resets (costs one point)")
    p.add_argument("--cleanup-tag", action="store_true",
                   help="with --delete release: also delete the release's git tag (or just the tag, if the release is already gone)")
    p.add_argument("--draft", action="store_true", help="with --release: create it as a draft")
    p.add_argument("--setup-dates", action="store_true",
                   help="create the Started and Completed date fields on the project if missing (for its Roadmap view)")
    p.add_argument("--backfill-dates", action="store_true",
                   help="set the missing Started / Completed dates on the project's items (never overwrites)")
    p.add_argument("--close", metavar="ISSUE",
                   help="close one issue in the current repo: ISSUE is a number, an issue URL, or text matching "
                        "exactly one open issue's title")
    p.add_argument("--refresh-bodies", action="store_true",
                   help="with --update / --sync: also rewrite a draft item's body when the files give one (a long entry's full "
                        "text, a grouped minor's bullet list) and the board's differs; overwrites body edits made on the board")
    p.add_argument("--apply-labels", action="store_true",
                   help="with --export issues|prs, --squash or --sync: first add to GitHub the labels ISSUES.md / PRS.md lists for "
                        "an issue / PR that lacks them (never removes labels), so a label added in the file is kept")
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
    p.add_argument("--dry-run", action="store_true", help="with --import/--update/--sync/--archive/--dedupe/--squash/--restore/--done/--start/--move/--close/--promote/--promote-all/--open/--reopen/--release: print what would change")
    p.add_argument("--force", action="store_true",
                   help="with --import: import even if the project has items; with --archive: also archive items that look reworded; "
                        "with --export issues/prs, --squash and --sync: overwrite an ISSUES.md/PRS.md kopi did not generate; "
                        "with --open: create the issue even if an open one has the same title")
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
    if isinstance(args.release, str):   # `--release TAG` is `--release --tag TAG`
        if args.tag and args.tag != args.release:
            parser.error(f"--release {args.release} and --tag {args.tag} name different tags")
        args.tag, args.release = args.release, True
    make_release = bool(args.release) and not args.delete   # with --delete, --release only names the release to delete
    modes = [args.init, args.do_import, args.update, args.diff, args.archive, args.dedupe, args.squash, args.restore, args.done, args.list, args.export,
             args.sync, args.start, args.move, args.close, args.report,
             args.rollup is not None, args.promote, args.promote_all, args.open, args.reopen, args.setup_dates,
             args.backfill_dates, args.gantt, make_release, args.delete, args.checkquota]
    if not (any(modes) or args.cmd):
        parser.error("a command is required (or use --init / --import / --update / --diff / --archive / --dedupe / --squash / --restore / --done / --list / --export / --sync / --start / --move / --close / --report / --rollup / --promote / --promote-all / --open / --reopen / --setup-dates / --backfill-dates / --gantt / --release / --delete / --checkquota)")
    if sum(map(bool, modes)) > 1:
        parser.error("--init, --import, --update, --diff, --archive, --dedupe, --squash, --restore, --done, --list, --export, --sync, --start, --move, --close, --report, --rollup, --promote, --promote-all, --open, --reopen, --setup-dates, --backfill-dates, --gantt, --release, --delete and --checkquota are mutually exclusive")
    if (args.body is not None or args.label) and not args.open:
        parser.error("--body and --label only apply to --open")
    if args.annotate and not (args.promote or args.promote_all):
        parser.error("--annotate only applies to --promote and --promote-all")
    if (args.draft or args.tag) and not (args.release or args.delete):
        parser.error("--draft and --tag only apply to --release (and --tag to --delete release)")
    if args.draft and args.delete:
        parser.error("--draft only applies to --release")
    if args.cleanup_tag and not args.delete:
        parser.error("--cleanup-tag only applies to --delete release")
    if args.to and not (args.gantt or args.report):
        parser.error("--to only applies to --gantt and --report")
    if args.post and not args.report:
        parser.error("--post only applies to --report")
    if args.yes and not args.promote_all:
        parser.error("--yes only applies to --promote-all")
    if args.check and not args.diff:
        parser.error("--check only applies to --diff")
    if args.refresh_bodies and not (args.update or args.sync):
        parser.error("--refresh-bodies only applies to --update and --sync")
    if args.apply_labels and not (args.export in LINKED_EXPORTS or args.squash or args.sync):
        parser.error("--apply-labels only applies to --export issues|prs, --squash and --sync")
    if args.all and not args.restore:
        parser.error("--all only applies to --restore")
    if args.local and not (args.list or args.open or args.reopen or args.close):
        parser.error("--local only applies to --list, --open, --reopen and --close")
    if args.local and args.body is not None:
        parser.error("--body is not stored in ISSUES.md, so it cannot be used with --local")
    args.cli_owner, args.cli_status_map = args.owner, args.status_map  # as typed, before .kopi.toml fills them in
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
        apply_config(args, load_config(args.config or CONFIG_FILE, required=bool(args.config)))
        if args.local:  # no GitHub, no login: --list reads TODO.md / ISSUES.md, --open/--reopen/--close edit ISSUES.md
            (cmd_list_kind if args.list else cmd_open_local if args.open else cmd_reopen_local if args.reopen
             else cmd_close_local)(args)
            return 0
        check_auth()
        if args.checkquota:   # nothing to do with a project, so no owner lookup
            return cmd_checkquota(args) or 0
        if not args.owner and args.config_data.get("owner"):
            args.owner = args.config_data["owner"]
            print(f"Using owner: {args.owner} (from {args.config or CONFIG_FILE})", file=sys.stderr)
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
                                          (args.move, cmd_move), (args.close, cmd_close), (args.report, cmd_report), (args.rollup is not None, cmd_rollup),
                                          (args.promote, cmd_promote), (args.promote_all, cmd_promote_all), (args.open, cmd_open), (args.reopen, cmd_reopen), (args.setup_dates, cmd_setup_dates),
                                          (args.backfill_dates, cmd_backfill_dates), (args.gantt, cmd_gantt), (make_release, cmd_release), (args.delete, cmd_delete_release)) if on), getattr(args, "fn", None))
        rc = handler(args)
        return rc if isinstance(rc, int) else 0
    except GhError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
