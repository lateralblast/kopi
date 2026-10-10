"""--report and --rollup."""
import json
import os
from unittest import mock

import pytest

import kopi as cli

from helpers import *  # noqa: F401,F403  (shared boards and runners)


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


def test_squash_and_sync_json_report_the_issues_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run_update(ISSUE_BOARD, argv=("--json", "--squash", "--dry-run"))
    assert json.loads(capsys.readouterr().out)["issues"] == {"file": "ISSUES.md", "changed": True, "skipped": None, "count": 3}
    _run_update(ISSUE_BOARD, argv=("--json", "--sync", "--dry-run"))
    assert json.loads(capsys.readouterr().out)["squash"]["issues"]["count"] == 3
