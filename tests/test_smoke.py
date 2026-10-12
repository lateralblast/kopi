"""End-to-end smoke tests: the real `kopi.py` as a subprocess, talking to a fake `gh` (tests/fakegh.py) on PATH.

Every other test mocks `kopi.run` / `kopi.graphql`, so none of them exercises the real subprocess layer: argv
quoting, GraphQL literals, JSON parsing of what `gh` prints, exit codes. These do, with a tiny fake GitHub that keeps
its projects and items in a JSON file between calls."""
import json
import os
import shutil
import stat
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
KOPI = os.path.join(os.path.dirname(HERE), "kopi.py")

CHANGELOG = """# Changelog

## [0.2.0] - 2026-02-01
- Added the second thing

## [0.1.0] - 2026-01-01
- First release
"""
TODO = """# TODO

## Open

- [ ] Write the docs
- [~] Fix the parser

## Done

- [x] Set up the repo
"""


class World:
    """A temp git repo (origin acme/widget) with a fake `gh` first on PATH and a fake GitHub behind it."""

    def __init__(self, tmp_path):
        self.root, self.repo = tmp_path, tmp_path / "widget"
        self.bin, self.state_file = tmp_path / "bin", tmp_path / "github.json"
        self.bin.mkdir()
        self.repo.mkdir()
        gh = self.bin / "gh"
        source = open(os.path.join(HERE, "fakegh.py")).read().split("\n", 1)[1]   # drop the `env python3` line: that can be a slow shim
        gh.write_text(f"#!{sys.executable}\n{source}")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.state_file.write_text(json.dumps({"projects": [], "issues": [], "prs": [], "comments": [], "calls": [], "counter": 0}))
        for argv in (["init", "-q"], ["remote", "add", "origin", "https://github.com/acme/widget.git"]):
            subprocess.run(["git", *argv], cwd=self.repo, check=True)
        (self.repo / "CHANGELOG.md").write_text(CHANGELOG)
        (self.repo / "TODO.md").write_text(TODO)
        self.env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_STATE": str(self.state_file),
                    "KOPI_STATE": str(tmp_path / "archived.json"), "HOME": str(tmp_path), "GH_TOKEN": "fake"}

    def kopi(self, *args, env=None):
        return subprocess.run([sys.executable, KOPI, *args], cwd=self.repo, env=env or self.env, capture_output=True, text=True)

    @property
    def state(self):
        return json.loads(self.state_file.read_text())

    def items(self, number=1):
        return [(i["title"], i["status"]) for i in self.state["projects"][number - 1]["items"]]

    def calls(self, *prefix):
        return [c for c in self.state["calls"] if c[:len(prefix)] == list(prefix)]


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_import_creates_the_project_and_every_item(world):
    done = world.kopi("--no-dates", "--import")
    assert done.returncode == 0, done.stderr
    assert "Imported 5 items into project #1" in done.stdout
    assert dict(world.items()) == {"[0.1.0] First release": "Done", "[0.2.0] Added the second thing": "Done",
                                   "Write the docs": "Todo", "Fix the parser": "In Progress", "Set up the repo": "Done"}
    assert world.state["projects"][0]["title"] == "widget" and world.calls("project", "link")


def test_the_default_import_creates_the_date_fields_and_fills_them(world):
    done = world.kopi("--import")                                   # dates on: the default
    assert done.returncode == 0, done.stderr
    assert [f["name"] for f in world.state["projects"][0]["fields"]] == ["Started", "Completed"]
    assert world.calls("project", "field-create")[0][-4:] == ["--name", "Started", "--data-type", "DATE"]
    dates = {i["title"]: i.get("dates") for i in world.state["projects"][0]["items"]}
    assert dates["[0.2.0] Added the second thing"]["Completed"] == "2026-02-01"
    assert dates["[0.2.0] Added the second thing"]["Started"] == "2026-01-01"          # the previous release
    again = world.kopi("--update")
    assert again.returncode == 0 and "up to date" in again.stdout                      # nothing left to add or fill
    assert world.kopi("--diff", "--check").returncode == 0


def test_the_board_then_matches_the_files(world):
    world.kopi("--no-dates", "--import")
    check = world.kopi("--diff", "--check")
    assert check.returncode == 0 and "No differences in project #1" in check.stdout
    update = world.kopi("--no-dates", "--update", "--dry-run")
    assert update.returncode == 0 and "Dry run: 0 to add, 0 to move in project #1" in update.stdout


def test_a_new_changelog_entry_fails_the_check_until_it_is_synced(world):
    world.kopi("--no-dates", "--import")
    (world.repo / "CHANGELOG.md").write_text(CHANGELOG.replace("## [0.2.0]", "## [0.3.0] - 2026-03-01\n- A third thing\n\n## [0.2.0]"))
    drift = world.kopi("--diff", "--check")
    assert drift.returncode == 2 and "files-only" in drift.stdout and "[0.3.0] A third thing" in drift.stdout
    assert world.kopi("--no-dates", "--update").returncode == 0
    assert ("[0.3.0] A third thing", "Done") in world.items()
    assert world.kopi("--diff", "--check").returncode == 0


def test_report_json_comes_back_as_one_document(world):
    world.kopi("--no-dates", "--import")
    report = world.kopi("--json", "--report")
    assert report.returncode == 0, report.stderr
    data = json.loads(report.stdout)
    assert data["project"]["title"] == "widget" and data["counts"]["todo"] == 1 and data["counts"]["in_progress"] == 1
    assert data["history"] == {"entries": 2, "releases": 2}


def test_issues_and_prs_are_added_as_linked_items(world):
    state = world.state
    state["issues"] = [{"node": "NI1", "type": "Issue", "number": 7, "title": "Login fails", "state": "OPEN", "repository": "acme/widget",
                        "url": "https://github.com/acme/widget/issues/7"}]
    state["prs"] = [{"node": "NP1", "type": "PullRequest", "number": 8, "title": "Fix login", "state": "MERGED", "repository": "acme/widget",
                     "url": "https://github.com/acme/widget/pull/8"}]
    world.state_file.write_text(json.dumps(state))
    assert world.kopi("--no-dates", "--import").returncode == 0
    linked = {i["url"]: i["status"] for i in world.state["projects"][0]["items"] if i.get("url")}
    assert linked == {"https://github.com/acme/widget/issues/7": "Todo", "https://github.com/acme/widget/pull/8": "Done"}
    assert world.kopi("--diff", "--check").returncode == 0


def test_checkquota_reads_the_rate_limit(world):
    done = world.kopi("--checkquota")
    assert done.returncode == 0 and "GraphQL quota: 4321 of 5000 points left (679 used)" in done.stdout
    assert json.loads(world.kopi("--json", "--checkquota").stdout)["remaining"] == 4321


AWKWARD = 'Say "hi" \\ back\\slash — ünï ☃ `code` $HOME'


def test_awkward_titles_survive_the_subprocess_and_graphql_layers(world):
    (world.repo / "TODO.md").write_text(f"# TODO\n\n- [ ] {AWKWARD}\n")
    assert world.kopi("--no-dates", "--import").returncode == 0
    assert (AWKWARD, "Todo") in world.items()                       # through a GraphQL string literal in argv
    added = world.kopi("add", "1", "--title", AWKWARD + " two")
    assert added.returncode == 0, added.stderr
    assert (AWKWARD + " two", None) in world.items()                # through `gh project item-create --title`
    assert ["project", "item-create", "1", "--owner", "acme", "--title", AWKWARD + " two"] in world.calls("project", "item-create")


def test_a_gh_failure_is_exit_1_with_the_message(world):
    done = world.kopi("--update")                                   # no project yet
    assert done.returncode == 1 and "no project for this repo under acme" in done.stderr and "Traceback" not in done.stderr


def test_a_missing_gh_is_exit_1_with_a_message(world, tmp_path):
    git = shutil.which("git")
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    os.symlink(git, only_git / "git")
    done = world.kopi("--checkquota", env={**world.env, "PATH": str(only_git)})
    assert done.returncode == 1 and "gh CLI not found" in done.stderr


def test_running_update_again_adds_nothing_so_a_push_workflow_is_safe_to_repeat(world):
    world.kopi("--no-dates", "--import")
    (world.repo / "CHANGELOG.md").write_text(CHANGELOG.replace("## [0.2.0]", "## [0.3.0] - 2026-03-01\n- A third thing\n\n## [0.2.0]"))
    assert world.kopi("--no-dates", "--update").returncode == 0
    count = len(world.items())
    again = world.kopi("--no-dates", "--update")
    assert again.returncode == 0 and "up to date" in again.stdout and len(world.items()) == count


def test_report_is_written_to_a_file_and_posted_then_edited_in_place(world):
    world.kopi("--no-dates", "--import")
    written = world.kopi("--report", "--to", "STATUS.md")
    assert written.returncode == 0 and "report written to STATUS.md" in written.stdout
    text = (world.repo / "STATUS.md").read_text()
    assert text.startswith("# widget (project #1)") and "## Progress" in text                      # a .md file is Markdown
    first = world.kopi("--report", "--post", "5")
    assert first.returncode == 0 and "Posted the report as a new comment on #5" in first.stdout
    [comment] = world.state["comments"]
    assert comment["issue"] == "5" and comment["body"].startswith("<!-- kopi-report -->\n# widget (project #1)")
    assert world.kopi("--report", "--post", "5").stdout.startswith("The report comment is already up to date on #5")
    (world.repo / "TODO.md").write_text(TODO + "- [ ] A new thing to do\n")
    world.kopi("--no-dates", "--update")
    second = world.kopi("--report", "--post", "5")
    assert "Updated the report comment on #5" in second.stdout
    assert len(world.state["comments"]) == 1 and "A new thing to do" in world.state["comments"][0]["body"]   # edited, not piled up
    assert world.calls("issue", "comment") and len(world.calls("issue", "comment")) == 1


def test_report_post_dry_run_writes_nothing(world):
    world.kopi("--no-dates", "--import")
    done = world.kopi("--report", "--post", "5", "--to", "STATUS.md", "--dry-run")
    assert done.returncode == 0 and "Dry run: would post the report comment on #5" in done.stdout and "report would be written to STATUS.md" in done.stdout
    assert world.state["comments"] == [] and not (world.repo / "STATUS.md").exists()
