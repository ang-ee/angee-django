"""A record reader cannot read answers from a separately protected decision."""

from rebac import RelationshipTuple, to_object_ref, to_subject_ref, write_relationships

from angee.decisions import schema as decision_schema
from angee.decisions.testing.models import Decision
from tests.conftest import addon_schema, execute_schema, result_data
from tests.test_decisions_inbox import inbox  # noqa: F401  (fixture)


def test_record_reader_cannot_see_unreadable_answered_decision(inbox):  # noqa: F811
    requester, reviewer, outsider, subject, answered = inbox
    answered.decide(actor=reviewer, chosen=["accept"])
    # outsider reads the record, is neither assignee nor requester nor admin.
    write_relationships([RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(outsider))])
    assert not Decision.objects.with_actor(outsider).filter(pk=answered.pk).exists()
    schema = addon_schema(decision_schema.schemas, "console")
    direct = result_data(execute_schema(schema, "{ decisions { id } }", user=outsider))
    data = result_data(execute_schema(
        schema,
        """query($model: String!, $id: ID!) {
          record_decision_answers(model: $model, id: $id) { id verdict verdict_label context proposal }
        }""",
        {"model": subject._meta.label, "id": subject.sqid},
        user=outsider,
    ))
    assert direct == {"decisions": []}
    assert data["record_decision_answers"] == [], data
