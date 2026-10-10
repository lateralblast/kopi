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
                "item-create": {"id": "ITEM"}}[a[1]]
    return fake


def _run_import(items, argv=("--import",)):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
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
         mock.patch("kopi.ensure_project", return_value=(2, False)), \
         mock.patch("kopi._read", side_effect=lambda p: CHANGELOG if p == "CHANGELOG.md" else TODO), \
         mock.patch("kopi.run_json", side_effect=fake), mock.patch("kopi.run"):
        assert cli.main(["--import"]) == 1
