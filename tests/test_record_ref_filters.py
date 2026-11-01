"""Record-reference resource filters preserve model identity and viewer scope."""

import pytest
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.test.utils import CaptureQueriesContext, isolate_apps
from rebac import actor_context

from angee.decisions import schema as decision_schema
from angee.decisions.testing.drivers import seed_decision
from angee.decisions.testing.models import Decision, DecisionRecord
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.relations import with_record_reference_access
from angee.iam.models.refs import ProtectedRecordRefMixin
from tests.conftest import Page, Vault, addon_schema, create_user, execute_schema, result_data, vault_for
from tests.tables import model_tables

pytestmark = pytest.mark.django_db(transaction=True)

CROSS_MODEL_REFERENCE_PK = 771


@pytest.fixture
def referenced_decisions(composed_tables):
    """Admit decisions for two readable records with colliding numeric IDs."""
    owner, outsider = create_user("reference-owner"), create_user("reference-outsider")
    with actor_context(owner):
        vault = Vault.objects.create_for(owner, pk=CROSS_MODEL_REFERENCE_PK, name="Research")
        page = Page.objects.create_in(vault, pk=CROSS_MODEL_REFERENCE_PK, title="Reference")
    assert page.pk == vault.pk
    records = (vault, page)
    decisions = []
    for record in records:
        decision = seed_decision(actor=owner, assignees=(owner,), reference=record, requester=None)
        decisions.append(DecisionRecord.system_queryset().get(decision=decision))
    return owner, outsider, records, decisions


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


@isolate_apps()
def test_record_reference_guard_uses_the_declared_string_key_and_current_actor(composed_tables):
    """A string reference key compares typed target keys and rebinds read scope safely."""
    class TextReference(ProtectedRecordRefMixin):
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
        TextReference.objects.create(
            content_type=ContentType.objects.get_for_model(owned), object_id="0" + str(owned.pk),
        )
        with actor_context(owner):
            guarded = with_record_reference_access(TextReference.objects.order_by("pk"))
            assert list(guarded.values_list("_angee_record_readable", flat=True)) == [True, False, False]
        with actor_context(other):
            rebound = with_record_reference_access(guarded)
            assert list(rebound.values_list("_angee_record_readable", flat=True)) == [False, True, False]


def test_unselected_reference_fields_do_not_discover_targets(referenced_decisions, monkeypatch):
    owner, _outsider, _records, decisions = referenced_decisions
    manager = ContentType.objects
    original = manager.filter
    discoveries = []

    def filter_types(*args, **kwargs):
        source = kwargs.get("pk__in")
        if isinstance(source, models.QuerySet) and source.model is DecisionRecord:
            discoveries.append(source)
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "filter", filter_types)
    schema = addon_schema(decision_schema.schemas, "console")
    data = result_data(execute_schema(schema, "{ decision_records(order_by: {id: asc}) { id } }", user=owner))
    assert [row["id"] for row in data["decision_records"]] == [row.sqid for row in decisions]
    assert discoveries == []


def test_reference_annotation_declaration_is_query_free(referenced_decisions):
    owner, _outsider, _records, _decisions = referenced_decisions
    with actor_context(owner), CaptureQueriesContext(connection) as queries:
        with_record_reference_access(DecisionRecord.objects.order_by("pk"))
    assert list(queries) == []


def test_selected_reference_fields_batch_aliases_and_fragments(referenced_decisions):
    owner, _outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = """{
      plain: decision_records(order_by: {id: asc}) { id }
      references: decision_records(order_by: {id: asc}) { ...Reference }
    }
    fragment Reference on DecisionRecordType { id record_model record_id }
    """
    data = result_data(execute_schema(schema, query, user=owner))
    assert data["plain"] == [{"id": row.sqid} for row in decisions]
    assert data["references"] == [
        {"id": row.sqid, "record_model": record._meta.label, "record_id": record.sqid}
        for row, record in zip(decisions, records, strict=True)
    ]


def test_reference_projection_keeps_authored_actor_until_redeclared(composed_tables):
    owner, other = create_user("reference-snapshot-owner"), create_user("reference-snapshot-other")
    records = (vault_for(owner), vault_for(other))
    records[0].with_actor(owner).grant_record_access("viewer", other)
    records[1].with_actor(other).grant_record_access("viewer", owner)
    decisions = [
        seed_decision(actor=owner, assignees=(owner, other), reference=record, requester=None)
        for record in records
    ]
    records[0].with_actor(owner).revoke_record_access("viewer", other)
    records[1].with_actor(other).revoke_record_access("viewer", owner)
    rows = DecisionRecord.objects.filter(decision__in=decisions).order_by("pk")
    prepared = with_record_reference_access(rows.with_actor(owner))
    assert list(prepared.all().values_list("_angee_record_readable", flat=True)) == [True, False]
    # Like native authored actor-bound subqueries, cloning the outer actor does
    # not rewrite an independent expression. Reapply at the owning type hook.
    changed_actor = prepared.with_actor(other)
    assert list(changed_actor.values_list("_angee_record_readable", flat=True)) == [True, False]
    refreshed = with_record_reference_access(changed_actor)
    assert list(refreshed.values_list("_angee_record_readable", flat=True)) == [False, True]


def test_selected_reference_readability_groups_by_boolean_not_target_identity(referenced_decisions):
    owner, _outsider, _records, _decisions = referenced_decisions
    prepared = with_record_reference_access(DecisionRecord.objects.with_actor(owner))
    grouped = prepared.order_by().values("_angee_record_readable").annotate(total=models.Count("pk"))
    assert list(grouped) == [{"_angee_record_readable": True, "total": 2}]


def test_selected_and_filtered_projection_discovers_for_each_sql_render(referenced_decisions, monkeypatch):
    owner, _outsider, _records, _decisions = referenced_decisions
    original = models.QuerySet.filter
    discoveries = []

    def filter_types(queryset, *args, **kwargs):
        source = kwargs.get("pk__in")
        if queryset.model is ContentType and isinstance(source, models.QuerySet) and source.model is DecisionRecord:
            discoveries.append(source)
        return original(queryset, *args, **kwargs)

    monkeypatch.setattr(models.QuerySet, "filter", filter_types)
    prepared = with_record_reference_access(DecisionRecord.objects.with_actor(owner))
    values = list(prepared.filter(_angee_record_readable=True).values_list("_angee_record_readable", flat=True))
    assert values == [True, True]
    assert len(discoveries) == 2  # SELECT and WHERE each compile the native expression.


@isolate_apps()
def test_reference_projection_uses_registered_key_lookup(composed_tables):
    from django.db.models.lookups import In

    class ReferenceKey(models.CharField):
        pass

    @ReferenceKey.register_lookup
    class NoMatch(In):
        def as_sql(self, compiler, connection):
            return "0 = 1", []

    class CustomReference(ProtectedRecordRefMixin):
        content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
        object_id = ReferenceKey(max_length=63)
        target = GenericForeignKey()

        class Meta:
            app_label = "auth"

    owner = create_user("custom-reference-owner")
    record = vault_for(owner)
    with model_tables((CustomReference,)), actor_context(owner):
        CustomReference.objects.create(
            content_type=ContentType.objects.get_for_model(record), object_id=str(record.pk),
        )
        prepared = with_record_reference_access(CustomReference.objects.all())
        assert list(prepared.values_list("_angee_record_readable", flat=True)) == [False]
