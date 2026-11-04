"""Document-level SQL budgets for actor-bound page reads."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import strawberry_django
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import (
    RelationshipTuple,
    actor_context,
    permission_expression,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.managers import RebacQuerySet

from angee.base.models.identity import public_id_of
from angee.decisions import schema as decisions_schema
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.schema import GraphQLSchemas
from angee.iam import schema as iam_schema
from angee.iam.graphql.capabilities import held_permissions, permissions_field
from angee.iam.graphql.node import ProtectedNode
from angee.iam.graphql.relations import actor_scoped_to_one
from angee.iam.testing.queries import is_permission_audit_insert
from angee.integrate import schema as integrate_schema
from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink
from angee.integrate_vcs import schema as integrate_vcs_schema
from angee.messaging.testing.models import Channel
from angee.projects import schema as projects_schema
from angee.projects.testing.models import Project, Task
from angee.provenance.context import changeset as open_changeset
from angee.timeline import schema as timeline_schema
from tests.conftest import (
    Page,
    SchemaAddon,
    addon_schema,
    authored_graphql_document,
    create_user,
    current_stream,
    make_integration,
    vault_for,
)
from tests.test_timeline import KINDS

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = authored_graphql_document(
    ROOT / "src/angee/decisions/web/src/documents.console.ts", "RecordDecisionsDocument"
)
SOURCES = authored_graphql_document(ROOT / "src/angee/integrate/web/src/documents.ts", "RecordSourcesDocument")
TIMELINE = authored_graphql_document(ROOT / "src/angee/timeline/web/src/documents.console.ts", "TimelineDocument")


@pytest.fixture
def record_decisions_reads(composed_tables: None, capture_graphql_document):
    """Execute the authored pane empty, then answered at one, five, ten and twenty."""

    del composed_tables
    owner = create_user("document-budget-decisions")
    record = vault_for(owner)
    schema = addon_schema(decisions_schema.schemas, "console")
    variables = {"model": record._meta.label, "id": public_id_of(record)}
    empty = capture_graphql_document(schema, DECISIONS, variables, actor=owner)
    assert empty.data["decision_records"] == []

    open_one = None
    answered = {}
    for index in range(20):
        decision = Decision.objects.ask(
            DecisionRequest(
                kind=f"budget_review_{index}",
                records=(record,),
                requester=owner,
                assignees=(),
                proposal=DecisionProposal(alternatives=[{"key": "accept", "label": "Accept", "outcome": "done"}]),
            ),
            actor=owner,
        )
        if index == 0:
            open_one = capture_graphql_document(schema, DECISIONS, variables, actor=owner)
            assert len(open_one.data["decision_records"]) == 1
        with system_context(reason="test.document_budget.decisions"):
            decision.withdraw(actor=owner)
        if index + 1 in (1, 5, 10, 20):
            read = capture_graphql_document(schema, DECISIONS, variables, actor=owner)
            assert len(read.data["decision_records"]) == index + 1
            assert all(row["decision"]["answered_by"]["display_name"] for row in read.data["decision_records"])
            assert all(row["decision"]["verdict_label"] == "Withdrawn" for row in read.data["decision_records"])
            answered[index + 1] = read
    return empty, open_one, answered


def test_record_decisions_document_budget(record_decisions_reads) -> None:
    empty, open_one, answered = record_decisions_reads
    empty.assert_bytes(total=43_000, largest=41_000, cause="REBAC L2 bounded target proof in the empty record scope")
    empty.assert_statements(4)
    open_one.assert_bytes(total=47_000, largest=41_000, cause="REBAC L2 bounded target proof")
    open_one.assert_statements(5)
    open_one.assert_no_audit_writes()
    for count, read in answered.items():
        read.assert_bytes(total=55_000, largest=41_000, cause="REBAC L2 candidate scope and L5 held_many field gates")
        read.assert_statements(7 if count == 1 else 8)
        read.assert_no_audit_writes()
    empty.assert_no_audit_writes()


def test_record_decisions_document_statement_count(record_decisions_reads) -> None:
    _, _, answered = record_decisions_reads
    one, five, ten, twenty = (answered[count] for count in (1, 5, 10, 20))
    assert five.statement_count == one.statement_count + 1, (
        "L2 multi-row TupleIn repeats actor role/group membership; L8 removes this step"
    )
    ten.assert_flat_from(five)
    twenty.assert_flat_from(ten)


@pytest.fixture
def record_sources_reads(composed_tables: None, capture_graphql_document):
    """Execute the authored pane at one, five, ten and twenty visible links."""

    del composed_tables
    bridge = make_integration("document-budget-sources", model=Channel)
    schema = addon_schema(iam_schema.schemas, "console", integrate_schema.schemas, integrate_vcs_schema.schemas)
    with system_context(reason="test.document_budget.sources"):
        stream = current_stream(bridge, "records", kind=StreamKind.RECORD_REPLICA)
        RecordLink.objects.observe(stream, "record:1", target=stream)
    variables = {"modelLabel": stream._meta.label, "recordId": public_id_of(stream)}
    one = capture_graphql_document(schema, SOURCES, variables, actor=bridge.owner)
    assert len(one.data["record_links"]) == 1
    reads = {1: one}
    for index in range(2, 21):
        with system_context(reason="test.document_budget.sources.more"):
            RecordLink.objects.observe(stream, f"record:{index}", target=stream)
        if index in (5, 10, 20):
            read = capture_graphql_document(schema, SOURCES, variables, actor=bridge.owner)
            assert len(read.data["record_links"]) == index
            reads[index] = read
    return reads


def test_record_sources_document_fixed_budgets(record_sources_reads) -> None:
    one, five, ten, twenty = (record_sources_reads[count] for count in (1, 5, 10, 20))
    # CI's full-profile SQLite shard measures more than a local checkout on this
    # document (one link 41 statements / 9 audits against 39 / 8 here; five links
    # 149 / 45 against 139 / 40, about +2.6 KB per link); the ceilings carry that
    # headroom. The strict pins are the xfails below; P12's batching lowers all of
    # these.
    one.assert_bytes(total=140_000, largest=21_000, cause="REBAC L2 candidate-root proof on one link")
    one.assert_statements(45)
    one.assert_audit_inserts(10)
    five.assert_bytes(total=530_000, largest=21_000, cause="REBAC L2 candidate-root proof per unbatched link")
    five.assert_statements(155)
    five.assert_audit_inserts(50)
    ten.assert_statements(300)
    twenty.assert_statements(600)


@pytest.mark.xfail(strict=True, reason="P12: unbatched concrete integration reads exceed the Sources byte target")
def test_record_sources_document_byte_budget(record_sources_reads) -> None:
    five = record_sources_reads[5]
    five.assert_bytes(
        total=450_000,
        largest=20_000,
        cause="REBAC L2 candidate-root proof repeats for each unbatched concrete integration",
    )


@pytest.mark.xfail(strict=True, reason="P12: Sources must batch concrete integrations across links")
def test_record_sources_document_statement_count(record_sources_reads) -> None:
    one, five, ten, twenty = (record_sources_reads[count] for count in (1, 5, 10, 20))
    twenty.assert_flat_from(ten)
    ten.assert_flat_from(five)
    five.assert_flat_from(one)


@pytest.mark.xfail(strict=True, reason="P5: Sources reads must not insert permission-audit events")
def test_record_sources_document_audit(record_sources_reads) -> None:
    for read in record_sources_reads.values():
        read.assert_no_audit_writes()


@pytest.fixture
def timeline_rail_reads(activity_catalog: None, composed_permissions: None, capture_graphql_document):
    """Execute the authored rail at one and five entries for a record."""

    del activity_catalog, composed_permissions
    owner = create_user("document-budget-timeline")
    with open_changeset(fresh=True, change_reason="Rail budget event"):
        record = vault_for(owner)
    with override_settings(ANGEE_TIMELINE_KINDS=KINDS):
        schema = addon_schema(timeline_schema.schemas, "console")
        variables = {"model": record._meta.label, "id": public_id_of(record)}
        # Kind and content-type discovery is process cached; compare warm reads.
        capture_graphql_document(schema, TIMELINE, variables, actor=owner)
        one = capture_graphql_document(schema, TIMELINE, variables, actor=owner)
        assert len(one.data["timeline"]["entries"]) == 1
        for index in range(4):
            with actor_context(owner), open_changeset(fresh=True, change_reason=f"Rail budget edit {index}"):
                record.name = f"Rail budget edit {index}"
                record.save(update_fields=["name"])
        five = capture_graphql_document(schema, TIMELINE, variables, actor=owner)
    assert len(five.data["timeline"]["entries"]) == 5
    return one, five


def test_timeline_rail_document_budget(timeline_rail_reads) -> None:
    for read in timeline_rail_reads:
        read.assert_bytes(total=685_000, largest=300_000)
        # 62 statements on CI's full-profile host against 58 locally: headroom.
        read.assert_statements(68)
        read.assert_audit_inserts(7)


def test_timeline_rail_document_statement_count(timeline_rail_reads) -> None:
    one, five = timeline_rail_reads
    five.assert_flat_from(one)


@pytest.mark.xfail(strict=True, reason="P5: Timeline rail reads must not insert permission-audit events")
def test_timeline_rail_document_audit(timeline_rail_reads) -> None:
    for read in timeline_rail_reads:
        read.assert_no_audit_writes()


def test_related_permission_resource_list_budget(composed_tables: None, capture_graphql_document) -> None:
    """A Task list projects the related protected Project's permissions."""

    del composed_tables
    owner = create_user("document-budget-project")
    with system_context(reason="test.document_budget.project"):
        for index in range(20):
            project = Project.objects.create(title=f"Budget project {index}", owner=owner)
            Task.objects.create(title=f"Budget task {index}", owner=owner, project=project)
    schema = addon_schema(projects_schema.schemas, "console")
    document = """
        query RelatedPermissions($limit: Int!) {
          project_tasks(limit: $limit) {
            id project { permissions }
          }
        }
    """
    reads = {
        count: capture_graphql_document(schema, document, {"limit": count}, actor=owner) for count in (1, 5, 10, 20)
    }
    for count, read in reads.items():
        assert len(read.data["project_tasks"]) == count
        read.assert_bytes(
            total=56_000,
            largest=40_000,
            cause="REBAC L8 bounds the multi-row prefetch; the related permission proof is shared",
        )
        read.assert_statements(4)
        read.assert_no_audit_writes()
    for count in (5, 10, 20):
        reads[count].assert_flat_from(reads[1])


@strawberry_django.type(Project)
class FourPermissionProject(ProtectedNode):
    permissions = permissions_field(("read", "write", "share", "delete"))


@strawberry_django.type(Task)
class TaskWithFourPermissionProject(ProtectedNode):
    project: FourPermissionProject | None = actor_scoped_to_one("project")


@strawberry_django.type(Project)
class WriteScopedProject(ProtectedNode):
    permissions = permissions_field(("read", "write", "share", "delete"))

    @classmethod
    def get_queryset(cls, queryset: RebacQuerySet, info: object) -> RebacQuerySet:
        return queryset.with_action("write")


@strawberry_django.type(Task)
class TaskWithWriteScopedProject(ProtectedNode):
    project: WriteScopedProject | None = actor_scoped_to_one("project")


def test_related_type_hook_keeps_write_scope(composed_tables: None, capture_graphql_document) -> None:
    """A read-only project actor cannot bypass the related type's write hook."""

    del composed_tables
    owner = create_user("document-budget-write-hook-owner")
    reader = create_user("document-budget-write-hook-reader")
    with system_context(reason="test.document_budget.write_hook"):
        for index in range(3):
            project = Project.objects.create(title=f"Write-hook project {index}", owner=owner)
            Task.objects.create(title=f"Write-hook task {index}", owner=owner, project=project)
            write_relationships([RelationshipTuple(to_object_ref(project), "reader", to_subject_ref(reader))])
    resource = hasura_model_resource(
        TaskWithWriteScopedProject,
        model=Task,
        name="write_scoped_tasks",
        filterable=["id"],
        sortable=["id"],
        aggregatable=["id"],
        insert=False,
        update=False,
        delete=False,
    )
    schema = GraphQLSchemas([SchemaAddon({"public": {"query": (resource.query,)}})]).build("public")
    document = "query { write_scoped_tasks { id project { permissions } } }"
    owner_rows = capture_graphql_document(schema, document, {}, actor=owner).data["write_scoped_tasks"]
    reader_rows = capture_graphql_document(schema, document, {}, actor=reader).data["write_scoped_tasks"]
    assert len(owner_rows) == len(reader_rows) == 3
    assert all(row["project"] is not None for row in owner_rows)
    assert all(row["project"] is None for row in reader_rows)


def test_four_related_permission_names_reuse_proven_read(composed_tables: None, capture_graphql_document) -> None:
    """A related Project answers four named actions without a second read proof."""

    del composed_tables
    owner = create_user("document-budget-four-project")
    reader = create_user("document-budget-four-project-reader")
    with system_context(reason="test.document_budget.four_project"):
        for index in range(20):
            project = Project.objects.create(title=f"Four project {index}", owner=owner)
            Task.objects.create(title=f"Four task {index}", owner=owner, project=project)
            write_relationships([RelationshipTuple(to_object_ref(project), "reader", to_subject_ref(reader))])
    resource = hasura_model_resource(
        TaskWithFourPermissionProject,
        model=Task,
        name="four_permission_tasks",
        filterable=["id"],
        sortable=["id"],
        aggregatable=["id"],
        insert=False,
        update=False,
        delete=False,
    )
    schema = GraphQLSchemas([SchemaAddon({"public": {"query": (resource.query,)}})]).build("public")
    document = """
        query FourRelatedPermissions($limit: Int!) {
          four_permission_tasks(limit: $limit) { id project { permissions } }
        }
    """
    reads = {
        count: capture_graphql_document(schema, document, {"limit": count}, actor=owner) for count in (1, 5, 10, 20)
    }
    for count, read in reads.items():
        read.assert_bytes(total=38_000, largest=21_000, cause="one bounded related permission closure")
        read.assert_statements(4)
        read.assert_no_audit_writes()
        assert len(read.data["four_permission_tasks"]) == count
        assert all(
            row["project"]["permissions"] == ["delete", "read", "share", "write"]
            for row in read.data["four_permission_tasks"]
        )
        assert sum(len(re.findall(r'"rebac_scope_[0-9a-f]+_closure" AS \(', sql)) for sql in read.statements) == 1
    for count in (5, 10, 20):
        reads[count].assert_flat_from(reads[1])
    reader_rows = capture_graphql_document(schema, document, {"limit": 5}, actor=reader)
    assert [row["project"]["permissions"] for row in reader_rows.data["four_permission_tasks"]] == [["read"]] * 5
    reader_rows.assert_no_audit_writes()


def test_recursive_permission_policy_keeps_inline_proofs(composed_tables: None) -> None:
    """A recursive page policy cannot use the multi-action closure."""

    del composed_tables
    actor = create_user("document-budget-recursive-page")
    rows = (
        Page.objects.with_actor(actor)
        .with_action("read_granted")
        .filter(pk__in=(1,))
        .annotate(can_write=permission_expression(Page, "write_granted", actor=actor))
    )
    scope = (
        Page.objects.with_actor(actor)
        .with_action("read_granted")
        .filter(pk__in=(1,))
        .scope_cte(actions=("read_granted", "write_granted"))
    )
    query = scope.attach(rows)
    assert "_closure" not in str(query.query)
    list(query.values_list("pk", flat=True))


def test_unoptimized_permission_fallback_uses_one_unbypassed_proof(composed_tables: None) -> None:
    """A mutation-style row keeps its permission values without an audit write."""

    del composed_tables
    actor = create_user("document-budget-unoptimized-project")
    with system_context(reason="test.document_budget.unoptimized_project"):
        project = Project.objects.create(title="Unoptimized project", owner=actor)
    with actor_context(actor), CaptureQueriesContext(connection) as queries:
        held = held_permissions(project, ("read", "write", "share", "delete"))
    assert held == {"read", "write", "share", "delete"}
    assert len(queries) == 6
    assert sum(len(query["sql"].encode("utf-8")) for query in queries) <= 18_000
    assert not any(is_permission_audit_insert(query["sql"], connection) for query in queries)
    assert sum(len(re.findall(r'"rebac_scope_[0-9a-f]+_closure" AS \(', query["sql"])) for query in queries) == 1
