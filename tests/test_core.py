"""Core behaviour: auth, owner, init, create, list, paths, --json and mode exclusivity."""
import json
import os
from unittest import mock

import pytest

import kopi as cli

from helpers import *  # noqa: F401,F403  (shared boards and runners)


def test_create_links_repo():
    with mock.patch("kopi.check_auth"), \
         mock.patch("kopi.run_json", return_value={"number": 7, "url": "u"}), \
         mock.patch("kopi.run") as run:
        assert cli.main(["--owner", "me", "create", "T", "--repo", "me/r"]) == 0
        run.assert_called_once_with("project", "link", "7", "--owner", "me", "--repo", "me/r")


def test_error_returns_1():
    import kopi as gh
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value=None), \
         mock.patch("kopi.run_json", side_effect=gh.GhError("boom")):
        assert cli.main(["list"]) == 1


def test_version_consistent():
    import pathlib
    import re
    root = pathlib.Path(__file__).parent.parent
    pyproject = re.search(r'^version = "(.+)"', (root / "pyproject.toml").read_text(), re.M).group(1)
    readme = re.search(r"^Version: (.+)$", (root / "README.md").read_text(), re.M).group(1)
    changelog = re.search(r"^## \[(\d+\.\d+\.\d+)\]", (root / "CHANGELOG.md").read_text(), re.M).group(1)  # newest release heading
    assert cli.__version__ == pyproject == readme == changelog


def test_check_auth_not_logged_in(capsys):
    with mock.patch("kopi.subprocess.run", return_value=_status(1)):
        assert cli.main(["list"]) == 1
    assert "gh auth login" in capsys.readouterr().err


def test_check_auth_missing_scope():
    out = "Token scopes: 'repo', 'read:org'"
    with mock.patch("kopi.subprocess.run", return_value=_status(0, out)):
        try:
            cli.check_auth()
        except cli.GhError as e:
            assert "gh auth refresh -s project" in str(e)
        else:
            raise AssertionError("expected GhError")


def test_check_auth_ok():
    out = "Token scopes: 'repo', 'project'"
    with mock.patch("kopi.subprocess.run", return_value=_status(0, out)):
        assert cli.check_auth() is None


def test_git_owner_parsing():
    for url, owner in [
        ("https://github.com/lateralblast/kopi.git\n", "lateralblast"),
        ("https://github.com/lateralblast/kopi\n", "lateralblast"),
        ("git@github.com:lateralblast/kopi.git\n", "lateralblast"),
        ("https://gitlab.com/someone/repo.git\n", None),
    ]:
        with mock.patch("kopi.subprocess.run", return_value=_status(0, url)):
            assert cli.git_owner() == owner


def test_git_owner_not_a_repo():
    with mock.patch("kopi.subprocess.run", return_value=_status(128)):
        assert cli.git_owner() is None


def test_owner_precedence():
    assert _owner_used(["--owner", "x", "list"], "gitorg") == "x"
    assert _owner_used(["list"], "gitorg") == "gitorg"
    assert _owner_used(["list"], None) == "@me"


def test_init_existing_project_does_nothing(capsys):
    rc, rj, run = _init([{"number": 2, "title": "kopi", "url": "u"}])
    assert rc == 0
    assert rj.call_count == 1  # list only, no create
    run.assert_not_called()
    assert "Project exists: #2" in capsys.readouterr().out


def test_init_creates_and_links():
    rc, rj, run = _init([{"number": 1, "title": "other", "url": "u"}])
    assert rc == 0
    assert rj.call_args_list[1].args == ("project", "create", "--title", "kopi", "--owner", "lateralblast")
    run.assert_called_once_with("project", "link", "3", "--owner", "lateralblast", "--repo", "lateralblast/kopi")


def test_init_other_owner_skips_link(capsys):
    rc, rj, run = _init([], argv=("--owner", "someone", "--init"))
    assert rc == 0
    run.assert_not_called()
    assert "Not linking" in capsys.readouterr().err


def test_init_outside_repo_errors(capsys):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_repo", return_value=None), \
         mock.patch("kopi.git_owner", return_value=None):
        assert cli.main(["--init"]) == 1
    assert "inside a git repo" in capsys.readouterr().err


def test_modes_are_mutually_exclusive(capsys):
    with mock.patch("kopi.check_auth") as auth, pytest.raises(SystemExit) as exc:
        cli.main(["--update", "--import"])
    assert exc.value.code == 2 and "mutually exclusive" in capsys.readouterr().err
    assert not auth.called  # rejected before anything touches GitHub


def test_diff_and_archive_are_exclusive_with_other_modes(capsys):
    for argv in (["--diff", "--update"], ["--archive", "--import"], ["--diff", "--archive"]):
        with mock.patch("kopi.check_auth") as auth, pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == 2 and "mutually exclusive" in capsys.readouterr().err and not auth.called


def test_update_path_changes_directory(tmp_path):
    with mock.patch("kopi.os.chdir") as chdir:
        rc, rj, run = _run_update(BOARD, argv=("--update", str(tmp_path)))
    assert rc == 0 and chdir.call_args.args == (str(tmp_path),)


def test_import_path_changes_directory(tmp_path):
    with mock.patch("kopi.os.chdir") as chdir:
        rc, *_ = _run_import([], argv=("--import", str(tmp_path), "--dry-run"))
    assert rc == 0 and chdir.call_args.args == (str(tmp_path),)


def test_path_must_be_a_directory(capsys):
    with mock.patch("kopi.check_auth"):
        assert cli.main(["--update", "/no/such/dir"]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_every_mode_accepts_a_path(tmp_path):
    for flag in ("--update", "--diff", "--archive", "--dedupe"):
        with mock.patch("kopi.os.chdir") as chdir:
            rc, *_ = _run_update(BOARD, argv=(flag, str(tmp_path), "--dry-run") if flag != "--diff" else (flag, str(tmp_path)))
        assert rc == 0 and chdir.call_args.args == (str(tmp_path),), flag


def test_init_accepts_a_path(tmp_path):
    with mock.patch("kopi.os.chdir") as chdir:
        rc = _init([{"number": 2, "title": "kopi", "url": "u"}], argv=("--init", str(tmp_path)))[0]
    assert rc == 0 and chdir.call_args.args == (str(tmp_path),)


def test_failed_link_after_create_is_a_warning(capsys):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.run_json", return_value={"number": 7, "url": "u"}), \
         mock.patch("kopi.run", side_effect=cli.GhError("repo not found")):
        rc = cli.main(["--owner", "me", "create", "T", "--repo", "me/r"])
    cap = capsys.readouterr()
    assert rc == 0 and "Created #7" in cap.out
    assert "warning: project #7 was created but linking to me/r failed: repo not found" in cap.err


def test_list_shows_header_state_and_repo(capsys):
    rc, rj = _list(("list", "--closed"))
    lines = capsys.readouterr().out.splitlines()
    assert rc == 0 and "--closed" in rj.call_args.args
    assert lines == ["NUMBER\tTITLE\tSTATE\tREPO\tURL", "2\tkopi\topen\to/kopi\tu2", "1\told\tclosed\t-\tu1"]


def test_list_survives_repo_lookup_failure(capsys):
    rc, _ = _list(gql={"side_effect": cli.GhError("boom")})
    cap = capsys.readouterr()
    assert rc == 0 and "2\tkopi\topen\t-\tu2" in cap.out and "could not look up linked repos" in cap.err


def test_json_list(capsys):
    rc, _ = _list(("--json", "list"))
    data, cap = _json_out(capsys)
    assert rc == 0 and data[0] == {"number": 2, "title": "kopi", "state": "open", "repos": ["o/kopi"], "url": "u2"}
    assert data[1]["state"] == "closed"


def test_json_update_dry_run_and_real(capsys):
    _run_update(BOARD, argv=("--json", "--update", "--dry-run"))
    data, _ = _json_out(capsys)
    assert data["dry_run"] is True and data["project"] == 2 and data["added"] and data["moved"]
    _run_update(BOARD, argv=("--json", "--update"))
    data, cap = _json_out(capsys)  # only the JSON document reaches stdout
    assert data["dry_run"] is False and "Updated project" not in cap.out


def test_json_diff_and_dedupe_and_archive(capsys):
    _run_update(BOARD, argv=("--json", "--diff"))
    data, _ = _json_out(capsys)
    assert set(data) == {"project", "files_only", "github_only", "board_only", "duplicates", "behind", "ahead", "in_sync"}
    _run_update(DUPES, argv=("--json", "--dedupe", "--dry-run"))
    data, _ = _json_out(capsys)
    assert [i["title"] for i in data["deleted"]] == ["[0.2.3] Verified  update live.", "Same idea", "Issue"]
    _run_update(ORPHANS, argv=("--json", "--archive", "--dry-run"))
    data, _ = _json_out(capsys)
    assert data["dry_run"] is True and data["archived"]


def test_json_init_and_import(capsys):
    _init([{"number": 2, "title": "kopi", "url": "u"}], argv=("--json", "--init"))
    data, cap = _json_out(capsys)
    assert data == {"number": 2, "created": False} and "Project exists" not in cap.out
    _run_import([], argv=("--json", "--import", "--dry-run"), linked=[("Todo", "Bug", "https://x/1")])
    data, _ = _json_out(capsys)
    assert data["dry_run"] is True and {"status": "Todo", "title": "Bug", "url": "https://x/1"} in data["items"]


def test_rate_limit_error_says_how_to_check_the_reset():
    proc = mock.Mock(returncode=1, stdout="", stderr="GraphQL: API rate limit exceeded for user ID 1.")
    with mock.patch("kopi.subprocess.run", return_value=proc), pytest.raises(cli.GhError) as exc:
        cli.run("project", "list")
    assert "rate limit exceeded" in str(exc.value) and "rateLimit { remaining resetAt }" in str(exc.value)


PLAIN_CHANGELOG = """# Changelog

## 0.4.0 - 2026-10-07

### Added
- `start_llama.py -m NAME` fetches and builds a model that is not under `models/` (new `model_fetch.py`:
  catalogue, size estimates; `--yes`, `--no-fetch`).
- Second thing.

## v0.3.0 (2026-10-06)

### Fixed
- Older fix.

## [0.2.0] - 2026-10-05

- Bracketed one.
"""


def test_changelog_headings_without_brackets_are_recognised():
    assert cli.parse_changelog(PLAIN_CHANGELOG) == [
        ("0.4.0", "`start_llama.py -m NAME` fetches and builds a model that is not under `models/` (new `model_fetch.py`: "
                  "catalogue, size estimates; `--yes`, `--no-fetch`)."),
        ("0.4.0", "Second thing."), ("0.3.0", "Older fix."), ("0.2.0", "Bracketed one.")]
    assert cli.changelog_release_dates(PLAIN_CHANGELOG) == {"0.4.0": "2026-10-07", "0.3.0": "2026-10-06", "0.2.0": "2026-10-05"}
    assert cli.latest_release_version(PLAIN_CHANGELOG) == "0.4.0"
    assert cli.changelog_section(PLAIN_CHANGELOG, "0.3.0") == "### Fixed\n- Older fix."
    assert [(r["version"], r["date"], r["entries"]) for r in cli.changelog_releases(PLAIN_CHANGELOG)] == [
        ("0.4.0", "2026-10-07", 2), ("0.3.0", "2026-10-06", 1), ("0.2.0", "2026-10-05", 1)]
    assert cli.changelog_heading("## Added") is None and cli.changelog_heading("### 1.2.3") is None


def test_wrapped_bullets_are_one_entry_in_changelog_and_todo():
    todo = ("## Code\n\n- [ ] **Interleaved functions have no callers.** They now assert on rows that\n"
            "  are not a multiple of 128.\n  - nested note\n- [x] Short one.\n\n- [~] Tail\n  wraps too\n").replace("\n", "\n")
    assert cli.parse_todo(todo) == [
        ("Todo", "**Interleaved functions have no callers.** They now assert on rows that are not a multiple of 128."),
        ("Done", "Short one."), ("In Progress", "Tail wraps too")]
    assert cli.bullet_blocks(["- a", "  b", "", "- c"]) == [(0, 1), (3, 3)]


def _waiting_args():
    return mock.Mock(owner="o", json=False, quiet=True, status_map=None, config_data={})


def test_wait_for_items_polls_the_cheap_count_not_the_whole_board():
    totals = iter([10, 10, 13])     # the three new items show up on the third poll
    with mock.patch("kopi.board_total", side_effect=lambda *a: next(totals)), \
         mock.patch("kopi.board_items") as listing, mock.patch("kopi.time.sleep"):
        cli.wait_for_items(_waiting_args(), 2, ["a", "b", "c"], before=10)
    assert not listing.called and next(totals, None) is None                  # three polls, no full listing


def test_wait_for_items_falls_back_to_the_listing_when_the_count_cannot_be_read():
    with mock.patch("kopi.board_total", return_value=None), \
         mock.patch("kopi.board_items", return_value=[{"id": "a"}, {"id": "b"}]) as listing:
        cli.wait_for_items(_waiting_args(), 2, ["a", "b"], before=5)
        assert listing.call_count == 1
        listing.reset_mock()
        cli.wait_for_items(_waiting_args(), 2, ["a"])                          # no count taken beforehand: the listing too
        assert listing.call_count == 1


def test_wait_for_items_gives_up_with_a_warning_and_a_failure_is_only_a_warning(capsys):
    with mock.patch("kopi.board_total", return_value=10), mock.patch("kopi.time.sleep"):
        cli.wait_for_items(_waiting_args(), 2, ["a"], before=10)               # never reaches 11
    assert "1 new items not yet listed" in capsys.readouterr().err
    with mock.patch("kopi.board_total", side_effect=cli.GhError("API rate limit exceeded")):
        cli.wait_for_items(_waiting_args(), 2, ["a"], before=10)               # does not raise: the writes were made
    assert "could not confirm that GitHub lists the new items (API rate limit exceeded)" in capsys.readouterr().err


def test_board_total_reads_one_count_and_gives_none_when_it_cannot():
    gql = FakeGraphql()
    gql.base_total = 41
    with mock.patch("kopi.run_json", return_value={"id": "PID"}), mock.patch("kopi.graphql", gql):
        assert cli.board_total(_waiting_args(), 2) == 41
    with mock.patch("kopi.run_json", side_effect=cli.GhError("boom")):
        assert cli.board_total(_waiting_args(), 2) is None


def test_rate_limit_hint_gives_the_reset_time_when_the_budget_is_spent():
    spent = mock.Mock(returncode=0, stdout=json.dumps({"data": {"rateLimit": {"remaining": 0, "resetAt": "2099-01-01T10:39:16Z"}}}), stderr="")
    failed = mock.Mock(returncode=1, stdout="", stderr="unknown owner type")
    with mock.patch("kopi.subprocess.run", side_effect=[failed, spent]), pytest.raises(cli.GhError) as exc:
        cli.run("project", "list")
    assert "resets at 10:39 UTC (in " in str(exc.value) and "0.7 points per item" in str(exc.value)
    left = mock.Mock(returncode=0, stdout=json.dumps({"data": {"rateLimit": {"remaining": 900, "resetAt": "2099-01-01T10:39:16Z"}}}), stderr="")
    with mock.patch("kopi.subprocess.run", side_effect=[failed, left]), pytest.raises(cli.GhError) as exc:
        cli.run("project", "list")
    assert str(exc.value) == "unknown owner type"                              # budget left: not a rate limit, so no hint
