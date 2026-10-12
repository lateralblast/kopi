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


WRAPPED_TODO = ("# TODO\n\n## Open\n\n- [ ] Add a `--json` output flag that prints one document and keeps\n"
                "  warnings on stderr.\n- [ ] Still open.\n")


def test_squash_ticks_a_wrapped_entry_on_its_first_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "TODO.md").write_text(WRAPPED_TODO)
    board = [{"id": "W1", "title": "Add a `--json` output flag that prints one document and keeps warnings on stderr.",
              "status": "Done"},
             {"id": "W2", "title": "Still open.", "status": "Todo"}]
    rc, *_ = _run_update(board, argv=("--squash",), todo=WRAPPED_TODO)
    assert rc == 0 and "1 TODO.md items ticked" in capsys.readouterr().out
    assert (tmp_path / "TODO.md").read_text() == WRAPPED_TODO.replace("- [ ] Add a", "- [x] Add a")


def test_diff_and_report_do_not_warn_about_similar_todos(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "TODO.md").write_text("- [ ] Start Ollama and the gateway at boot on this host\n"
                                      "- [ ] Start Ollama and the Hermes gateway at boot as services\n")
    board = [{"id": "S1", "title": "Start Ollama and the gateway at boot on this host", "status": "Todo"},
             {"id": "S2", "title": "Start Ollama and the Hermes gateway at boot as services", "status": "Todo"}]
    todo = (tmp_path / "TODO.md").read_text()
    _run_update(board, argv=("--diff",), todo=todo)
    assert "possible duplicate" not in capsys.readouterr().err
    _run_update(board, argv=("--update", "--dry-run"), todo=todo)
    assert "possible duplicate TODO.md entries" in capsys.readouterr().err


def test_entries_outside_the_changelog_window_are_not_orphans(capsys):
    board = SETTLED + [{"id": "Z", "title": "Something nobody tracks", "status": "Todo"}]
    with mock.patch("kopi.load_config", return_value={"changelog": {"since": "0.0.2"}}):
        rc, _, _ = _run_update(board, argv=("--json", "--diff"))
    data, _ = _json_out(capsys)
    titles = [i["title"] for i in data["board_only"]]
    assert titles == ["Something nobody tracks"]                      # [0.0.1] is outside the window, so it is not board-only


def test_ci_example_workflow_runs_the_documented_check():
    """docs/kopi-check.yml and the README's CI section must keep naming the command and the token the check needs."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    workflow = open(os.path.join(root, "docs", "kopi-check.yml")).read()
    assert "run: kopi --diff --check" in workflow and "GH_TOKEN: ${{ secrets.KOPI_TOKEN }}" in workflow
    assert workflow.count("name: kopi") == 1 and "pull_request:" in workflow
    readme = open(os.path.join(root, "README.md")).read()
    assert "docs/kopi-check.yml" in readme and "entry: kopi --diff --check" in readme


def test_update_example_workflow_only_updates_the_board_and_never_overlaps():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    workflow = open(os.path.join(root, "docs", "kopi-update.yml")).read()
    assert "run: kopi --update" in workflow and "GH_TOKEN: ${{ secrets.KOPI_TOKEN }}" in workflow
    assert "--sync" not in workflow.split("jobs:")[1]                                  # a runner cannot commit the files --sync edits
    assert "concurrency:" in workflow and "cancel-in-progress: false" in workflow      # overlapping runs could duplicate items
    assert "paths: [CHANGELOG.md, TODO.md]" in workflow and "name: kopi-update" in workflow
    readme = open(os.path.join(root, "README.md")).read()
    assert "docs/kopi-update.yml" in readme
