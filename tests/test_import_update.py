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
    todo = "".join(f"- [ ] Task number {n} about topic{n}\n" for n in range(120))
    gql = FakeGraphql()
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="x"), mock.patch("kopi.graphql", gql), \
         mock.patch("kopi.ensure_project", return_value=(2, False)), mock.patch("kopi.fetch_linked", return_value=[]), \
         mock.patch("kopi._read", side_effect=lambda p: "" if p == "CHANGELOG.md" else todo), \
         mock.patch("kopi.run_json", side_effect=_import_mocks([])), mock.patch("kopi.run") as run:
        assert cli.main(["--import"]) == 0
    assert len(gql.created) == 120 and len(gql.statuses) == 120 and not run.called
    mutations = [q for q in gql.queries if q.startswith("mutation")]
    assert len(mutations) == 3 + 3                                    # 120 creates and 120 statuses, 50 per request
    err = capsys.readouterr().err
    assert "Importing 120 items into project #2" in err and "Creating 120 draft items..." in err
    assert "  created 50/120" in err and "  created 120/120" in err and "Setting 120 statuses..." in err and "  set 120/120" in err
    assert "Waiting for GitHub to list the 120 new items..." in err


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


def test_import_and_update_wait_for_the_listing_after_the_date_writes():
    order = []
    with mock.patch("kopi.wait_for_items", side_effect=lambda *a: order.append("wait")), \
         mock.patch("kopi.sync_dates", side_effect=lambda *a: order.append("dates") or ([], [])):
        _run_import([], argv=("--import",))
        assert order == ["dates", "wait"]
        order.clear()
        _run_update(BOARD[:1], argv=("--update",))
        assert order == ["dates", "wait"]


def test_context_dependent_entries_are_recognised_and_warned_about(capsys):
    for text in ("Neither is run by `--status`, which only reports that an update exists.",
                 "Both were run for real on the host.", "It stops the dashboard first.", "Fix it as above."):
        assert cli.context_dependent(text), text
    for text in ("Items are created in batches.", "Verified live (deleted afterwards).", "Each bullet becomes an item."):
        assert not cli.context_dependent(text), text
    cli.warn_context_dependent("## [1.0.0] - 2026-01-01\n- Adds `--update`.\n- Neither is run by `--status`.\n", "- [ ] Both need a retest\n")
    err = capsys.readouterr().err
    assert "2 entries may not make sense on their own" in err and "[1.0.0] Neither is run by `--status`." in err and "Both need a retest" in err
    cli.warn_context_dependent("## [1.0.0] - 2026-01-01\n- Adds `--update`.\n", "- [ ] Fine\n")
    assert capsys.readouterr().err == ""


def test_import_dry_run_warns_about_context_dependent_entries(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("## [0.1.0] - 2026-01-01\n- Adds things.\n- Both were run live.\n")
    with mock.patch.object(cli, "fetch_linked", return_value=[]):
        cli.main(["--owner", "o", "--import", "--dry-run"])
    assert "1 entry may not make sense on their own" in capsys.readouterr().err


def test_bold_markers_are_stripped_from_titles_but_kept_in_the_body():
    assert cli.strip_emphasis("**Bold lead.** Then `**kwargs` and __this__ and a * star") == "Bold lead. Then `**kwargs` and this and a * star"
    plan, _ = cli.build_plan("## [0.1.0] - 2026-01-01\n- **Added** a thing.\n", "- [ ] **The functions have no callers.** Delete them.\n- [ ] Plain\n")
    assert plan == [("Done", "[0.1.0] Added a thing.", "**Added** a thing."),
                    ("Todo", "The functions have no callers. Delete them.", "**The functions have no callers.** Delete them."),
                    ("Todo", "Plain", "")]


def test_fetch_linked_says_once_that_issues_are_off_and_otherwise_stays_quiet(capsys):
    def fake(kind, *a):
        if kind == "issue":
            raise cli.GhError("the 'o/r' repository has disabled issues")
        return "[]"
    with mock.patch("kopi.run", side_effect=fake):
        assert cli.fetch_linked(("o", "r")) == []
        assert capsys.readouterr().err == ""
        cli.fetch_linked(("o", "r"), notice=True)
        assert "note: issues are turned off for o/r" in capsys.readouterr().err
    with mock.patch("kopi.run", side_effect=cli.GhError("boom")):
        cli.fetch_linked(("o", "r"))
        assert "warning: could not list issues for o/r: boom" in capsys.readouterr().err      # other failures still warn


def test_import_waits_on_the_item_count_not_by_listing_the_board_again(capsys):
    gql = FakeGraphql()
    rc, rj, _ = _run_import([], argv=("--import",), gql=gql)
    assert rc == 0
    lists = [c for c in rj.call_args_list if c.args[1] == "item-list"]
    assert len(lists) == 1 and "1" in lists[0].args           # only the "is the project empty" check, with --limit 1
    assert any("totalCount" in q for q in gql.queries)


def _parts(n):
    return [f"updateProjectV2ItemFieldValue(input: {{n: {i}}}) {{ projectV2Item {{ id }} }}" for i in range(n)]


def _echo(query, **_):
    import re
    return {"data": {f"a{n}": {"op": int(i)} for n, i in re.findall(r"a(\d+): \w+\(input: \{n: (\d+)", query)}}


def test_repeatable_writes_run_in_parallel_and_keep_their_order(capsys):
    with mock.patch("kopi.graphql", side_effect=_echo) as gql:
        out = cli.mutate_batched(mock.Mock(json=False), _parts(170), "set", repeatable=True)
    assert [r["op"] for r in out] == list(range(170)) and gql.call_count == 4         # 50 + 50 + 50 + 20
    assert "  set 170/170" in capsys.readouterr().err


def test_a_failed_parallel_request_is_retried_on_its_own():
    calls = {"n": 0}

    def flaky(query, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise cli.GhError("gh: Something went wrong while executing your query")
        return _echo(query)
    with mock.patch("kopi.graphql", side_effect=flaky):
        out = cli.mutate_batched(mock.Mock(json=True), _parts(120), "set", repeatable=True)
    assert [r["op"] for r in out] == list(range(120)) and calls["n"] == 4              # 3 requests, one retried


def test_a_request_that_keeps_failing_still_raises():
    with mock.patch("kopi.graphql", side_effect=cli.GhError("down")), pytest.raises(cli.GhError, match="down"):
        cli.mutate_batched(mock.Mock(json=True), _parts(120), "set", repeatable=True)


def test_creating_items_is_never_parallel_or_retried():
    """A failed create may have partly applied, so repeating it would duplicate items."""
    with mock.patch("kopi.graphql", side_effect=cli.GhError("down")) as gql, pytest.raises(cli.GhError):
        cli.mutate_batched(mock.Mock(json=True), _parts(120), "created")
    assert gql.call_count == 1


BIG_CHANGELOG = "# Changelog\n\n" + "".join(f"## [{n // 100}.{n // 10 % 10}.{n % 10}] - 2026-01-01\n- Change number {n}\n\n" for n in range(349, -1, -1))


def _import_big(argv=("--import", "--dry-run"), config=None):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="x"), \
         mock.patch("kopi.load_config", return_value=config or {}), mock.patch("kopi.fetch_linked", return_value=[]), \
         mock.patch("kopi._read", side_effect=lambda p: BIG_CHANGELOG if p == "CHANGELOG.md" else ""):
        return cli.main(list(argv))


def test_big_unwindowed_import_advises_narrowing_the_changelog(capsys):
    assert _import_big() == 0
    cap = capsys.readouterr()
    assert "warning: CHANGELOG.md has 350 entries in 350 releases, so this import creates 350 items" in cap.err
    assert "`[changelog] last = 50`" in cap.err and "`group = \"minor\"`" in cap.err
    assert "Dry run: 350 items would be imported (about 2 minutes to write)" in cap.out


def test_a_changelog_setting_or_a_small_import_gets_no_advisory(capsys):
    _import_big(config={"changelog": {"last": 50}})
    assert "this import creates" not in capsys.readouterr().err
    _import_big(config={"changelog": {"group": "minor"}})
    assert "this import creates" not in capsys.readouterr().err
    _run_import([], argv=("--import", "--dry-run"))
    cap = capsys.readouterr()
    assert "this import creates" not in cap.err and "Dry run: 5 items would be imported\n" in cap.out   # no estimate when small


def test_dry_run_json_estimates_the_write_time(capsys):
    _import_big(argv=("--json", "--import", "--dry-run"))
    assert json.loads(capsys.readouterr().out)["estimated_seconds"] == 140


GROUPED = "# Changelog\n\n## [0.0.2] - 2026-10-10\n- Auth check before every command.\n\n## [0.0.1] - 2026-10-10\n- Single-file CLI.\n"
GROUP_BODY = "- 0.0.2: Auth check before every command.\n- 0.0.1: Single-file CLI."
NO_TODO = "# TODO\n"


def _draft(item_id, title, body, draft_id="DI_1", status="Done"):
    content = {"type": "DraftIssue", "body": body, **({"id": draft_id} if draft_id else {})}
    return {"id": item_id, "title": title, "status": status, "content": content}


def _grouped_update(items, argv, **kw):
    with mock.patch("kopi.load_config", return_value={"changelog": {"group": "minor"}}):
        return _run_update(items, argv=argv, changelog=GROUPED, todo=NO_TODO, **kw)


def test_refresh_bodies_rewrites_a_stale_grouped_body(capsys):
    stale = _draft("G", "[0.0] Releases 0.0.x", "- 0.0.1: Single-file CLI.", "DI_G")   # written when only 0.0.1 existed
    rc, rj, _ = _grouped_update([stale], ("--update", "--refresh-bodies"))
    assert rc == 0 and rj.gql.refreshed == [("DI_G", GROUP_BODY)]
    assert "1 bodies refreshed" in capsys.readouterr().out


def test_bodies_are_left_alone_without_the_flag_or_when_nothing_differs(capsys):
    stale = _draft("G", "[0.0] Releases 0.0.x", "old", "DI_G")
    assert _grouped_update([stale], ("--update",))[1].gql.refreshed == []
    current = _draft("G", "[0.0] Releases 0.0.x", GROUP_BODY.replace("\n", "\r\n"), "DI_G")       # CRLF from the web UI is the same text
    rj = _grouped_update([current], ("--update", "--refresh-bodies"))[1]
    assert rj.gql.refreshed == [] and "up to date" in capsys.readouterr().out


def test_refresh_bodies_never_touches_notes_on_items_whose_file_side_has_no_body(capsys):
    short = _draft("A", "[0.0.1] Single-file CLI.", "my own notes added on the board", "DI_A")
    no_id = _draft("B", "[0.0] Releases 0.0.x", "stale", None)                                      # no draft id: cannot be edited
    linked = {"id": "L", "title": "An issue", "status": "Todo", "content": {"type": "Issue", "number": 3, "repository": "o/kopi", "body": "x"}}
    for board in ([short], [no_id], [linked]):
        assert _run_update(board, argv=("--update", "--refresh-bodies"), changelog=GROUPED, todo=NO_TODO)[1].gql.refreshed == []


def test_refresh_bodies_dry_run_and_json(capsys):
    stale = _draft("G", "[0.0] Releases 0.0.x", "old", "DI_G")
    rc, rj, _ = _grouped_update([stale], ("--update", "--refresh-bodies", "--dry-run"))
    out = capsys.readouterr().out
    assert "refresh\tbody\t[0.0] Releases 0.0.x" in out and "0 to add, 0 to move, 1 bodies to refresh in project #2" in out
    assert rj.gql.refreshed == []                                                                    # nothing written
    _grouped_update([stale], ("--json", "--update", "--refresh-bodies"))
    assert json.loads(capsys.readouterr().out)["refreshed"] == [{"title": "[0.0] Releases 0.0.x"}]
    _grouped_update([stale], ("--json", "--update"))
    assert "refreshed" not in json.loads(capsys.readouterr().out)                                    # the JSON shape only grows with the flag


def test_refresh_bodies_needs_update_or_sync():
    with pytest.raises(SystemExit):
        cli.main(["--diff", "--refresh-bodies"])
