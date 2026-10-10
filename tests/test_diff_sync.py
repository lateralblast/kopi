"""--diff, --sync and --squash."""
import json
import os
from unittest import mock

import pytest

import kopi as cli

from helpers import *  # noqa: F401,F403  (shared boards and runners)


def test_diff_no_differences(capsys):
    rc, _, run = _run_update(SETTLED, argv=("--diff",))
    assert rc == 0 and "No differences in project #2" in capsys.readouterr().out
    run.assert_not_called()


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
