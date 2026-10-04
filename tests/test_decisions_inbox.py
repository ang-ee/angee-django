"""Public inbox reads, filters, and the one verdict mutation."""

import pytest
from rebac import RelationshipTuple, to_object_ref, to_subject_ref, write_relationships

from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from tests.conftest import addon_schema, create_user, execute_schema, result_data, vault_for


@pytest.fixture
def inbox(composed_tables):
    requester, reviewer, outsider = (
        create_user(name) for name in ("inbox-requester", "inbox-reviewer", "inbox-outsider")
    )
    subject = vault_for(requester)
    write_relationships([RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(reviewer))])
    decision = Decision.objects.ask(
        DecisionRequest(
            kind="review_note",
            records=(subject,),
            assignees=(reviewer,),
            proposal=DecisionProposal(
                alternatives=[
                    {"key": "accept", "label": "Accept", "outcome": "accepted"},
                    {"key": "reject", "label": "Keep current record", "outcome": "rejected"},
                ]
            ),
        ),
        actor=requester,
    )
    return requester, reviewer, outsider, subject, decision


@pytest.mark.parametrize("viewer", [0, 1, 2])
def test_inbox_scope_and_permission_field(inbox, viewer):
    requester, reviewer, outsider, subject, decision = inbox
    schema = addon_schema(decision_schema.schemas, "console")
    data = result_data(
        execute_schema(
            schema, "{ decisions { id is_open verdict permissions records { record_id } } }", user=inbox[viewer]
        )
    )
    if viewer == 2:
        assert data == {"decisions": []}
    else:
        row = data["decisions"][0]
        assert row["id"] == decision.sqid and row["is_open"] and row["verdict"] is None
        assert ("act" in row["permissions"]) is (viewer == 1)
        assert row["records"] == [{"record_id": subject.sqid}]


def test_public_mutation_records_only_chosen_keys_and_audit(inbox):
    requester, reviewer, _, _, decision = inbox
    schema = addon_schema(decision_schema.schemas, "console")
    mutation = """mutation($id: ID!, $revision: Int!) {
      decide(id: $id, revision: $revision, chosen: ["accept"]) { __typename }
    }"""
    result = execute_schema(schema, mutation, {"id": decision.sqid, "revision": decision.revision}, user=reviewer)
    assert not result.errors
    decision.refresh_from_db()
    assert decision.verdict == ["accept"] and decision.answered_by_id == reviewer.pk
    result = result_data(
        execute_schema(schema, "{ decisions { is_open verdict answered_at answered_by { id } } }", user=reviewer)
    )
    row = result["decisions"][0]
    assert row["is_open"] is False and row["verdict"] == ["accept"] and row["answered_at"]


def test_native_can_act_filter_and_open_resource(inbox):
    _, reviewer, _, subject, decision = inbox
    schema = addon_schema(decision_schema.schemas, "console")
    result = result_data(
        execute_schema(
            schema,
            """query($model: String!, $id: ID!) {
      decisions(where: {can_act: {_eq: true}, is_open: {_eq: true}}) { id }
      open_decisions(record_model: $model, record_id: $id) { id }
    }""",
            {"model": subject._meta.label, "id": subject.sqid},
            user=reviewer,
        )
    )
    assert result == {"decisions": [{"id": decision.sqid}], "open_decisions": [{"id": decision.sqid}]}
