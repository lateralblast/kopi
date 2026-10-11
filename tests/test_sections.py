"""The opt-in Section field: the TODO.md heading each board item sat under."""
from unittest import mock

import kopi as cli

from helpers import *  # noqa: F401,F403  (shared boards and runners)

SECTION_TODO = """# TODO

## ARM / Apple Silicon

- [ ] **Other CPUs.** The NEON path without DOTPROD is compiled, not run, and nobody has measured the
  fallback on older cores.
- [x] Token embedding as Q8_0.

### Sub heading

- [~] Deeper entry

## Housekeeping

- [ ] Delete stale model files.
- plain bullet, not an entry
"""
SECTION_FIELD = {"id": "SF", "name": "Section", "dataType": "TEXT"}


def _args(enabled=True, **extra):
    return mock.Mock(config_data={"sections": {"enabled": enabled, **extra}}, owner="o", json=False, quiet=False)


def _sync(items, todo=SECTION_TODO, fields=(SECTION_FIELD,), args=None):
    gql = FakeGraphql(fields=fields)
    with mock.patch("kopi.graphql", gql), mock.patch("kopi.run_json", return_value={"id": "PID"}), \
         mock.patch("kopi.run") as run:
        done = cli.sync_sections(args or _args(), 2, items, todo)
    return done, gql, run


def test_todo_sections_maps_each_checkbox_entry_to_its_nearest_heading():
    long = "Other CPUs. The NEON path without DOTPROD is compiled, not run, and nobody has measured the fallback on older cores."
    assert cli.todo_sections(SECTION_TODO) == {
        cli._title(long): "ARM / Apple Silicon",                          # titles are cut to 100 characters, as on the board
        "Token embedding as Q8_0.": "ARM / Apple Silicon", "Deeper entry": "Sub heading",
        "Delete stale model files.": "Housekeeping"}


def test_sections_are_set_on_matching_items_and_never_overwritten():
    items = [{"id": "I1", "title": "Token embedding as Q8_0.", "status": "Done"},
             {"id": "I2", "title": "Delete stale model files.", "status": "Todo", "section": "Chosen by hand"},   # already set
             {"id": "I3", "title": "Other CPUs. The NEON path without DOTPROD is compiled, not run, and nobody has measured the fa…", "status": "Todo"},
             {"id": "I4", "title": "Other CPUs. The NEON path without DOTPROD is compiled, not run, and nobody has", "status": "Todo"},  # old: first line only
             {"id": "I5", "title": "Unrelated", "status": "Todo"}]
    done, gql, run = _sync(items)
    assert gql.sections == [("I1", "SF", "ARM / Apple Silicon"), ("I3", "SF", "ARM / Apple Silicon"), ("I4", "SF", "ARM / Apple Silicon")]
    assert [d["title"] for d in done][0] == "Token embedding as Q8_0." and not run.called      # the field existed: nothing created


def test_sections_create_the_field_when_missing_and_are_off_by_default():
    items = [{"id": "I1", "title": "Token embedding as Q8_0.", "status": "Done"}]
    gql = FakeGraphql()
    calls = iter([[], [SECTION_FIELD]])          # no field at first, then the one just created
    with mock.patch("kopi.graphql", side_effect=lambda q, **v: {"data": {"node": {"fields": {"nodes": next(calls)}}}} if "fields(first" in q else gql(q, **v)), \
         mock.patch("kopi.run_json", return_value={"id": "PID"}), mock.patch("kopi.run") as run:
        cli.sync_sections(_args(), 2, items, SECTION_TODO)
    assert run.call_args.args == ("project", "field-create", "2", "--owner", "o", "--name", "Section", "--data-type", "TEXT")
    assert gql.sections == [("I1", "SF", "ARM / Apple Silicon")]
    done, gql, run = _sync(items, args=_args(enabled=False))
    assert done == [] and not gql.queries and not run.called


def test_a_non_text_field_is_skipped_with_a_warning(capsys):
    done, gql, _ = _sync([{"id": "I1", "title": "Token embedding as Q8_0.", "status": "Done"}],
                         fields=[{"id": "SF", "name": "Section", "dataType": "NUMBER"}])
    assert done == [] and gql.sections == [] and "not a text field" in capsys.readouterr().err


def test_import_with_sections_enabled_sets_them(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kopi.toml").write_text("[sections]\nenabled = true\n")
    gql = FakeGraphql(fields=[SECTION_FIELD])
    _run_import([], argv=("--json", "--import"), gql=gql)             # helpers' TODO: entries under "Open" and "Done"
    data = json.loads(capsys.readouterr().out)
    assert {s["section"] for s in data["sections"]} == {"Open", "Done"} and len(gql.sections) == len(data["sections"])
