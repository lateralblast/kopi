"""Fixtures applied to every test."""
import pytest
from unittest import mock


@pytest.fixture(autouse=True)
def state_file(tmp_path, monkeypatch):
    """Keep tests away from the real ~/.config/kopi archive log."""
    monkeypatch.setenv("KOPI_STATE", str(tmp_path / "state" / "archived.json"))


@pytest.fixture(autouse=True)
def no_sleep():
    with mock.patch("kopi.time.sleep") as sleep:
        yield sleep


@pytest.fixture(autouse=True)
def no_dates():
    """Date-writing is off by default in tests, so --import / --update / --done never touch (or create) date fields.
    tests/test_dates.py switches it back on."""
    with mock.patch("kopi.dates_enabled", return_value=False):
        yield


@pytest.fixture(autouse=True)
def gh_item_list():
    """Boards are read through `gh project item-list` in tests (they mock run_json); tests/test_lean.py switches the
    slim GraphQL read back on."""
    with mock.patch("kopi.LEAN_READS", False):
        yield


@pytest.fixture(autouse=True)
def no_changelog_window():
    """A [changelog] window set by one test must not cut the changelog in the next."""
    import kopi
    kopi.CHANGELOG_WINDOW.clear()
    yield
    kopi.CHANGELOG_WINDOW.clear()
