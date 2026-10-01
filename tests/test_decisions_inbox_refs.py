"""Canonical decision subjects resolve through their own readable resource."""

import strawberry_django
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.scoping import system_queryset
from angee.decisions import schema as decision_schema
from angee.decisions.testing.drivers import seed_group
from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode
from tests.conftest import addon_schema, create_user, execute_schema, result_data
from tests.decisions_models import Decision
from tests.mtidemo.models import MtiChild, MtiParent


def test_mti_inbox_reference_resolves_the_readable_canonical_parent_label(composed_tables):
    """A non-admin follows the returned base identity without exposing other rows."""
    issuer = create_user("reference-issuer")
    reviewer = create_user("reference-reviewer")
    with system_context(reason="test canonical inbox reference"):
        child = MtiChild.objects.create(title="Shared subject", detail="Concrete child")
        parent = MtiParent.objects.get(pk=child.pk)
        inaccessible = MtiChild.objects.create(title="Private subject", detail="Private child")
    write_relationships([
        RelationshipTuple(to_object_ref(record), "reader", to_subject_ref(person))
        for record in (child, parent)
        for person in (issuer, reviewer)
    ])
    group = seed_group(actor=issuer, assignees=(reviewer,), reference=child)
    decision = system_queryset(Decision).get(group=group)

    @strawberry_django.type(MtiParent)
    class InboxSubjectType(AngeeNode):
        title: str

    subjects = hasura_model_resource(
        InboxSubjectType, model=MtiParent, name="inbox_subjects",
        filterable=("id",), sortable=("id",), aggregatable=(),
        record_representation="title", insert=False, update=False, delete=False,
    )
    console = decision_schema.schemas["console"]
    schema = addon_schema({"console": {
        **console,
        "query": [*console["query"], subjects.query],
        "types": [*console["types"], *subjects.types],
    }}, "console")
    result = result_data(execute_schema(schema, """
        query {
          decisions { id subject_model subject_id }
        }
    """, user=reviewer))
    assert result == {"decisions": [{
        "id": str(decision.sqid),
        "subject_model": MtiParent._meta.label,
        "subject_id": str(parent.sqid),
    }]}
    reference = result["decisions"][0]
    resource = next(item for item in schema.angee_resources if item.model_label == reference["subject_model"])
    assert resource.record_representation == "title"
    document = f"""
        query($id: String!, $privateId: String!) {{
          subject: {resource.roots.detail_name}(id: $id) {{ id {resource.record_representation} }}
          private: {resource.roots.detail_name}(id: $privateId) {{ id {resource.record_representation} }}
        }}
    """
    assert result_data(execute_schema(schema, document, {
        "id": reference["subject_id"], "privateId": str(inaccessible.sqid),
    }, user=reviewer)) == {
        "subject": {"id": reference["subject_id"], "title": "Shared subject"},
        "private": None,
    }
