"""Document-level SQL budgets for actor-bound page reads."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings
from rebac import actor_context, system_context

from angee.base.models.identity import public_id_of
from angee.decisions import schema as decisions_schema
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.iam import schema as iam_schema
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
    """Execute the authored pane empty, then open and answered at one and five."""

    del composed_tables
    owner = create_user("document-budget-decisions")
    record = vault_for(owner)
    schema = addon_schema(decisions_schema.schemas, "console")
    variables = {"model": record._meta.label, "id": public_id_of(record)}
    empty = capture_graphql_document(schema, DECISIONS, variables, actor=owner)
    assert empty.data["decision_records"] == []

    open_one = None
    one = None
    for index in range(5):
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
        if index == 0:
            one = capture_graphql_document(schema, DECISIONS, variables, actor=owner)
            assert len(one.data["decision_records"]) == 1
    five = capture_graphql_document(schema, DECISIONS, variables, actor=owner)
    assert len(five.data["decision_records"]) == 5
    assert all(row["decision"]["answered_by"]["display_name"] for row in five.data["decision_records"])
    assert all(row["decision"]["verdict_label"] == "Withdrawn" for row in five.data["decision_records"])
    return empty, open_one, one, five


def test_record_decisions_document_budget(record_decisions_reads) -> None:
    empty, open_one, one, five = record_decisions_reads
    # An empty result still carries one bounded target proof since lane P1
    # (36,252 bytes today); the library's bounded identity contract (L2) lowers it.
    empty.assert_bytes(total=40_000, largest=35_000)
    empty.assert_statements(4)
    open_one.assert_bytes(total=90_000, largest=80_000)
    open_one.assert_statements(6)
    open_one.assert_no_audit_writes()
    for read in (one, five):
        read.assert_bytes(total=120_000, largest=80_000)
        read.assert_statements(13)
        read.assert_no_audit_writes()
    empty.assert_no_audit_writes()


def test_record_decisions_document_statement_count(record_decisions_reads) -> None:
    _, _, one, five = record_decisions_reads
    five.assert_flat_from(one)


@pytest.fixture
def record_sources_reads(composed_tables: None, capture_graphql_document):
    """Execute the authored pane at one and five visible record links."""

    del composed_tables
    bridge = make_integration("document-budget-sources", model=Channel)
    schema = addon_schema(iam_schema.schemas, "console", integrate_schema.schemas, integrate_vcs_schema.schemas)
    with system_context(reason="test.document_budget.sources"):
        stream = current_stream(bridge, "records", kind=StreamKind.RECORD_REPLICA)
        RecordLink.objects.observe(stream, "record:1", target=stream)
    variables = {"modelLabel": stream._meta.label, "recordId": public_id_of(stream)}
    one = capture_graphql_document(schema, SOURCES, variables, actor=bridge.owner)
    assert len(one.data["record_links"]) == 1
    with system_context(reason="test.document_budget.sources.more"):
        for index in range(2, 6):
            RecordLink.objects.observe(stream, f"record:{index}", target=stream)
    five = capture_graphql_document(schema, SOURCES, variables, actor=bridge.owner)
    assert len(five.data["record_links"]) == 5
    return one, five


def test_record_sources_document_budget(record_sources_reads) -> None:
    one, five = record_sources_reads
    one.assert_bytes(total=150_000, largest=25_000)
    # CI's full-profile test host composes more addons than a local checkout
    # (41 statements there against 39 here); the ceiling carries that headroom,
    # the flatness pin below is the strict one.
    one.assert_statements(45)
    one.assert_audit_inserts(8)
    five.assert_bytes(total=450_000, largest=25_000)
    five.assert_statements(139)
    five.assert_audit_inserts(40)


@pytest.mark.xfail(strict=True, reason="P12/P6: Sources must batch concrete integrations across links")
def test_record_sources_document_statement_count(record_sources_reads) -> None:
    one, five = record_sources_reads
    five.assert_flat_from(one)


@pytest.mark.xfail(strict=True, reason="P5: Sources reads must not insert permission-audit events")
def test_record_sources_document_audit(record_sources_reads) -> None:
    for read in record_sources_reads:
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
        read.assert_bytes(total=750_000, largest=400_000)
        # 64 statements on CI's full-profile host against 60 locally: headroom.
        read.assert_statements(70)
        read.assert_audit_inserts(8)


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
        for index in range(5):
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
    one = capture_graphql_document(schema, document, {"limit": 1}, actor=owner)
    five = capture_graphql_document(schema, document, {"limit": 5}, actor=owner)
    assert len(one.data["project_tasks"]) == 1
    assert len(five.data["project_tasks"]) == 5
    one.assert_bytes(total=70_000, largest=40_000)
    five.assert_bytes(total=70_000, largest=40_000)
    one.assert_statements(6)
    five.assert_statements(6)
    five.assert_flat_from(one)
    one.assert_no_audit_writes()
    five.assert_no_audit_writes()
