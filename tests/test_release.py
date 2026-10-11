"""--release: a GitHub release at the last commit."""
import json
from unittest import mock

import pytest

import kopi as cli

from helpers import *  # noqa: F401,F403  (shared boards and runners)

SHA = "a" * 40
OTHER = "b" * 40
RELEASE_CHANGELOG = """# Changelog

## [Unreleased]

- Not yet.

## [0.9.0] - 2026-05-01

### Added
- The newest thing.
- Another new thing.

## [0.10.0] - 2026-04-01

### Fixed
- An older fix (but a higher version number).

## [0.8.0] - 2026-03-01

## [0.7.0]
"""


def _release(argv, changelog=RELEASE_CHANGELOG, view=None, commit_ok=True, tag_at=None, tag_kind="commit", dirty=False,
             sha=SHA):
    """Run kopi with the git and gh sides faked; returns (rc, the gh calls that were made)."""
    calls = []

    def fake_run(*a):
        calls.append(a)
        if a[:2] == ("release", "view"):
            if view:
                return json.dumps({"url": view, "isDraft": False})
            raise cli.GhError("release not found")
        if a[0] == "api" and "/commits/" in a[1]:
            if not commit_ok:
                raise cli.GhError("No commit found for SHA")
            return sha
        if a[0] == "api" and "/git/ref/tags/" in a[1]:
            if tag_at is None:
                raise cli.GhError("Not Found")
            return f"{tag_kind} {tag_at}"
        if a[0] == "api" and "/git/tags/" in a[1]:
            return OTHER                                          # an annotated tag's commit
        if a[:2] == ("release", "create"):
            return "https://github.com/lateralblast/kopi/releases/tag/" + a[2] + "\n"
        return ""
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
         mock.patch("kopi.git_repo", return_value=("lateralblast", "kopi")), \
         mock.patch("kopi.head_commit", return_value=(sha, "Fix the thing")), mock.patch("kopi.git_dirty", return_value=dirty), \
         mock.patch("kopi._read", side_effect=lambda p: changelog if p == "CHANGELOG.md" else ""), \
         mock.patch("kopi.run", side_effect=fake_run):
        rc = cli.main(list(argv))
    return rc, calls


def _created(calls):
    return [c for c in calls if c[:2] == ("release", "create")]


def test_changelog_helpers():
    assert cli.latest_release_version(RELEASE_CHANGELOG) == "0.9.0"        # the latest date wins, not the highest number
    assert cli.latest_release_version("## [Unreleased]\n- x\n") is None     # undated headings do not count
    assert cli.latest_release_version("## [1.2.0] - 2026-01-01\n## [1.10.0] - 2026-01-01\n") == "1.10.0"   # same day: the higher version
    assert cli.changelog_section(RELEASE_CHANGELOG, "0.9.0") == "### Added\n- The newest thing.\n- Another new thing."
    assert cli.changelog_section(RELEASE_CHANGELOG, "0.8.0") == "" and cli.changelog_section(RELEASE_CHANGELOG, "9.9.9") == ""
    assert cli.changelog_section("## [0.1.0] - 2026-01-01\n- only\n", "0.1.0") == "- only"    # the last section, to end of file


def test_release_is_created_at_the_last_commit_with_the_changelog_notes(capsys):
    rc, calls = _release(("--release",))
    assert rc == 0
    (create,) = _created(calls)
    assert create[:5] == ("release", "create", "0.9.0", "--repo", "lateralblast/kopi")
    assert create[create.index("--target") + 1] == SHA and create[create.index("--title") + 1] == "0.9.0"
    assert create[create.index("--notes") + 1] == "### Added\n- The newest thing.\n- Another new thing."
    assert "--generate-notes" not in create and "--draft" not in create
    assert f"Released 0.9.0 at {SHA[:7]} (Fix the thing): https://github.com/lateralblast/kopi/releases/tag/0.9.0" in capsys.readouterr().out


def test_release_tag_notes_and_draft_options(capsys):
    rc, calls = _release(("--release", "--draft"))
    create = _created(calls)[0]
    assert create[2] == "0.9.0" and "--draft" in create                      # the tag is the plain version number by default
    rc, calls = _release(("--release", "--tag", "v0.9.0"))                  # --tag overrides it; the v is ignored when finding the notes
    create = _created(calls)[0]
    assert create[2] == "v0.9.0" and create[create.index("--notes") + 1].startswith("### Added")
    rc, calls = _release(("--release", "--tag", "0.8.0"))                   # a tag whose changelog section is empty
    create = _created(calls)[0]
    assert create[2] == "0.8.0" and "--generate-notes" in create and "--notes" not in create
    capsys.readouterr()                                                     # drop the output of the runs above
    rc, calls = _release(("--json", "--release", "--tag", "2.0.0"))                  # an explicit tag with no changelog entry at all
    assert _created(calls)[0][2] == "2.0.0" and "--generate-notes" in _created(calls)[0]
    assert json.loads(capsys.readouterr().out)["notes"] == "generated"


def test_release_dry_run_json_and_existing(capsys):
    capsys.readouterr()
    rc, calls = _release(("--release", "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 0 and not _created(calls) and f"release\t0.9.0\t{SHA[:7]} Fix the thing\tnotes: changelog" in out
    rc, calls = _release(("--json", "--release", "--dry-run"))
    data = json.loads(capsys.readouterr().out)
    assert data == {"repo": "lateralblast/kopi", "tag": "0.9.0", "target": SHA, "subject": "Fix the thing", "dry_run": True,
                    "draft": False, "notes": "changelog", "existing": False, "url": None}
    url = "https://github.com/lateralblast/kopi/releases/tag/0.9.0"
    rc, calls = _release(("--release",), view=url)                          # already released: reported, nothing created
    assert rc == 0 and not _created(calls) and f"Already released: 0.9.0 {url}" in capsys.readouterr().out
    rc, calls = _release(("--json", "--release"))
    assert json.loads(capsys.readouterr().out)["url"].endswith("/0.9.0") and len(_created(calls)) == 1


def test_release_refuses_when_the_commit_or_tag_is_not_right(capsys):
    rc, calls = _release(("--release",), commit_ok=False)
    assert rc == 1 and not _created(calls) and "is not on GitHub yet; push it first" in capsys.readouterr().err
    rc, calls = _release(("--release",), tag_at=OTHER)                      # the tag exists, on another commit
    assert rc == 1 and not _created(calls) and f"tag 0.9.0 already exists on GitHub at {OTHER[:7]}" in capsys.readouterr().err
    rc, calls = _release(("--release",), tag_at=OTHER, tag_kind="tag")      # an annotated tag is looked through to its commit
    assert rc == 1 and f"at {OTHER[:7]}" in capsys.readouterr().err
    rc, calls = _release(("--release",), tag_at=SHA)                        # a tag pushed first, on this very commit: fine
    assert rc == 0 and len(_created(calls)) == 1
    rc, calls = _release(("--release",), changelog="## [Unreleased]\n- x\n")
    assert rc == 1 and not _created(calls) and "no dated `## [version] - date` heading" in capsys.readouterr().err
    rc, calls = _release(("--release",), sha=None)                          # a repo with no commits
    assert rc == 1 and not _created(calls) and "no commits to release" in capsys.readouterr().err


def test_release_warns_about_uncommitted_changes_and_checks_its_flags(capsys):
    rc, calls = _release(("--release",), dirty=True)
    assert rc == 0 and len(_created(calls)) == 1 and "uncommitted changes are not in the release" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["--draft", "--update"])                                   # --draft belongs to --release
    with pytest.raises(SystemExit):
        cli.main(["--release", "--update"])
    with pytest.raises(SystemExit):
        cli.main(["--tag", "1.0.0", "--update"])                            # --tag belongs to --release
    rc, calls = _release(("--release", "--tag", "has space"))
    assert rc == 1 and not _created(calls) and "not a usable tag name" in capsys.readouterr().err


GH_RELEASES = json.dumps([
    {"tagName": "0.9.0", "name": "Nine", "isDraft": False, "isPrerelease": False, "isLatest": True, "publishedAt": "2026-05-01T10:00:00Z", "createdAt": "2026-05-01T09:00:00Z"},
    {"tagName": "1.0.0-rc1", "name": "", "isDraft": False, "isPrerelease": True, "isLatest": False, "publishedAt": "2026-06-01T10:00:00Z", "createdAt": "2026-06-01T09:00:00Z"},
    {"tagName": "1.0.0", "name": "One", "isDraft": True, "isPrerelease": False, "isLatest": False, "publishedAt": None, "createdAt": "2026-07-04T09:00:00Z"}])


def _list_releases(argv, repo=("lateralblast", "kopi")):
    calls = []

    def fake_run(*a):
        calls.append(a)
        return GH_RELEASES
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="lateralblast"), \
         mock.patch("kopi.git_repo", return_value=repo), mock.patch("kopi.run", side_effect=fake_run), \
         mock.patch("kopi._read", side_effect=lambda p: RELEASE_CHANGELOG if p == "CHANGELOG.md" else ""):
        rc = cli.main(list(argv))
    return rc, calls


def test_list_releases_shows_github_releases_with_their_flags(capsys):
    rc, calls = _list_releases(("--list", "releases"))
    assert rc == 0 and calls[0][:4] == ("release", "list", "--repo", "lateralblast/kopi")
    assert capsys.readouterr().out.splitlines() == [
        "0.9.0\t2026-05-01\tlatest\tNine",
        "1.0.0-rc1\t2026-06-01\tpre-release\t1.0.0-rc1",                        # no name: the tag stands in
        "1.0.0\t2026-07-04\tdraft\tOne"]                                         # a draft has no publish date: its created date
    rc, calls = _list_releases(("--list", "release"))                            # the singular works too
    assert rc == 0 and capsys.readouterr().out.count("\n") == 3


def test_list_releases_json_and_errors(capsys):
    rc, _ = _list_releases(("--json", "--list", "releases"))
    rows = json.loads(capsys.readouterr().out)
    assert rows[0] == {"tag": "0.9.0", "name": "Nine", "date": "2026-05-01", "latest": True, "draft": False, "prerelease": False,
                       "url": "https://github.com/lateralblast/kopi/releases/tag/0.9.0"}
    assert [r["tag"] for r in rows if r["draft"] or r["prerelease"]] == ["1.0.0-rc1", "1.0.0"]
    rc, calls = _list_releases(("--list", "releases"), repo=None)
    assert rc == 1 and not calls and "must be run inside a git repo" in capsys.readouterr().err


def test_list_releases_local_reads_the_changelog_without_github(capsys):
    with mock.patch("kopi._read", side_effect=lambda p: RELEASE_CHANGELOG), mock.patch("kopi.run") as run, \
         mock.patch("kopi.check_auth") as auth:
        assert cli.main(["--list", "releases", "--local"]) == 0
        assert capsys.readouterr().out.splitlines() == ["0.9.0\t2026-05-01\t2", "0.10.0\t2026-04-01\t1", "0.8.0\t2026-03-01\t0"]
        assert cli.main(["--json", "--list", "releases", "--local"]) == 0
    assert json.loads(capsys.readouterr().out)[0] == {"version": "0.9.0", "date": "2026-05-01", "entries": 2}
    assert not run.called and not auth.called                                    # --local needs no GitHub and no login


def test_release_tag_prefix_from_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kopi.toml").write_text('[release]\ntag_prefix = "v"\n')
    rc, calls = _release(("--release",))
    create = _created(calls)[0]
    assert rc == 0 and create[2] == "v0.9.0" and create[create.index("--notes") + 1].startswith("### Added")   # notes still found
    rc, calls = _release(("--release", "--tag", "0.8.0"))                    # an explicit --tag is used as typed
    assert _created(calls)[0][2] == "0.8.0"
    (tmp_path / ".kopi.toml").write_text("[release]\ntag_prefix = 1\n")
    assert _release(("--release",))[0] == 1 and "release.tag_prefix must be a str" in capsys.readouterr().err
