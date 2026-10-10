"""Working on single items: --done / --start / --move, set, and --list."""
import json
import os
from unittest import mock

import pytest

import kopi as cli

from helpers import *  # noqa: F401,F403  (shared boards and runners)


def test_update_adds_missing_and_moves_forward():
    rc, rj, run = _run_update()
    assert rc == 0
    assert _created_titles(rj) == ["[0.0.2] Auth check before every command.", "Created the project."]
    # two new Done items, C Todo -> In Progress, D (done in TODO, covered by changelog) Todo -> Done
    assert rj.gql.statuses == [("NEW0", "o3"), ("NEW1", "o3"), ("C", "o2"), ("D", "o3")]


def test_update_never_moves_backwards_or_duplicates():
    board = [{"id": "A", "title": "[0.0.1] Single-file CLI.", "status": "Done"},
             {"id": "A2", "title": "[0.0.2] Auth check before every command.", "status": "Done"},
             {"id": "B", "title": "Add a `--json` output flag.", "status": "In Progress"},  # TODO says Todo
             {"id": "C", "title": "Run against real GitHub.", "status": "Done"},  # TODO says In Progress
             {"id": "E", "title": "Created the project.", "status": "Done"}]
    rc, rj, run = _run_update(board)
    assert rc == 0
    assert not run.called and not rj.gql.statuses
    assert _created_titles(rj) == []


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
                }[a[1]]
    gql = FakeGraphql()
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="x"), mock.patch("kopi.graphql", gql), \
         mock.patch("kopi.git_repo", return_value=("x", "kopi")), mock.patch("kopi.fetch_linked", return_value=[]), \
         mock.patch("kopi._read", side_effect=lambda p: changelog if p == "CHANGELOG.md" else todo), \
         mock.patch("kopi.run_json", side_effect=fake) as rj, mock.patch("kopi.run") as run:
        assert cli.main(["--update"]) == 0
    created = [t for t, _ in gql.created]
    edits = {item: option for item, option in gql.statuses if not item.startswith("NEW")}
    # X (reworded in TODO) and J (matches changelog 0.0.3) move to Done; K stays open; neither is re-added
    assert edits == {"X": "o3", "J": "o3"}
    assert created == ["[0.0.2] Auth check before every command."]


def test_update_matches_existing_issue_by_url_and_moves_forward_only():
    board = SETTLED + [_linked_item("L1", "Open bug", "Todo", "https://x/1"),
                       _linked_item("L2", "Reopened thing", "Done", "https://x/2")]
    linked = [("Done", "Open bug", "https://x/1"),            # closed on GitHub -> move to Done
              ("Todo", "Reopened thing", "https://x/2")]      # reopened -> never moved backwards
    rc, rj, run = _run_update(board, linked=linked)
    assert rc == 0
    assert rj.gql.added == []
    assert rj.gql.statuses == [("L1", "o3")]


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
