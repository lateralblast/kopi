"""--archive, --restore and --dedupe."""
import json
import os
from unittest import mock

import pytest

import kopi as cli

from helpers import *  # noqa: F401,F403  (shared boards and runners)


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


def test_dedupe_keeps_furthest_along():
    rc, rj, run = _run_update(DUPES, argv=("--dedupe",))
    assert rc == 0 and _deleted(run) == ["B", "C", "F"]


def test_dedupe_dry_run_deletes_nothing(capsys):
    rc, rj, run = _run_update(DUPES, argv=("--dedupe", "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 0 and _deleted(run) == [] and "3 duplicates to delete" in out


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


def test_restore_all_finds_items_archived_outside_kopi(capsys):
    gql = mock.Mock(side_effect=_archived_pages())
    rc, rj, run = _run_update([], argv=("--restore", "--all"), gql=gql)
    assert rc == 0 and _undone(run) == ["A1", "A2"]  # not the live item; not in kopi's log
    assert gql.call_args_list[0].kwargs == {"id": "PID"} and gql.call_args_list[1].kwargs == {"id": "PID", "after": "C1"}
    assert "Restored 2 items" in capsys.readouterr().out
    rc, rj, run = _run_update([], argv=("--json", "--restore", "--all", "--dry-run"), gql=mock.Mock(side_effect=_archived_pages()))
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
