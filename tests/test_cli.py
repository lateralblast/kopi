from unittest import mock

import kopi as cli


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
    assert cli.__version__ == pyproject == readme


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


def _run_update(items=BOARD, argv=("--update",), project=True, todo=None):
    def fake(*a):
        return {"list": {"projects": [{"number": 2, "title": "kopi", "url": "u"}] if project else []},
                "item-list": {"items": items}, "view": {"id": "PID"},
                "field-list": {"fields": [{"id": "FID", "name": "Status", "options": [
                    {"id": "o1", "name": "Todo"}, {"id": "o2", "name": "In Progress"}, {"id": "o3", "name": "Done"}]}]},
                "item-create": {"id": "NEW"}}[a[1]]
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
         mock.patch("kopi.git_repo", return_value=("lateralblast", "kopi")), \
         mock.patch("kopi._read", side_effect=lambda p: CHANGELOG if p == "CHANGELOG.md" else (todo or TODO)), \
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
