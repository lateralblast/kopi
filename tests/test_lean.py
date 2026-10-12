"""The slim GraphQL board read (lean_items) and its fallback to `gh project item-list`."""
import types
from unittest import mock

import pytest

import kopi as cli


ARGS = types.SimpleNamespace(owner="o", status_map=None)


def _node(item_id, title, status="Todo", archived=False, values=(), content=None, more=False):
    fields = [{"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": status, "field": {"name": "Status"}}, *values]
    return {"id": item_id, "isArchived": archived, "fieldValues": {"pageInfo": {"hasNextPage": more}, "nodes": fields},
            "content": content or {"__typename": "DraftIssue", "title": title, "body": f"body of {title}"}}


def _page(nodes, cursor=None):
    return {"data": {"node": {"items": {"pageInfo": {"hasNextPage": bool(cursor), "endCursor": cursor}, "nodes": nodes}}}}


@pytest.fixture
def lean():
    with mock.patch("kopi.LEAN_READS", True), mock.patch("kopi.run_json", return_value={"id": "PVT_1"}) as rj, \
            mock.patch("kopi.graphql") as gql:
        gql.rj = rj
        yield gql


def test_lean_items_match_the_gh_item_shape(lean):
    date = {"__typename": "ProjectV2ItemFieldDateValue", "date": "2026-10-01", "field": {"name": "Started"}}
    text = {"__typename": "ProjectV2ItemFieldTextValue", "text": "Code gaps", "field": {"name": "Section"}}
    issue = {"__typename": "Issue", "title": "Bug", "body": "details", "url": "https://github.com/o/r/issues/4", "number": 4,
             "repository": {"nameWithOwner": "o/r"}, "labels": {"nodes": [{"name": "bug"}]}}
    lean.return_value = _page([_node("I1", "Draft", values=[date, text]), _node("I2", "Bug", "Done", content=issue)])
    first, second = cli.board_items(ARGS, 7)
    assert first == {"id": "I1", "title": "Draft", "status": "Todo", "started": "2026-10-01", "section": "Code gaps",
                     "content": {"type": "DraftIssue", "title": "Draft", "body": "body of Draft"}}
    assert second["content"] == {"type": "Issue", "title": "Bug", "body": "details", "number": 4, "repository": "o/r",
                                 "url": "https://github.com/o/r/issues/4"}
    assert second["labels"] == ["bug"] and second["status"] == "Done" and second["repository"] == "https://github.com/o/r"


def test_lean_items_leave_labels_out_when_there_are_none(lean):
    pr = {"__typename": "PullRequest", "title": "P", "body": "", "url": "https://github.com/o/r/pull/2", "number": 2,
          "repository": {"nameWithOwner": "o/r"}, "labels": {"nodes": []}}
    lean.return_value = _page([_node("P1", "P", content=pr)])
    assert "labels" not in cli.board_items(ARGS, 7)[0]


def test_lean_items_page_through_the_board_and_skip_archived(lean):
    lean.side_effect = [_page([_node("A", "a"), _node("B", "b", archived=True)], cursor="c1"), _page([_node("C", "c")])]
    assert [i["id"] for i in cli.board_items(ARGS, 7)] == ["A", "C"]
    assert "cursor" not in lean.call_args_list[0].kwargs and lean.call_args_list[1].kwargs["cursor"] == "c1"


def test_board_items_still_normalise_custom_status_names(lean):
    lean.return_value = _page([_node("A", "a", "Backlog")])
    assert cli.board_items(ARGS, 7)[0]["status"] == "Todo"


@pytest.mark.parametrize("failure", [cli.GhError("API rate limit exceeded"), KeyError("data")])
def test_a_failed_slim_read_falls_back_to_gh(lean, capsys, failure):
    lean.side_effect = failure
    fallback = {"items": [{"id": "G1", "title": "t", "status": "Done", "content": {}}]}
    with mock.patch("kopi.run_json", side_effect=[{"id": "PVT_1"}, fallback]) as rj:
        items = cli.board_items(ARGS, 7)
    assert [i["id"] for i in items] == ["G1"]
    assert "slim board read failed" in capsys.readouterr().err
    assert rj.call_args_list[-1].args[:3] == ("project", "item-list", "7")


def test_an_item_with_too_many_field_values_falls_back_to_gh(lean, capsys):
    lean.return_value = _page([_node("A", "a", more=True)])
    with mock.patch("kopi.run_json", side_effect=[{"id": "PVT_1"}, {"items": []}]):
        assert cli.board_items(ARGS, 7) == []
    assert "more than 12 field values" in capsys.readouterr().err


def test_lean_items_carry_assignees(lean):
    issue = {"__typename": "Issue", "title": "Bug", "body": "", "url": "https://github.com/o/r/issues/4", "number": 4,
             "repository": {"nameWithOwner": "o/r"}, "labels": {"nodes": []}, "assignees": {"nodes": [{"login": "alice"}]}}
    lean.return_value = _page([_node("I2", "Bug", content=issue)])
    assert cli.board_items(ARGS, 7)[0]["assignees"] == ["alice"]


def test_lean_items_carry_the_milestone(lean):
    issue = {"__typename": "Issue", "title": "Bug", "body": "", "url": "https://github.com/o/r/issues/4", "number": 4,
             "repository": {"nameWithOwner": "o/r"}, "labels": {"nodes": []}, "milestone": {"title": "v2.0", "dueOn": "2026-12-01T00:00:00Z"}}
    plain = dict(issue, number=5, url="https://github.com/o/r/issues/5", milestone=None)
    lean.return_value = _page([_node("I2", "Bug", content=issue), _node("I3", "Plain", content=plain)])
    first, second = cli.board_items(ARGS, 7)
    assert first["milestone"] == {"title": "v2.0", "dueOn": "2026-12-01T00:00:00Z"} and "milestone" not in second


def test_lean_drafts_carry_their_own_id(lean):
    draft = {"__typename": "DraftIssue", "id": "DI_9", "title": "A draft", "body": "b"}
    lean.return_value = _page([_node("I1", "A draft", content=draft)])
    assert cli.board_items(ARGS, 7)[0]["content"]["id"] == "DI_9"
