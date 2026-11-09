"""Canonical concern links resolve through their own readable resource."""

from types import SimpleNamespace

import pytest
import strawberry_django
from pydantic import ValidationError
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.relationships import delete_relationships
from rebac.types import RelationshipFilter

from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionRequest
from angee.decisions.testing.drivers import seed_decision
from angee.decisions.testing.models import Decision
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.testing.documents import execute_schema, graphql_request, result_data
from tests.conftest import addon_schema, create_user, vault_for
from tests.mtidemo.models import MtiChild, MtiParent


@pytest.mark.parametrize("fields", [
    "concerns_label records { record_model record_id }",
    "records { record_model record_id } concerns_label",
])
def test_concern_labels_batch_mixed_targets_without_exposing_revoked_records(composed_tables, fields):
    issuer = create_user("concern-label-issuer")
    reviewer = create_user("concern-label-reviewer")
    outsider = create_user("concern-label-outsider")
    vault = vault_for(issuer, name="Readable concern")
    with system_context(reason="test.concern_labels"):
        other = MtiParent.objects.create(title="Second type")
    write_relationships([
        RelationshipTuple(to_object_ref(vault), "viewer", to_subject_ref(reviewer)),
        *(RelationshipTuple(to_object_ref(other), "reader", to_subject_ref(person)) for person in (issuer, reviewer)),
    ])
    decision = Decision.objects.ask(DecisionRequest(
        kind="review_concerns", records=(vault, other), assignees=(reviewer,),
        proposal={"alternatives": [{"key": "keep", "label": "Keep", "outcome": "done"}]},
    ), actor=issuer)
    schema = addon_schema(decision_schema.schemas, "console")
    document = "query { decisions { " + fields + " } }"
    row = result_data(execute_schema(schema, document, user=reviewer))["decisions"][0]
    assert set(row["concerns_label"].split("; ")) == {str(vault), str(other)}
    assert {link["record_id"] for link in row["records"]} == {vault.sqid, other.sqid}

    subject = to_subject_ref(reviewer)
    reference = to_object_ref(other)
    delete_relationships(RelationshipFilter(
        resource_type=reference.resource_type, resource_id=reference.resource_id, relation="reader",
        subject_type=subject.object.resource_type, subject_id=subject.object.resource_id,
    ))
    row = result_data(execute_schema(schema, document, user=reviewer))["decisions"][0]
    assert row["concerns_label"] == str(vault)
    assert {link["record_id"] for link in row["records"]} == {vault.sqid, None}
    assert result_data(execute_schema(schema, document, user=outsider)) == {"decisions": []}

    # Mutation/owner-loaded rows miss the list cache but use the same readable-target prefetch.
    with actor_context(reviewer):
        fresh = Decision.objects.get(pk=decision.pk)
        info = SimpleNamespace(context=SimpleNamespace(request=graphql_request(reviewer)))
        assert decision_schema.DecisionType.concerns_label.base_resolver.wrapped_func(fresh, info) == str(vault)


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
