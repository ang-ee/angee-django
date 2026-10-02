"""The complete inbox selection stays bounded as visible decisions grow."""

import json
from collections import Counter
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, to_object_ref, to_subject_ref, write_relationships
from rebac.backends import backend

from angee.base.scoping import system_queryset
from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionRequest
from angee.decisions.testing.drivers import Reject
from angee.decisions.testing.models import Decision
from tests.conftest import addon_schema, create_user, execute_schema, result_data, vault_for
from tests.test_decisions_inbox import inbox as inbox

FIELDS = """
  id kind_label subject_id subject_model
  requester { id display_name } expires_at verdict is_open permissions
"""
LIST = """query Inbox($viewer: String!) {
  decisions(where: {assignees: {_eq: $viewer}, is_open: {_eq: true}},
            order_by: [{created_at: desc}], limit: 50) { FIELDS }
}""".replace("FIELDS", FIELDS)
GROUPS = """query InboxGroups($viewer: String!) {
  decisions_groups(where: {assignees: {_eq: $viewer}, is_open: {_eq: true}},
                   group_by: []) { aggregate { count } }
}"""
RECORD = """query InboxRecord($id: String!) {
  decisions_by_pk(id: $id) {
    FIELDS revision form_schema basis context closed_reason resolution resolved_at
    resolved_by { display_name } group { id }
  }
}""".replace("FIELDS", FIELDS)


def test_inbox_list_group_count_and_record_queries_do_not_scale_per_row(composed_tables, tmp_path):
    """Measure real non-admin list, grouped counts, and detail at 1, 10, 50 seats."""
    issuer = create_user("inbox-cost-issuer")
    viewer = create_user("inbox-cost-assignee")
    requesters = [create_user(f"inbox-cost-requester-{index}") for index in range(3)]
    subjects = [vault_for(issuer, name=f"Reference {index}") for index in range(3)]
    write_relationships([
        RelationshipTuple(resource=to_object_ref(subject), relation="viewer", subject=to_subject_ref(person))
        for subject in subjects for person in (viewer, *requesters)
    ])
    write_relationships([
        RelationshipTuple(resource=to_object_ref(person), relation="directory_reader", subject=to_subject_ref(viewer))
        for person in requesters
    ])
    schema = addon_schema(decision_schema.schemas, "console")
    counts = {name: [] for name in ("list", "groups", "record")}
    metrics = []
    previous = 0
    for size in (1, 10, 50):
        for bucket in range(3):
            requests = [DecisionRequest(
                kind=f"review_{index % 3}", subject=subjects[index % 3], requester=requesters[index % 3],
                assignees=(viewer,), actions=(Reject,),
            ) for index in range(previous, size) if index % 3 == bucket]
            if requests:
                Decision.objects.admit_group(requests, actor=issuer, policy="all")
        decision = system_queryset(Decision).latest("pk")
        for name, document, variables in (
            ("list", LIST, {"viewer": str(viewer.sqid)}),
            ("groups", GROUPS, {"viewer": str(viewer.sqid)}),
            ("record", RECORD, {"id": str(decision.sqid)}),
        ):
            with CaptureQueriesContext(connection) as captured, patch.object(
                backend(), "check_access", wraps=backend().check_access,
            ) as checks:
                result = result_data(execute_schema(schema, document, variables, user=viewer))
            if name == "list":
                rows = result["decisions"]
                assert len(rows) == size
                assert all("act" in row["permissions"] and row["is_open"] for row in rows)
                assert all(row["requester"] is not None for row in rows)
                assert {row["subject_id"] for row in rows} == {
                    str(subject.sqid) for subject in subjects[:min(size, 3)]
                }
            elif name == "groups":
                assert sum(row["aggregate"]["count"] for row in result["decisions_groups"]) == size
            else:
                assert result["decisions_by_pk"]["id"] == str(decision.sqid)
                assert "act" in result["decisions_by_pk"]["permissions"]
            counts[name].append(len(captured))
            sql = [item["sql"] for item in captured]
            (tmp_path / f"{name}-{size}.json").write_text(json.dumps(sql, indent=2))
            tables = Counter(query.split('FROM "', 1)[1].split('"', 1)[0] for query in sql if 'FROM "' in query)
            metrics.append({"size": size, "query": name, "count": len(captured),
                            "permission_checks": checks.call_count, "tables": dict(tables)})
            assert checks.call_count == 0
        previous = size
    print(json.dumps({"metrics": metrics, "sql_directory": str(tmp_path)}, indent=2))
    (tmp_path / "metrics.json").write_text(json.dumps(metrics, indent=2))
    assert all(len(set(values)) == 1 for values in counts.values()), counts
    for name, budget in (("list", 300), ("groups", 285), ("record", 310)):
        assert max(counts[name]) <= budget, counts


def test_inbox_user_relations_redact_unreadable_people(inbox):
    """Directory restrictions redact nullable people without losing visible decisions."""
    issuer, requester, reviewer, _outsider, _subject, _group, decision = inbox
    Decision.objects.decide(
        decision.pk, actor=reviewer, revision=decision.revision, action="accept", values={},
    )
    schema = addon_schema(decision_schema.schemas, "console")
    document = """query($id: String!) {
      decisions { id requester { display_name } resolved_by { display_name } }
      decisions_by_pk(id: $id) { id requester { display_name } resolved_by { display_name } }
    }"""
    expected = {"id": str(decision.sqid), "requester": None, "resolved_by": None}
    for person in (requester, reviewer):
        assert not person.with_actor(issuer).has_access("read")
    assert result_data(execute_schema(schema, document, {"id": str(decision.sqid)}, user=issuer)) == {
        "decisions": [expected], "decisions_by_pk": expected,
    }
    write_relationships([
        RelationshipTuple(resource=to_object_ref(person), relation="directory_reader", subject=to_subject_ref(issuer))
        for person in (requester, reviewer)
    ])
    expected = {"id": str(decision.sqid), "requester": {"display_name": requester.username},
                "resolved_by": {"display_name": reviewer.username}}
    assert result_data(execute_schema(schema, document, {"id": str(decision.sqid)}, user=issuer)) == {
        "decisions": [expected], "decisions_by_pk": expected,
    }
