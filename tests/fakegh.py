#!/usr/bin/env python3
"""A stand-in `gh` for tests/test_smoke.py: a tiny fake GitHub behind the real command line.

Put this on PATH as `gh` (the tests copy it into a temp bin directory) and set FAKE_GH_STATE to a JSON file. It
answers the `gh` subcommands and GraphQL queries kopi sends, keeps projects, items, issues and pull requests in
that file between calls, and logs every call's argv under "calls" so a test can see exactly what was sent.
Only what the smoke tests use is implemented; anything else exits 1 with "fakegh: unsupported ...".
"""
import json
import os
import re
import sys

STATE_FILE = os.environ["FAKE_GH_STATE"]
OPTIONS = [{"id": "o1", "name": "Todo"}, {"id": "o2", "name": "In Progress"}, {"id": "o3", "name": "Done"}]
STATUS_BY_OPTION = {o["id"]: o["name"] for o in OPTIONS}
STRING = r'("(?:[^"\\]|\\.)*")'


def load():
    with open(STATE_FILE) as f:
        return json.load(f)


def save(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def out(data):
    print(data if isinstance(data, str) else json.dumps(data))
    sys.exit(0)


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def flag(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def project_of(state, ref):
    """The project for a number (as `gh project ... N`) or a node id."""
    return next((p for p in state["projects"] if str(p["number"]) == str(ref) or p["id"] == ref), None)


def gh_item(item):
    """An item the way `gh project item-list --format json` shows it."""
    content = {"type": item["type"], "title": item["title"], "body": item.get("body", "")}
    if item.get("url"):
        content.update(url=item["url"], number=item["number"], repository=item["repository"])
    shaped = {"id": item["id"], "title": item["title"], "content": content}
    if item.get("status"):
        shaped["status"] = item["status"]
    shaped.update({name.lower(): date for name, date in item.get("dates", {}).items()})
    return shaped


def lean_node(item):
    """An item the way kopi's slim GraphQL query asks for it."""
    values = [{"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": item["status"], "field": {"name": "Status"}}] if item.get("status") else []
    values += [{"__typename": "ProjectV2ItemFieldDateValue", "date": date, "field": {"name": name}} for name, date in item.get("dates", {}).items()]
    content = {"__typename": item["type"], "title": item["title"], "body": item.get("body", "")}
    if item.get("url"):
        content.update(url=item["url"], number=item["number"], repository={"nameWithOwner": item["repository"]},
                       labels={"nodes": []}, assignees={"nodes": []}, milestone=None)
    return {"id": item["id"], "isArchived": item.get("archived", False),
            "fieldValues": {"pageInfo": {"hasNextPage": False}, "nodes": values}, "content": content}


def new_id(state, prefix):
    state["counter"] += 1
    return f"{prefix}{state['counter']}"


def graphql(state, argv):
    query = flag(argv, "-f", "")[len("query="):]
    variables = {argv[i + 1].split("=", 1)[0]: argv[i + 1].split("=", 1)[1] for i, a in enumerate(argv) if a == "-F"}
    if "rateLimit" in query:
        out({"data": {"rateLimit": {"limit": 5000, "remaining": 4321, "used": 679, "resetAt": "2030-01-01T00:00:00Z"}}})
    if query.startswith("mutation"):
        data = {}
        for n, pid, title, body in re.findall(r'a(\d+): addProjectV2DraftIssue\(input: \{projectId: "([^"]*)", title: ' + STRING + ", body: " + STRING, query):
            item = {"id": new_id(state, "PVTI_"), "type": "DraftIssue", "title": json.loads(title), "body": json.loads(body), "status": None}
            project_of(state, pid)["items"].append(item)
            data[f"a{n}"] = {"projectItem": {"id": item["id"]}}
        for n, pid, content in re.findall(r'a(\d+): addProjectV2ItemById\(input: \{projectId: "([^"]*)", contentId: "([^"]*)"', query):
            issue = next(i for i in state["issues"] + state["prs"] if i["node"] == content)
            item = {"id": new_id(state, "PVTI_"), "type": issue["type"], "title": issue["title"], "status": None, "url": issue["url"],
                    "number": issue["number"], "repository": issue["repository"]}
            project_of(state, pid)["items"].append(item)
            data[f"a{n}"] = {"item": {"id": item["id"]}}
        for n, item_id, field, date in re.findall(r'a(\d+): updateProjectV2ItemFieldValue\(input: \{projectId: "[^"]*", itemId: "([^"]*)", '
                                                  r'fieldId: "([^"]*)", value: \{date: "([^"]*)"', query):
            item = next(i for p in state["projects"] for i in p["items"] if i["id"] == item_id)
            name = next(f["name"] for p in state["projects"] for f in p["fields"] if f["id"] == field)
            item.setdefault("dates", {})[name] = date
            data[f"a{n}"] = {"projectV2Item": {"id": item_id}}
        for n, item_id, option in re.findall(r'a(\d+): updateProjectV2ItemFieldValue\(input: \{projectId: "[^"]*", itemId: "([^"]*)", '
                                             r'fieldId: "[^"]*", value: \{singleSelectOptionId: "([^"]*)"', query):
            item = next(i for p in state["projects"] for i in p["items"] if i["id"] == item_id)
            item["status"] = STATUS_BY_OPTION[option]
            data[f"a{n}"] = {"projectV2Item": {"id": item_id}}
        save(state)
        out({"data": data})
    if "resource(url" in query:
        data = {}
        for n, url in re.findall(r'a(\d+): resource\(url: "([^"]*)"\)', query):
            found = next((i for i in state["issues"] + state["prs"] if i["url"] == url), None)
            data[f"a{n}"] = {"id": found["node"]} if found else None
        out({"data": data})
    if "totalCount" in query:
        project = project_of(state, variables["id"])
        out({"data": {"node": {"items": {"totalCount": len(project["items"])}}}})
    if "fieldValues(first" in query:
        project = project_of(state, variables["id"])
        out({"data": {"node": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                         "nodes": [lean_node(i) for i in project["items"]]}}}})
    if "fields(first: 100)" in query:
        out({"data": {"node": {"fields": {"nodes": project_of(state, variables["id"])["fields"]}}}})
    if "repositories(first" in query:
        out({"data": {f"p{n}": {"repositories": {"nodes": [{"nameWithOwner": "acme/widget"}]}} for n in range(len(state["projects"]))}})
    fail("fakegh: unsupported GraphQL: " + query[:80])


def main(argv):
    state = load()
    state["calls"].append(argv)
    save(state)
    head = argv[:2]
    if head[0] == "project" and head[1] in ("list", "create", "view", "field-list", "item-list") \
            and flag(argv, "--format") != "json":
        out("NUMBER\tTITLE\tSTATE")   # what the real gh prints without --format json: a table, which is not JSON
    if head == ["auth", "status"]:
        out("github.com\n  - Token scopes: 'project', 'repo'")
    if head == ["api", "graphql"]:
        return graphql(state, argv[2:])
    if head == ["project", "list"]:
        out({"projects": [{k: p[k] for k in ("number", "title", "url", "id")} | {"closed": False} for p in state["projects"]], "totalCount": len(state["projects"])})
    if head == ["project", "create"]:
        number = len(state["projects"]) + 1
        project = {"number": number, "title": flag(argv, "--title"), "id": f"PVT_{number}", "url": f"https://github.com/orgs/acme/projects/{number}", "items": [], "fields": []}
        state["projects"].append(project)
        save(state)
        out({k: project[k] for k in ("number", "title", "url", "id")})
    if head == ["project", "link"]:
        out("")
    if head == ["project", "view"]:
        project = project_of(state, argv[2])
        out({"id": project["id"], "number": project["number"], "title": project["title"], "url": project["url"], "items": {"totalCount": len(project["items"])}})
    if head == ["project", "field-create"]:
        project = project_of(state, argv[2])
        project["fields"].append({"id": new_id(state, "PVTF_"), "name": flag(argv, "--name"), "dataType": flag(argv, "--data-type")})
        save(state)
        out({"id": project["fields"][-1]["id"], "name": flag(argv, "--name")})
    if head == ["project", "field-list"]:
        out({"fields": [{"id": "F0", "name": "Title"}, {"id": "FS", "name": "Status", "options": OPTIONS}]})
    if head == ["project", "item-list"]:
        items = project_of(state, argv[2])["items"][:int(flag(argv, "--limit", 30))]
        out({"items": [gh_item(i) for i in items if not i.get("archived")], "totalCount": len(items)})
    if head == ["project", "item-create"]:
        item = {"id": new_id(state, "PVTI_"), "type": "DraftIssue", "title": flag(argv, "--title"), "body": flag(argv, "--body", ""), "status": None}
        project_of(state, argv[2])["items"].append(item)
        save(state)
        out({"id": item["id"], "title": item["title"], "type": "DraftIssue"})
    if head == ["project", "item-edit"]:
        item = next(i for p in state["projects"] for i in p["items"] if i["id"] == flag(argv, "--id"))
        item["status"] = STATUS_BY_OPTION[flag(argv, "--single-select-option-id")]
        save(state)
        out({"id": item["id"]})
    if argv[0] == "issue" and argv[1] == "comment":
        comment = {"id": new_id(state, ""), "issue": argv[2], "body": flag(argv, "--body")}
        state["comments"].append(comment)
        save(state)
        out(f"https://github.com/acme/widget/issues/{argv[2]}#issuecomment-{comment['id']}")
    if argv[0] == "api" and "/issues/" in " ".join(argv):
        path = next(a for a in argv if a.startswith("repos/"))
        if path.endswith("/comments"):          # the listing: ids of the comments that start with kopi's marker
            marker = re.search(r'startswith\("([^"]*)"\)', flag(argv, "--jq")).group(1)
            out("\n".join(str(c["id"]) for c in state["comments"] if c["body"].startswith(marker) and c["issue"] == path.split("/")[-2]))
        comment = next(c for c in state["comments"] if str(c["id"]) == path.rsplit("/", 1)[1])
        if "-X" in argv:                        # PATCH: edit in place
            comment["body"] = flag(argv, "-f")[len("body="):]
            save(state)
        out(comment["body"] if flag(argv, "--jq") == ".body" else f"https://github.com/acme/widget/issues/{comment['issue']}#issuecomment-{comment['id']}")
    if argv[0] in ("issue", "pr") and argv[1] == "list":
        rows = state["issues"] if argv[0] == "issue" else state["prs"]
        out([{k: r[k] for k in ("number", "title", "state", "url")} for r in rows])
    fail("fakegh: unsupported command: gh " + " ".join(argv))


if __name__ == "__main__":
    main(sys.argv[1:])
