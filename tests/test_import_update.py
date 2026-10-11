"""--import and --update: planning, matching, issues/PRs and single files."""
import json
import os
from unittest import mock

import pytest

import kopi as cli

from helpers import *  # noqa: F401,F403  (shared boards and runners)


def test_parse_changelog_and_todo():
    assert cli.parse_changelog(CHANGELOG) == [("0.0.2", "Auth check before every command."),
                                              ("0.0.1", "Single-file CLI.")]
    assert cli.parse_todo(TODO) == [("Todo", "Add a `--json` output flag."),
                                    ("In Progress", "Run against real GitHub."),
                                    ("Done", "Auth check before every command, with tests."),
                                    ("Done", "Created the project.")]


def test_build_plan_long_text_goes_to_body():
    long = "x" * 150
    plan, _ = cli.build_plan("## [0.0.1]\n- " + long + "\n", "")
    status, title, body = plan[0]
    assert len(title) == 100 and title.endswith("…") and body == long


def test_import_adds_items_with_status():
    rc, rj, run = _run_import([])
    assert rc == 0
    gql = rj.gql
    assert len(gql.created) == 5 and len(gql.statuses) == 5 and not run.called      # batched: no per-item gh calls
    assert [item for item, _ in gql.statuses] == ["NEW0", "NEW1", "NEW2", "NEW3", "NEW4"]
    # first plan entry is Done -> option o3; the Todo item uses o1
    options = [option for _, option in gql.statuses]
    assert options[0] == "o3" and options[2] == "o1" and options[3] == "o2"


def test_import_skips_when_items_exist(capsys):
    rc, rj, run = _run_import([{"id": "x"}])
    assert rc == 0
    run.assert_not_called()
    assert "already has items" in capsys.readouterr().out


def test_import_force_imports_anyway():
    rc, rj, run = _run_import([{"id": "x"}], argv=("--import", "--force"))   # the project already has an item
    assert rc == 0 and len(_created_titles(rj)) == 5
    assert len(rj.gql.statuses) == 5  # ... and each new item had its status set


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
    assert rj.gql.added == ["https://x/1", "https://x/2"]
    assert len(rj.gql.statuses) == 5 + 2
    assert rj.gql.statuses[-1][1] == "o3"  # closed issue -> Done


def test_import_dry_run_lists_linked(capsys):
    rc, rj, run = _run_import([], argv=("--import", "--dry-run"), linked=[("Todo", "Open bug", "https://x/1")])
    out = capsys.readouterr().out
    assert "Todo\tOpen bug\thttps://x/1" in out and "6 items would be imported" in out


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
    assert _created_titles(rj) == ["Run `--update` for real against the `kopi` project (only `--dry-run` and mocks so far)."]
    assert rj.gql.statuses == [("NEW0", "o1")] and not run.called  # only the new item's status; the existing --import item is untouched


def test_update_adds_new_issues_and_prs_by_url():
    linked = [("Todo", "Open bug", "https://x/1"), ("In Progress", "WIP PR", "https://x/2")]
    rc, rj, run = _run_update(SETTLED, linked=linked)
    assert rc == 0
    assert rj.gql.added == ["https://x/1", "https://x/2"]
    assert [option for _, option in rj.gql.statuses] == ["o1", "o2"]


def test_update_open_issue_item_is_not_swept_or_title_matched():
    """A linked item must never be closed by a changelog entry that merely shares words with its title."""
    board = SETTLED + [_linked_item("L1", "Auth check before every command fails", "Todo", "https://x/1")]
    linked = [("Todo", "Auth check before every command fails", "https://x/1")]
    rc, rj, run = _run_update(board, linked=linked)
    assert rc == 0
    assert not (rj.gql.created or rj.gql.added or rj.gql.statuses) and not run.called   # nothing written at all


def test_update_todo_duplicating_new_issue_is_not_added():
    todo = TODO + "- [ ] Fix the flaky login test.\n"
    linked = [("Todo", "Fix the flaky login test", "https://x/9")]
    rc, rj, run = _run_update(SETTLED, todo=todo, linked=linked)
    assert "Fix the flaky login test." not in _created_titles(rj)
    assert rj.gql.added == ["https://x/9"]


def test_update_dry_run_lists_new_issue_url(capsys):
    rc, rj, run = _run_update(SETTLED, argv=("--update", "--dry-run"), linked=[("Todo", "Open bug", "https://x/1")])
    out = capsys.readouterr().out
    assert rc == 0 and "add\tTodo\tOpen bug\thttps://x/1" in out
    run.assert_not_called()


def test_item_url_fallback_from_repository_and_number():
    assert cli._item_url({"content": {"type": "PullRequest", "number": 4, "repository": "o/r"}}) == "https://github.com/o/r/pull/4"
    assert cli._item_url({"content": {"type": "Issue", "number": 5, "repository": "o/r"}}) == "https://github.com/o/r/issues/5"
    assert cli._item_url({"content": {"type": "DraftIssue"}}) is None


def test_wait_for_items_polls_until_listed(no_sleep):
    seen = [[], [], [{"id": "N1"}, {"id": "N2"}]]
    with mock.patch("kopi.run_json", side_effect=lambda *a: {"items": seen.pop(0)}):
        cli.wait_for_items(mock.Mock(owner="o"), 2, ["N1", "N2"])
    assert no_sleep.call_count == 2 and not seen


def test_wait_for_items_gives_up_with_warning(no_sleep, capsys):
    with mock.patch("kopi.run_json", return_value={"items": []}):
        cli.wait_for_items(mock.Mock(owner="o"), 2, ["N1"])
    assert "not yet listed" in capsys.readouterr().err
    assert no_sleep.call_count == cli.SETTLE_TIMEOUT // cli.SETTLE_INTERVAL


def test_update_waits_for_new_items():
    with mock.patch("kopi.wait_for_items") as wait:
        _run_update(BOARD, argv=("--update",))
    ids = wait.call_args.args[2]
    assert ids and ids == [f"NEW{i}" for i in range(len(ids))]


def test_update_single_file_only_reads_that_file(tmp_path):
    f = tmp_path / "CHANGELOG.md"
    f.write_text("x")
    with mock.patch("kopi.os.chdir") as chdir:
        rc, rj, run = _run_update([], argv=("--update", str(f)), linked=[("Todo", "An issue", "https://x/1")])
    titles = _created_titles(rj)
    assert rc == 0 and chdir.call_args.args == (str(tmp_path),)
    assert titles and all(t.startswith("[0.0.") for t in titles)  # changelog only: no TODO items
    assert not any(c.args[1] == "item-add" for c in rj.call_args_list)  # and no issues/PRs

    f = tmp_path / "TODO.md"
    f.write_text("x")
    with mock.patch("kopi.os.chdir"):
        rc, rj, run = _run_update([], argv=("--update", str(f)))
    titles = _created_titles(rj)
    assert rc == 0 and titles and not any(t.startswith("[0.0.") for t in titles)


def test_import_single_file_dry_run(tmp_path, capsys):
    f = tmp_path / "TODO.md"
    f.write_text("x")
    with mock.patch("kopi.os.chdir"):
        rc, *_ = _run_import([], argv=("--import", str(f), "--dry-run"), linked=[("Todo", "Bug", "https://x/1")])
    out = capsys.readouterr().out
    assert rc == 0 and "Run against real GitHub." in out and "Single-file CLI" not in out and "Bug" not in out


def test_file_argument_errors(tmp_path, capsys):
    other = tmp_path / "README.md"
    other.write_text("x")
    f = tmp_path / "TODO.md"
    f.write_text("x")
    with mock.patch("kopi.check_auth"):
        assert cli.main(["--update", str(other)]) == 1       # not a source file
        assert cli.main(["--diff", str(f)]) == 1             # only --import/--update take a file
        assert cli.main(["--import", str(tmp_path / "CHANGELOG.md")]) == 1  # missing
    err = capsys.readouterr().err
    assert "only CHANGELOG.md or TODO.md" in err and "expected a directory" in err and "no such file" in err


def test_import_creates_items_and_statuses_in_batches(capsys):
    todo = "".join(f"- [ ] Task number {n} about topic{n}\n" for n in range(45))
    gql = FakeGraphql()
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="x"), mock.patch("kopi.graphql", gql), \
         mock.patch("kopi.ensure_project", return_value=(2, False)), mock.patch("kopi.fetch_linked", return_value=[]), \
         mock.patch("kopi._read", side_effect=lambda p: "" if p == "CHANGELOG.md" else todo), \
         mock.patch("kopi.run_json", side_effect=_import_mocks([])), mock.patch("kopi.run") as run:
        assert cli.main(["--import"]) == 0
    assert len(gql.created) == 45 and len(gql.statuses) == 45 and not run.called
    mutations = [q for q in gql.queries if q.startswith("mutation")]
    assert len(mutations) == 3 + 3                                    # 45 creates and 45 statuses, 20 per request
    err = capsys.readouterr().err
    assert "Importing 45 items into project #2" in err and "Creating 45 draft items..." in err
    assert "  created 20/45" in err and "  created 45/45" in err and "Setting 45 statuses..." in err and "  set 45/45" in err
    assert "Waiting for GitHub to list the 45 new items..." in err


def test_progress_goes_to_stderr_and_not_under_json(capsys):
    _run_import([])
    cap = capsys.readouterr()
    assert "Creating 5 draft items..." in cap.err and "Creating" not in cap.out
    _run_import([], argv=("--json", "--import"))
    cap = capsys.readouterr()
    assert "Creating" not in cap.err and json.loads(cap.out)["imported"] == 5     # a script's stderr stays quiet too


def test_batched_writes_report_graphql_errors_and_missing_issues():
    info = mock.Mock(project_id="P", field_id="F", option=lambda s: "o1")
    with mock.patch("kopi.graphql", return_value={"errors": [{"message": "boom"}, {"message": "bang"}]}):
        with pytest.raises(cli.GhError, match="boom; bang"):
            cli.create_items(mock.Mock(json=True), info, [("T", "")], [])
        with pytest.raises(cli.GhError, match="boom; bang"):
            cli.content_ids(mock.Mock(json=True), ["https://x/1"])
    with mock.patch("kopi.graphql", return_value={"data": {"a0": None}}):
        with pytest.raises(cli.GhError, match="could not find https://x/1"):
            cli.content_ids(mock.Mock(json=True), ["https://x/1"])
    # quotes, newlines and non-ASCII survive the trip into the GraphQL literal
    gql = FakeGraphql()
    with mock.patch("kopi.graphql", gql):
        cli.create_items(mock.Mock(json=True), info, [('Say "hi"\n— ünï', 'body with "quotes"\nand lines')], [])
    assert gql.created == [('Say "hi"\n— ünï', 'body with "quotes"\nand lines')]


def test_similar_todos_flags_reworded_open_entries_only():
    todo = ("- [ ] Start Ollama and the gateway at boot on this host (nothing starts at boot)\n"
            "- [ ] Start Ollama and the Hermes gateway at boot, as systemd user services so they survive reboots\n"
            "- [x] Start Ollama and the gateway at boot on this host\n"          # done entries are ignored
            "- [ ] Retry the lfm2.5 multi-step test\n"
            "- [ ] Fix it\n")                                                     # too short to compare
    pairs = cli.similar_todos(todo)
    assert len(pairs) == 1 and "on this host" in pairs[0][0] and "systemd" in pairs[0][1]


def test_import_dry_run_warns_about_similar_todos_on_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "TODO.md").write_text("- [ ] Start Ollama and the gateway at boot on this host\n"
                                      "- [ ] Start Ollama and the Hermes gateway at boot as services\n")
    with mock.patch.object(cli, "fetch_linked", return_value=[]):
        cli.main(["--owner", "o", "--import", "--dry-run"])
    cap = capsys.readouterr()
    assert "possible duplicate TODO.md entries" in cap.err and "possible duplicate" not in cap.out
