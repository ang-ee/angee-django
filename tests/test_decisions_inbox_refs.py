"""Canonical concern links resolve through their own readable resource."""

import pytest
import strawberry_django
from pydantic import ValidationError
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionRequest
from angee.decisions.testing.drivers import seed_decision
from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode
from tests.conftest import addon_schema, create_user, execute_schema, result_data
from tests.mtidemo.models import MtiChild, MtiParent


@pytest.mark.parametrize(
    "identity,field,valid",
    [
        ("parent", "title", True),
        ("parent", "detail", False),
        ("child", "title", True),
    ],
)
def test_proposals_use_the_concern_relations_canonical_identity_and_model(composed_tables, identity, field, valid):
    reviewer = create_user("canonical-proposal-reviewer")
    with system_context(reason="test canonical proposal"):
        child = MtiChild.objects.create(title="Shared record", detail="Concrete child")
        parent = MtiParent.objects.get(pk=child.pk)
    values = dict(
        kind="confirm_record",
        records=(child,),
        assignees=(reviewer,),
        proposal={
            "alternatives": [
                {
                    "key": "confirm",
                    "label": "Confirm",
                    "outcome": "done",
                    "actions": {parent.sqid: {"fields": {field: {"set": "Proposed"}}}},
                }
            ]
        },
    )
    if valid:
        DecisionRequest(**values)
    else:
        with pytest.raises(ValidationError):
            DecisionRequest(**values)


def test_mti_inbox_reference_resolves_the_readable_canonical_parent_label(composed_tables):
    """A non-admin follows the returned base identity without exposing other rows."""
    issuer = create_user("reference-issuer")
    reviewer = create_user("reference-reviewer")
    with system_context(reason="test canonical inbox reference"):
        child = MtiChild.objects.create(title="Shared subject", detail="Concrete child")
        parent = MtiParent.objects.get(pk=child.pk)
        inaccessible = MtiChild.objects.create(title="Private subject", detail="Private child")
    write_relationships(
        [
            RelationshipTuple(to_object_ref(record), "reader", to_subject_ref(person))
            for record in (child, parent)
            for person in (issuer, reviewer)
        ]
    )
    decision = seed_decision(actor=issuer, assignees=(reviewer,), reference=child)

    @strawberry_django.type(MtiParent)
    class InboxSubjectType(AngeeNode):
        title: str

    subjects = hasura_model_resource(
        InboxSubjectType,
        model=MtiParent,
        name="inbox_subjects",
        filterable=("id",),
        sortable=("id",),
        aggregatable=(),
        record_representation="title",
        insert=False,
        update=False,
        delete=False,
    )
    console = decision_schema.schemas["console"]
    schema = addon_schema(
        {
            "console": {
                **console,
                "query": [*console["query"], subjects.query],
                "types": [*console["types"], *subjects.types],
            }
        },
        "console",
    )
    result = result_data(
        execute_schema(
            schema,
            """
        query {
          decisions { id records { record_model record_id } }
        }
    """,
            user=reviewer,
        )
    )
    assert result == {
        "decisions": [
            {
                "id": str(decision.sqid),
                "records": [{"record_model": MtiParent._meta.label, "record_id": str(parent.sqid)}],
            }
        ]
    }
    reference = result["decisions"][0]["records"][0]
    resource = next(item for item in schema.angee_resources if item.model_label == reference["record_model"])
    assert resource.record_representation == "title"
    document = f"""
        query($id: String!, $privateId: String!) {{
          subject: {resource.roots.detail_name}(id: $id) {{ id {resource.record_representation} }}
          private: {resource.roots.detail_name}(id: $privateId) {{ id {resource.record_representation} }}
        }}
    """
    assert result_data(
        execute_schema(
            schema,
            document,
            {
                "id": reference["record_id"],
                "privateId": str(inaccessible.sqid),
            },
            user=reviewer,
        )
    ) == {
        "subject": {"id": reference["record_id"], "title": "Shared subject"},
        "private": None,
    }
