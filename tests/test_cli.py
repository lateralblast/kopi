import json
import os
from unittest import mock

import pytest

import kopi as cli


@pytest.fixture(autouse=True)
def state_file(tmp_path, monkeypatch):
    """Keep tests away from the real ~/.config/kopi archive log."""
    monkeypatch.setenv("KOPI_STATE", str(tmp_path / "state" / "archived.json"))


@pytest.fixture(autouse=True)
def no_sleep():
    with mock.patch("kopi.time.sleep") as sleep:
        yield sleep


def test_create_links_repo():
    with mock.patch("kopi.check_auth"), \
         mock.patch("kopi.run_json", return_value={"number": 7, "url": "u"}), \
         mock.patch("kopi.run") as run:
        assert cli.main(["--owner", "me", "create", "T", "--repo", "me/r"]) == 0
        run.assert_called_once_with("project", "link", "7", "--owner", "me", "--repo", "me/r")


def test_error_returns_1():
    import kopi as gh
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value=None), \
         mock.patch("kopi.run_json", side_effect=gh.GhError("boom")):
        assert cli.main(["list"]) == 1


def test_version_consistent():
    import pathlib
    import re
    root = pathlib.Path(__file__).parent.parent
    pyproject = re.search(r'^version = "(.+)"', (root / "pyproject.toml").read_text(), re.M).group(1)
    readme = re.search(r"^Version: (.+)$", (root / "README.md").read_text(), re.M).group(1)
    changelog = re.search(r"^## \[(\d+\.\d+\.\d+)\]", (root / "CHANGELOG.md").read_text(), re.M).group(1)  # newest release heading
    assert cli.__version__ == pyproject == readme == changelog


def _status(returncode, out=""):
    return mock.Mock(returncode=returncode, stdout=out, stderr="")


def test_check_auth_not_logged_in(capsys):
    with mock.patch("kopi.subprocess.run", return_value=_status(1)):
        assert cli.main(["list"]) == 1
    assert "gh auth login" in capsys.readouterr().err


def test_check_auth_missing_scope():
    out = "Token scopes: 'repo', 'read:org'"
    with mock.patch("kopi.subprocess.run", return_value=_status(0, out)):
        try:
            cli.check_auth()
        except cli.GhError as e:
            assert "gh auth refresh -s project" in str(e)
        else:
            raise AssertionError("expected GhError")


def test_check_auth_ok():
    out = "Token scopes: 'repo', 'project'"
    with mock.patch("kopi.subprocess.run", return_value=_status(0, out)):
        cli.check_auth()


def test_git_owner_parsing():
    for url, owner in [
        ("https://github.com/lateralblast/kopi.git\n", "lateralblast"),
        ("https://github.com/lateralblast/kopi\n", "lateralblast"),
        ("git@github.com:lateralblast/kopi.git\n", "lateralblast"),
        ("https://gitlab.com/someone/repo.git\n", None),
    ]:
        with mock.patch("kopi.subprocess.run", return_value=_status(0, url)):
            assert cli.git_owner() == owner


def test_git_owner_not_a_repo():
    with mock.patch("kopi.subprocess.run", return_value=_status(128)):
        assert cli.git_owner() is None


def _owner_used(argv, git):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value=git), \
         mock.patch("kopi.run_json", return_value={"projects": []}) as rj:
        assert cli.main(argv) == 0
    return rj.call_args.args[rj.call_args.args.index("--owner") + 1]


def test_owner_precedence():
    assert _owner_used(["--owner", "x", "list"], "gitorg") == "x"
    assert _owner_used(["list"], "gitorg") == "gitorg"
    assert _owner_used(["list"], None) == "@me"


def _init(projects, repo=("lateralblast", "kopi"), argv=("--init",)):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_repo", return_value=repo), \
         mock.patch("kopi.git_owner", return_value=repo[0] if repo else None), \
         mock.patch("kopi.run_json", side_effect=[{"projects": projects}, {"number": 3, "url": "u"}]) as rj, \
         mock.patch("kopi.run") as run:
        rc = cli.main(list(argv))
    return rc, rj, run


def test_init_existing_project_does_nothing(capsys):
    rc, rj, run = _init([{"number": 2, "title": "kopi", "url": "u"}])
    assert rc == 0
    assert rj.call_count == 1  # list only, no create
    run.assert_not_called()
    assert "Project exists: #2" in capsys.readouterr().out


def test_init_creates_and_links():
    rc, rj, run = _init([{"number": 1, "title": "other", "url": "u"}])
    assert rc == 0
    assert rj.call_args_list[1].args == ("project", "create", "--title", "kopi", "--owner", "lateralblast")
    run.assert_called_once_with("project", "link", "3", "--owner", "lateralblast", "--repo", "lateralblast/kopi")


def test_init_other_owner_skips_link(capsys):
    rc, rj, run = _init([], argv=("--owner", "someone", "--init"))
    assert rc == 0
    run.assert_not_called()
    assert "Not linking" in capsys.readouterr().err


def test_init_outside_repo_errors(capsys):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_repo", return_value=None), \
         mock.patch("kopi.git_owner", return_value=None):
        assert cli.main(["--init"]) == 1
    assert "inside a git repo" in capsys.readouterr().err


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


def test_parse_changelog_and_todo():
    assert cli.parse_changelog(CHANGELOG) == [("0.0.2", "Auth check before every command."),
                                              ("0.0.1", "Single-file CLI.")]
    assert cli.parse_todo(TODO) == [("Todo", "Add a `--json` output flag."),
                                    ("In Progress", "Run against real GitHub."),
                                    ("Done", "Auth check before every command, with tests."),
                                    ("Done", "Created the project.")]


def test_build_plan_orders_maps_and_dedupes():
    plan, skipped = cli.build_plan(CHANGELOG, TODO)
    assert [(st, t) for st, t, _ in plan] == [
        ("Done", "[0.0.1] Single-file CLI."),
        ("Done", "[0.0.2] Auth check before every command."),
        ("Todo", "Add a `--json` output flag."),
        ("In Progress", "Run against real GitHub."),
        ("Done", "Created the project."),
    ]
    assert skipped == ["Auth check before every command, with tests."]


def test_build_plan_long_text_goes_to_body():
    long = "x" * 150
    plan, _ = cli.build_plan("## [0.0.1]\n- " + long + "\n", "")
    status, title, body = plan[0]
    assert len(title) == 100 and title.endswith("…") and body == long


def _import_mocks(items):
    def fake(*a):
        return {"item-list": {"items": items}, "view": {"id": "PID"},
                "field-list": {"fields": [{"name": "Title"}, {"id": "FID", "name": "Status", "options": [
                    {"id": "o1", "name": "Todo"}, {"id": "o2", "name": "In Progress"}, {"id": "o3", "name": "Done"}]}]},
                "item-create": {"id": "ITEM"}, "item-add": {"id": "LINKED"}}[a[1]]
    return fake


def _run_import(items, argv=("--import",), linked=()):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
         mock.patch("kopi.fetch_linked", return_value=list(linked)), \
         mock.patch("kopi.ensure_project", return_value=(2, False)), \
         mock.patch("kopi._read", side_effect=lambda p: CHANGELOG if p == "CHANGELOG.md" else TODO), \
         mock.patch("kopi.run_json", side_effect=_import_mocks(items)) as rj, mock.patch("kopi.run") as run:
        rc = cli.main(list(argv))
    return rc, rj, run


def test_import_adds_items_with_status():
    rc, rj, run = _run_import([])
    assert rc == 0
    created = [c for c in rj.call_args_list if c.args[1] == "item-create"]
    assert len(created) == 5 and run.call_count == 5
    # first plan entry is Done -> option o3; the Todo item uses o1
    assert run.call_args_list[0].args[-1] == "o3"
    assert run.call_args_list[2].args[-1] == "o1"
    assert run.call_args_list[3].args[-1] == "o2"
    assert run.call_args_list[0].args[:2] == ("project", "item-edit")


def test_import_skips_when_items_exist(capsys):
    rc, rj, run = _run_import([{"id": "x"}])
    assert rc == 0
    run.assert_not_called()
    assert "already has items" in capsys.readouterr().out


def test_import_force_imports_anyway():
    rc, rj, run = _run_import([{"id": "x"}], argv=("--import", "--force"))
    assert rc == 0 and run.call_count == 5


def test_import_dry_run_touches_nothing(capsys):
    rc, rj, run = _run_import([], argv=("--import", "--dry-run"))
    assert rc == 0
    rj.assert_not_called()
    run.assert_not_called()
    assert "5 items would be imported" in capsys.readouterr().out


def test_import_missing_status_option():
    def fake(*a):
        return {"item-list": {"items": []}, "view": {"id": "P"},
                "field-list": {"fields": [{"id": "F", "name": "Status", "options": [{"id": "o", "name": "Todo"}]}]}}[a[1]]
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="x"), \
         mock.patch("kopi.fetch_linked", return_value=[]), \
         mock.patch("kopi.ensure_project", return_value=(2, False)), \
         mock.patch("kopi._read", side_effect=lambda p: CHANGELOG if p == "CHANGELOG.md" else TODO), \
         mock.patch("kopi.run_json", side_effect=fake), mock.patch("kopi.run"):
        assert cli.main(["--import"]) == 1


def test_fetch_linked_maps_states():
    issues = '[{"number":1,"title":"Open bug","state":"OPEN","url":"u1"},{"number":2,"title":"Fixed","state":"CLOSED","url":"u2"}]'
    prs = ('[{"number":3,"title":"WIP","state":"OPEN","url":"u3"},{"number":4,"title":"Shipped","state":"MERGED","url":"u4"},'
           '{"number":5,"title":"Abandoned","state":"CLOSED","url":"u5"}]')
    with mock.patch("kopi.run", side_effect=[issues, prs]) as run:
        linked = cli.fetch_linked(("lateralblast", "kopi"))
    assert linked == [("Todo", "Open bug", "u1"), ("Done", "Fixed", "u2"),
                      ("In Progress", "WIP", "u3"), ("Done", "Shipped", "u4")]
    assert run.call_args_list[0].args[:4] == ("issue", "list", "--repo", "lateralblast/kopi")


def test_fetch_linked_failure_is_a_warning(capsys):
    with mock.patch("kopi.run", side_effect=cli.GhError("issues disabled")):
        assert cli.fetch_linked(("o", "r")) == []
    assert "warning: could not list issues" in capsys.readouterr().err


def test_fetch_linked_no_repo():
    assert cli.fetch_linked(None) == []


def test_build_plan_skips_todo_duplicating_linked():
    plan, skipped = cli.build_plan(CHANGELOG, TODO, [("Todo", "Add a --json output flag", "u")])
    assert "Add a `--json` output flag." in skipped
    assert all("--json" not in t for _, t, _ in plan)


def test_import_adds_linked_items_by_url():
    rc, rj, run = _run_import([], linked=[("Todo", "Open bug", "https://x/1"), ("Done", "Fixed", "https://x/2")])
    assert rc == 0
    added = [c for c in rj.call_args_list if c.args[1] == "item-add"]
    assert [c.args[c.args.index("--url") + 1] for c in added] == ["https://x/1", "https://x/2"]
    assert run.call_count == 5 + 2
    assert run.call_args_list[-1].args[-1] == "o3"  # closed issue -> Done


def test_import_dry_run_lists_linked(capsys):
    rc, rj, run = _run_import([], argv=("--import", "--dry-run"), linked=[("Todo", "Open bug", "https://x/1")])
    out = capsys.readouterr().out
    assert "Todo\tOpen bug\thttps://x/1" in out and "6 items would be imported" in out


BOARD = [
    {"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"},
    {"id": "B", "title": "Add a `--json` output flag.", "status": "Todo"},
    {"id": "C", "title": "Run against real GitHub.", "status": "Todo"},
    {"id": "D", "title": "Auth check before every command, with tests.", "status": "Todo"},
]


def _run_update(items=BOARD, argv=("--update",), project=True, todo=None, linked=(), issues_md=""):
    def fake(*a):
        return {"list": {"projects": [{"number": 2, "title": "kopi", "url": "u"}] if project else []},
                "item-list": {"items": items}, "view": {"id": "PID"},
                "field-list": {"fields": [{"id": "FID", "name": "Status", "options": [
                    {"id": "o1", "name": "Todo"}, {"id": "o2", "name": "In Progress"}, {"id": "o3", "name": "Done"}]}]},
                "item-create": {"id": "NEW"}, "item-add": {"id": "LNEW"}}[a[1]]
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
         mock.patch("kopi.git_repo", return_value=("lateralblast", "kopi")), \
         mock.patch("kopi.fetch_linked", return_value=list(linked)), \
         mock.patch("kopi._read", side_effect=lambda p: CHANGELOG if p == "CHANGELOG.md" else (
             issues_md if p in ("ISSUES.md", "PRS.md") else (todo or TODO))), \
         mock.patch("kopi.run_json", side_effect=fake) as rj, mock.patch("kopi.run") as run:
        rc = cli.main(list(argv))
    return rc, rj, run


def test_update_adds_missing_and_moves_forward():
    rc, rj, run = _run_update()
    assert rc == 0
    created = [c.args[c.args.index("--title") + 1] for c in rj.call_args_list if c.args[1] == "item-create"]
    assert created == ["[0.0.2] Auth check before every command.", "Created the project."]
    edits = [(c.args[c.args.index("--id") + 1], c.args[-1]) for c in run.call_args_list]
    # two new Done items, C Todo -> In Progress, D (done in TODO, covered by changelog) Todo -> Done
    assert edits == [("NEW", "o3"), ("NEW", "o3"), ("C", "o2"), ("D", "o3")]


def test_update_never_moves_backwards_or_duplicates():
    board = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"},
             {"id": "A2", "title": "[0.0.2] Auth check before every command.", "status": "Done"},
             {"id": "B", "title": "Add a `--json` output flag.", "status": "In Progress"},  # TODO says Todo
             {"id": "C", "title": "Run against real GitHub.", "status": "Done"},  # TODO says In Progress
             {"id": "E", "title": "Created the project.", "status": "Done"}]
    rc, rj, run = _run_update(board)
    assert rc == 0
    run.assert_not_called()
    assert not [c for c in rj.call_args_list if c.args[1] == "item-create"]


def test_update_up_to_date_message(capsys):
    board = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"},
             {"id": "A2", "title": "[0.0.2] Auth check before every command.", "status": "Done"},
             {"id": "B", "title": "Add a `--json` output flag.", "status": "Todo"},
             {"id": "C", "title": "Run against real GitHub.", "status": "In Progress"},
             {"id": "E", "title": "Created the project.", "status": "Done"}]
    rc, _, _ = _run_update(board)
    assert rc == 0 and "up to date" in capsys.readouterr().out


def test_update_dry_run_writes_nothing(capsys):
    rc, rj, run = _run_update(argv=("--update", "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 0
    run.assert_not_called()
    assert not [c for c in rj.call_args_list if c.args[1] in ("item-create", "view", "field-list")]
    assert "add\tDone\t[0.0.2] Auth check before every command." in out
    assert "move\tTodo -> In Progress\tRun against real GitHub." in out
    assert "2 to add, 2 to move" in out


def test_update_requires_existing_project(capsys):
    rc, _, _ = _run_update(project=False)
    assert rc == 1 and "run --init or --import first" in capsys.readouterr().err


def test_modes_are_mutually_exclusive():
    import pytest
    with mock.patch("kopi.check_auth"), pytest.raises(SystemExit):
        cli.main(["--update", "--import"])


def test_update_similar_but_distinct_items_are_both_kept():
    """Regression: a new open item sharing most words with an existing one must still be added."""
    board = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"},
             {"id": "A2", "title": "[0.0.2] Auth check before every command.", "status": "Done"},
             {"id": "X", "title": "Run `--import` for real against the `kopi` project and confirm item statuses "
                                  "(only `--dry-run` was run live).", "status": "Todo"}]
    todo = ("- [ ] Run `--update` for real against the `kopi` project (only `--dry-run` and mocks so far).\n"
            "- [ ] Run `--import` for real against the `kopi` project and confirm item statuses "
            "(only `--dry-run` was run live).\n")
    rc, rj, run = _run_update(board, todo=todo)
    assert rc == 0
    created = [c.args[c.args.index("--title") + 1] for c in rj.call_args_list if c.args[1] == "item-create"]
    assert created == ["Run `--update` for real against the `kopi` project (only `--dry-run` and mocks so far)."]
    run.assert_called_once()  # only the new item's status; the existing --import item is untouched


def test_update_sweep_moves_reworded_completed_items_to_done():
    board = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"},
             {"id": "X", "title": "Run `--import` for real against the `kopi` project and confirm item statuses "
                                  "(only `--dry-run` was run live).", "status": "Todo"},
             {"id": "J", "title": "Add a `--json` output flag.", "status": "In Progress"},
             {"id": "K", "title": "Unrelated open item about tagging a release.", "status": "Todo"}]
    changelog = CHANGELOG + "\n## [0.0.3]\n- Added a JSON output flag.\n"
    todo = ("- [x] Ran `--import` for real against the `kopi` project; 31 items created with correct statuses.\n"
            "- [ ] Unrelated open item about tagging a release.\n")
    def fake(*a):
        return {"list": {"projects": [{"number": 2, "title": "kopi", "url": "u"}]}, "item-list": {"items": board},
                "view": {"id": "P"}, "field-list": {"fields": [{"id": "F", "name": "Status", "options": [
                    {"id": "o1", "name": "Todo"}, {"id": "o2", "name": "In Progress"}, {"id": "o3", "name": "Done"}]}]},
                "item-create": {"id": "NEW"}}[a[1]]
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="x"), \
         mock.patch("kopi.git_repo", return_value=("x", "kopi")), mock.patch("kopi.fetch_linked", return_value=[]), \
         mock.patch("kopi._read", side_effect=lambda p: changelog if p == "CHANGELOG.md" else todo), \
         mock.patch("kopi.run_json", side_effect=fake) as rj, mock.patch("kopi.run") as run:
        assert cli.main(["--update"]) == 0
    created = [c.args[c.args.index("--title") + 1] for c in rj.call_args_list if c.args[1] == "item-create"]
    edits = {c.args[c.args.index("--id") + 1]: c.args[-1] for c in run.call_args_list if "NEW" != c.args[c.args.index("--id") + 1]}
    # X (reworded in TODO) and J (matches changelog 0.0.3) move to Done; K stays open; neither is re-added
    assert edits == {"X": "o3", "J": "o3"}
    assert created == ["[0.0.2] Auth check before every command."]


SETTLED = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"},
           {"id": "A2", "title": "[0.0.2] Auth check before every command.", "status": "Done"},
           {"id": "B", "title": "Add a `--json` output flag.", "status": "Todo"},
           {"id": "C", "title": "Run against real GitHub.", "status": "In Progress"},
           {"id": "E", "title": "Created the project.", "status": "Done"}]


def _linked_item(id_, title, status, url):
    return {"id": id_, "title": title, "status": status, "content": {"type": "Issue", "url": url}}


def test_update_adds_new_issues_and_prs_by_url():
    linked = [("Todo", "Open bug", "https://x/1"), ("In Progress", "WIP PR", "https://x/2")]
    rc, rj, run = _run_update(SETTLED, linked=linked)
    assert rc == 0
    added = [c.args[c.args.index("--url") + 1] for c in rj.call_args_list if c.args[1] == "item-add"]
    assert added == ["https://x/1", "https://x/2"]
    assert [c.args[-1] for c in run.call_args_list] == ["o1", "o2"]


def test_update_matches_existing_issue_by_url_and_moves_forward_only():
    board = SETTLED + [_linked_item("L1", "Open bug", "Todo", "https://x/1"),
                       _linked_item("L2", "Reopened thing", "Done", "https://x/2")]
    linked = [("Done", "Open bug", "https://x/1"),            # closed on GitHub -> move to Done
              ("Todo", "Reopened thing", "https://x/2")]      # reopened -> never moved backwards
    rc, rj, run = _run_update(board, linked=linked)
    assert rc == 0
    assert not [c for c in rj.call_args_list if c.args[1] == "item-add"]
    assert [(c.args[c.args.index("--id") + 1], c.args[-1]) for c in run.call_args_list] == [("L1", "o3")]


def test_update_open_issue_item_is_not_swept_or_title_matched():
    """A linked item must never be closed by a changelog entry that merely shares words with its title."""
    board = SETTLED + [_linked_item("L1", "Auth check before every command fails", "Todo", "https://x/1")]
    linked = [("Todo", "Auth check before every command fails", "https://x/1")]
    rc, rj, run = _run_update(board, linked=linked)
    assert rc == 0
    run.assert_not_called()


def test_update_todo_duplicating_new_issue_is_not_added():
    todo = TODO + "- [ ] Fix the flaky login test.\n"
    linked = [("Todo", "Fix the flaky login test", "https://x/9")]
    rc, rj, run = _run_update(SETTLED, todo=todo, linked=linked)
    created = [c.args[c.args.index("--title") + 1] for c in rj.call_args_list if c.args[1] == "item-create"]
    assert "Fix the flaky login test." not in created
    assert [c.args[c.args.index("--url") + 1] for c in rj.call_args_list if c.args[1] == "item-add"] == ["https://x/9"]


def test_update_dry_run_lists_new_issue_url(capsys):
    rc, rj, run = _run_update(SETTLED, argv=("--update", "--dry-run"), linked=[("Todo", "Open bug", "https://x/1")])
    out = capsys.readouterr().out
    assert rc == 0 and "add\tTodo\tOpen bug\thttps://x/1" in out
    run.assert_not_called()


def test_item_url_fallback_from_repository_and_number():
    assert cli._item_url({"content": {"type": "PullRequest", "number": 4, "repository": "o/r"}}) == "https://github.com/o/r/pull/4"
    assert cli._item_url({"content": {"type": "Issue", "number": 5, "repository": "o/r"}}) == "https://github.com/o/r/issues/5"
    assert cli._item_url({"content": {"type": "DraftIssue"}}) is None


def _archived(run):
    return [c.args[c.args.index("--id") + 1] for c in run.call_args_list if c.args[:2] == ("project", "item-archive")]


ORPHANS = SETTLED + [{"id": "O", "title": "Old idea nobody wants", "status": "Todo"},
                     {"id": "OP", "title": "Half-done old idea", "status": "In Progress"},
                     {"id": "OD", "title": "Old finished work", "status": "Done"},
                     _linked_item("L1", "Some issue", "Todo", "https://x/1")]


def test_archive_archives_open_orphans_only():
    rc, rj, run = _run_update(ORPHANS, argv=("--archive",), linked=[("Todo", "Some issue", "https://x/1")])
    assert rc == 0
    assert _archived(run) == ["O", "OP"]  # not the Done orphan, not the linked issue, not matched items
    assert not [c for c in rj.call_args_list if c.args[1] in ("item-create", "item-add")]


def test_archive_dry_run_archives_nothing(capsys):
    rc, rj, run = _run_update(ORPHANS, argv=("--archive", "--dry-run"), linked=[("Todo", "Some issue", "https://x/1")])
    out = capsys.readouterr().out
    assert rc == 0 and _archived(run) == []
    assert "archive\tTodo\tOld idea nobody wants" in out and "2 to archive" in out
    assert "Old finished work" not in out and "Some issue" not in out


def test_archive_skips_reworded_orphan_unless_forced(capsys):
    board = [i for i in SETTLED if i["id"] != "B"] + [{"id": "R", "title": "Add JSON list view count", "status": "Todo"}]
    # TODO has "Add a `--json` output flag." which the board lacks; the orphan shares 50% of its words
    rc, rj, run = _run_update(board, argv=("--archive",))
    assert rc == 0 and _archived(run) == []
    assert "1 skipped as possibly reworded" in capsys.readouterr().out
    rc, rj, run = _run_update(board, argv=("--archive", "--force"))
    assert _archived(run) == ["R"]


def test_archive_nothing_to_do(capsys):
    rc, _, run = _run_update(SETTLED, argv=("--archive",))
    assert rc == 0 and _archived(run) == [] and "Archived 0 items" in capsys.readouterr().out


def test_diff_reports_every_kind_of_difference(capsys):
    board = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"},
             # [0.0.2] changelog entry missing from board -> files-only
             {"id": "B", "title": "Add a `--json` output flag.", "status": "Done"},        # file says Todo -> ahead
             {"id": "C", "title": "Run against real GitHub.", "status": "Todo"},            # file says In Progress -> behind
             {"id": "E", "title": "Created the project.", "status": "Done"},
             {"id": "O", "title": "Old idea nobody wants", "status": "Todo"}]               # -> board-only
    rc, rj, run = _run_update(board, argv=("--diff",), linked=[("Todo", "New issue", "https://x/3")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "files-only\tDone\t[0.0.2] Auth check before every command." in out
    assert "github-only\tTodo\tNew issue\thttps://x/3" in out
    assert "board-only\tTodo\tOld idea nobody wants" in out
    assert "behind\tTodo -> In Progress\tRun against real GitHub." in out
    assert "ahead\tDone (file: Todo)\tAdd a `--json` output flag." in out
    assert "2 only in files" not in out and "1 only in files, 1 only on GitHub, 1 only on board, 1 behind, 1 ahead" in out
    run.assert_not_called()  # read-only
    assert not [c for c in rj.call_args_list if c.args[1] in ("item-create", "item-add", "view", "field-list")]


def test_diff_no_differences(capsys):
    rc, _, run = _run_update(SETTLED, argv=("--diff",))
    assert rc == 0 and "No differences in project #2" in capsys.readouterr().out
    run.assert_not_called()


def test_diff_and_archive_are_exclusive_with_other_modes():
    import pytest
    for argv in (["--diff", "--update"], ["--archive", "--import"], ["--diff", "--archive"]):
        with mock.patch("kopi.check_auth"), pytest.raises(SystemExit):
            cli.main(argv)


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


def test_dedupe_keeps_furthest_along():
    rc, rj, run = _run_update(DUPES, argv=("--dedupe",))
    assert rc == 0 and _deleted(run) == ["B", "C", "F"]


def test_dedupe_dry_run_deletes_nothing(capsys):
    rc, rj, run = _run_update(DUPES, argv=("--dedupe", "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 0 and _deleted(run) == [] and "3 duplicates to delete" in out


def test_wait_for_items_polls_until_listed(no_sleep):
    seen = [[], [], [{"id": "N1"}, {"id": "N2"}]]
    with mock.patch("kopi.run_json", side_effect=lambda *a: {"items": seen.pop(0)}):
        cli.wait_for_items(mock.Mock(owner="o"), 2, ["N1", "N2"])
    assert no_sleep.call_count == 2 and not seen


def test_wait_for_items_gives_up_with_warning(no_sleep, capsys):
    with mock.patch("kopi.run_json", return_value={"items": []}):
        cli.wait_for_items(mock.Mock(owner="o"), 2, ["N1"])
    assert "not yet listed" in capsys.readouterr().err
    assert no_sleep.call_count == cli.SETTLE_TIMEOUT // cli.SETTLE_INTERVAL


def test_update_waits_for_new_items():
    with mock.patch("kopi.wait_for_items") as wait:
        _run_update(BOARD, argv=("--update",))
    assert wait.call_args.args[2] == ["NEW"] * len(wait.call_args.args[2]) and wait.call_args.args[2]


def test_update_path_changes_directory(tmp_path):
    with mock.patch("kopi.os.chdir") as chdir:
        rc, rj, run = _run_update(BOARD, argv=("--update", str(tmp_path)))
    assert rc == 0 and chdir.call_args.args == (str(tmp_path),)


def test_import_path_changes_directory(tmp_path):
    with mock.patch("kopi.os.chdir") as chdir:
        rc, *_ = _run_import([], argv=("--import", str(tmp_path), "--dry-run"))
    assert rc == 0 and chdir.call_args.args == (str(tmp_path),)


def test_path_must_be_a_directory(capsys):
    with mock.patch("kopi.check_auth"):
        assert cli.main(["--update", "/no/such/dir"]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_every_mode_accepts_a_path(tmp_path):
    for flag in ("--update", "--diff", "--archive", "--dedupe"):
        with mock.patch("kopi.os.chdir") as chdir:
            rc, *_ = _run_update(BOARD, argv=(flag, str(tmp_path), "--dry-run") if flag != "--diff" else (flag, str(tmp_path)))
        assert rc == 0 and chdir.call_args.args == (str(tmp_path),), flag


def test_init_accepts_a_path(tmp_path):
    with mock.patch("kopi.os.chdir") as chdir:
        rc = _init([{"number": 2, "title": "kopi", "url": "u"}], argv=("--init", str(tmp_path)))[0]
    assert rc == 0 and chdir.call_args.args == (str(tmp_path),)


def test_failed_link_after_create_is_a_warning(capsys):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.run_json", return_value={"number": 7, "url": "u"}), \
         mock.patch("kopi.run", side_effect=cli.GhError("repo not found")):
        rc = cli.main(["--owner", "me", "create", "T", "--repo", "me/r"])
    cap = capsys.readouterr()
    assert rc == 0 and "Created #7" in cap.out
    assert "warning: project #7 was created but linking to me/r failed: repo not found" in cap.err


PROJECTS = [{"id": "PVT_a", "number": 2, "title": "kopi", "url": "u2", "closed": False},
            {"id": "PVT_b", "number": 1, "title": "old", "url": "u1", "closed": True}]


def _list(argv=("list",), gql=None):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="o"), \
         mock.patch("kopi.run_json", return_value={"projects": PROJECTS}) as rj, \
         mock.patch("kopi.graphql", **(gql or {"return_value": {"data": {
             "p0": {"repositories": {"nodes": [{"nameWithOwner": "o/kopi"}]}},
             "p1": {"repositories": {"nodes": []}}}}})):
        return cli.main(list(argv)), rj


def test_list_shows_header_state_and_repo(capsys):
    rc, rj = _list(("list", "--closed"))
    lines = capsys.readouterr().out.splitlines()
    assert rc == 0 and "--closed" in rj.call_args.args
    assert lines == ["NUMBER\tTITLE\tSTATE\tREPO\tURL", "2\tkopi\topen\to/kopi\tu2", "1\told\tclosed\t-\tu1"]


def test_list_survives_repo_lookup_failure(capsys):
    rc, _ = _list(gql={"side_effect": cli.GhError("boom")})
    cap = capsys.readouterr()
    assert rc == 0 and "2\tkopi\topen\t-\tu2" in cap.out and "could not look up linked repos" in cap.err


def _json_out(capsys):
    cap = capsys.readouterr()
    return json.loads(cap.out), cap


def test_json_list(capsys):
    rc, _ = _list(("--json", "list"))
    data, cap = _json_out(capsys)
    assert rc == 0 and data[0] == {"number": 2, "title": "kopi", "state": "open", "repos": ["o/kopi"], "url": "u2"}
    assert data[1]["state"] == "closed"


def test_json_update_dry_run_and_real(capsys):
    _run_update(BOARD, argv=("--json", "--update", "--dry-run"))
    data, _ = _json_out(capsys)
    assert data["dry_run"] is True and data["project"] == 2 and data["added"] and data["moved"]
    _run_update(BOARD, argv=("--json", "--update"))
    data, cap = _json_out(capsys)  # only the JSON document reaches stdout
    assert data["dry_run"] is False and "Updated project" not in cap.out


def test_json_diff_and_dedupe_and_archive(capsys):
    _run_update(BOARD, argv=("--json", "--diff"))
    data, _ = _json_out(capsys)
    assert set(data) == {"project", "files_only", "github_only", "board_only", "duplicates", "behind", "ahead", "in_sync"}
    _run_update(DUPES, argv=("--json", "--dedupe", "--dry-run"))
    data, _ = _json_out(capsys)
    assert [i["title"] for i in data["deleted"]] == ["[0.2.3] Verified  update live.", "Same idea", "Issue"]
    _run_update(ORPHANS, argv=("--json", "--archive", "--dry-run"))
    data, _ = _json_out(capsys)
    assert data["dry_run"] is True and data["archived"]


def test_json_init_and_import(capsys):
    _init([{"number": 2, "title": "kopi", "url": "u"}], argv=("--json", "--init"))
    data, cap = _json_out(capsys)
    assert data == {"number": 2, "created": False} and "Project exists" not in cap.out
    _run_import([], argv=("--json", "--import", "--dry-run"), linked=[("Todo", "Bug", "https://x/1")])
    data, _ = _json_out(capsys)
    assert data["dry_run"] is True and {"status": "Todo", "title": "Bug", "url": "https://x/1"} in data["items"]


def test_diff_flags_duplicates_once(capsys):
    board = BOARD + [{"id": "A2", "title": "[0.0.1] Single-file CLI.", "status": "Done"}]
    _run_update(board, argv=("--diff",))
    out = capsys.readouterr().out
    assert "duplicate\tDone\t[0.0.1] Single-file CLI.\t(kept copy: Done)" in out
    assert "board-only\tDone\t[0.0.1] Single-file CLI." not in out  # not also listed as board-only
    assert "1 duplicates (see --dedupe)" in out
    _run_update(board, argv=("--json", "--diff"))
    data, _ = _json_out(capsys)
    assert [d["title"] for d in data["duplicates"]] == ["[0.0.1] Single-file CLI."]
    assert all(i["title"] != "[0.0.1] Single-file CLI." for i in data["board_only"])


def _created_titles(run_json_mock):
    return [c.args[c.args.index("--title") + 1] for c in run_json_mock.call_args_list if c.args[1] == "item-create"]


def test_update_single_file_only_reads_that_file(tmp_path):
    f = tmp_path / "CHANGELOG.md"
    f.write_text("x")
    with mock.patch("kopi.os.chdir") as chdir:
        rc, rj, run = _run_update([], argv=("--update", str(f)), linked=[("Todo", "An issue", "https://x/1")])
    titles = _created_titles(rj)
    assert rc == 0 and chdir.call_args.args == (str(tmp_path),)
    assert titles and all(t.startswith("[0.0.") for t in titles)  # changelog only: no TODO items
    assert not any(c.args[1] == "item-add" for c in rj.call_args_list)  # and no issues/PRs

    f = tmp_path / "TODO.md"
    f.write_text("x")
    with mock.patch("kopi.os.chdir"):
        rc, rj, run = _run_update([], argv=("--update", str(f)))
    titles = _created_titles(rj)
    assert rc == 0 and titles and not any(t.startswith("[0.0.") for t in titles)


def test_import_single_file_dry_run(tmp_path, capsys):
    f = tmp_path / "TODO.md"
    f.write_text("x")
    with mock.patch("kopi.os.chdir"):
        rc, *_ = _run_import([], argv=("--import", str(f), "--dry-run"), linked=[("Todo", "Bug", "https://x/1")])
    out = capsys.readouterr().out
    assert rc == 0 and "Run against real GitHub." in out and "Single-file CLI" not in out and "Bug" not in out


def test_file_argument_errors(tmp_path, capsys):
    other = tmp_path / "README.md"
    other.write_text("x")
    f = tmp_path / "TODO.md"
    f.write_text("x")
    with mock.patch("kopi.check_auth"):
        assert cli.main(["--update", str(other)]) == 1       # not a source file
        assert cli.main(["--diff", str(f)]) == 1             # only --import/--update take a file
        assert cli.main(["--import", str(tmp_path / "CHANGELOG.md")]) == 1  # missing
    err = capsys.readouterr().err
    assert "only CHANGELOG.md or TODO.md" in err and "expected a directory" in err and "no such file" in err


def _undone(run):
    return [c.args[c.args.index("--id") + 1] for c in run.call_args_list
            if c.args[:2] == ("project", "item-archive") and "--undo" in c.args]


def test_archive_logs_and_restore_undoes_it(capsys):
    _run_update(ORPHANS, argv=("--archive",), linked=[("Todo", "Some issue", "https://x/1")])
    log = cli.load_archive_log()
    assert [e["id"] for e in log] == ["O", "OP"] and log[0]["owner"] == "lateralblast" and log[0]["project"] == 2
    _run_update([], argv=("--restore", "--dry-run"))
    assert "Dry run: 2 to restore" in capsys.readouterr().out and len(cli.load_archive_log()) == 2
    rc, rj, run = _run_update([], argv=("--restore",))
    assert rc == 0 and _undone(run) == ["O", "OP"] and cli.load_archive_log() == []
    assert "Restored 2 items" in capsys.readouterr().out


def test_archive_dry_run_does_not_log():
    _run_update(ORPHANS, argv=("--archive", "--dry-run"), linked=[("Todo", "Some issue", "https://x/1")])
    assert cli.load_archive_log() == []


def test_restore_only_this_project_and_by_id(capsys):
    cli.save_archive_log([{"owner": "lateralblast", "project": 2, "id": "A", "title": "mine", "status": "Todo"},
                          {"owner": "lateralblast", "project": 9, "id": "B", "title": "other project", "status": "Todo"}])
    rc, rj, run = _run_update([], argv=("--restore",))
    assert _undone(run) == ["A"] and [e["id"] for e in cli.load_archive_log()] == ["B"]
    rc, rj, run = _run_update([], argv=("--restore", "--id", "X1", "--id", "X2"))
    assert _undone(run) == ["X1", "X2"]


def test_restore_failure_is_a_warning_and_keeps_log(capsys):
    cli.save_archive_log([{"owner": "lateralblast", "project": 2, "id": "A", "title": "mine", "status": "Todo"}])
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
         mock.patch("kopi.git_repo", return_value=("lateralblast", "kopi")), \
         mock.patch("kopi.run_json", return_value={"projects": [{"number": 2, "title": "kopi", "url": "u"}]}), \
         mock.patch("kopi.run", side_effect=cli.GhError("nope")):
        rc = cli.main(["--json", "--restore"])
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    assert rc == 0 and data["restored"] == [] and data["failed"][0]["id"] == "A" and "could not restore A" in cap.err
    assert [e["id"] for e in cli.load_archive_log()] == ["A"]


def _archived_pages():
    node = lambda i, arch, title, status: {"id": i, "isArchived": arch, "content": {"__typename": "DraftIssue", "title": title},
                                          "fieldValueByName": {"name": status} if status else None}
    return [{"data": {"node": {"items": {"pageInfo": {"hasNextPage": True, "endCursor": "C1"},
                                         "nodes": [node("LIVE", False, "live", "Todo"), node("A1", True, "gone one", "Todo")]}}}},
            {"data": {"node": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                         "nodes": [node("A2", True, "gone two", None)]}}}}]


def test_restore_all_finds_items_archived_outside_kopi(capsys):
    with mock.patch("kopi.graphql", side_effect=_archived_pages()) as gql:
        rc, rj, run = _run_update([], argv=("--restore", "--all"))
    assert rc == 0 and _undone(run) == ["A1", "A2"]  # not the live item; not in kopi's log
    assert gql.call_args_list[0].kwargs == {"id": "PID"} and gql.call_args_list[1].kwargs == {"id": "PID", "after": "C1"}
    assert "Restored 2 items" in capsys.readouterr().out
    with mock.patch("kopi.graphql", side_effect=_archived_pages()):
        rc, rj, run = _run_update([], argv=("--json", "--restore", "--all", "--dry-run"))
    data = json.loads(capsys.readouterr().out)
    assert _undone(run) == [] and [(e["id"], e["status"]) for e in data["restored"]] == [("A1", "Todo"), ("A2", "none")]


def test_restore_all_rejects_id_and_other_modes(capsys):
    with mock.patch("kopi.graphql") as gql:
        assert _run_update([], argv=("--restore", "--all", "--id", "X"))[0] == 1
        assert not gql.called
    with pytest.raises(SystemExit):
        cli.main(["--update", "--all"])


def test_restore_nothing_logged(capsys):
    rc, rj, run = _run_update([], argv=("--restore",))
    assert rc == 0 and _undone(run) == [] and "Nothing to restore" in capsys.readouterr().out


def test_archive_single_item_by_id_any_status():
    rc, rj, run = _run_update(ORPHANS, argv=("--archive", "--id", "OD", "--id", "L1"))
    assert rc == 0 and _archived(run) == ["OD", "L1"]  # a Done item and a linked one: explicit, so allowed
    assert [e["id"] for e in cli.load_archive_log()] == ["OD", "L1"]  # logged, so --restore can undo it
    assert not any(c.args[1] == "item-add" for c in rj.call_args_list)  # no file/issue sync for single items


def test_archive_single_item_by_match_and_dry_run(capsys):
    rc, rj, run = _run_update(ORPHANS, argv=("--archive", "--match", "half-done", "--dry-run"))
    assert rc == 0 and _archived(run) == [] and cli.load_archive_log() == []
    assert "archive\tIn Progress\tHalf-done old idea" in capsys.readouterr().out
    rc, rj, run = _run_update(ORPHANS, argv=("--archive", "--match", "half-done", "--json"))
    assert _archived(run) == ["OP"]
    assert json.loads(capsys.readouterr().out)["archived"] == [{"status": "In Progress", "title": "Half-done old idea"}]


def test_archive_single_item_errors(capsys):
    assert _run_update(ORPHANS, argv=("--archive", "--id", "NOPE"))[0] == 1
    assert _run_update(ORPHANS, argv=("--archive", "--match", "old"))[0] == 1   # several match
    assert _run_update(ORPHANS, argv=("--archive", "--match", "zzz"))[0] == 1   # none match
    err = capsys.readouterr().err
    assert "no item with id NOPE" in err and "matched 3 items" in err and "matched 0 items" in err
    assert "OP\tHalf-done old idea" in err


def _edited(run):
    return [(c.args[c.args.index("--id") + 1], c.args[c.args.index("--single-select-option-id") + 1])
            for c in run.call_args_list if c.args[:2] == ("project", "item-edit")]


def test_done_by_text_moves_todo_and_in_progress(capsys):
    rc, rj, run = _run_update(ORPHANS, argv=("--done", "old idea nobody"))
    assert rc == 0 and _edited(run) == [("O", "o3")]
    assert "Moved to Done: Old idea nobody wants (was Todo)" in capsys.readouterr().out
    rc, rj, run = _run_update(ORPHANS, argv=("--done", "OP"))  # by id; In Progress -> Done
    assert _edited(run) == [("OP", "o3")]


def test_done_already_done_and_dry_run(capsys):
    rc, rj, run = _run_update(ORPHANS, argv=("--done", "OD"))
    assert rc == 0 and _edited(run) == [] and "Already Done" in capsys.readouterr().out
    rc, rj, run = _run_update(ORPHANS, argv=("--done", "OP", "--dry-run"))
    assert _edited(run) == [] and "move\tIn Progress -> Done\tHalf-done old idea" in capsys.readouterr().out


def test_done_json_and_errors(capsys):
    _run_update(ORPHANS, argv=("--json", "--done", "O"))
    assert json.loads(capsys.readouterr().out)["moved"] == [{"title": "Old idea nobody wants", "from": "Todo", "to": "Done"}]
    assert _run_update(ORPHANS, argv=("--done", "old"))[0] == 1       # ambiguous text
    assert _run_update(ORPHANS, argv=("--done", "zzz"))[0] == 1       # no match
    assert "matched 3 items" in capsys.readouterr().err


def test_done_value_is_not_treated_as_a_path():
    with mock.patch("kopi.os.chdir") as chdir:
        _run_update(ORPHANS, argv=("--done", "O"))
    chdir.assert_not_called()


SQUASH_BOARD = [
    {"id": "J", "title": "Add a `--json` output flag.", "status": "Done"},        # [ ] in TODO.md
    {"id": "R", "title": "Run against real GitHub.", "status": "Done"},           # [~] in TODO.md
    {"id": "A", "title": "Auth check before every command, with tests.", "status": "Done"},  # already [x]
]


def test_squash_ticks_todo_entries_done_on_board(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc, rj, run = _run_update(SQUASH_BOARD, argv=("--squash",))
    out = capsys.readouterr().out
    assert rc == 0 and "2 TODO.md items ticked" in out
    new = (tmp_path / "TODO.md").read_text()
    assert "- [x] Add a `--json` output flag.\n" in new and "- [x] Run against real GitHub.\n" in new
    assert new.count("[x]") == 4 and "[ ]" not in new and "[~]" not in new
    assert not any(c.args[:2] == ("project", "item-edit") for c in run.call_args_list)  # board untouched
    assert not any(c.args[1] == "item-add" for c in rj.call_args_list)  # no issue/PR lookups


def test_squash_leaves_open_board_items_and_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    board = [{"id": "J", "title": "Add a `--json` output flag.", "status": "In Progress"}] + SQUASH_BOARD[1:]
    rc, *_ = _run_update(board, argv=("--squash", "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 0 and "tick\tIn Progress\tRun against real GitHub." in out and "1 TODO.md items to tick" in out
    assert not (tmp_path / "TODO.md").exists()
    rc, *_ = _run_update(board, argv=("--squash",))
    new = (tmp_path / "TODO.md").read_text()
    assert "- [ ] Add a `--json` output flag." in new and "- [x] Run against real GitHub." in new


def test_squash_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run_update(SQUASH_BOARD, argv=("--json", "--squash", "--dry-run"))
    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] is True and sorted(t["from"] for t in data["ticked"]) == ["In Progress", "Todo"]


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


def test_set_each_field_type():
    cases = [("Notes", "say \"hi\"", '{text: "say \\"hi\\""}'), ("Points", "3", "{number: 3.0}"),
             ("Due", "2026-10-31", '{date: "2026-10-31"}'), ("status", "done", '{singleSelectOptionId: "o3"}'),
             ("Sprint", "sprint 1", '{iterationId: "it1"}')]
    for field, value, literal in cases:
        rc, mut = _set("set", "2", "Old idea nobody", "--field", field, "--value", value)
        assert rc == 0 and len(mut) == 1, field
        assert literal in mut[0] and 'itemId: "O"' in mut[0] and 'projectId: "PID"' in mut[0]


def test_set_clear_dry_run_and_json(capsys):
    rc, mut = _set("set", "2", "OP", "--field", "Notes", "--clear")
    assert rc == 0 and "clearProjectV2ItemFieldValue" in mut[0]
    rc, mut = _set("--dry-run", "set", "2", "OP", "--field", "Notes", "--value", "x")
    assert rc == 0 and mut == [] and "set\tNotes\tx\tHalf-done old idea" in capsys.readouterr().out
    capsys.readouterr()
    rc, mut = _set("--json", "set", "2", "OP", "--field", "Points", "--value", "5")
    assert json.loads(capsys.readouterr().out) == {"project": 2, "dry_run": False, "item": {"id": "OP", "title": "Half-done old idea"},
                                                   "field": "Points", "value": "5"}


def test_set_errors(capsys):
    for argv in (("--field", "Nope", "--value", "x"), ("--field", "Labels", "--value", "x"),
                 ("--field", "Points", "--value", "abc"), ("--field", "Due", "--value", "31/10"),
                 ("--field", "Status", "--value", "Maybe"), ("--field", "Notes"),
                 ("--field", "Notes", "--value", "x", "--clear")):
        assert _set("set", "2", "OP", *argv)[0] == 1, argv
    assert _set("set", "2", "old", "--field", "Notes", "--value", "x")[0] == 1  # ambiguous item
    assert _set("set", "2", "OP", "--field", "Notes", "--value", "x", gql_result={"errors": [{"message": "boom"}]})[0] == 1
    err = capsys.readouterr().err
    assert "no field 'Nope'" in err and "settable fields: Notes, Points, Due, Status, Sprint" in err
    assert "can't be set here" in err and "not a number" in err and "YYYY-MM-DD" in err
    assert "choose from: Todo, Done" in err and "exactly one of --value or --clear" in err and "boom" in err


def test_list_todo_shows_project_todo_items(capsys):
    rc, rj, run = _run_update(ORPHANS, argv=("--list", "todo"))
    lines = capsys.readouterr().out.splitlines()
    assert rc == 0 and [l.split("\t")[0] for l in lines] == [i["id"] for i in ORPHANS if i["status"] == "Todo"]
    assert "O\tOld idea nobody wants" in lines
    assert "OP" not in "".join(lines) and "OD" not in "".join(lines)  # not In Progress or Done


def test_list_todo_json_and_unknown_kind(capsys):
    _run_update(ORPHANS, argv=("--json", "--list", "todo"))
    data = json.loads(capsys.readouterr().out)
    assert {"id": "O", "status": "Todo", "title": "Old idea nobody wants"} in data
    assert any(d.get("url") == "https://x/1" for d in data)  # linked items carry their URL
    try:
        cli.main(["--list", "nonsense"])
    except SystemExit as e:
        assert e.code == 2


def test_list_todo_local_reads_only_open_entries_without_github(capsys):
    with mock.patch("kopi.check_auth") as auth, mock.patch("kopi.run_json") as rj, mock.patch("kopi.run") as run, \
         mock.patch("kopi._read", return_value=TODO):
        rc = cli.main(["--list", "todo", "--local"])
    assert rc == 0 and capsys.readouterr().out.splitlines() == ["Add a `--json` output flag."]  # not [~] or [x]
    auth.assert_not_called(); rj.assert_not_called(); run.assert_not_called()
    with mock.patch("kopi._read", return_value=TODO):
        cli.main(["--json", "--list", "todo", "--local"])
    assert json.loads(capsys.readouterr().out) == [{"status": "Todo", "title": "Add a `--json` output flag."}]


def test_local_requires_list():
    try:
        cli.main(["--local", "--diff"])
    except SystemExit as e:
        assert e.code == 2


def test_list_done_project_and_local(capsys):
    _run_update(ORPHANS, argv=("--list", "done"))
    ids = [l.split("\t")[0] for l in capsys.readouterr().out.splitlines()]
    assert ids == [i["id"] for i in ORPHANS if i["status"] == "Done"] and "OD" in ids and "O" not in ids
    with mock.patch("kopi._read", return_value=TODO):
        assert cli.main(["--list", "done", "--local"]) == 0
    assert capsys.readouterr().out.splitlines() == ["Auth check before every command, with tests.", "Created the project."]


def test_list_inprogress_project_and_local(capsys):
    for kind in ("inprogress", "in-progress"):
        _run_update(ORPHANS, argv=("--list", kind))
        ids = [l.split("\t")[0] for l in capsys.readouterr().out.splitlines()]
        assert ids == [i["id"] for i in ORPHANS if i["status"] == "In Progress"] and "OP" in ids, kind
    with mock.patch("kopi._read", return_value=TODO):
        assert cli.main(["--list", "inprogress", "--local"]) == 0
    assert capsys.readouterr().out.splitlines() == ["Run against real GitHub."]


EXPORT_BOARD = [
    {"id": "N1", "title": "Brand new idea", "status": "Todo"},
    {"id": "N2", "title": "Add a `--json` output flag.", "status": "Todo"},        # already [ ] in TODO.md
    {"id": "N3", "title": "Created the project.", "status": "Todo"},               # already [x]: must not be re-opened
    {"id": "N4", "title": "In progress thing", "status": "In Progress"},           # not Todo
    {"id": "N5", "title": "Long idea…", "status": "Todo", "content": {"body": "Long idea that was cut off in the title"}},
    {"id": "N6", "title": "Fix the bug", "status": "Todo", "content": {"url": "https://x/9", "title": "Fix the bug"}},
    {"id": "N7", "title": "Done thing", "status": "Done"},
]


def test_export_todo_adds_missing_unchecked_entries_under_open(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc, rj, run = _run_update(EXPORT_BOARD, argv=("--export", "todo"))
    out = capsys.readouterr().out
    assert rc == 0 and "3 todo items added to TODO.md (2 already present)" in out
    new = (tmp_path / "TODO.md").read_text()
    assert new.index("- [~] Run against real GitHub.\n- [ ] Brand new idea\n") < new.index("## Done")  # end of ## Open
    assert "- [ ] Long idea that was cut off in the title\n" in new and "- [ ] Fix the bug (https://x/9)\n" in new
    assert "In progress thing" not in new and "Done thing" not in new
    assert new.count("Created the project.") == 1 and "- [x] Created the project." in new  # not re-opened
    assert new.count("--json") == 1                                                          # not duplicated
    assert not any(c.args[:2] == ("project", "item-edit") for c in run.call_args_list)       # board untouched


def test_export_is_idempotent_and_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run_update(EXPORT_BOARD, argv=("--export", "todo", "--dry-run"))
    assert "Dry run: 3 todo items to export" in capsys.readouterr().out and not (tmp_path / "TODO.md").exists()
    _run_update(EXPORT_BOARD, argv=("--export", "todo"))
    exported = (tmp_path / "TODO.md").read_text()
    capsys.readouterr()
    _run_update(EXPORT_BOARD, argv=("--json", "--export", "todo"), todo=exported)  # a second run adds nothing
    data = json.loads(capsys.readouterr().out)
    assert data["added"] == [] and data["already_present"] == 5 and (tmp_path / "TODO.md").read_text() == exported


def test_insert_open_without_open_section_and_before_next_heading():
    assert cli._insert_entries("# TODO\n\n## Done\n\n- [x] a\n", "Open", ["- [ ] b"]) == "# TODO\n\n## Done\n\n- [x] a\n\n## Open\n\n- [ ] b\n"
    assert cli._insert_entries("## Open\n\n- [ ] a\n\n## Done\n", "Open", ["- [ ] b"]) == "## Open\n\n- [ ] a\n- [ ] b\n\n## Done\n"
    assert cli._insert_entries("## Open\n## Done\n", "Open", ["- [ ] b"]) == "## Open\n\n- [ ] b\n\n## Done\n"
    assert cli._insert_entries("", "Open", ["- [ ] a"]) == "# TODO\n\n## Open\n\n- [ ] a\n"  # missing file


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


def test_export_done_goes_to_top_of_done_newest_first_and_skips_changelog(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc, rj, run = _run_update(EXPORT_DONE_BOARD, argv=("--export", "done"))
    assert rc == 0 and "3 done items added to TODO.md (3 already present)" in capsys.readouterr().out
    new = (tmp_path / "TODO.md").read_text()
    assert "## Done\n\n- [x] Fixed the bug (https://x/8)\n- [x] Rewrote the parser entirely\n" \
           "- [x] Shipped the login page\n- [x] Auth check before every command" in new   # newest first, above old
    assert "0.9.9" not in new and "Single-file" not in new and new.count("Created the project.") == 1
    assert "Working on a thing" not in new and "A todo item" not in new


def test_export_inprogress_goes_to_open_as_tilde(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for kind in ("inprogress", "in-progress"):
        rc, *_ = _run_update(EXPORT_DONE_BOARD, argv=("--export", kind))
        assert rc == 0
        new = (tmp_path / "TODO.md").read_text()
        assert "- [~] Run against real GitHub.\n- [~] Working on a thing\n\n## Done" in new
        assert new.count("Run against real GitHub.") == 1 and "Shipped" not in new and "Rewrote" not in new
        (tmp_path / "TODO.md").unlink()
    capsys.readouterr()


def test_insert_entries_top_of_done_and_new_section():
    assert cli._insert_entries("## Open\n\n- [ ] a\n\n## Done\n\n- [x] old\n", "Done", ["- [x] new"], top=True) == \
        "## Open\n\n- [ ] a\n\n## Done\n\n- [x] new\n- [x] old\n"
    assert cli._insert_entries("## Open\n\n- [ ] a\n", "Done", ["- [x] n"], top=True) == "## Open\n\n- [ ] a\n\n## Done\n\n- [x] n\n"
    assert cli._insert_entries("## Done\n\n", "Done", ["- [x] n"], top=True) == "## Done\n\n- [x] n\n"


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


def test_export_issues_writes_grouped_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc, rj, run = _run_update(ISSUE_BOARD, argv=("--export", "issues"))
    assert rc == 0 and "3 issues written to ISSUES.md" in capsys.readouterr().out
    text = (tmp_path / "ISSUES.md").read_text()
    assert text.startswith("<!-- Generated by kopi --export issues from project #2 (lateralblast)")
    assert text.index("## Todo") < text.index("## Done") < text.index("## No status")
    assert "- [#3](https://github.com/o/kopi/issues/3) Login fails (bug)\n" in text
    assert "- [#7](https://github.com/o/kopi/issues/7) Add dark mode\n" in text and "Fix typo" in text
    assert "pull request" not in text and "A draft" not in text and "In Progress" not in text  # issues only
    assert not run.called  # nothing written to the board


def test_export_issues_dry_run_unchanged_and_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run_update(ISSUE_BOARD, argv=("--export", "issues", "--dry-run"))
    out = capsys.readouterr().out
    assert "export\tTodo\t#3\tLogin fails" in out and "Dry run: 3 issues would be written" in out
    assert not (tmp_path / "ISSUES.md").exists()
    _run_update(ISSUE_BOARD, argv=("--export", "issues"))
    text = (tmp_path / "ISSUES.md").read_text()
    capsys.readouterr()
    _run_update(ISSUE_BOARD, argv=("--json", "--export", "issues"), issues_md=text)
    data = json.loads(capsys.readouterr().out)
    assert data["changed"] is False and [i["number"] for i in data["issues"]] == [3, 7, 9]
    _run_update(ISSUE_BOARD, argv=("--export", "issues"), issues_md=text)
    assert "ISSUES.md is up to date (3 issues)" in capsys.readouterr().out


def test_export_issues_protects_a_hand_written_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert _run_update(ISSUE_BOARD, argv=("--export", "issues"), issues_md="# My own notes\n")[0] == 1
    assert "was not generated by kopi" in capsys.readouterr().err and not (tmp_path / "ISSUES.md").exists()
    assert _run_update(ISSUE_BOARD, argv=("--export", "issues", "--force"), issues_md="# My own notes\n")[0] == 0
    assert (tmp_path / "ISSUES.md").read_text().startswith("<!-- Generated by kopi")


def test_export_prs_writes_pull_requests_only(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc, rj, run = _run_update(ISSUE_BOARD, argv=("--export", "prs"))
    assert rc == 0 and "1 pull requests written to PRS.md" in capsys.readouterr().out
    text = (tmp_path / "PRS.md").read_text()
    assert text.startswith("<!-- Generated by kopi --export prs from project #2") and "# Pull requests" in text
    assert "## In Progress\n\n- [#8](https://github.com/o/kopi/pull/8) A pull request\n" in text
    assert "Login fails" not in text and "A draft" not in text
    assert not (tmp_path / "ISSUES.md").exists() and not run.called
    # a file generated by --export issues is not treated as a PRS.md of ours
    assert _run_update(ISSUE_BOARD, argv=("--export", "prs"), issues_md="<!-- Generated by kopi --export issues -->\n")[0] == 1
    assert "_No pull requests in the project._" in cli.render_issues([], 2, "o", "prs")


def test_render_issues_multiple_repos_and_empty():
    rows = [{"status": "Todo", "number": 1, "repository": "a/x", "title": "T1", "url": "u1", "labels": []},
            {"status": "Todo", "number": 2, "repository": "a/y", "title": "T2", "url": "u2", "labels": []}]
    assert "- [a/x#1](u1) T1\n- [a/y#2](u2) T2" in cli.render_issues(rows, 2, "o")
    assert "_No issues in the project._" in cli.render_issues([], 2, "o")


def test_export_creates_missing_todo_md_even_with_nothing_to_add(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for kind in ("todo", "inprogress", "done"):
        (tmp_path / "TODO.md").unlink(missing_ok=True)
        rc, *_ = _run_update([{"id": "X", "title": "Unrelated", "status": "Done"}], argv=("--export", kind), todo=" ")
        # todo=" ": the mocked _read returns blank text, as for a missing/empty file
        assert rc == 0 and "Created TODO.md" in capsys.readouterr().out, kind
        text = (tmp_path / "TODO.md").read_text()
        assert text.startswith("# TODO\n\nStatus:") and "## Open" in text and "## Done" in text, kind


def test_export_into_new_file_puts_entries_in_the_right_sections(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    board = [{"id": "A", "title": "Open thing", "status": "Todo"}, {"id": "B", "title": "Doing thing", "status": "In Progress"},
             {"id": "C", "title": "Finished thing", "status": "Done"}]
    for kind, expect in (("todo", "## Open\n\n- [ ] Open thing\n\n## Done\n"), ("inprogress", "## Open\n\n- [~] Doing thing\n\n## Done\n"),
                         ("done", "## Open\n\n## Done\n\n- [x] Finished thing\n")):
        (tmp_path / "TODO.md").unlink(missing_ok=True)
        _run_update(board, argv=("--export", kind), todo=" ")
        assert expect in (tmp_path / "TODO.md").read_text(), kind
    capsys.readouterr()


def test_export_dry_run_does_not_create_and_existing_file_is_not_created(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run_update(EXPORT_BOARD, argv=("--export", "todo", "--dry-run"))
    assert "Would create TODO.md" in capsys.readouterr().out and not (tmp_path / "TODO.md").exists()
    (tmp_path / "TODO.md").write_text(TODO)
    _run_update(EXPORT_BOARD, argv=("--json", "--export", "todo", "--dry-run"))
    assert json.loads(capsys.readouterr().out)["created"] is False


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


def test_import_issues_creates_missing(tmp_path, monkeypatch):
    rc, calls = _import_issues(["--import", "issues"], tmp_path, monkeypatch)
    assert rc == 0
    created = [c for c in calls if c[:2] == ("issue", "create")]
    assert [c[c.index("--title") + 1] for c in created] == ["Brand new", "Gone issue"]
    assert ("issue", "close", "https://github.com/me/r/issues/10") in calls
    assert len([c for c in calls if c[:2] == ("issue", "close")]) == 1
    gone = created[1]
    assert [gone[i + 1] for i, a in enumerate(gone) if a == "--label"] == ["X"]  # "x" matched the repo's "X"; "y" is unknown
    assert "--label" not in created[0]  # "Brand new" has none


def test_import_issues_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    rc, calls = _import_issues(["--json", "--import", "issues", "--dry-run"], tmp_path, monkeypatch)
    assert rc == 0
    assert all(c[:2] in (("issue", "list"), ("label", "list")) for c in calls)  # reads only
    out = json.loads(capsys.readouterr().out)
    assert [c["title"] for c in out["created"]] == ["Brand new", "Gone issue"]
    assert out["created"][1]["labels"] == ["X"] and out["unknown_labels"] == ["y"]
    assert out["existing"] == ["Existing one", "Hand written", "Elsewhere"]


def test_import_issues_needs_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_repo", return_value=("me", "r")), \
         mock.patch("kopi.run"):
        assert cli.main(["--import", "issues"]) == 1


def test_diff_check_exit_status(capsys):
    assert _run_update(BOARD, argv=("--diff", "--check"))[0] == 2      # drifted: distinct from the error status 1
    assert _run_update(BOARD, argv=("--diff",))[0] == 0                # no --check: report only
    assert _run_update(SETTLED, argv=("--diff", "--check"))[0] == 0    # in sync
    capsys.readouterr()
    assert _run_update(BOARD, argv=("--json", "--diff", "--check"))[0] == 2
    assert json.loads(capsys.readouterr().out)["in_sync"] is False
    _run_update(SETTLED, argv=("--json", "--diff", "--check"))
    assert json.loads(capsys.readouterr().out)["in_sync"] is True
    with pytest.raises(SystemExit):
        cli.main(["--update", "--check"])


def test_start_moves_to_in_progress(capsys):
    rc, rj, run = _run_update(ORPHANS, argv=("--start", "old idea nobody"))
    assert rc == 0 and _edited(run) == [("O", "o2")]
    assert "Moved to In Progress: Old idea nobody wants (was Todo)" in capsys.readouterr().out
    rc, rj, run = _run_update(ORPHANS, argv=("--start", "OP"))   # already In Progress
    assert _edited(run) == [] and "Already In Progress" in capsys.readouterr().out


def test_move_any_direction_with_aliases(capsys):
    rc, rj, run = _run_update(ORPHANS, argv=("--move", "OD", "todo"))   # Done -> Todo: explicit moves may go back
    assert rc == 0 and _edited(run) == [("OD", "o1")]
    rc, rj, run = _run_update(ORPHANS, argv=("--move", "O", "in-progress"))
    assert _edited(run) == [("O", "o2")]
    capsys.readouterr()
    rc, rj, run = _run_update(ORPHANS, argv=("--json", "--move", "O", "DONE", "--dry-run"))
    assert _edited(run) == []
    assert json.loads(capsys.readouterr().out)["moved"] == [{"title": "Old idea nobody wants", "from": "Todo", "to": "Done"}]
    assert _run_update(ORPHANS, argv=("--move", "O", "Blocked"))[0] == 1   # no such Status option
    assert "no option(s): Blocked" in capsys.readouterr().err
    assert _run_update(ORPHANS, argv=("--move", "old", "Done"))[0] == 1    # ambiguous text
    with pytest.raises(SystemExit):
        cli.main(["--move", "O"])  # needs ITEM and STATUS


def test_sync_runs_update_then_squash(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc, rj, run = _run_update(SQUASH_BOARD, argv=("--sync",))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Updated project #2" in out and "TODO.md items ticked" in out
    assert (tmp_path / "TODO.md").exists()  # the squash step ticked entries that are Done on the board


def test_sync_json_is_one_document(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run_update(SQUASH_BOARD, argv=("--json", "--sync", "--dry-run"))
    data = json.loads(capsys.readouterr().out)  # a single document, not two
    assert data["dry_run"] is True and data["project"] == 2
    assert set(data["update"]) == {"project", "dry_run", "added", "moved"}
    assert sorted(x["from"] for x in data["squash"]["ticked"]) == ["In Progress", "Todo"]
    with pytest.raises(SystemExit):
        cli.main(["--sync", "--update"])  # modes stay mutually exclusive


def test_import_issues_hand_written_labels():
    text = "- Plain\n- Tagged (labels: docs, nope)\n- Done one (label: X)\n"
    assert [(e[0], e[3]) for e in cli.parse_issues_file(text)] == [
        ("Plain", []), ("Tagged", ["docs", "nope"]), ("Done one", ["X"])]


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


def test_custom_status_names_are_recognised(capsys):
    rc, _ = _run_custom(STATUS_BOARD, ("--json", "--list", "done"))
    assert rc == 0 and [r["id"] for r in json.loads(capsys.readouterr().out)] == ["S1"]
    rc, _ = _run_custom(STATUS_BOARD, ("--json", "--list", "inprogress"))
    assert [r["id"] for r in json.loads(capsys.readouterr().out)] == ["D1"]
    rc, run = _run_custom(STATUS_BOARD, ("--move", "Backlogged", "done"))   # writes the board's own option name
    assert rc == 0 and _edited(run) == [("B1", "o3")]
    rc, run = _run_custom(STATUS_BOARD, ("--start", "Backlogged"))
    assert _edited(run) == [("B1", "o2")]


def test_status_map_overrides_and_validates(capsys):
    items = [{"id": "X1", "title": "Parked", "status": "Icebox"}]
    rc, run = _run_custom(items, ("--status-map", "Todo=Icebox,Done=Closed It", "--move", "Parked", "Done"),
                          options=("Icebox", "Doing", "Closed It"))
    assert rc == 0 and _edited(run) == [("X1", "o3")]
    rc, run = _run_custom(items, ("--move", "Parked", "Done"), options=("Icebox", "Doing", "Closed It"))
    err = capsys.readouterr().err
    assert rc == 1 and "no option(s): Done" in err and "--status-map" in err
    with pytest.raises(SystemExit):
        cli.main(["--status-map", "Nonsense", "list"])
    assert cli.parse_status_map("todo=Backlog, In Progress=Doing") == {"Todo": "Backlog", "In Progress": "Doing"}


def test_limits_cover_big_boards_and_warn_when_full(capsys):
    with mock.patch("kopi.run_json", return_value={"items": []}) as rj:
        cli.board_items(SimpleNs(owner="o", status_map=None), 2)
    assert rj.call_args.args[-1] == str(cli.ITEM_LIMIT)
    with mock.patch("kopi.ITEM_LIMIT", 2), mock.patch("kopi.run_json", return_value={"items": [{"id": "a"}, {"id": "b"}]}):
        cli.board_items(SimpleNs(owner="o", status_map=None), 2)
    assert "stopped at the limit of 2" in capsys.readouterr().err


def SimpleNs(**kw):
    import types
    return types.SimpleNamespace(**kw)


def test_list_issues_and_prs_from_board_and_files(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run_update(ISSUE_BOARD, argv=("--json", "--list", "issues"))
    assert [r["url"].rsplit("/", 1)[1] for r in json.loads(capsys.readouterr().out)] == ["3", "7", "9"]
    _run_update(ISSUE_BOARD, argv=("--list", "prs"))
    assert capsys.readouterr().out == "PR1\tA pull request\thttps://github.com/o/kopi/pull/8\n"
    _run_update([], argv=("--list", "issues", "--local"), issues_md=ISSUES_TEXT)  # no GitHub lookup needed
    out = capsys.readouterr().out
    assert "Brand new\n" in out and "Existing one\thttps://github.com/me/r/issues/1" in out


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


def test_close_by_number_url_and_text(capsys):
    for target, number in (("4", "4"), ("#1", "1"), ("https://github.com/me/r/issues/2", "2"), ("dark", "4")):
        rc, closes = _close("--close", target)
        assert rc == 0 and closes == [("issue", "close", number, "--repo", "me/r")], target
    assert "Closed #4: Add dark mode" in capsys.readouterr().out
    rc, closes = _close("--close", "login fails")  # two partial matches, but one is exact
    assert rc == 0 and closes == [("issue", "close", "1", "--repo", "me/r")]


def test_close_already_closed_dry_run_json_and_errors(capsys):
    rc, closes = _close("--close", "3")
    assert rc == 0 and closes == [] and "Already closed: #3 Old bug" in capsys.readouterr().out
    rc, closes = _close("--json", "--close", "4", "--dry-run")
    data = json.loads(capsys.readouterr().out)
    assert closes == [] and data["dry_run"] is True and data["closed"][0]["number"] == 4
    assert _close("--close", "login")[0] == 1      # two open matches, none exact
    assert _close("--close", "nothing like it")[0] == 1
    assert _close("--close", "99")[0] == 1         # no such number
    assert _close("--close", "old bug")[0] == 1    # closed issues are not matched by text
    err = capsys.readouterr().err
    assert "matched 2 issues" in err and "#2\tLogin fails on mobile" in err and "matched 0 issues" in err
    with pytest.raises(SystemExit):
        cli.main(["--close", "1", "--update"])


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


def test_toml_subset_parser_matches_tomllib():
    expected = {"owner": "acme", "project": "Roadmap", "status": {"Todo": "Backlog", "In Progress": "Doing"},
                "sources": {"code_comments": True},
                "comments": {"tags": ["TODO", "FIXME", "HACK"], "exclude": ["vendor/*", "*.min.js"]}}
    assert cli.parse_toml_subset(CONFIG_SAMPLE) == expected
    try:
        import tomllib
    except ImportError:
        return
    assert tomllib.loads(CONFIG_SAMPLE) == expected  # the fallback agrees with the real parser
    with pytest.raises(ValueError):
        cli.parse_toml_subset("x = {a = 1}")


def test_load_config_validates(tmp_path, capsys):
    path = tmp_path / ".kopi.toml"
    assert cli.load_config(str(path)) == {}                      # missing is fine by default
    with pytest.raises(cli.GhError):
        cli.load_config(str(path), required=True)
    path.write_text('owner = "me"\ncolour = "red"\n[sources]\nreleases = true\nnope = 1\n')
    assert cli.load_config(str(path))["owner"] == "me"
    assert "unknown setting 'colour'" in capsys.readouterr().err
    path.write_text("owner = 3\n")
    with pytest.raises(cli.GhError, match="owner must be a str"):
        cli.load_config(str(path))
    path.write_text("owner = \n")
    with pytest.raises(cli.GhError):
        cli.load_config(str(path))
    path.write_text("[sources]\nreleases = \"yes\"\n")
    with pytest.raises(cli.GhError, match="sources.releases must be a bool"):
        cli.load_config(str(path))


def test_config_supplies_owner_project_and_status_map(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kopi.toml").write_text(CONFIG_SAMPLE.replace('Todo = "Backlog"', 'Todo = "Icebox"'))
    items = [{"id": "B1", "title": "Parked", "status": "Icebox"}]  # not a built-in synonym, so only the config explains it

    def fake(*a):
        return {"list": {"projects": [{"number": 2, "title": "kopi", "url": "u"}, {"number": 5, "title": "Roadmap", "url": "u5"}]},
                "item-list": {"items": [dict(i) for i in items]}}[a[1]]
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
         mock.patch("kopi.git_repo", return_value=("lateralblast", "kopi")), \
         mock.patch("kopi.run_json", side_effect=fake) as rj:
        assert cli.main(["--json", "--list", "todo"]) == 0
        cap = capsys.readouterr()
        assert [r["id"] for r in json.loads(cap.out)] == ["B1"]            # Icebox -> Todo via [status]
        assert "Using owner: acme (from .kopi.toml)" in cap.err
        assert all(c.args[c.args.index("--owner") + 1] == "acme" for c in rj.call_args_list)
        assert any(c.args[:3] == ("project", "item-list", "5") for c in rj.call_args_list)  # the "Roadmap" project
        rj.reset_mock()
        assert cli.main(["--owner", "flag-wins", "--json", "--list", "todo"]) == 0           # flags beat the file
        assert all(c.args[c.args.index("--owner") + 1] == "flag-wins" for c in rj.call_args_list)
        capsys.readouterr()
        assert cli.main(["--status-map", "Todo=Other", "--json", "--list", "todo"]) == 0
        assert json.loads(capsys.readouterr().out) == []                     # the flag replaced [status]: Icebox is no longer Todo


def test_explicit_missing_config_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--config", "nope.toml", "--list", "todo"]) == 1
    assert "no such config file" in capsys.readouterr().err


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


def test_scan_code_comments(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _tree(tmp_path)
    got = cli.scan_code_comments()
    assert [(p, n, t, x) for p, n, t, x in got if "gen_x" not in p] == [
        ("a.py", 1, T, "handle rate limits"), ("a.py", 2, "FIXME", "breaks on empty input"),
        ("b.js", 1, T, "handle rate limits"), ("b.js", 2, T, "close the socket")]
    # .kopi.toml's [comments]: other tags, include globs, extra excludes
    cfg = {"comments": {"tags": [T], "include": ["*.py", "*.js"], "exclude": ["gen_*"]}}
    assert sorted({x for *_, x in cli.scan_code_comments(cfg)}) == ["close the socket", "handle rate limits"]


def test_code_comment_items_are_keyed_by_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _tree(tmp_path)
    items = {t: (st, b) for st, t, b in cli.code_comment_items()}
    st, body = items["[code] handle rate limits"]
    assert st == "Todo" and f"{T}: handle rate limits" in body and "a.py:1" in body and "b.js:1" in body  # one item, two places
    assert len(items) == 4  # incl. pkg/gen_x.py: only an `exclude` in .kopi.toml hides it


def test_code_comments_are_opt_in_and_not_re_added(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _tree(tmp_path)
    _run_update(SETTLED, argv=("--json", "--update", "--dry-run"))
    assert json.loads(capsys.readouterr().out)["added"] == []                   # off by default
    _run_update(SETTLED, argv=("--json", "--update", "--dry-run", "--code-comments"))
    added = json.loads(capsys.readouterr().out)["added"]
    assert sorted(a["title"] for a in added if a["title"].startswith("[code] ")) == [
        "[code] breaks on empty input", "[code] close the socket", "[code] generated", "[code] handle rate limits"]
    board = SETTLED + [{"id": "C1", "title": "[code] handle rate limits", "status": "Todo"}]
    _run_update(board, argv=("--json", "--update", "--dry-run", "--code-comments"))
    assert "[code] handle rate limits" not in [a["title"] for a in json.loads(capsys.readouterr().out)["added"]]


def test_removed_code_comment_is_an_orphan_only_while_the_source_is_on(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _tree(tmp_path)
    board = SETTLED + [{"id": "G", "title": "[code] long gone", "status": "Todo", "content": {"type": "DraftIssue"}}]
    _run_update(board, argv=("--json", "--diff", "--code-comments"))
    assert [i["title"] for i in json.loads(capsys.readouterr().out)["board_only"]] == ["[code] long gone"]
    _run_update(board, argv=("--json", "--diff"))   # source off: --archive must not sweep these away
    assert json.loads(capsys.readouterr().out)["board_only"] == []


def test_release_items_skip_versions_in_the_changelog(capsys):
    changelog = "## [0.3.8] - 2026-01-01\n\n### Added\n- Something.\n"
    with mock.patch("kopi.run", return_value="v0.3.9\nv0.3.8\n0.1.0\n") as run:
        items = cli.release_items("me/r", changelog)
    assert run.call_args.args[:2] == ("api", "repos/me/r/tags")
    assert items == [("Done", "[release] v0.3.9", ""), ("Done", "[release] 0.1.0", "")]
    with mock.patch("kopi.run", side_effect=cli.GhError("nope")):
        assert cli.release_items("me/r", changelog) == []
    assert "could not list tags" in capsys.readouterr().err


def test_releases_never_complete_an_open_item_by_fuzzy_match(capsys):
    board = SETTLED + [{"id": "OPEN", "title": "Fix the crash in v9.9.9 handling", "status": "Todo", "content": {"type": "DraftIssue"}}]
    with mock.patch("kopi.release_items", return_value=[("Done", "[release] v9.9.9", "")]):
        rc, rj, run = _run_update(board, argv=("--json", "--update", "--dry-run", "--releases"))
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and [a["title"] for a in data["added"]] == ["[release] v9.9.9"]
    assert data["moved"] == []  # exact title match only: the open item is not moved to Done by the release


REPORT_BOARD = [
    {"id": "H1", "title": "[0.0.1] Old release entry.", "status": "Done"},
    {"id": "D1", "title": "Draft done", "status": "Done", "content": {"type": "DraftIssue"}},
    {"id": "D2", "title": "Draft doing", "status": "In Progress", "content": {"type": "DraftIssue"}},
    {"id": "I1", "title": "An issue", "status": "Todo", "content": {"type": "Issue", "number": 1, "repository": "o/kopi"}},
    {"id": "P1", "title": "A PR", "status": "Done", "content": {"type": "PullRequest", "number": 2, "repository": "o/kopi"}},
    {"id": "N1", "title": "No status item", "status": None, "content": {"type": "DraftIssue"}},
]


def test_report_json(capsys):
    rc, _, _ = _run_update(REPORT_BOARD, argv=("--json", "--report"))
    r = json.loads(capsys.readouterr().out)
    assert rc == 0 and r["project"]["number"] == 2
    assert r["counts"] == {"todo": 1, "in_progress": 1, "done": 2, "other": 1, "history": 1}  # history is not work
    assert r["percent_done"] == 50 and r["by_type"] == {"drafts": 3, "issues": 1, "prs": 1}
    assert r["in_progress"] == ["Draft doing"] and r["todo"] == ["An issue"]
    assert r["releases"] and r["releases"][0]["entries"] > 0 and isinstance(r["differences"], int)


def test_report_text_and_markdown(capsys):
    _run_update(REPORT_BOARD, argv=("--report",))
    out = capsys.readouterr().out
    assert "Project #2: kopi" in out and "50% done: 2 done, 1 in progress, 1 todo, 1 without a status" in out
    assert "3 drafts, 1 issue, 1 pull request" in out and "In progress (1):\n  - Draft doing" in out
    _run_update(REPORT_BOARD, argv=("--report", "markdown"))
    md = capsys.readouterr().out
    assert md.startswith("# kopi (project #2)") and "## Progress" in md and "- Draft doing" in md
    with pytest.raises(SystemExit):
        cli.main(["--report", "pdf"])


def test_report_without_files_or_work(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with mock.patch("kopi.sync_plan", side_effect=cli.GhError("nothing to compare")), \
         mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="o"), \
         mock.patch("kopi.git_repo", return_value=("o", "kopi")), \
         mock.patch("kopi.run_json", side_effect=lambda *a: {"projects": [{"number": 2, "title": "kopi", "url": "u"}]}
                    if a[1] == "list" else {"items": []}):
        assert cli.main(["--report"]) == 0
    out = capsys.readouterr().out
    assert "no work items yet" in out and "not compared" in out


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


def test_rollup_every_open_project_under_the_owner(capsys):
    assert _rollup_run(["--json", "--rollup"], ROLLUP_BOARDS, {}) == 0
    data = json.loads(capsys.readouterr().out)
    assert [p["number"] for p in data["projects"]] == [2, 3]                      # the closed project is left out
    assert data["projects"][0]["counts"] == {"todo": 1, "in_progress": 0, "done": 1, "other": 0, "history": 1}
    assert data["total"] == {"todo": 1, "in_progress": 1, "done": 1, "other": 1, "history": 1}  # "Icebox" is not a known status
    assert _rollup_run(["--rollup"], ROLLUP_BOARDS, {}) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("OWNER\tNUMBER\tTITLE\tTODO") and out[1].startswith("lateralblast\t2\tkopi\t1\t0\t1\t50%\t1")
    assert out[-1].startswith("TOTAL\t2 projects")


def test_rollup_directories_use_each_repos_own_settings(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for name in ("kopi", "other", "plain"):
        (tmp_path / name).mkdir()
    (tmp_path / "other" / ".kopi.toml").write_text('project = "Roadmap"\nowner = "acme"\n[status]\nTodo = "Icebox"\n')
    repos = {"kopi": ("lateralblast", "kopi"), "other": ("lateralblast", "other")}  # "plain" has no origin
    owners = []
    assert _rollup_run(["--json", "--rollup", "kopi", "other", "plain", "missing"], ROLLUP_BOARDS, repos, owners) == 0
    data = json.loads(capsys.readouterr().out)
    assert [(p["owner"], p["number"], p["where"]) for p in data["projects"]] == [("lateralblast", 2, "kopi"), ("acme", 3, "other")]
    assert data["projects"][1]["counts"]["todo"] == 1 and data["projects"][1]["counts"]["other"] == 0  # its own Status names
    assert [s["where"] for s in data["skipped"]] == ["plain", "missing"]
    owners.clear()
    _rollup_run(["--json", "--owner", "flag", "--rollup", "kopi", "other"], ROLLUP_BOARDS, repos, owners)
    assert set(owners) == {"flag"}                                                   # an explicit --owner beats every repo's own


def test_rollup_repos_from_config_and_exclusive(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "kopi").mkdir()
    (tmp_path / ".kopi.toml").write_text('repos = ["kopi"]\n')
    assert _rollup_run(["--json", "--rollup"], ROLLUP_BOARDS, {"kopi": ("lateralblast", "kopi")}) == 0
    assert [p["number"] for p in json.loads(capsys.readouterr().out)["projects"]] == [2]
    with pytest.raises(SystemExit):
        cli.main(["--rollup", "--update"])
