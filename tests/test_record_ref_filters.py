"""Record-reference resource filters preserve model identity and viewer scope."""

import pytest
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.test.utils import CaptureQueriesContext, isolate_apps
from rebac import actor_context, system_context

from angee.base.models.refs import RecordRefMixin
from angee.decisions import schema as decision_schema
from angee.decisions.testing.drivers import seed_decision
from angee.decisions.testing.models import Decision, DecisionRecord
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.relations import actor_scoped_relation_expression, with_record_reference_access
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import execute_schema, result_data
from angee.knowledge import schema as knowledge_schema
from tests.conftest import Page, SchemaAddon, addon_schema, create_user, vault_for
from tests.tables import model_tables

pytestmark = pytest.mark.django_db


@pytest.fixture
def referenced_decisions(composed_tables):
    """Admit decisions for two readable records with colliding numeric IDs."""
    owner, outsider = create_user("reference-owner"), create_user("reference-outsider")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, pk=vault.pk, title="Reference")
    assert page.pk == vault.pk
    records = (vault, page)
    decisions = []
    for record in records:
        decision = seed_decision(actor=owner, assignees=(owner,), reference=record, requester=None)
        decisions.append(DecisionRecord.system_queryset().get(decision=decision))
    return owner, outsider, records, decisions


def test_related_reads_distinguish_system_bypass_from_missing_actor(composed_tables):
    """Related projections and public-ID filters honor REBAC relation precedence."""
    owner = create_user("relation-scope-owner")
    outsider = create_user("relation-scope-outsider")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Related page")
    decision = seed_decision(actor=owner, assignees=(owner,), reference=page, requester=None)

    schemas = GraphQLSchemas([SchemaAddon(knowledge_schema.schemas)])
    condition = schemas.resource_filter(Page, {"vault": {"_eq": str(vault.sqid)}})

    def related_values(source):
        expression = actor_scoped_relation_expression(source, "vault__name")
        return list(
            source.with_actor(owner)
            .filter(pk=page.pk)
            .annotate(related_name=expression)
            .values_list("related_name", flat=True)
        )

    def related_ids(source):
        return list(condition(source).with_actor(owner).filter(pk=page.pk).values_list("pk", flat=True))

    def reference_readable(links):
        return list(with_record_reference_access(links).filter(decision=decision).values_list(
            "_angee_record_readable", flat=True,
        ))

    source = Page.objects.all()
    assert related_values(source) == [None]
    assert related_ids(source) == []

    with system_context(reason="tests.related_read.bypass"):
        assert related_values(source) == [vault.name]
        assert related_ids(source) == [page.pk]
        assert reference_readable(DecisionRecord.objects.all()) == [True]
        pinned = source.with_actor(outsider)
        assert related_values(pinned) == [None]
        assert related_ids(pinned) == []
        assert reference_readable(DecisionRecord.objects.with_actor(outsider)) == []


@pytest.mark.parametrize("position", [0, 1])
def test_record_id_filters_do_not_confuse_equal_keys_from_different_models(referenced_decisions, position):
    """Public identity filters work alone and retain the model in SQL decoding."""
    owner, outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = """query($id: String!) {
      decision_records(where: {record_id: {_eq: $id}}) { id }
      decision_records_aggregate(where: {record_id: {_eq: $id}}) { aggregate { count } }
    }"""
    variables = {"id": records[position].sqid}
    assert result_data(execute_schema(schema, query, variables, user=owner)) == {
        "decision_records": [{"id": decisions[position].sqid}],
        "decision_records_aggregate": {"aggregate": {"count": 1}},
    }
    assert result_data(execute_schema(schema, query, variables, user=outsider)) == {
        "decision_records": [], "decision_records_aggregate": {"aggregate": {"count": 0}},
    }


def test_record_model_filter_composes_with_identity_and_decision_filter(referenced_decisions):
    owner, _outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = """query($model: String!, $id: String!, $decision: String!) {
      decision_records(where: {_and: [
        {record_model: {_eq: $model}}, {record_id: {_eq: $id}}, {decision: {_eq: $decision}}
      ]}) { id }
    }"""
    for position, record in enumerate(records):
        assert result_data(execute_schema(schema, query, {
            "model": record._meta.label, "id": record.sqid,
            "decision": Decision.system_queryset().get(pk=decisions[position].decision_id).sqid,
        }, user=owner)) == {"decision_records": [{"id": decisions[position].sqid}]}
    assert result_data(execute_schema(schema, query, {
        "model": records[0]._meta.label, "id": records[1].sqid,
        "decision": Decision.system_queryset().get(pk=decisions[0].decision_id).sqid,
    }, user=owner)) == {"decision_records": []}


def test_record_model_and_identity_membership_use_native_boolean_filters(referenced_decisions):
    owner, _outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = """query($model: String!, $ids: [String!]!) {
      by_model: decision_records(where: {record_model: {_eq: $model}}) { id }
      by_ids: decision_records(where: {record_id: {_in: $ids}}, order_by: [{id: asc}]) { id }
      other: decision_records(where: {_not: {record_model: {_eq: $model}}}) { id }
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
    query = "query($id: String!) { decision_records(where: {record_id: {_eq: $id}}) { id } }"
    assert result_data(execute_schema(schema, query, {"id": value}, user=owner)) == {"decision_records": []}


def test_reference_filter_operand_is_a_query_free_native_expression(referenced_decisions):
    _owner, _outsider, records, _decisions = referenced_decisions
    with CaptureQueriesContext(connection) as queries:
        operand = DecisionRecord.record_public_id_operand(records[0].sqid)
    assert len(queries) == 0
    assert operand.cases


def test_reference_filter_metadata_exposes_exact_public_string_inputs():
    schema = addon_schema(decision_schema.schemas, "console")
    resource = next(item for item in schema.angee_resources if item.model_label == "decisions.DecisionRecord")
    filter_type = schema._schema.get_type(resource.type_names.filter)
    for name in ("record_model", "record_id"):
        assert str(filter_type.fields[name].type) == "ID_comparison_exp"
        assert resource.query.fields[name].filter is not None


@pytest.mark.parametrize("name", ["record_model", "record_id"])
def test_reference_filter_declaration_rejects_claimed_decoder_names(name):
    """Composition cannot silently replace a caller's existing identity decoder."""
    with pytest.raises(ImproperlyConfigured, match="distinct and unclaimed"):
        hasura_model_resource(
            decision_schema.DecisionRecordType, model=DecisionRecord,
            filterable=["id"], sortable=["id"], aggregatable=["id"],
            record_ref_filters=("record_model", "record_id"), field_id_decode={name: str},
        )


@pytest.mark.django_db(transaction=True)
@isolate_apps()
def test_record_reference_guard_uses_the_declared_string_key_and_current_actor(composed_tables):
    """A string reference key compares typed target keys and rebinds read scope safely."""
    class TextReference(RecordRefMixin, models.Model):
        content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
        object_id = models.CharField(max_length=63)
        target = GenericForeignKey()

        @classmethod
        def declared_target_models(cls, **kwargs):
            # Old edges remain readable even after write admission is withdrawn.
            return {}

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
            with CaptureQueriesContext(connection) as queries:
                guarded = with_record_reference_access(TextReference.objects.order_by("pk"))
            assert len(queries) == 0
            sql = str(guarded.query)
            guarded.query.get_compiler(connection=connection).as_sql()
            assert list(guarded.values_list("_angee_record_readable", flat=True)) == [True, False]
            assert "varchar(63)" in sql and ("CAST(" in sql or "::varchar(63)" in sql)
            assert list(guarded.values("_angee_record_readable").annotate(
                total=models.Count("pk"),
            ).order_by("_angee_record_readable")) == [
                {"_angee_record_readable": False, "total": 1},
                {"_angee_record_readable": True, "total": 1},
            ]
        with actor_context(other):
            rebound = with_record_reference_access(guarded)
            assert list(rebound.values_list("_angee_record_readable", flat=True)) == [False, True]


@pytest.mark.parametrize("empty", [False, True])
def test_record_reference_declaration_is_query_free(empty):
    """Schema and optimizer probes resolve no target types or permission scopes."""
    with CaptureQueriesContext(connection) as queries:
        rows = DecisionRecord.objects.none() if empty else DecisionRecord.objects.all()
        guarded = with_record_reference_access(rows)
        guarded.filter(_angee_record_readable=True).query.clone()
        assert actor_scoped_relation_expression(guarded, "decision__kind") is not None
    assert len(queries) == 0
    assert guarded.query.is_empty() is empty


def test_unused_record_reference_guard_compiles_without_reads(referenced_decisions):
    """An unused annotation never scans represented types or resolves permissions."""
    owner, _outsider, _records, decisions = referenced_decisions
    with CaptureQueriesContext(connection) as queries:
        guarded = with_record_reference_access(DecisionRecord.objects.with_actor(owner)).order_by("pk")
        str(guarded.values("pk").query)
        guarded.values("pk").query.get_compiler(connection=connection).as_sql()
    assert len(queries) == 0
    assert list(guarded.values_list("pk", "_angee_record_readable")) == [
        (row.pk, True) for row in sorted(decisions, key=lambda row: row.pk)
    ]
