"""Record-reference resource filters preserve model identity and viewer scope."""

import json

import pytest
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.http import HttpRequest
from django.test.utils import CaptureQueriesContext, isolate_apps
from rebac import actor_context, system_context

from angee.decisions import schema as decision_schema
from angee.decisions.testing.drivers import seed_decision
from angee.decisions.testing.models import Decision, DecisionRecord
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.schema import GraphQLSchemas
from angee.iam.graphql.capabilities import permission_annotations
from angee.iam.graphql.relations import (
    RECORD_REFERENCE_ANNOTATIONS,
    RecordReferenceNode,
    actor_scoped_relation_expression,
    prove_record_references,
    with_record_reference_access,
)
from angee.iam.models.refs import ProtectedRecordRefMixin
from angee.knowledge import schema as knowledge_schema
from tests.conftest import (
    Page,
    SchemaAddon,
    Vault,
    addon_schema,
    create_user,
    execute_schema,
    result_data,
    vault_for,
)
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


@pytest.fixture
def content_type_discoveries(monkeypatch):
    original = models.QuerySet.filter
    discoveries = []

    def filter_types(queryset, *args, **kwargs):
        source = kwargs.get("pk__in")
        if queryset.model is ContentType and (
            isinstance(source, dict)
            or (isinstance(source, models.QuerySet) and source.model is DecisionRecord)
        ):
            discoveries.append(source)
        return original(queryset, *args, **kwargs)

    monkeypatch.setattr(models.QuerySet, "filter", filter_types)
    return discoveries


def test_related_reads_distinguish_system_bypass_from_missing_actor(composed_tables):
    """Related projections and public-ID filters honor REBAC relation precedence."""
    owner = create_user("relation-scope-owner")
    outsider = create_user("relation-scope-outsider")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Related page")

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

    source = Page.objects.all()
    assert related_values(source) == [None]
    assert related_ids(source) == []

    with system_context(reason="tests.related_read.bypass"):
        assert related_values(source) == [vault.name]
        assert related_ids(source) == [page.pk]
        pinned = source.with_actor(outsider)
        assert related_values(pinned) == [None]
        assert related_ids(pinned) == []


@pytest.mark.postgresql
@pytest.mark.parametrize("position", [0, 1])
def test_record_id_filters_do_not_confuse_equal_keys_from_different_models(
    referenced_decisions, content_type_discoveries, position,
):
    """Public identity filters work alone and retain the model in SQL decoding."""
    owner, outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    variables = {"id": records[position].sqid}
    selections = (
        ("decision_records", "decision_records(where: {record_id: {_eq: $id}}) { id }",
         [{"id": decisions[position].sqid}], []),
        ("decision_records_aggregate",
         "decision_records_aggregate(where: {record_id: {_eq: $id}}) { aggregate { count } }",
         {"aggregate": {"count": 1}}, {"aggregate": {"count": 0}}),
    )
    for name, selection, owner_rows, outsider_rows in selections:
        query = f"query($id: String!) {{ {selection} }}"
        for actor, expected in ((owner, owner_rows), (outsider, outsider_rows)):
            content_type_discoveries.clear()
            assert result_data(execute_schema(schema, query, variables, user=actor)) == {name: expected}
            assert len(content_type_discoveries) == 1


@pytest.mark.postgresql
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


@pytest.mark.postgresql
def test_model_and_id_filter_uses_one_bounded_target_proof(
    referenced_decisions, content_type_discoveries,
):
    owner, outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = """query($model: String!, $id: String!) {
      decision_records(where: {record_model: {_eq: $model}, record_id: {_eq: $id}}) { id }
      decision_records_aggregate(where: {record_model: {_eq: $model}, record_id: {_eq: $id}}) {
        aggregate { count }
      }
    }"""
    variables = {"model": records[0]._meta.label, "id": records[0].sqid}
    for actor, rows, count in ((owner, [{"id": decisions[0].sqid}], 1), (outsider, [], 0)):
        content_type_discoveries.clear()
        with CaptureQueriesContext(connection) as captured:
            data = result_data(execute_schema(schema, query, variables, user=actor))
        assert data == {
            "decision_records": rows,
            "decision_records_aggregate": {"aggregate": {"count": count}},
        }
        assert content_type_discoveries == []
        statements = [
            item["sql"] for item in captured
            if f'FROM "{DecisionRecord._meta.db_table}"' in item["sql"]
        ]
        assert len(statements) == 2
        for sql in statements:
            assert f'FROM "{records[0]._meta.db_table}"' in sql
            assert f'FROM "{records[1]._meta.db_table}"' not in sql
            assert sql.count("_angee_reference_key") == 1
            assert len(sql.encode()) < 100_000


@pytest.mark.postgresql
def test_identity_filter_uses_only_the_decoded_target_proof(referenced_decisions):
    owner, _outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = "query($id: String!) { decision_records(where: {record_id: {_eq: $id}}) { id } }"
    with CaptureQueriesContext(connection) as captured:
        data = result_data(execute_schema(schema, query, {"id": records[0].sqid}, user=owner))
    assert data == {"decision_records": [{"id": decisions[0].sqid}]}
    list_sql = [item["sql"] for item in captured if item["sql"].startswith(
        f'SELECT "{DecisionRecord._meta.db_table}".'
    )]
    assert len(list_sql) == 1
    # Measured on the integrated line: 58,499 B on SQLite and 64,246 B on PostgreSQL —
    # one decoded target proof carrying the composed policy's per-type pre-reads (about
    # 44 KB), the record's own scope (about 14 KB) and the typed candidate predicate; a
    # ceiling, not a target, that a lane lowering the policy cost lowers again.
    assert len(list_sql[0].encode()) < 72_000


@pytest.mark.postgresql
def test_identity_filter_cost_is_flat_across_other_types_and_link_history(composed_tables, tmp_path):
    """One public ID does not import the actor's unrelated concern history."""
    owner = create_user("reference-history-owner")
    schema = addon_schema(decision_schema.schemas, "console")
    query = "query($id: String!) { decision_records(where: {record_id: {_eq: $id}}) { id } }"
    with actor_context(owner):
        vault = Vault.objects.create_for(owner, name="Selected vault")
        page = Page.objects.create_in(vault, title="Other type")
    selected = seed_decision(actor=owner, assignees=(owner,), reference=vault, requester=None)
    selected_link = DecisionRecord.system_queryset().get(decision=selected)

    def read() -> tuple[dict, int, int, int]:
        with CaptureQueriesContext(connection) as captured:
            payload = result_data(execute_schema(schema, query, {"id": vault.sqid}, user=owner))
        sizes = [len(item["sql"].encode()) for item in captured]
        return payload, len(sizes), sum(sizes), max(sizes)

    read()  # Warm schema and content-type caches before both measurements.
    one = read()
    seed_decision(actor=owner, assignees=(owner,), reference=page, requester=None)
    with actor_context(owner):
        other_vaults = [Vault.objects.create_for(owner, name=f"Other vault {index}") for index in range(60)]
    for other in other_vaults:
        seed_decision(actor=owner, assignees=(owner,), reference=other, requester=None)
    read()
    many = read()
    expected = {"decision_records": [{"id": selected_link.sqid}]}
    assert one[0] == many[0] == expected
    (tmp_path / "history-cost.json").write_text(json.dumps({"one": one[1:], "many": many[1:]}))
    assert many[1] == one[1], (one, many)
    assert abs(many[2] - one[2]) < 256, (one, many)
    assert abs(many[3] - one[3]) < 256, (one, many)


def test_display_list_statement_does_not_inline_target_policies(referenced_decisions):
    owner, _outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = """{
      decision_records(order_by: {id: asc}) { id record_model record_id }
    }"""
    with CaptureQueriesContext(connection) as captured:
        payload = result_data(execute_schema(schema, query, user=owner))
    assert payload == {
        "decision_records": [
            {"id": row.sqid, "record_model": record._meta.label, "record_id": record.sqid}
            for row, record in zip(decisions, records, strict=True)
        ]
    }
    statements = [item["sql"] for item in captured]
    list_statements = [sql for sql in statements if 'AS "_angee_record_readable"' in sql]
    assert len(list_statements) == 1
    assert len(list_statements[0].encode()) < 200_000
    assert list_statements[0].count("WITH RECURSIVE") <= 1
    assert len(statements) <= 15


def test_record_reference_page_proofs_reuse_request_admissions(composed_tables):
    owner = create_user("reference-proof-memo")
    other = create_user("reference-proof-other")
    vault = vault_for(owner)
    request = HttpRequest()
    content_type = ContentType.objects.get_for_model(Vault)
    first = [DecisionRecord(content_type=content_type, object_id=str(vault.pk))]
    second = [DecisionRecord(content_type=content_type, object_id=str(vault.pk))]
    with actor_context(owner):
        prove_record_references(first, request=request)
        with CaptureQueriesContext(connection) as captured:
            prove_record_references(second, request=request)
    assert len(captured) == 0
    assert second[0]._angee_record_readable is first[0]._angee_record_readable is True
    with actor_context(other):
        prove_record_references(first, request=request)
    assert first[0]._angee_record_readable is False


@pytest.mark.postgresql
@pytest.mark.parametrize("filter_body,expected_positions", [
    ("{_not: {record_model: {_eq: $model}, record_id: {_eq: $id}}}", (1,)),
    ("{_or: [{record_model: {_eq: $model}, record_id: {_eq: $id}}, "
     "{record_id: {_eq: $other}}]}", (0, 1)),
])
def test_pair_inside_not_or_or_keeps_unbounded_guard(
    referenced_decisions, content_type_discoveries, filter_body, expected_positions,
):
    owner, _outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    other_variable = ", $other: String!" if "$other" in filter_body else ""
    query = f"""query($model: String!, $id: String!{other_variable}) {{
      decision_records(where: {filter_body}) {{ id }}
    }}"""
    content_type_discoveries.clear()
    rows = result_data(execute_schema(schema, query, {
        "model": records[0]._meta.label, "id": records[0].sqid, "other": records[1].sqid,
    }, user=owner))["decision_records"]
    assert {row["id"] for row in rows} == {decisions[position].sqid for position in expected_positions}
    assert len(content_type_discoveries) == 1


@pytest.mark.postgresql
def test_positive_pair_bounds_with_unrelated_or_sibling(referenced_decisions, content_type_discoveries):
    owner, _outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = """query($model: String!, $id: String!, $other: String!) {
      decision_records(where: {_and: [
        {record_model: {_eq: $model}}, {record_id: {_eq: $id}},
        {_or: [{record_id: {_eq: $id}}, {record_id: {_eq: $other}}]}
      ]}) { id }
    }"""
    rows = result_data(execute_schema(schema, query, {
        "model": records[0]._meta.label, "id": records[0].sqid, "other": records[1].sqid,
    }, user=owner))["decision_records"]
    assert rows == [{"id": decisions[0].sqid}]
    assert content_type_discoveries == []


@pytest.mark.postgresql
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


@pytest.mark.postgresql
@pytest.mark.parametrize("value", ["invalid_identity", "1", ""])
def test_unknown_public_identity_does_not_match_a_record_key(referenced_decisions, value):
    owner, _outsider, _records, _decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    query = "query($id: String!) { decision_records(where: {record_id: {_eq: $id}}) { id } }"
    assert result_data(execute_schema(schema, query, {"id": value}, user=owner)) == {"decision_records": []}


@pytest.mark.postgresql
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
    owned, hidden, another_owned = vault_for(owner), vault_for(other), vault_for(owner, name="Other research")
    with model_tables((TextReference,)):
        for record in (owned, hidden, another_owned):
            TextReference.objects.create(
                content_type=ContentType.objects.get_for_model(record), object_id=str(record.pk),
            )
        TextReference.objects.create(
            content_type=ContentType.objects.get_for_model(owned), object_id="0" + str(owned.pk),
        )
        plain_type = ContentType.objects.get_for_model(ContentType)
        TextReference.objects.create(content_type=plain_type, object_id=str(plain_type.pk))
        with actor_context(owner):
            guarded = with_record_reference_access(TextReference.objects.order_by("pk"))
            assert list(guarded.values_list("_angee_record_readable", flat=True)) == [True, False, True, False, False]
            bounded = with_record_reference_access(TextReference.objects.order_by("pk"), target=(Vault, owned.pk))
            assert list(bounded.values_list("_angee_record_readable", flat=True)) == [True, False, False, False, False]
            hidden_target = with_record_reference_access(
                TextReference.objects.order_by("pk"), target=(Vault, hidden.pk)
            )
            assert list(hidden_target.values_list("_angee_record_readable", flat=True)) == [False] * 5
            plain = with_record_reference_access(
                TextReference.objects.order_by("pk"), target=(ContentType, plain_type.pk)
            )
            assert list(plain.values_list("_angee_record_readable", flat=True)) == [False] * 5
        with actor_context(other):
            rebound = with_record_reference_access(guarded)
            assert list(rebound.values_list("_angee_record_readable", flat=True)) == [False, True, False, False, False]
        missing_actor = with_record_reference_access(TextReference.objects.order_by("pk"), target=(Vault, owned.pk))
        assert list(missing_actor.values_list("_angee_record_readable", flat=True)) == [False] * 5


def test_unselected_reference_fields_do_not_discover_targets(referenced_decisions, content_type_discoveries):
    owner, _outsider, _records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    data = result_data(execute_schema(schema, "{ decision_records(order_by: {id: asc}) { id } }", user=owner))
    assert [row["id"] for row in data["decision_records"]] == [row.sqid for row in decisions]
    assert content_type_discoveries == []


def test_reference_node_and_hint_declaration_are_query_free(referenced_decisions):
    owner, _outsider, _records, _decisions = referenced_decisions
    with actor_context(owner), CaptureQueriesContext(connection) as queries:
        rows = DecisionRecord.objects.with_actor(owner)
        assert RecordReferenceNode.get_queryset(rows, None) is rows
        hint = RECORD_REFERENCE_ANNOTATIONS["_angee_record_readable"](None)
    assert isinstance(hint, models.Expression)
    assert list(queries) == []


def test_explicit_reference_annotation_discovers_once(referenced_decisions, content_type_discoveries):
    owner, _outsider, _records, _decisions = referenced_decisions
    with actor_context(owner):
        with_record_reference_access(DecisionRecord.objects.with_actor(owner))
    assert len(content_type_discoveries) == 1


@pytest.fixture
def reference_compile_queries(referenced_decisions):
    owner, _outsider, _records, _decisions = referenced_decisions
    with actor_context(owner):
        rows = models.QuerySet(model=DecisionRecord)
        reference = with_record_reference_access(rows)
        scoped_reference = with_record_reference_access(DecisionRecord.objects.with_actor(owner))
        related_id = actor_scoped_relation_expression(rows, "decision__id")
        assert related_id is not None
        relation = rows.annotate(_related_id=related_id)
        permissions = rows.annotate(**permission_annotations(DecisionRecord, ("read",)))
        yield reference, scoped_reference, relation, permissions


def test_reference_permission_and_relation_sql_compilation_does_not_discover_targets(reference_compile_queries):
    with CaptureQueriesContext(connection) as queries:
        for prepared in reference_compile_queries:
            compiler = prepared.query.get_compiler(using=prepared.db)
            compiler.as_sql()
            compiler.as_sql()
    reference_table = DecisionRecord._meta.db_table.lower()
    assert all(
        "django_content_type" not in query["sql"].lower() and reference_table not in query["sql"].lower()
        for query in queries
    )


# The REBAC inline-compilation lane owns _Scope.as_sql's render-time reads.
@pytest.mark.xfail(strict=True, reason="REBAC-R1: _Scope.as_sql prepares at render")
def test_reference_permission_and_relation_sql_compilation_is_query_free(reference_compile_queries):
    with CaptureQueriesContext(connection) as queries:
        for prepared in reference_compile_queries:
            compiler = prepared.query.get_compiler(using=prepared.db)
            compiler.as_sql()
            compiler.as_sql()
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


def test_nested_record_references_keep_the_same_payload(referenced_decisions):
    owner, _outsider, records, decisions = referenced_decisions
    schema = addon_schema(decision_schema.schemas, "console")
    payload = result_data(
        execute_schema(
            schema,
            """{
      decisions(order_by: {id: asc}) { id records { record_model record_id } }
    }""",
            user=owner,
        )
    )
    assert payload == {
        "decisions": [
            {
                "id": Decision.system_queryset().get(pk=row.decision_id).sqid,
                "records": [{"record_model": record._meta.label, "record_id": record.sqid}],
            }
            for row, record in zip(decisions, records, strict=True)
        ]
    }


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
    schema = addon_schema(decision_schema.schemas, "console")
    payload = result_data(
        execute_schema(
            schema,
            """{
      decision_records(order_by: {id: asc}) { id record_model record_id }
    }""",
            user=owner,
        )
    )
    expected = {
        "decision_records": [
            {
                "id": row.sqid,
                "record_model": record._meta.label if index == 0 else None,
                "record_id": record.sqid if index == 0 else None,
            }
            for index, (row, record) in enumerate(
                zip(
                    DecisionRecord.system_queryset().filter(decision__in=decisions).order_by("pk"),
                    records,
                    strict=True,
                )
            )
        ]
    }
    assert json.dumps(payload, separators=(",", ":")) == json.dumps(expected, separators=(",", ":"))


def test_prepared_reference_filter_rechecks_target_read_after_revocation(composed_tables):
    owner, assignee = create_user("reference-revocation-owner"), create_user("reference-revocation-assignee")
    record = vault_for(owner)
    record.with_actor(owner).grant_record_access("viewer", assignee)
    decision = seed_decision(actor=owner, assignees=(assignee,), reference=record, requester=None)
    prepared = with_record_reference_access(DecisionRecord.objects.with_actor(assignee))
    assert list(prepared.filter(decision=decision).values_list("_angee_record_readable", flat=True)) == [True]
    record.with_actor(owner).revoke_record_access("viewer", assignee)
    assert list(prepared.filter(decision=decision).values_list("_angee_record_readable", flat=True)) == [False]


def test_selected_reference_readability_groups_by_boolean_not_target_identity(referenced_decisions):
    owner, _outsider, _records, _decisions = referenced_decisions
    prepared = with_record_reference_access(DecisionRecord.objects.with_actor(owner))
    grouped = prepared.order_by().values("_angee_record_readable").annotate(total=models.Count("pk"))
    assert list(grouped) == [{"_angee_record_readable": True, "total": 2}]


def test_selected_and_filtered_projection_resolves_target_types_once(referenced_decisions, content_type_discoveries):
    owner, _outsider, _records, _decisions = referenced_decisions
    prepared = with_record_reference_access(DecisionRecord.objects.with_actor(owner))
    filtered = prepared.filter(_angee_record_readable=True).values_list("_angee_record_readable", flat=True)
    assert len(content_type_discoveries) == 1
    values = list(filtered)
    assert values == [True, True]
    assert len(content_type_discoveries) == 1


def test_nested_reference_projection_does_not_rediscover_targets(referenced_decisions, content_type_discoveries):
    owner, _outsider, _records, _decisions = referenced_decisions
    with actor_context(owner):
        prepared = with_record_reference_access(DecisionRecord.objects.with_actor(owner))
        assert len(content_type_discoveries) == 1
        nested = DecisionRecord.objects.filter(pk__in=prepared.values("pk"))
        assert nested.count() == 2
    assert len(content_type_discoveries) == 1


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
