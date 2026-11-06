"""Page-sized record-reference proofs across native GraphQL row roots."""

import re
from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from angee.decisions import schema as decision_schema
from angee.decisions.testing.drivers import seed_decision
from angee.decisions.testing.models import Decision, DecisionRecord
from angee.iam.testing.graphql import graphql_request
from angee.storage.models.file import File
from angee.workflows.testing.models import WorkflowRun
from tests.conftest import Page, Vault, addon_schema, create_user, execute_schema, result_data, vault_for

pytestmark = pytest.mark.django_db(transaction=True)

TARGETS = (Vault, Page, File, Decision, WorkflowRun)
COUNTS = (1, 5, 10, 20)
LIST = """query($ids: [String!]!) {
  decision_records(where: {id: {_in: $ids}}, order_by: {id: asc}) { id record_model record_id }
}"""
LABELS = """query($ids: [String!]!) {
  decision_records(where: {id: {_in: $ids}}, order_by: {id: asc}) {
    id record_model record_id record_label
  }
}"""
LIST_IDS = """query($ids: [String!]!) {
  decision_records(where: {id: {_in: $ids}}, order_by: {id: asc}) { id }
}"""
NODES = """query($ids: [String!]!) {
  decision_records_aggregate(where: {id: {_in: $ids}}) {
    aggregate { count }
    nodes { id record_model record_id }
  }
}"""


def _link_page(owner, model_count):
    """One admitted decision with distinct reference keys and mixed target visibility."""
    outsider = create_user(f"reference-page-other-{model_count}")
    readable = vault_for(owner)
    unreadable = vault_for(outsider)
    decision = seed_decision(actor=owner, assignees=(owner,), reference=readable)
    content_types = [ContentType.objects.get_for_model(model) for model in TARGETS[:model_count]]
    links = []
    for index in range(20):
        for position, content_type in enumerate(content_types):
            if index == position == 0:
                continue  # seed_decision already wrote this first link
            object_id = unreadable.pk if index == 1 and position == 0 else 100_000 + index * 5 + position
            links.append(DecisionRecord(decision=decision, content_type=content_type, object_id=object_id))
    with system_context(reason="tests.record_reference_page"):
        DecisionRecord.objects.bulk_create(links)
    rows = list(DecisionRecord.system_queryset().filter(decision=decision).order_by("id"))
    # ContentTypeManager is the cache owner; measurement starts after its first use.
    for content_type in content_types:
        ContentType.objects.get_for_id(content_type.pk)
    return rows, readable


def _sql_for(schema, document, ids, owner):
    with CaptureQueriesContext(connection) as captured:
        data = result_data(execute_schema(schema, document, {"ids": ids}, user=owner))
    return data, [entry["sql"] for entry in captured]


def _assert_page_proofs(sql, model_count, *, row_root=True):
    proofs = [
        statement for statement in sql
        if re.match(r'^SELECT "[^"]+"\."[^"]+" AS "pk" FROM ', statement)
    ]
    assert len(proofs) == model_count
    for statement in proofs:
        assert statement.count("WITH RECURSIVE") <= 1
        names = re.findall(r'"([A-Za-z0-9_]+)"\s+AS\s*\(', statement)
        assert len(names) == len(set(names))
    if row_root:
        list_statements = [statement for statement in sql if 'AS "_angee_record_readable"' in statement]
        assert len(list_statements) == 1
        assert list_statements[0].count("WITH RECURSIVE") == 1
        for model in TARGETS[:model_count]:
            # The source-row decision read includes Decision and WorkflowRun
            # through workflows' requesting_step->operate arm. Their presence
            # here is separate from the one batched target proof per model.
            if model not in (Decision, WorkflowRun):
                assert f'FROM "{model._meta.db_table}"' not in list_statements[0]


def test_inbox_record_labels_share_the_target_proof(composed_tables, capture_graphql_document):
    """The decisions inbox records cell reads its targets once at each page size."""

    owner = create_user("reference-inbox-labels")
    targets = [vault_for(owner, name=f"Inbox target {index}") for index in range(20)]
    for target in targets:
        seed_decision(actor=owner, assignees=(owner,), reference=target)
    schema = addon_schema(decision_schema.schemas, "console")
    document = """query($limit: Int!) {
      decisions(limit: $limit, order_by: {id: asc}) {
        id records { record_model record_id record_label }
      }
    }"""
    reads = {
        count: capture_graphql_document(schema, document, {"limit": count}, actor=owner)
        for count in (1, 5, 20)
    }
    for count, read in reads.items():
        assert [row["records"][0]["record_label"] for row in read.data["decisions"]] == [
            str(target) for target in targets[:count]
        ]
        target_reads = [statement for statement in read.statements if re.match(
            rf'^SELECT .* FROM "{targets[0]._meta.db_table}"', statement,
        )]
        assert len(target_reads) == 1, (count, target_reads)
        read.assert_no_audit_writes()
    reads[5].assert_flat_from(reads[1])
    reads[20].assert_flat_from(reads[5])
    assert reads[20].total_sql_bytes <= reads[1].total_sql_bytes + 8_000


def test_reference_labels_read_each_target_model_once(composed_tables, capture_graphql_document):
    """Mixed reference columns share one admitted target read per model."""

    owner = create_user("reference-mixed-labels")
    rows, readable = _link_page(owner, len(TARGETS))
    schema = addon_schema(decision_schema.schemas, "console")
    reads = {
        count: capture_graphql_document(
            schema, LABELS, {"ids": [row.sqid for row in rows[:count * len(TARGETS)]]}, actor=owner,
        )
        for count in (1, 5, 20)
    }
    for count, read in reads.items():
        assert len(read.data["decision_records"]) == count * len(TARGETS)
        assert read.data["decision_records"][0]["record_label"] == str(readable)
        if count > 1:
            assert read.data["decision_records"][len(TARGETS)]["record_label"] is None
        for model in TARGETS:
            target_model = ContentType.objects.get_for_model(model).model_class()
            assert target_model is not None
            target_reads = [
                statement for statement in read.statements
                if statement.partition(' FROM "')[2].partition('"')[0] == target_model._meta.db_table
            ]
            assert len(target_reads) == 1, (count, model._meta.label, target_reads)
        read.assert_no_audit_writes()
    reads[5].assert_flat_from(reads[1])
    reads[20].assert_flat_from(reads[5])
    assert reads[20].total_sql_bytes <= reads[1].total_sql_bytes + 8_000


@pytest.mark.parametrize("model_count", (1, 2, 5))
def test_list_reference_proofs_are_flat_in_rows(composed_tables, capture_graphql_document, model_count):
    owner = create_user(f"reference-page-list-{model_count}")
    rows, readable = _link_page(owner, model_count)
    schema = addon_schema(decision_schema.schemas, "console")
    reads = {}
    statements = {}
    baselines = {}
    for count in COUNTS:
        ids = [row.sqid for row in rows[: count * model_count]]
        read = capture_graphql_document(schema, LIST, {"ids": ids}, actor=owner)
        data, sql = _sql_for(schema, LIST, ids, owner)
        assert read.data == data
        assert len(data["decision_records"]) == count * model_count
        baseline = capture_graphql_document(schema, LIST_IDS, {"ids": ids}, actor=owner)
        reads[count] = read
        statements[count] = sql
        baselines[count] = baseline
    for count in COUNTS:
        assert reads[count].statement_count == baselines[count].statement_count + model_count
        _assert_page_proofs(statements[count], model_count)
    for smaller, larger in zip(COUNTS, COUNTS[1:], strict=False):
        reads[larger].assert_flat_from(reads[smaller])
    assert reads[20].data["decision_records"][0]["record_id"] == readable.sqid
    assert reads[20].data["decision_records"][model_count]["record_id"] is None


@pytest.mark.parametrize("model_count", (1, 2, 5))
def test_aggregate_nodes_reference_proofs_are_flat_in_rows(composed_tables, capture_graphql_document, model_count):
    owner = create_user(f"reference-page-nodes-{model_count}")
    rows, _readable = _link_page(owner, model_count)
    schema = addon_schema(decision_schema.schemas, "console")
    reads = {}
    statements = {}
    for count in COUNTS:
        ids = [row.sqid for row in rows[: count * model_count]]
        read = capture_graphql_document(schema, NODES, {"ids": ids}, actor=owner)
        data, sql = _sql_for(schema, NODES, ids, owner)
        assert read.data == data
        assert data["decision_records_aggregate"]["aggregate"]["count"] == count * model_count
        reads[count] = read
        statements[count] = sql
    for count in COUNTS:
        _assert_page_proofs(statements[count], model_count)
    for smaller, larger in zip(COUNTS, COUNTS[1:], strict=False):
        reads[larger].assert_flat_from(reads[smaller])


def test_nested_reference_proofs_are_flat_in_parents(composed_tables, capture_graphql_document):
    owner = create_user("reference-page-nested")
    page_type = ContentType.objects.get_for_model(Page)
    for index in range(12):
        vault = vault_for(owner, name=f"Nested vault {index}")
        with actor_context(owner):
            page = Page.objects.create_in(vault, title=f"Nested page {index}")
        decision = seed_decision(actor=owner, assignees=(owner,), reference=vault)
        with system_context(reason="tests.nested_record_reference_page"):
            DecisionRecord.objects.create(decision=decision, content_type=page_type, object_id=page.pk)
    schema = addon_schema(decision_schema.schemas, "console")
    document = """query($limit: Int!) {
      decisions(limit: $limit, order_by: {id: asc}) { id records { record_model record_id } }
    }"""
    reads = {}
    statements = {}
    for count in (2, 6, 12):
        read = capture_graphql_document(schema, document, {"limit": count}, actor=owner)
        with CaptureQueriesContext(connection) as captured:
            data = result_data(execute_schema(schema, document, {"limit": count}, user=owner))
        assert read.data == data
        assert len(data["decisions"]) == count
        reads[count] = read
        statements[count] = [item["sql"] for item in captured]
    for count in (2, 6, 12):
        _assert_page_proofs(statements[count], 2, row_root=False)
    reads[6].assert_flat_from(reads[2])
    reads[12].assert_flat_from(reads[6])


def test_async_reference_page_returns_payload_and_bounded_proof(composed_tables):
    owner = create_user("reference-page-async")
    rows, readable = _link_page(owner, 1)
    schema = addon_schema(decision_schema.schemas, "console")
    request = graphql_request(owner)
    with actor_context(owner), CaptureQueriesContext(connection) as captured:
        result = async_to_sync(schema.execute)(
            LIST, variable_values={"ids": [row.sqid for row in rows[:5]]},
            context_value=SimpleNamespace(request=request),
        )
    assert result_data(result)["decision_records"][0]["record_id"] == readable.sqid
    sql = [item["sql"] for item in captured]
    _assert_page_proofs(sql, 1)
