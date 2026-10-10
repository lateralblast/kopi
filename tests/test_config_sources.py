"""Settings (.kopi.toml), Status names, limits and the opt-in code-comment and release sources."""
import json
import os
import types
from unittest import mock

import pytest

import kopi as cli

from helpers import *  # noqa: F401,F403  (shared boards and runners)


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
        cli.board_items(types.SimpleNamespace(owner="o", status_map=None), 2)
    assert rj.call_args.args[-1] == str(cli.ITEM_LIMIT)
    with mock.patch("kopi.ITEM_LIMIT", 2), mock.patch("kopi.run_json", return_value={"items": [{"id": "a"}, {"id": "b"}]}):
        cli.board_items(types.SimpleNamespace(owner="o", status_map=None), 2)
    assert "stopped at the limit of 2" in capsys.readouterr().err


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
