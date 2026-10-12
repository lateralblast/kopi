"""--report and --rollup."""
import datetime
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
    assert r["history"] == {"entries": 1, "releases": 1}
    assert r["percent_done"] == 50 and r["by_type"] == {"drafts": 3, "issues": 1, "prs": 1}
    assert r["in_progress"] == ["Draft doing"] and r["todo"] == ["An issue"]
    assert r["releases"] and r["releases"][0]["entries"] > 0 and isinstance(r["differences"], int)


def test_report_text_and_markdown(capsys):
    _run_update(REPORT_BOARD, argv=("--report",))
    out = capsys.readouterr().out
    assert "Project #2: kopi" in out and "Tracked work: 50% done (2 done, 1 in progress, 1 todo, 1 without a status)" in out
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
    assert "Tracked work: none yet\n" in out and "not compared" in out


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


def test_report_keeps_shipped_history_apart_from_the_percentage(capsys):
    board = [{"id": f"H{i}", "title": f"[0.1.{i % 3}] Thing {i}", "status": "Done"} for i in range(9)] + [
        {"id": "T1", "title": "Open work", "status": "Todo"}]
    _run_update(board, argv=("--report",))
    out = capsys.readouterr().out
    assert "Tracked work: 0% done (0 done, 0 in progress, 1 todo)\nShipped: 9 changelog entries in 3 releases" in out
    _run_update([{"id": "T1", "title": "Open work", "status": "Todo"}], argv=("--report",))
    assert "Shipped" not in capsys.readouterr().out          # nothing shipped: no line


def test_report_reads_the_board_only_once(capsys):
    rc, rj, _ = _run_update(REPORT_BOARD, argv=("--report",))
    assert rc == 0 and len([c for c in rj.call_args_list if c.args[1] == "item-list"]) == 1   # reading costs API points per item


NAMES = {"started": "Started", "completed": "Completed"}


def _item(title, started=None, completed=None):
    return {"id": title, "title": title, "status": "Done", "started": started, "completed": completed, "content": {}}


def test_trends_count_releases_and_completed_work_by_month():
    items = [_item("Fix login", "2026-08-01", "2026-08-05"), _item("Add search", "2026-08-10", "2026-09-10"),
             _item("Same day", "2026-09-02", "2026-09-02"), _item("No dates"),
             _item("[1.0.1] Not work", "2026-08-01", "2026-09-01")]                 # changelog entries are not work items
    releases = {"1.0.0": "2026-08-01", "1.0.1": "2026-08-01", "1.1.0": "2026-09-15"}  # two versions on one day both count
    t = cli.build_trends(items, releases, NAMES, datetime.date(2026, 9, 20), months=3)
    assert t["months"] == [{"month": "2026-07", "releases": 0, "completed": 0},
                           {"month": "2026-08", "releases": 2, "completed": 1},
                           {"month": "2026-09", "releases": 1, "completed": 2}]
    assert t["cycle_days"] == {"n": 3, "median": 4, "mean": 11.7, "longest": 31}
    assert t["release_interval_days"] == {"n": 1, "median": 45, "mean": 45, "longest": 45}      # gaps between release days


def test_trends_window_crosses_a_year_and_ignores_older_dates():
    t = cli.build_trends([_item("Old", "2020-01-01", "2020-01-03")], {"1.0.0": "2025-12-30"}, NAMES, datetime.date(2026, 2, 1), months=3)
    assert [m["month"] for m in t["months"]] == ["2025-12", "2026-01", "2026-02"]
    assert t["months"][0]["releases"] == 1 and all(m["completed"] == 0 for m in t["months"])
    assert t["cycle_days"]["n"] == 1                                    # the span still counts towards cycle time


def test_trends_with_nothing_to_measure():
    t = cli.build_trends([_item("Undated")], {}, NAMES, datetime.date(2026, 9, 20), months=2)
    assert t["cycle_days"] is None and t["release_interval_days"] is None and all(not m["releases"] for m in t["months"])


def test_trends_honour_configured_date_field_names():
    item = {"id": "a", "title": "Ship it", "status": "Done", "began": "2026-09-01", "finished": "2026-09-04", "content": {}}
    t = cli.build_trends([item], {}, {"started": "Began", "completed": "Finished"}, datetime.date(2026, 9, 20), months=1)
    assert t["months"][0]["completed"] == 1 and t["cycle_days"]["median"] == 3


def test_report_shows_trends_in_text_and_json(capsys):
    board = REPORT_BOARD + [_item("Dated work", "2026-10-01", "2026-10-04")]
    with mock.patch("kopi.datetime") as dt:
        dt.date.today.return_value = datetime.date(2026, 10, 12)
        dt.date.fromisoformat = datetime.date.fromisoformat
        _run_update(board, argv=("--report",))
    out = capsys.readouterr().out
    assert "Trends (last 12 months):" in out and "2026-10:" in out and "1 work item completed" in out
    assert "Cycle time (Started to Completed): median 3 days, mean 3 days, longest 3 days (1 item)" in out
    _run_update(board, argv=("--json", "--report"))
    trends = json.loads(capsys.readouterr().out)["trends"]
    assert len(trends["months"]) == 12 and set(trends) == {"months", "cycle_days", "release_interval_days"}


def test_report_has_no_trends_section_without_dates(capsys):
    _run_update(REPORT_BOARD, argv=("--report",), changelog="# Changelog\n\n## [0.0.1]\n- Undated release entry\n")
    assert "Trends" not in capsys.readouterr().out


def _linked(number, status, milestone=None):
    item = {"id": f"L{number}", "title": f"Issue {number}", "status": status, "content": {"type": "Issue", "number": number, "repository": "o/kopi"}}
    if milestone:
        item["milestone"] = milestone
    return item


def test_milestones_count_done_items_and_sort_by_due_date():
    items = [_linked(1, "Done", {"title": "v2.0", "dueOn": "2026-12-01T00:00:00Z"}), _linked(2, "Todo", {"title": "v2.0", "dueOn": "2026-12-01T00:00:00Z"}),
             _linked(3, "Done", {"title": "v1.5", "dueOn": "2026-11-01T00:00:00Z"}), _linked(4, "Todo", {"title": "Someday", "dueOn": None}),
             _linked(5, "Done", "Backlog"), _linked(6, "Todo"), _item("A draft")]
    assert cli.build_milestones(items) == [
        {"title": "v1.5", "due": "2026-11-01", "done": 1, "total": 1}, {"title": "v2.0", "due": "2026-12-01", "done": 1, "total": 2},
        {"title": "Backlog", "due": None, "done": 1, "total": 1}, {"title": "Someday", "due": None, "done": 0, "total": 1}]
    assert cli.build_milestones([_item("A draft")]) == []


def test_report_lists_milestone_progress(capsys):
    board = REPORT_BOARD + [_linked(10, "Done", {"title": "v2.0", "dueOn": "2026-12-01T00:00:00Z"}), _linked(11, "Todo", {"title": "v2.0", "dueOn": "2026-12-01T00:00:00Z"})]
    _run_update(board, argv=("--report",))
    assert "Milestones:\n  - v2.0: 1 of 2 done (due 2026-12-01)" in capsys.readouterr().out
    _run_update(board, argv=("--json", "--report"))
    assert json.loads(capsys.readouterr().out)["milestones"] == [{"title": "v2.0", "due": "2026-12-01", "done": 1, "total": 2}]
    _run_update(REPORT_BOARD, argv=("--report",))
    assert "Milestones" not in capsys.readouterr().out


def _post(issue=5, dry_run=False, runs=()):
    """post_report with `gh` answering from `runs` (a list of return values, in call order)."""
    with mock.patch("kopi.git_repo", return_value=("o", "kopi")), mock.patch("kopi.run", side_effect=list(runs)) as run:
        result = cli.post_report(mock.Mock(dry_run=dry_run), issue, "<!-- kopi-report -->\nbody")
    return result, run


def test_post_report_creates_a_comment_when_there_is_none():
    result, run = _post(runs=["", "https://github.com/o/kopi/issues/5#issuecomment-1\n"])
    assert result == {"issue": 5, "url": "https://github.com/o/kopi/issues/5#issuecomment-1", "action": "posted"}
    assert run.call_args_list[0].args[:2] == ("api", "repos/o/kopi/issues/5/comments")
    assert run.call_args_list[1].args == ("issue", "comment", "5", "--repo", "o/kopi", "--body", "<!-- kopi-report -->\nbody")


def test_post_report_edits_its_own_comment_in_place_and_skips_an_identical_one():
    result, run = _post(runs=["11\n12\n", "<!-- kopi-report -->\nolder", "https://github.com/o/kopi/issues/5#issuecomment-12\n"])
    assert result["action"] == "updated" and result["url"].endswith("#issuecomment-12")           # the newest of its comments
    assert run.call_args_list[2].args == ("api", "-X", "PATCH", "repos/o/kopi/issues/comments/12", "-f", "body=<!-- kopi-report -->\nbody", "--jq", ".html_url")
    same, run = _post(runs=["12\n", "<!-- kopi-report -->\nbody\n"])
    assert same["action"] == "unchanged" and run.call_count == 2                                   # nothing written


def test_post_report_dry_run_reads_but_never_writes():
    posted, run = _post(dry_run=True, runs=[""])
    assert posted["action"] == "posted" and run.call_count == 1
    updated, run = _post(dry_run=True, runs=["12\n", "old"])
    assert updated["action"] == "updated" and run.call_count == 2


def test_post_report_needs_a_repo():
    with mock.patch("kopi.git_repo", return_value=None), pytest.raises(cli.GhError, match="--post needs a GitHub repo"):
        cli.post_report(mock.Mock(dry_run=False), 5, "x")


def test_report_to_file_and_post_in_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with mock.patch("kopi.post_report", return_value={"issue": 5, "url": "u", "action": "posted"}) as post:
        _run_update(REPORT_BOARD, argv=("--json", "--report", "--to", "out.txt", "--post", "5"))
    data = json.loads(capsys.readouterr().out)
    assert data["file"] == "out.txt" and data["posted"]["action"] == "posted" and data["project"]["number"] == 2
    assert (tmp_path / "out.txt").read_text().startswith("Project #2: kopi")                       # plain text: not a .md file
    assert post.call_args.args[2].startswith("<!-- kopi-report -->\n# kopi (project #2)")          # a comment is always Markdown
    _run_update(REPORT_BOARD, argv=("--report", "markdown", "--to", "out.md"))
    assert (tmp_path / "out.md").read_text().startswith("# kopi (project #2)")
    _run_update(REPORT_BOARD, argv=("--report", "--to", "notes.md"))
    assert (tmp_path / "notes.md").read_text().startswith("# kopi (project #2)")                  # a .md name is enough


def test_report_dry_run_writes_no_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run_update(REPORT_BOARD, argv=("--report", "--to", "out.md", "--dry-run"))
    assert "report would be written to out.md" in capsys.readouterr().out and not (tmp_path / "out.md").exists()


def test_to_and_post_need_report():
    for argv in (["--diff", "--to", "x.md"], ["--diff", "--post", "5"]):
        with pytest.raises(SystemExit):
            cli.main(argv)
