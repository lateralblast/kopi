"""--export into TODO.md."""
import json
import os
from unittest import mock

import pytest

import kopi as cli

from helpers import *  # noqa: F401,F403  (shared boards and runners)


def test_export_todo_adds_missing_unchecked_entries_under_open(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc, rj, run = _run_update(EXPORT_BOARD, argv=("--export", "todo"))
    out = capsys.readouterr().out
    assert rc == 0 and "3 todo items added to TODO.md (2 already present)" in out
    new = (tmp_path / "TODO.md").read_text()
    assert new.index("- [~] Run against real GitHub.\n- [ ] Brand new idea\n") < new.index("## Done")  # end of ## Open
    assert "- [ ] Long idea that was cut off in the title\n" in new and "- [ ] Fix the bug (https://x/9)\n" in new
    assert "In progress thing" not in new and "Done thing" not in new
    assert new.count("Created the project.") == 1 and "- [x] Created the project." in new  # not re-opened
    assert new.count("--json") == 1                                                          # not duplicated
    assert not any(c.args[:2] == ("project", "item-edit") for c in run.call_args_list)       # board untouched


def test_export_is_idempotent_and_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run_update(EXPORT_BOARD, argv=("--export", "todo", "--dry-run"))
    assert "Dry run: 3 todo items to export" in capsys.readouterr().out and not (tmp_path / "TODO.md").exists()
    _run_update(EXPORT_BOARD, argv=("--export", "todo"))
    exported = (tmp_path / "TODO.md").read_text()
    capsys.readouterr()
    _run_update(EXPORT_BOARD, argv=("--json", "--export", "todo"), todo=exported)  # a second run adds nothing
    data = json.loads(capsys.readouterr().out)
    assert data["added"] == [] and data["already_present"] == 5 and (tmp_path / "TODO.md").read_text() == exported


def test_insert_open_without_open_section_and_before_next_heading():
    assert cli._insert_entries("# TODO\n\n## Done\n\n- [x] a\n", "Open", ["- [ ] b"]) == "# TODO\n\n## Done\n\n- [x] a\n\n## Open\n\n- [ ] b\n"
    assert cli._insert_entries("## Open\n\n- [ ] a\n\n## Done\n", "Open", ["- [ ] b"]) == "## Open\n\n- [ ] a\n- [ ] b\n\n## Done\n"
    assert cli._insert_entries("## Open\n## Done\n", "Open", ["- [ ] b"]) == "## Open\n\n- [ ] b\n\n## Done\n"
    assert cli._insert_entries("", "Open", ["- [ ] a"]) == "# TODO\n\n## Open\n\n- [ ] a\n"  # missing file


def test_export_done_goes_to_top_of_done_newest_first_and_skips_changelog(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc, rj, run = _run_update(EXPORT_DONE_BOARD, argv=("--export", "done"))
    assert rc == 0 and "3 done items added to TODO.md (3 already present)" in capsys.readouterr().out
    new = (tmp_path / "TODO.md").read_text()
    assert "## Done\n\n- [x] Fixed the bug (https://x/8)\n- [x] Rewrote the parser entirely\n" \
           "- [x] Shipped the login page\n- [x] Auth check before every command" in new   # newest first, above old
    assert "0.9.9" not in new and "Single-file" not in new and new.count("Created the project.") == 1
    assert "Working on a thing" not in new and "A todo item" not in new


def test_export_inprogress_goes_to_open_as_tilde(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for kind in ("inprogress", "in-progress"):
        rc, *_ = _run_update(EXPORT_DONE_BOARD, argv=("--export", kind))
        assert rc == 0
        new = (tmp_path / "TODO.md").read_text()
        assert "- [~] Run against real GitHub.\n- [~] Working on a thing\n\n## Done" in new
        assert new.count("Run against real GitHub.") == 1 and "Shipped" not in new and "Rewrote" not in new
        (tmp_path / "TODO.md").unlink()
    capsys.readouterr()


def test_insert_entries_top_of_done_and_new_section():
    assert cli._insert_entries("## Open\n\n- [ ] a\n\n## Done\n\n- [x] old\n", "Done", ["- [x] new"], top=True) == \
        "## Open\n\n- [ ] a\n\n## Done\n\n- [x] new\n- [x] old\n"
    assert cli._insert_entries("## Open\n\n- [ ] a\n", "Done", ["- [x] n"], top=True) == "## Open\n\n- [ ] a\n\n## Done\n\n- [x] n\n"
    assert cli._insert_entries("## Done\n\n", "Done", ["- [x] n"], top=True) == "## Done\n\n- [x] n\n"


def test_export_creates_missing_todo_md_even_with_nothing_to_add(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for kind in ("todo", "inprogress", "done"):
        (tmp_path / "TODO.md").unlink(missing_ok=True)
        rc, *_ = _run_update([{"id": "X", "title": "Unrelated", "status": "Done"}], argv=("--export", kind), todo=" ")
        # todo=" ": the mocked _read returns blank text, as for a missing/empty file
        assert rc == 0 and "Created TODO.md" in capsys.readouterr().out, kind
        text = (tmp_path / "TODO.md").read_text()
        assert text.startswith("# TODO\n\nStatus:") and "## Open" in text and "## Done" in text, kind


def test_export_into_new_file_puts_entries_in_the_right_sections(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    board = [{"id": "A", "title": "Open thing", "status": "Todo"}, {"id": "B", "title": "Doing thing", "status": "In Progress"},
             {"id": "C", "title": "Finished thing", "status": "Done"}]
    for kind, expect in (("todo", "## Open\n\n- [ ] Open thing\n\n## Done\n"), ("inprogress", "## Open\n\n- [~] Doing thing\n\n## Done\n"),
                         ("done", "## Open\n\n## Done\n\n- [x] Finished thing\n")):
        (tmp_path / "TODO.md").unlink(missing_ok=True)
        _run_update(board, argv=("--export", kind), todo=" ")
        assert expect in (tmp_path / "TODO.md").read_text(), kind
    capsys.readouterr()


def test_export_dry_run_does_not_create_and_existing_file_is_not_created(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run_update(EXPORT_BOARD, argv=("--export", "todo", "--dry-run"))
    assert "Would create TODO.md" in capsys.readouterr().out and not (tmp_path / "TODO.md").exists()
    (tmp_path / "TODO.md").write_text(TODO)
    _run_update(EXPORT_BOARD, argv=("--json", "--export", "todo", "--dry-run"))
    assert json.loads(capsys.readouterr().out)["created"] is False
