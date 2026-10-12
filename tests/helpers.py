"""Shared boards, mock runners and fixtures for the tests (imported with `from helpers import *`)."""
import json
import os
import re
from unittest import mock

import pytest

import kopi as cli


def _status(returncode, out=""):
    return mock.Mock(returncode=returncode, stdout=out, stderr="")


def _owner_used(argv, git):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value=git), \
         mock.patch("kopi.run_json", return_value={"projects": []}) as rj:
        assert cli.main(argv) == 0
    return rj.call_args.args[rj.call_args.args.index("--owner") + 1]


def _init(projects, repo=("lateralblast", "kopi"), argv=("--init",)):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_repo", return_value=repo), \
         mock.patch("kopi.git_owner", return_value=repo[0] if repo else None), \
         mock.patch("kopi.run_json", side_effect=[{"projects": projects}, {"number": 3, "url": "u"}]) as rj, \
         mock.patch("kopi.run") as run:
        rc = cli.main(list(argv))
    return rc, rj, run


CHANGELOG = """# Changelog

## [Unreleased]

## [0.0.2] - 2026-10-10

### Added
- Auth check before every command.

## [0.0.1] - 2026-10-10

### Added
- Single-file CLI.
"""


TODO = """# TODO

## Open

- [ ] Add a `--json` output flag.
- [~] Run against real GitHub.

## Done

- [x] Auth check before every command, with tests.
- [x] Created the project.
"""


class FakeGraphql:
    """Stands in for kopi.graphql: records the batched operations --import / --update send and answers them.

    `.created` is [(title, body)] of draft items, `.added` the [url]s of linked items, `.statuses` [(item id, option id)],
    `.dates` [(item id, field id, date)], `.sections` [(item id, field id, text)]; `fields` are the date-field nodes the board reports."""

    def __init__(self, fields=()):
        self.fields, self.created, self.added, self.statuses, self.dates, self.queries = list(fields), [], [], [], [], []
        self.sections = []
        self.refreshed = []   # [(draft id, body)] from updateProjectV2DraftIssue
        self.base_total = 0

    def __call__(self, query, **variables):
        self.queries.append(query)
        if "totalCount" in query:   # the board's item count: what it had, plus what has been created or added
            return {"data": {"node": {"items": {"totalCount": self.base_total + len(self.created) + len(self.added)}}}}
        if "fields(first: 100)" in query:
            return {"data": {"node": {"fields": {"nodes": self.fields}}}}
        data = {}
        if query.startswith("query {"):
            for n, url in re.findall(r'a(\d+): resource\(url: "([^"]*)"\)', query):
                data[f"a{n}"] = {"id": f"NODE:{url}"}
            return {"data": data}
        string = r'("(?:[^"\\]|\\.)*")'
        for n, title, body in re.findall(r'a(\d+): addProjectV2DraftIssue\(input: \{projectId: "[^"]*", title: ' + string + ", body: " + string, query):
            self.created.append((json.loads(title), json.loads(body)))
            data[f"a{n}"] = {"projectItem": {"id": f"NEW{len(self.created) - 1}"}}
        for n, content in re.findall(r'a(\d+): addProjectV2ItemById\(input: \{projectId: "[^"]*", contentId: "([^"]*)"', query):
            self.added.append(content.removeprefix("NODE:"))
            data[f"a{n}"] = {"item": {"id": f"LNEW{len(self.added) - 1}"}}
        for n, draft, body in re.findall(r'a(\d+): updateProjectV2DraftIssue\(input: \{draftIssueId: "([^"]*)", body: ' + string, query):
            self.refreshed.append((draft, json.loads(body)))
            data[f"a{n}"] = {"draftIssue": {"id": draft}}
        for n, item, field, option in re.findall(r'a(\d+): updateProjectV2ItemFieldValue\(input: \{projectId: "[^"]*", itemId: "([^"]*)", '
                                               r'fieldId: "([^"]*)", value: \{singleSelectOptionId: "([^"]*)"', query):
            self.statuses.append((item, option))
            data[f"a{n}"] = {"projectV2Item": {"id": item}}
        for n, item, field, date in re.findall(r'a(\d+): updateProjectV2ItemFieldValue\(input: \{projectId: "[^"]*", itemId: "([^"]*)", '
                                             r'fieldId: "([^"]*)", value: \{date: "([^"]*)"', query):
            self.dates.append((item, field, date))
            data[f"a{n}"] = {"projectV2Item": {"id": item}}
        for n, item, field, text in re.findall(r'a(\d+): updateProjectV2ItemFieldValue\(input: \{projectId: "[^"]*", itemId: "([^"]*)", '
                                             r'fieldId: "([^"]*)", value: \{text: ' + string, query):
            self.sections.append((item, field, json.loads(text)))
            data[f"a{n}"] = {"projectV2Item": {"id": item}}
        return {"data": data}


def _import_mocks(items):
    def fake(*a):
        return {"item-list": {"items": items}, "view": {"id": "PID"},
                "field-list": {"fields": [{"name": "Title"}, {"id": "FID", "name": "Status", "options": [
                    {"id": "o1", "name": "Todo"}, {"id": "o2", "name": "In Progress"}, {"id": "o3", "name": "Done"}]}]},
                "item-create": {"id": "ITEM"}, "item-add": {"id": "LINKED"}}[a[1]]
    return fake


def _run_import(items, argv=("--import",), linked=(), gql=None):
    gql = gql or FakeGraphql()
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), mock.patch("kopi.graphql", gql), \
         mock.patch("kopi.fetch_linked", return_value=list(linked)), \
         mock.patch("kopi.ensure_project", return_value=(2, False)), \
         mock.patch("kopi._read", side_effect=lambda p: CHANGELOG if p == "CHANGELOG.md" else TODO), \
         mock.patch("kopi.run_json", side_effect=_import_mocks(items)) as rj, mock.patch("kopi.run") as run:
        rc = cli.main(list(argv))
    rj.gql = gql
    return rc, rj, run


BOARD = [
    {"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"},
    {"id": "B", "title": "Add a `--json` output flag.", "status": "Todo"},
    {"id": "C", "title": "Run against real GitHub.", "status": "Todo"},
    {"id": "D", "title": "Auth check before every command, with tests.", "status": "Todo"},
]


_REAL_READ = cli._read   # any other file (a chart, say) is read for real, from the test's directory


def _run_update(items=BOARD, argv=("--update",), project=True, todo=None, linked=(), issues_md="", changelog=None, gql=None):
    gql = gql or FakeGraphql()
    def fake(*a):
        return {"list": {"projects": [{"number": 2, "title": "kopi", "url": "u"}] if project else []},
                "item-list": {"items": items}, "view": {"id": "PID"},
                "field-list": {"fields": [{"id": "FID", "name": "Status", "options": [
                    {"id": "o1", "name": "Todo"}, {"id": "o2", "name": "In Progress"}, {"id": "o3", "name": "Done"}]}]},
                "item-create": {"id": "NEW"}, "item-add": {"id": "LNEW"}}[a[1]]
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
         mock.patch("kopi.git_repo", return_value=("lateralblast", "kopi")), mock.patch("kopi.graphql", gql), \
         mock.patch("kopi.fetch_linked", return_value=list(linked)), \
         mock.patch("kopi._read", side_effect=lambda p: (changelog or CHANGELOG) if p == "CHANGELOG.md" else (
             issues_md if p in ("ISSUES.md", "PRS.md") else ((todo or TODO) if p == "TODO.md" else _REAL_READ(p)))), \
         mock.patch("kopi.run_json", side_effect=fake) as rj, mock.patch("kopi.run") as run:
        rc = cli.main(list(argv))
    rj.gql = gql
    return rc, rj, run


SETTLED = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"},
           {"id": "A2", "title": "[0.0.2] Auth check before every command.", "status": "Done"},
           {"id": "B", "title": "Add a `--json` output flag.", "status": "Todo"},
           {"id": "C", "title": "Run against real GitHub.", "status": "In Progress"},
           {"id": "E", "title": "Created the project.", "status": "Done"}]


def _linked_item(id_, title, status, url):
    return {"id": id_, "title": title, "status": status, "content": {"type": "Issue", "url": url}}


def _archived(run):
    return [c.args[c.args.index("--id") + 1] for c in run.call_args_list if c.args[:2] == ("project", "item-archive")]


ORPHANS = SETTLED + [{"id": "O", "title": "Old idea nobody wants", "status": "Todo"},
                     {"id": "OP", "title": "Half-done old idea", "status": "In Progress"},
                     {"id": "OD", "title": "Old finished work", "status": "Done"},
                     _linked_item("L1", "Some issue", "Todo", "https://x/1")]


DUPES = [
    {"id": "A", "title": "[0.2.3] Verified update live.", "status": "Done"},
    {"id": "B", "title": "[0.2.3] Verified  update live.", "status": "Done"},
    {"id": "C", "title": "Same idea", "status": "Todo"},
    {"id": "D", "title": "same idea", "status": "In Progress"},
    {"id": "E", "title": "Unique", "status": "Todo"},
    {"id": "F", "title": "Issue", "status": "Todo", "content": {"url": "https://x/1"}},
    {"id": "G", "title": "Issue renamed", "status": "Done", "content": {"url": "https://x/1"}},
]


def _deleted(run):
    return [c.args[c.args.index("--id") + 1] for c in run.call_args_list if c.args[:2] == ("project", "item-delete")]


PROJECTS = [{"id": "PVT_a", "number": 2, "title": "kopi", "url": "u2", "closed": False},
            {"id": "PVT_b", "number": 1, "title": "old", "url": "u1", "closed": True}]


def _list(argv=("list",), gql=None):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="o"), \
         mock.patch("kopi.run_json", return_value={"projects": PROJECTS}) as rj, \
         mock.patch("kopi.graphql", **(gql or {"return_value": {"data": {
             "p0": {"repositories": {"nodes": [{"nameWithOwner": "o/kopi"}]}},
             "p1": {"repositories": {"nodes": []}}}}})):
        return cli.main(list(argv)), rj


def _json_out(capsys):
    cap = capsys.readouterr()
    return json.loads(cap.out), cap


def _created_titles(run_json_mock):
    """Titles of the draft items created (they go through the batched GraphQL fake attached to the run_json mock)."""
    return [t for t, _ in run_json_mock.gql.created]


def _undone(run):
    return [c.args[c.args.index("--id") + 1] for c in run.call_args_list
            if c.args[:2] == ("project", "item-archive") and "--undo" in c.args]


def _archived_pages():
    node = lambda i, arch, title, status: {"id": i, "isArchived": arch, "content": {"__typename": "DraftIssue", "title": title},
                                          "fieldValueByName": {"name": status} if status else None}
    return [{"data": {"node": {"items": {"pageInfo": {"hasNextPage": True, "endCursor": "C1"},
                                         "nodes": [node("LIVE", False, "live", "Todo"), node("A1", True, "gone one", "Todo")]}}}},
            {"data": {"node": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                         "nodes": [node("A2", True, "gone two", None)]}}}}]


def _edited(run):
    return [(c.args[c.args.index("--id") + 1], c.args[c.args.index("--single-select-option-id") + 1])
            for c in run.call_args_list if c.args[:2] == ("project", "item-edit")]


SQUASH_BOARD = [
    {"id": "J", "title": "Add a `--json` output flag.", "status": "Done"},        # [ ] in TODO.md
    {"id": "R", "title": "Run against real GitHub.", "status": "Done"},           # [~] in TODO.md
    {"id": "A", "title": "Auth check before every command, with tests.", "status": "Done"},  # already [x]
]


GQL_FIELDS = {"data": {"node": {"fields": {"nodes": [
    {"id": "F_T", "name": "Notes", "dataType": "TEXT"},
    {"id": "F_N", "name": "Points", "dataType": "NUMBER"},
    {"id": "F_D", "name": "Due", "dataType": "DATE"},
    {"id": "F_S", "name": "Status", "dataType": "SINGLE_SELECT", "options": [
        {"id": "o1", "name": "Todo"}, {"id": "o3", "name": "Done"}]},
    {"id": "F_I", "name": "Sprint", "dataType": "ITERATION",
     "configuration": {"iterations": [{"id": "it1", "title": "Sprint 1"}]}},
    {"id": "F_L", "name": "Labels", "dataType": "LABELS"},
    {}]}}}}


def _set(*argv, gql_result=None):
    def fake(*a):
        return {"view": {"id": "PID"}, "item-list": {"items": ORPHANS}}[a[1]]
    mutation = []

    def gql(query, **kw):
        if "fields(first" in query:
            return GQL_FIELDS
        mutation.append(query)
        return gql_result or {"data": {}}
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="o"), \
         mock.patch("kopi.run_json", side_effect=fake), mock.patch("kopi.graphql", side_effect=gql):
        rc = cli.main(list(argv))
    return rc, mutation


EXPORT_BOARD = [
    {"id": "N1", "title": "Brand new idea", "status": "Todo"},
    {"id": "N2", "title": "Add a `--json` output flag.", "status": "Todo"},        # already [ ] in TODO.md
    {"id": "N3", "title": "Created the project.", "status": "Todo"},               # already [x]: must not be re-opened
    {"id": "N4", "title": "In progress thing", "status": "In Progress"},           # not Todo
    {"id": "N5", "title": "Long idea…", "status": "Todo", "content": {"body": "Long idea that was cut off in the title"}},
    {"id": "N6", "title": "Fix the bug", "status": "Todo", "content": {"url": "https://x/9", "title": "Fix the bug"}},
    {"id": "N7", "title": "Done thing", "status": "Done"},
]


EXPORT_DONE_BOARD = [
    {"id": "D1", "title": "[0.9.9] A changelog item on the board", "status": "Done"},  # belongs in CHANGELOG.md
    {"id": "D2", "title": "Single-file CLI built on gh.", "status": "Done"},              # resembles a changelog entry
    {"id": "D3", "title": "Created the project.", "status": "Done"},                      # already [x] in TODO.md
    {"id": "D4", "title": "Shipped the login page", "status": "Done"},
    {"id": "D5", "title": "Rewrote the parser entirely", "status": "Done"},
    {"id": "D6", "title": "Fixed the bug", "status": "Done", "content": {"url": "https://x/8"}},
    {"id": "P1", "title": "Working on a thing", "status": "In Progress"},
    {"id": "P2", "title": "Run against real GitHub.", "status": "In Progress"},          # already [~]
    {"id": "T1", "title": "A todo item", "status": "Todo"},
]


ISSUE_BOARD = [
    {"id": "I1", "title": "Login fails", "status": "Todo", "labels": ["bug"],
     "content": {"type": "Issue", "number": 3, "repository": "o/kopi", "url": "https://github.com/o/kopi/issues/3"}},
    {"id": "I2", "title": "Add dark mode", "status": "Done",
     "content": {"type": "Issue", "number": 7, "repository": "o/kopi", "url": "https://github.com/o/kopi/issues/7"}},
    {"id": "I3", "title": "Fix typo", "status": None,
     "content": {"type": "Issue", "number": 9, "repository": "o/kopi", "url": "https://github.com/o/kopi/issues/9"}},
    {"id": "PR1", "title": "A pull request", "status": "In Progress",
     "content": {"type": "PullRequest", "number": 8, "repository": "o/kopi", "url": "https://github.com/o/kopi/pull/8"}},
    {"id": "D1", "title": "A draft", "status": "Todo", "content": {"type": "DraftIssue"}},
]


ISSUES_TEXT = """<!-- Generated by kopi --export issues -->

# Issues

## Todo

- [#1](https://github.com/me/r/issues/1) Existing one (bug)
- Brand new
- [ ] Hand written

## Done

- [#9](https://github.com/me/r/issues/9) Gone issue (x, y)
- [#2](https://github.com/other/z/issues/2) Elsewhere
"""


def _import_issues(args, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ISSUES.md").write_text(ISSUES_TEXT)
    existing = json.dumps([{"number": 1, "title": "Existing one", "url": "https://github.com/me/r/issues/1"},
                           {"number": 5, "title": "hand WRITTEN", "url": "https://github.com/me/r/issues/5"}])
    calls = []

    def fake_run(*a):
        calls.append(a)
        if a[:2] == ("issue", "list"):
            return existing
        if a[:2] == ("label", "list"):
            return json.dumps([{"name": "X"}, {"name": "docs"}])
        return "https://github.com/me/r/issues/10\n"
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_repo", return_value=("me", "r")), \
         mock.patch("kopi.run", side_effect=fake_run):
        rc = cli.main(args)
    return rc, calls


STATUS_BOARD = [
    {"id": "B1", "title": "Backlogged", "status": "Backlog"},
    {"id": "D1", "title": "Doing it", "status": "Doing"},
    {"id": "S1", "title": "Shipped it", "status": "Shipped"},
]


def _run_custom(items, argv, options=("Backlog", "Doing", "Shipped")):
    def fake(*a):
        return {"list": {"projects": [{"number": 2, "title": "kopi", "url": "u"}]}, "item-list": {"items": [dict(i) for i in items]},
                "view": {"id": "PID"},
                "field-list": {"fields": [{"id": "FID", "name": "Status",
                                           "options": [{"id": f"o{n}", "name": o} for n, o in enumerate(options, 1)]}]}}[a[1]]
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
         mock.patch("kopi.git_repo", return_value=("lateralblast", "kopi")), \
         mock.patch("kopi.run_json", side_effect=fake), mock.patch("kopi.run") as run:
        return cli.main(list(argv)), run


CLOSE_ISSUES = json.dumps([
    {"number": 1, "title": "Login fails", "state": "OPEN", "url": "https://github.com/me/r/issues/1"},
    {"number": 2, "title": "Login fails on mobile", "state": "OPEN", "url": "https://github.com/me/r/issues/2"},
    {"number": 3, "title": "Old bug", "state": "CLOSED", "url": "https://github.com/me/r/issues/3"},
    {"number": 4, "title": "Add dark mode", "state": "OPEN", "url": "https://github.com/me/r/issues/4"}])


def _close(*argv):
    calls = []

    def fake(*a):
        calls.append(a)
        return CLOSE_ISSUES if a[:2] == ("issue", "list") else ""
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_repo", return_value=("me", "r")), \
         mock.patch("kopi.run", side_effect=fake):
        rc = cli.main(list(argv))
    return rc, [c for c in calls if c[:2] == ("issue", "close")]


CONFIG_SAMPLE = """# kopi settings
owner = "acme"
project = "Roadmap"   # the board's title

[status]
Todo = "Backlog"
"In Progress" = 'Doing'

[sources]
code_comments = true

[comments]
tags = ["TODO", "FIXME",
        "HACK"]
exclude = ["vendor/*", "*.min.js"]
"""


F = "FIX" + "ME"


T = "TO" + "DO"  # keeps this file's own lines from looking like code comments to kopi --code-comments


def _tree(tmp_path):
    (tmp_path / "a.py").write_text(f"x = 1  # {T}: handle rate limits\n# {F}(bob): breaks on empty input\n"
                                   f'print("{T}: not a comment")\n# {T} heading without a colon\n')
    (tmp_path / "b.js").write_text(f"// {T}: handle rate limits\n/* {T}: close the socket */\n")
    (tmp_path / "README.md").write_text(f"# {T}: docs are not scanned\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "gen_x.py").write_text(f"# {T}: generated\n")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "h.py").write_text(f"# {T}: hidden\n")


REPORT_BOARD = [
    {"id": "H1", "title": "[0.0.1] Old release entry.", "status": "Done"},
    {"id": "D1", "title": "Draft done", "status": "Done", "content": {"type": "DraftIssue"}},
    {"id": "D2", "title": "Draft doing", "status": "In Progress", "content": {"type": "DraftIssue"}},
    {"id": "I1", "title": "An issue", "status": "Todo", "content": {"type": "Issue", "number": 1, "repository": "o/kopi"}},
    {"id": "P1", "title": "A PR", "status": "Done", "content": {"type": "PullRequest", "number": 2, "repository": "o/kopi"}},
    {"id": "N1", "title": "No status item", "status": None, "content": {"type": "DraftIssue"}},
]


def _rollup_run(argv, boards, repos, owner_calls=None):
    """boards: {project number: items}; repos: {dir name: (owner, repo) or None}."""
    projects = [{"number": 2, "title": "kopi", "url": "u2"}, {"number": 3, "title": "Roadmap", "url": "u3"},
                {"number": 4, "title": "old", "url": "u4", "closed": True}]

    def fake(*a):
        if owner_calls is not None:
            owner_calls.append(a[a.index("--owner") + 1])
        return {"projects": projects} if a[1] == "list" else {"items": [dict(i) for i in boards[int(a[2])]]}
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
         mock.patch("kopi.git_repo", side_effect=lambda path=None: repos.get(os.path.basename(path or "."))), \
         mock.patch("kopi.run_json", side_effect=fake):
        return cli.main(argv)


ROLLUP_BOARDS = {
    2: [{"id": "a", "title": "x", "status": "Todo"}, {"id": "b", "title": "y", "status": "Done"},
        {"id": "h", "title": "[0.0.1] history", "status": "Done"}],
    3: [{"id": "c", "title": "z", "status": "Icebox"}, {"id": "d", "title": "w", "status": "In Progress"}],
    4: [{"id": "e", "title": "closed project", "status": "Todo"}],
}


PROMOTE_BOARD = [
    {"id": "D1", "title": "Add dark mode", "status": "Todo", "content": {"type": "DraftIssue"}},
    {"id": "D2", "title": "Add dark theme tweaks", "status": "In Progress", "content": {"type": "DraftIssue"}},
    {"id": "I1", "title": "An issue", "status": "Todo",
     "content": {"type": "Issue", "number": 3, "repository": "o/kopi", "url": "https://github.com/o/kopi/issues/3"}},
]


def _promote(argv, todo=None):
    calls = []

    def fake_graphql(query, **variables):
        calls.append((query, variables))
        if "convertProjectV2DraftIssueItemToIssue" in query:
            return {"data": {"convertProjectV2DraftIssueItemToIssue": {"item": {"id": variables["item"], "content": {
                "number": 12, "title": "Add dark mode", "url": "https://github.com/lateralblast/kopi/issues/12"}}}}}
        return {"data": {"repository": {"id": "REPO_NODE"}}}
    rc, rj, run = _run_update(PROMOTE_BOARD, argv=argv, todo=todo, gql=fake_graphql)
    return rc, calls, run


BULK_BOARD = [
    {"id": "T1", "title": "Add dark mode", "status": "Todo", "content": {"type": "DraftIssue"}},
    {"id": "P1", "title": "Write the docs", "status": "In Progress", "content": {"type": "DraftIssue"}},
    {"id": "T2", "title": "Fix the login bug", "status": "Todo", "content": {"type": "DraftIssue"}},
    {"id": "DN", "title": "Already shipped", "status": "Done", "content": {"type": "DraftIssue"}},
    {"id": "CH", "title": "[0.1.0] A changelog entry", "status": "Todo", "content": {"type": "DraftIssue"}},
    {"id": "CO", "title": "[code] handle rate limits", "status": "Todo", "content": {"type": "DraftIssue"}},
    {"id": "RE", "title": "[release] v1.0.0", "status": "In Progress", "content": {"type": "DraftIssue"}},
    {"id": "IS", "title": "An issue", "status": "Todo", "content": {"type": "Issue", "number": 3, "repository": "o/kopi"}},
    {"id": "NS", "title": "No status draft", "status": None, "content": {"type": "DraftIssue"}},
]


def _promote_all(argv, fail=(), todo=None):
    calls = []

    def fake_graphql(query, **variables):
        if "convertProjectV2DraftIssueItemToIssue" not in query:
            return {"data": {"repository": {"id": "REPO_NODE"}}}
        if variables["item"] in fail:
            raise cli.GhError("rate limited")
        calls.append(variables["item"])
        n = 10 + len(calls)
        return {"data": {"convertProjectV2DraftIssueItemToIssue": {"item": {"content": {
            "number": n, "title": "t", "url": f"https://github.com/lateralblast/kopi/issues/{n}"}}}}}
    rc, rj, run = _run_update(BULK_BOARD, argv=argv, todo=todo, gql=fake_graphql)
    return rc, calls


def _open(argv, existing=(), project=True, field_options=True):
    """Run `--open`; returns (rc, calls to gh run, run_json calls)."""
    calls = []

    def fake_run(*a):
        calls.append(a)
        if a[:2] == ("issue", "list"):
            return json.dumps(list(existing))
        if a[:2] == ("issue", "create"):
            return "https://github.com/lateralblast/kopi/issues/21\n"
        return ""

    def fake_json(*a):
        return {"list": {"projects": [{"number": 2, "title": "kopi", "url": "u"}] if project else []},
                "view": {"id": "PID"}, "item-add": {"id": "NEWITEM"},
                "item-list": {"items": [{"id": "NEWITEM", "title": "x", "status": None}]},
                "field-list": {"fields": [{"id": "FID", "name": "Status", "options": (
                    [{"id": "o1", "name": "Todo"}, {"id": "o3", "name": "Done"}] if field_options else [{"id": "o9", "name": "Icebox"}])}]}}[a[1]]
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
         mock.patch("kopi.git_repo", return_value=("lateralblast", "kopi")), \
         mock.patch("kopi.run", side_effect=fake_run), mock.patch("kopi.run_json", side_effect=fake_json) as rj:
        rc = cli.main(list(argv))
    return rc, calls, rj


REOPEN_ISSUES = json.dumps([
    {"number": 3, "title": "Old bug", "state": "CLOSED", "url": "https://github.com/lateralblast/kopi/issues/3"},
    {"number": 4, "title": "Old bug again", "state": "CLOSED", "url": "https://github.com/lateralblast/kopi/issues/4"},
    {"number": 5, "title": "Flaky test", "state": "CLOSED", "url": "https://github.com/lateralblast/kopi/issues/5"},
    {"number": 6, "title": "Live one", "state": "OPEN", "url": "https://github.com/lateralblast/kopi/issues/6"}])


def _reopen(argv, board=(), project=True):
    calls = []

    def fake_run(*a):
        calls.append(a)
        return REOPEN_ISSUES if a[:2] == ("issue", "list") else ""

    def fake_json(*a):
        return {"list": {"projects": [{"number": 2, "title": "kopi", "url": "u"}] if project else []},
                "item-list": {"items": [dict(i) for i in board]}, "view": {"id": "PID"}, "item-add": {"id": "ADDED"},
                "field-list": {"fields": [{"id": "FID", "name": "Status", "options": [
                    {"id": "o1", "name": "Todo"}, {"id": "o2", "name": "In Progress"}, {"id": "o3", "name": "Done"}]}]}}[a[1]]
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
         mock.patch("kopi.git_repo", return_value=("lateralblast", "kopi")), \
         mock.patch("kopi.run", side_effect=fake_run), mock.patch("kopi.run_json", side_effect=fake_json) as rj:
        rc = cli.main(list(argv))
    return rc, calls, rj


def _item(id_, status, number):
    return {"id": id_, "title": "x", "status": status,
            "content": {"type": "Issue", "number": number, "repository": "lateralblast/kopi",
                        "url": f"https://github.com/lateralblast/kopi/issues/{number}"}}


LOCAL_ISSUES = """<!-- Generated by kopi --export issues from project #2 (lateralblast). -->

# Issues

## Todo

- [#3](https://github.com/o/kopi/issues/3) Login fails (bug)
- Hand written one

## In Progress

- [#7](https://github.com/o/kopi/issues/7) Add dark mode

## Done

- [#9](https://github.com/o/kopi/issues/9) Fix typo
"""


def _local(tmp_path, monkeypatch, *argv, text=LOCAL_ISSUES):
    monkeypatch.chdir(tmp_path)
    if text is not None:
        (tmp_path / "ISSUES.md").write_text(text)
    with mock.patch("kopi.run") as run, mock.patch("kopi.run_json") as rj, mock.patch("kopi.check_auth") as auth:
        rc = cli.main(list(argv))
    assert not run.called and not rj.called and not auth.called   # --local never talks to GitHub, nor needs a login
    return rc, ((tmp_path / "ISSUES.md").read_text() if (tmp_path / "ISSUES.md").exists() else None)


ANNOTATE_TODO = """# TODO

## Open

- [ ] Add dark mode
- [~] Write the docs for the whole thing, including the reference section and examples for every command that exists
- [ ] Add dark mode toggle to the settings page
- [x] Already done thing (#4)
"""


__all__ = [
    "json",
    "os",
    "mock",
    "pytest",
    "cli",
    "_status",
    "_owner_used",
    "_init",
    "CHANGELOG",
    "TODO",
    "FakeGraphql",
    "_import_mocks",
    "_run_import",
    "BOARD",
    "_REAL_READ",
    "_run_update",
    "SETTLED",
    "_linked_item",
    "_archived",
    "ORPHANS",
    "DUPES",
    "_deleted",
    "PROJECTS",
    "_list",
    "_json_out",
    "_created_titles",
    "_undone",
    "_archived_pages",
    "_edited",
    "SQUASH_BOARD",
    "GQL_FIELDS",
    "_set",
    "EXPORT_BOARD",
    "EXPORT_DONE_BOARD",
    "ISSUE_BOARD",
    "ISSUES_TEXT",
    "_import_issues",
    "STATUS_BOARD",
    "_run_custom",
    "CLOSE_ISSUES",
    "_close",
    "CONFIG_SAMPLE",
    "F",
    "T",
    "_tree",
    "REPORT_BOARD",
    "_rollup_run",
    "ROLLUP_BOARDS",
    "PROMOTE_BOARD",
    "_promote",
    "BULK_BOARD",
    "_promote_all",
    "_open",
    "REOPEN_ISSUES",
    "_reopen",
    "_item",
    "LOCAL_ISSUES",
    "_local",
    "ANNOTATE_TODO",
]
