"""Started / Completed dates for the project's Roadmap view: --update / --import, --done / --start / --move, --setup-dates, --backfill-dates."""
import datetime
import json
import os
from types import SimpleNamespace
from unittest import mock

import pytest

import kopi as cli

from helpers import *  # noqa: F401,F403  (shared boards and runners)

DATED = """# Changelog

## [Unreleased]

## [0.0.3] - 2026-03-01

### Added
- Third feature.

## [0.0.2] - 2026-02-01

### Added
- Auth check before every command.

## [0.0.1] - 2026-01-01

### Added
- Single-file CLI.
"""

REAL_DATES_ENABLED = cli.dates_enabled   # the real function, before conftest's autouse fixture patches it


@pytest.fixture(autouse=True)
def dates_on():
    with mock.patch("kopi.dates_enabled", return_value=True):
        yield


FIELDS = [{"id": "FS", "name": "Started", "dataType": "DATE"}, {"id": "FC", "name": "Completed", "dataType": "DATE"}]


def _with_dates(argv, items, nodes=FIELDS, **kw):
    """_run_update on a board whose date fields are `nodes`; returns (rc, rj, run, gql) with gql the FakeGraphql that
    recorded the writes (`gql.dates` is [(item id, field id, date)])."""
    gql = FakeGraphql(fields=nodes)
    rc, rj, run = _run_update(items, argv=argv, gql=gql, **kw)
    return rc, rj, run, gql


def _sets(gql):
    """[(item id, field id, date)] written."""
    return list(gql.dates)


def test_release_dates_and_default_started():
    assert cli.changelog_release_dates(DATED) == {"0.0.3": "2026-03-01", "0.0.2": "2026-02-01", "0.0.1": "2026-01-01"}
    dates = ["2026-03-01", "2026-02-01", "2026-01-01"]
    assert cli.default_started(dates, "2026-03-01") == "2026-02-01"     # the previous release
    assert cli.default_started(dates, "2026-01-01") == "2026-01-01"     # nothing earlier: the same day
    assert cli.default_started(dates, "2026-02-15") == "2026-02-01"
    assert cli.default_started([], "2026-05-05") == "2026-05-05"


def _ctx(linked=None):
    return mock.Mock(releases=cli.changelog_release_dates(DATED), linked=linked or {})


def test_resolve_dates_for_each_kind_of_item():
    # a changelog entry: its release date, started at the previous release
    assert cli.resolve_dates(_ctx(), {"title": "[0.0.2] Auth check", "status": "Done"}) == ("2026-01-01", "2026-02-01")
    assert cli.resolve_dates(_ctx(), {"title": "[0.0.1] First", "status": "Done"}) == ("2026-01-01", "2026-01-01")
    # an issue / PR: GitHub's own dates; an open one has no completion
    url = "https://github.com/o/kopi/issues/7"
    ctx = _ctx({url: ("2026-02-10", "2026-02-20")})
    item = {"title": "An issue", "status": "Done", "content": {"url": url}}
    assert cli.resolve_dates(ctx, item) == ("2026-02-10", "2026-02-20")
    assert cli.resolve_dates(ctx, {**item, "status": "Todo"}) == ("2026-02-10", None)
    # a release tag: the tag's commit date
    with mock.patch("kopi.tag_date", return_value="2026-02-15") as tag:
        assert cli.resolve_dates(_ctx(), {"title": "[release] v1.0.0", "status": "Done"}) == ("2026-02-01", "2026-02-15")
    tag.assert_called_once_with("v1.0.0")
    # a Done TODO.md draft: the commit that ticked it (full text from the body when the title was cut)
    with mock.patch("kopi.tick_date", return_value="2026-03-05") as tick:
        done = {"title": "Cut title…", "status": "Done", "content": {"body": "Cut title in full"}}
        assert cli.resolve_dates(_ctx(), done) == ("2026-03-01", "2026-03-05")
        tick.assert_called_once_with("Cut title in full")
        assert cli.resolve_dates(_ctx(), {"title": "Open thing", "status": "In Progress"}) == (None, None)
    with mock.patch("kopi.tick_date", return_value=None):
        assert cli.resolve_dates(_ctx(), {"title": "Never committed", "status": "Done"}) == (None, None)


def test_git_date_helpers(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.chdir(tmp_path)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*a, date=None):
        subprocess.run(["git", *a], check=True, capture_output=True, env={**env, **({"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else {})})
    git("init", "-q")
    (tmp_path / "TODO.md").write_text("- [ ] Ship the thing\n- [ ] Other\n")
    git("add", "."), git("commit", "-qm", "one", date="2026-01-05T12:00:00")
    (tmp_path / "TODO.md").write_text("- [x] Ship the thing\n- [ ] Other\n")
    git("commit", "-qam", "two", date="2026-02-07T12:00:00")
    git("tag", "v1")
    assert cli.tick_date("Ship the thing") == "2026-02-07"      # when it was ticked, not when it was added
    assert cli.tick_date("Other") is None                        # never ticked
    assert cli.tag_date("v1") == "2026-02-07" and cli.tag_date("nope") is None


def test_writer_batches_writes_only_empty_values_and_skips_missing_fields(capsys):
    args = mock.Mock(owner="o", config_data={})
    with mock.patch("kopi.run_json", return_value={"id": "PID"}), \
         mock.patch("kopi.graphql", return_value={"data": {"node": {"fields": {"nodes": FIELDS}}}}):
        writer = cli.DateWriter(args, 2)
        assert writer
        writer.want({"id": "I1", "title": "t1"}, "2026-01-01", "2026-02-01")
        writer.want({"id": "I2", "title": "t2", "completed": "2026-09-09"}, "2026-01-01", "2026-02-01")   # Completed already set
        for n in range(75):
            writer.want({"id": f"X{n}", "title": f"x{n}"}, None, "2026-02-01")
    fake = FakeGraphql()
    with mock.patch("kopi.graphql", fake):
        done = writer.flush()
    assert len(done) == 2 + 1 + 75 and len(fake.queries) == 2               # t1 twice, t2 once (Completed was set), 75 more; 50 per request
    assert ("I2", "FC", "2026-02-01") not in fake.dates and ("I2", "FS", "2026-01-01") in fake.dates
    assert writer.queue == []
    # a board with no such fields: a falsy writer, so callers do nothing
    with mock.patch("kopi.run_json", return_value={"id": "PID"}), \
         mock.patch("kopi.graphql", return_value={"data": {"node": {"fields": {"nodes": []}}}}):
        assert not cli.DateWriter(args, 2)
    # a field of the right name but the wrong type is skipped with a warning
    with mock.patch("kopi.run_json", return_value={"id": "PID"}), \
         mock.patch("kopi.graphql", return_value={"data": {"node": {"fields": {"nodes": [
             {"id": "F", "name": "Completed", "dataType": "TEXT"}]}}}}):
        assert not cli.DateWriter(args, 2)
    assert "not a date field" in capsys.readouterr().err


def test_update_sets_dates_on_the_items_it_adds_and_fills_existing_ones(capsys):
    board = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"}]       # on the board already, undated
    rc, rj, run, gql = _with_dates(("--json", "--update"), board, changelog=DATED, todo="# TODO\n")
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    sets = _sets(gql)
    assert sorted(d["date"] for d in data["dates"] if d["field"] == "Completed") == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert sorted(d["date"] for d in data["dates"] if d["field"] == "Started") == ["2026-01-01", "2026-01-01", "2026-02-01"]
    assert len(sets) == 6 and {f for _, f, _ in sets} == {"FS", "FC"} and ("A", "FC", "2026-01-01") in sets


def test_update_with_nothing_to_add_still_backfills_dates(capsys):
    board = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"},
             {"id": "B", "title": "[0.0.2] Auth check before every command.", "status": "Done", "started": "2026-01-01"},
             {"id": "C", "title": "[0.0.3] Third feature.", "status": "Done", "started": "2026-02-01", "completed": "2026-03-01"}]
    rc, rj, run, gql = _with_dates(("--json", "--update"), board, changelog=DATED, todo="# TODO\n")
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["added"] == [] and data["moved"] == []
    assert sorted(_sets(gql)) == [("A", "FC", "2026-01-01"), ("A", "FS", "2026-01-01"), ("B", "FC", "2026-02-01")]
    assert len(data["dates"]) == 3                                          # C was already complete


def test_update_and_import_create_missing_date_fields(capsys):
    board = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"}]
    rc, rj, run, gql = _with_dates(("--json", "--update"), board, nodes=[FIELDS[0]], changelog=DATED, todo="# TODO\n")
    created = [c.args for c in run.call_args_list if c.args[:2] == ("project", "field-create")]
    assert rc == 0 and len(created) == 1 and created[0][created[0].index("--name") + 1] == "Completed"
    assert json.loads(capsys.readouterr().out)["date_fields_created"] == ["Completed"]
    rc, rj, run, gql = _with_dates(("--json", "--update"), board, nodes=[], changelog=DATED, todo="# TODO\n")
    assert len([c for c in run.call_args_list if c.args[:2] == ("project", "field-create")]) == 2
    out = capsys.readouterr().out
    assert json.loads(out)["date_fields_created"] == ["Started", "Completed"]


def test_no_dates_flag_and_config_switch_dates_off(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "dates_enabled", REAL_DATES_ENABLED)         # use the real switch, not the test default
    board = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"}]
    rc, rj, run, gql = _with_dates(("--json", "--update", "--no-dates"), board, nodes=[], changelog=DATED, todo="# TODO\n")
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and _sets(gql) == [] and "dates" not in out and "date_fields_created" not in out
    assert not [c for c in run.call_args_list if c.args[:2] == ("project", "field-create")]
    # the real switch: flag, then `[dates] enabled = false`
    assert REAL_DATES_ENABLED(mock.Mock(no_dates=False, config_data={})) is True
    assert REAL_DATES_ENABLED(mock.Mock(no_dates=True, config_data={})) is False
    assert REAL_DATES_ENABLED(mock.Mock(no_dates=False, config_data={"dates": {"enabled": False}})) is False
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kopi.toml").write_text("[dates]\nenabled = false\n")
    rc, rj, run, gql = _with_dates(("--done", "write docs"), [{"id": "T1", "title": "Write docs", "status": "Todo"}], changelog=DATED)
    assert _sets(gql) == []                                                  # --done stamps nothing either


def test_update_without_date_fields_writes_nothing(capsys):
    board = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"}]
    rc, rj, run, gql = _with_dates(("--json", "--update"), board, nodes=[], changelog=DATED, todo="# TODO\n")
    assert rc == 0 and _sets(gql) == [] and "dates" not in json.loads(capsys.readouterr().out)


def test_update_moving_an_item_to_done_sets_completed_but_keeps_a_manual_date(capsys):
    board = [{"id": "M1", "title": "[0.0.2] Auth check before every command.", "status": "Todo"},
             {"id": "M2", "title": "[0.0.3] Third feature.", "status": "In Progress", "completed": "2026-03-09"}]
    rc, rj, run, gql = _with_dates(("--update",), board, changelog=DATED, todo="# TODO\n")
    sets = _sets(gql)
    assert ("M1", "FC", "2026-02-01") in sets and ("M1", "FS", "2026-01-01") in sets
    assert not [s for s in sets if s[0] == "M2" and s[1] == "FC"]       # the hand-entered Completed date stays
    assert ("M2", "FS", "2026-02-01") in sets


def test_done_start_and_move_stamp_today(capsys):
    today = datetime.date.today().isoformat()
    board = [{"id": "T1", "title": "Write docs", "status": "Todo"}, {"id": "P1", "title": "Fix bug", "status": "In Progress"}]
    rc, _, _, gql = _with_dates(("--done", "write docs"), board, changelog=DATED)
    assert ("T1", "FC", today) in _sets(gql) and ("T1", "FS", "2026-03-01") in _sets(gql)   # started: previous release
    assert f"Set Completed {today}" in capsys.readouterr().out
    rc, _, _, gql = _with_dates(("--start", "write docs"), board)
    assert _sets(gql) == [("T1", "FS", today)]
    rc, _, _, gql = _with_dates(("--move", "Fix bug", "Done"), [{**board[1], "started": "2026-02-02"}], changelog=DATED)
    assert _sets(gql) == [("P1", "FC", today)]                    # already started: only Completed is added
    rc, _, _, gql = _with_dates(("--move", "Write docs", "Todo"), board)
    assert _sets(gql) == []                                               # moving back stamps nothing
    rc, _, _, gql = _with_dates(("--done", "write docs", "--dry-run"), board, changelog=DATED)
    assert _sets(gql) == []
    rc, _, _, gql = _with_dates(("--done", "write docs"), board, nodes=[], changelog=DATED)
    assert _sets(gql) == []                                               # no date fields on the board


def test_custom_date_field_names_from_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kopi.toml").write_text('[dates]\nstarted = "Kickoff"\ncompleted = "Shipped"\n')
    nodes = [{"id": "K", "name": "Kickoff", "dataType": "DATE"}, {"id": "S", "name": "Shipped", "dataType": "DATE"}]
    board = [{"id": "T1", "title": "Write docs", "status": "Todo"}]
    rc, _, _, gql = _with_dates(("--done", "write docs"), board, nodes=nodes, changelog=DATED)
    assert {f for _, f, _ in _sets(gql)} == {"K", "S"}
    assert cli.date_field_names(mock.Mock(config_data={})) == {"started": "Started", "completed": "Completed"}


def test_setup_dates_creates_only_the_missing_fields(capsys):
    rc, rj, run, _ = _with_dates(("--setup-dates",), [], nodes=[FIELDS[0]])
    created = [c for c in run.call_args_list if c.args[:2] == ("project", "field-create")]
    assert rc == 0 and len(created) == 1 and created[0].args[created[0].args.index("--name") + 1] == "Completed"
    assert "--data-type" in created[0].args and "DATE" in created[0].args
    out = capsys.readouterr().out
    assert "exists\tStarted\tDATE" in out and "Created date field Completed" in out
    rc, rj, run, _ = _with_dates(("--json", "--setup-dates", "--dry-run"), [], nodes=[])
    data = json.loads(capsys.readouterr().out)
    assert [f["field"] for f in data["created"]] == ["Started", "Completed"] and not run.called


def test_backfill_dates_fills_gaps_without_overwriting(capsys):
    board = [{"id": "H1", "title": "[0.0.2] Auth check before every command.", "status": "Done"},
             {"id": "H2", "title": "[0.0.3] Third feature.", "status": "Done", "started": "2026-02-02", "completed": "2026-03-02"},
             {"id": "O1", "title": "Still open", "status": "Todo"}]
    rc, rj, run, gql = _with_dates(("--json", "--backfill-dates", "--dry-run"), board, changelog=DATED)
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and _sets(gql) == [] and [(d["title"][:7], d["field"], d["date"]) for d in data["set"]] == [
        ("[0.0.2]", "Started", "2026-01-01"), ("[0.0.2]", "Completed", "2026-02-01")]     # H2 is complete; O1 has nothing
    rc, rj, run, gql = _with_dates(("--backfill-dates",), board, changelog=DATED)
    assert sorted(_sets(gql)) == [("H1", "FC", "2026-02-01"), ("H1", "FS", "2026-01-01")]
    assert "Set\tStarted\t2026-01-01" in capsys.readouterr().out
    rc, *_ = _with_dates(("--backfill-dates",), board, nodes=[], changelog=DATED)
    assert rc == 1 and "run --setup-dates first" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["--backfill-dates", "--update"])


def test_tick_date_searches_only_a_prefix_so_wrapped_entries_are_found():
    long = "word " * 40
    with mock.patch.object(cli, "_git_first_line", return_value="2026-10-07") as g:
        assert cli.tick_date(long) == "2026-10-07"
    assert g.call_args.args[3] == "-S- [x] " + long[:cli.TICK_PREFIX]


def test_a_grouped_changelog_item_spans_its_releases():
    ctx = SimpleNamespace(releases={"2.1.2": "2026-03-03", "2.1.1": "2026-03-02", "2.1.0": "2026-03-01", "2.0.0": "2026-02-01"}, linked={})
    item = {"title": "[2.1] Releases 2.1.x", "status": "Done", "content": {}}
    assert cli.resolve_dates(ctx, item) == ("2026-02-01", "2026-03-03")     # from the release before the group to its last
    first = {"title": "[2.0] Releases 2.0.x", "status": "Done", "content": {}}
    assert cli.resolve_dates(ctx, first) == ("2026-02-01", "2026-02-01")    # nothing earlier: a one-day bar
    assert cli.resolve_dates(ctx, {**item, "status": "Todo"}) == (None, None)
    assert cli.resolve_dates(ctx, {**item, "title": "[9.9] Releases 9.9.x"}) == (None, None)   # a group with no dated release
