"""An owning addon derives decision access through retained concern links."""

import pytest
from django.core.exceptions import PermissionDenied
from rebac import (
    RelationshipTuple,
    actor_context,
    delete_relationship,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.base.scoping import system_queryset
from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision, DecisionRecord
from angee.graphql.schema import GraphQLSchemas
from angee.workflows import schema as workflow_schema
from tests.conftest import SchemaAddon, create_platform_admin, create_user, execute_schema, result_data
from tests.extcontrib.models import Record


@pytest.fixture
def concerned_question(composed_permissions):
    admin = create_platform_admin("concern-admin")
    writer, reader, outsider = (create_user(name) for name in ("concern-writer", "concern-reader", "concern-outsider"))
    with actor_context(admin):
        record = Record.objects.create(name="Concern")
        unrelated = Record.objects.create(name="Unrelated")
    write_relationships([
        RelationshipTuple(to_object_ref(record), "writer", to_subject_ref(writer)),
        RelationshipTuple(to_object_ref(record), "reader", to_subject_ref(reader)),
        RelationshipTuple(to_object_ref(unrelated), "writer", to_subject_ref(outsider)),
    ])
    question = Decision.objects.ask(DecisionRequest(
        kind="confirm_record", records=(record,),
        proposal=DecisionProposal(alternatives=[{"key": "confirm", "label": "Confirm", "outcome": "confirmed"}]),
    ), actor=admin)
    return admin, writer, reader, outsider, record, question


@pytest.fixture
def schema():
    return GraphQLSchemas([
        SchemaAddon(decision_schema.schemas), SchemaAddon(workflow_schema.schemas),
    ]).build("console")


@pytest.mark.parametrize("viewer,can_read,can_act", [(1, True, True), (2, True, False), (3, False, False)])
def test_read_and_can_act_follow_the_typed_concern(schema, concerned_question, viewer, can_read, can_act):
    *_, record, question = concerned_question
    actor = concerned_question[viewer]
    assert question.with_actor(actor).has_access("read") is can_read
    assert question.with_actor(actor).has_access("act") is can_act
    data = result_data(execute_schema(schema, """{
      decisions { id permissions records { record_model record_id } }
      answerable: decisions(where: {can_act: {_eq: true}, is_open: {_eq: true}}) { id }
    }""", user=actor))
    assert data == {
        "decisions": [{
            "id": question.sqid, "permissions": ["act"] if can_act else [],
            "records": [{"record_model": record._meta.label, "record_id": record.sqid}],
        }] if can_read else [],
        "answerable": [{"id": question.sqid}] if can_act else [],
    }
    assert list(Decision.objects.with_actor(actor).open_for(record)) == ([question] if can_read else [])
    if not can_act:
        with pytest.raises(PermissionDenied):
            question.decide(actor=actor, chosen=["confirm"])
    else:
        result_data(execute_schema(schema, """mutation($id: ID!, $revision: Int!) {
          decide(id: $id, revision: $revision, chosen: ["confirm"]) { __typename }
        }""", {"id": question.sqid, "revision": question.revision}, user=actor))
        question.refresh_from_db()
        assert question.verdict == ["confirm"] and question.answered_by_id == actor.pk


@pytest.mark.parametrize("viewer", [1, 2])
def test_record_timeline_includes_the_unassigned_question(schema, concerned_question, viewer):
    *_, record, question = concerned_question
    data = result_data(execute_schema(schema, """query($records: [TimelineRecordInput!]!) {
      record_timeline(records: $records, include_runs: false) { open_decision_count records {
        record_id decisions { id permissions } }
      }
    }""", {"records": [{"model": record._meta.label, "id": record.sqid}]}, user=concerned_question[viewer]))
    assert data["record_timeline"] == {
        "open_decision_count": 1,
        "records": [{"record_id": record.sqid,
                     "decisions": [{"id": question.sqid, "permissions": ["act"] if viewer == 1 else []}]}],
    }


def test_concern_permissions_are_live_after_admission(schema, concerned_question):
    _admin, writer, _reader, _outsider, record, question = concerned_question
    delete_relationship(RelationshipTuple(to_object_ref(record), "writer", to_subject_ref(writer)))
    write_relationships([RelationshipTuple(to_object_ref(record), "reader", to_subject_ref(writer))])
    data = result_data(execute_schema(schema, """{
      decisions { id permissions } answerable: decisions(where: {can_act: {_eq: true}}) { id }
    }""", user=writer))
    assert data == {"decisions": [{"id": question.sqid, "permissions": []}], "answerable": []}
    with pytest.raises(PermissionDenied):
        question.decide(actor=writer, chosen=["confirm"])
    delete_relationship(RelationshipTuple(to_object_ref(record), "reader", to_subject_ref(writer)))
    assert result_data(execute_schema(schema, "{ decisions { id } }", user=writer)) == {"decisions": []}
    assert not Decision.objects.with_actor(writer).open_for(record).exists()


@pytest.mark.parametrize("queryset_delete", [False, True])
def test_source_deletion_keeps_the_decision_and_its_retained_record(concerned_question, queryset_delete):
    admin, writer, _reader, _outsider, record, question = concerned_question
    link = DecisionRecord.objects.with_actor(admin).get(extcontrib_record=record)
    assert record.decision_records.with_actor(admin).get() == link
    record_pk = record.pk
    with actor_context(admin):
        if queryset_delete:
            Record.objects.filter(pk=record_pk).delete()
        else:
            record.with_actor(admin).delete()
    assert not system_queryset(Record).filter(pk=record_pk).exists()
    retained = system_queryset(DecisionRecord).get(pk=link.pk)
    assert (retained.decision_id, retained.content_type_id, retained.object_id) == (
        question.pk, link.content_type_id, record_pk,
    )
    assert retained.record is None
    assert system_queryset(Decision).get(pk=question.pk).is_open
    assert not Decision.objects.with_actor(writer).filter(pk=question.pk).exists()
