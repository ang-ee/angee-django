"""Record-reference resource filters preserve model identity and viewer scope."""

import pytest
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.test.utils import CaptureQueriesContext, isolate_apps
from rebac import actor_context

from angee.base.refs import RecordRefMixin
from angee.base.scoping import system_queryset
from angee.decisions import schema as decision_schema
from angee.decisions.testing.drivers import seed_group
from angee.decisions.testing.models import Decision
from angee.graphql.data import hasura_model_resource
from angee.graphql.relations import with_record_reference_access
from tests.conftest import Page, addon_schema, create_user, execute_schema, result_data, vault_for
from tests.tables import model_tables

pytestmark = pytest.mark.django_db(transaction=True, reset_sequences=True)


@pytest.fixture
def referenced_decisions(composed_tables):
    """Admit decisions for two readable records with colliding numeric IDs."""
    owner, outsider = create_user("reference-owner"), create_user("reference-outsider")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Reference")
    assert page.pk == vault.pk
    records = (vault, page)
    decisions = []
    for record in records:
        group = seed_group(actor=owner, assignees=(owner,), reference=record, requester=None)
        decisions.append(system_queryset(Decision).get(group=group))
    return owner, outsider, records, decisions


@pytest.mark.parametrize("position", [0, 1])
def test_record_id_filters_do_not_confuse_equal_keys_from_different_models(referenced_decisions, position):
    """Public identity filters work alone and retain the model in SQL decoding."""
    owner, outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = """query($id: String!) {
      decisions(where: {subject_id: {_eq: $id}}) { id }
      decisions_aggregate(where: {subject_id: {_eq: $id}}) { aggregate { count } }
    }"""
    variables = {"id": records[position].sqid}
    assert result_data(execute_schema(schema, query, variables, user=owner)) == {
        "decisions": [{"id": decisions[position].sqid}],
        "decisions_aggregate": {"aggregate": {"count": 1}},
    }
    assert result_data(execute_schema(schema, query, variables, user=outsider)) == {
        "decisions": [], "decisions_aggregate": {"aggregate": {"count": 0}},
    }


def test_record_model_filter_composes_with_identity_and_existing_open_filter(referenced_decisions):
    owner, _outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = """query($model: String!, $id: String!) {
      decisions(where: {_and: [
        {subject_model: {_eq: $model}}, {subject_id: {_eq: $id}}, {is_open: {_eq: true}}
      ]}) { id }
    }"""
    for position, record in enumerate(records):
        assert result_data(execute_schema(schema, query, {
            "model": record._meta.label, "id": record.sqid,
        }, user=owner)) == {"decisions": [{"id": decisions[position].sqid}]}
    assert result_data(execute_schema(schema, query, {
        "model": records[0]._meta.label, "id": records[1].sqid,
    }, user=owner)) == {"decisions": []}


def test_record_model_and_identity_membership_use_native_boolean_filters(referenced_decisions):
    owner, _outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = """query($model: String!, $ids: [String!]!) {
      by_model: decisions(where: {subject_model: {_eq: $model}}) { id }
      by_ids: decisions(where: {subject_id: {_in: $ids}}, order_by: [{index: asc}]) { id }
      other: decisions(where: {_not: {subject_model: {_eq: $model}}}) { id }
    }"""
    result = result_data(execute_schema(schema, query, {
        "model": records[0]._meta.label_lower, "ids": [record.sqid for record in records],
    }, user=owner))
    assert result["by_model"] == [{"id": decisions[0].sqid}]
    assert {row["id"] for row in result["by_ids"]} == {decision.sqid for decision in decisions}
    assert result["other"] == [{"id": decisions[1].sqid}]


@pytest.mark.parametrize("value", ["invalid_identity", "1", ""])
def test_unknown_public_identity_does_not_match_a_record_key(referenced_decisions, value):
    owner, _outsider, _records, _decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = "query($id: String!) { decisions(where: {subject_id: {_eq: $id}}) { id } }"
    assert result_data(execute_schema(schema, query, {"id": value}, user=owner)) == {"decisions": []}


def test_reference_filter_operand_is_a_query_free_native_expression(referenced_decisions):
    _owner, _outsider, records, _decisions = referenced_decisions
    with CaptureQueriesContext(connection) as queries:
        operand = Decision.record_public_id_operand(records[0].sqid)
    assert len(queries) == 0
    assert operand.cases


def test_reference_filter_metadata_exposes_exact_public_string_inputs():
    schema = addon_schema(decision_schema.schemas, "console")
    resource = next(item for item in schema.angee_resources if item.model_label == "decisions.Decision")
    filter_type = schema._schema.get_type(resource.type_names.filter)
    for name in ("subject_model", "subject_id"):
        assert str(filter_type.fields[name].type) == "ID_comparison_exp"
        assert resource.query.fields[name].filter is not None


@pytest.mark.parametrize("name", ["subject_model", "subject_id"])
def test_reference_filter_declaration_rejects_claimed_decoder_names(name):
    """Composition cannot silently replace a caller's existing identity decoder."""
    with pytest.raises(ImproperlyConfigured, match="distinct and unclaimed"):
        hasura_model_resource(
            decision_schema.DecisionType, model=Decision,
            filterable=["id"], sortable=["id"], aggregatable=["id"],
            record_ref_filters=("subject_model", "subject_id"), field_id_decode={name: str},
        )


@isolate_apps()
def test_record_reference_guard_uses_the_declared_string_key_and_current_actor(composed_tables):
    """A string reference key compares typed target keys and rebinds read scope safely."""
    class TextReference(RecordRefMixin, models.Model):
        content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
        object_id = models.CharField(max_length=63)
        target = GenericForeignKey()

        class Meta:
            app_label = "auth"

    owner, other = create_user("reference-guard-owner"), create_user("reference-guard-other")
    owned, hidden = vault_for(owner), vault_for(other)
    with model_tables((TextReference,)):
        for record in (owned, hidden):
            TextReference.objects.create(
                content_type=ContentType.objects.get_for_model(record), object_id=str(record.pk),
            )
        with actor_context(owner):
            guarded = with_record_reference_access(TextReference.objects.order_by("pk"))
            assert list(guarded.values_list("_angee_record_readable", flat=True)) == [True, False]
            assert "CAST(" in str(guarded.query) and "varchar(63)" in str(guarded.query)
        with actor_context(other):
            rebound = with_record_reference_access(guarded)
            assert list(rebound.values_list("_angee_record_readable", flat=True)) == [False, True]
