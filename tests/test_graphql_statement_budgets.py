"""Document-level SQL budgets for actor-bound page reads."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.agents import schema as agents_schema
from angee.agents.testing.models import Agent, AgentSession
from angee.base.models.identity import public_id_of
from angee.decisions import schema as decisions_schema
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.iam import schema as iam_schema
from angee.integrate import schema as integrate_schema
from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink
from angee.integrate_vcs import schema as integrate_vcs_schema
from angee.knowledge import schema as knowledge_schema
from angee.messaging.testing.models import Channel
from angee.messaging.testing.models import Party as PartyModel
from angee.parties import schema as parties_schema
from angee.projects import schema as projects_schema
from angee.projects.testing.models import Milestone, Project, Task
from angee.provenance.context import changeset as open_changeset
from angee.timeline import schema as timeline_schema
from tests.conftest import (
    Page,
    RecordBinding,
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
KNOWLEDGE_DOCUMENTS = ROOT / "src/angee/knowledge/web/src/data/documents.ts"
KNOWLEDGE_PAGES = authored_graphql_document(KNOWLEDGE_DOCUMENTS, "KnowledgeRecordPages")
KNOWLEDGE_NOTES = authored_graphql_document(KNOWLEDGE_DOCUMENTS, "KnowledgeRecordNotes")


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
        assert read.statement_count == 7, (count, read.statement_count)
        read.assert_no_audit_writes()
    empty.assert_no_audit_writes()


def test_record_decisions_document_statement_count(record_decisions_reads) -> None:
    _, _, answered = record_decisions_reads
    one, five, ten, twenty = (answered[count] for count in (1, 5, 10, 20))
    assert five.statement_count == one.statement_count == 7
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
    # Concrete integration reads remain per link until P12 batches them.
    # Suite context and the full-profile host add one statement per link.
    one.assert_bytes(total=139_500, largest=20_900, cause="REBAC L2 candidate-root proof on one link")
    one.assert_statements(34)
    one.assert_no_audit_writes()
    five.assert_bytes(total=525_000, largest=20_900, cause="REBAC L2 candidate-root proof per unbatched link")
    five.assert_statements(110)
    five.assert_no_audit_writes()
    ten.assert_statements(205)
    twenty.assert_statements(395)


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
        read.assert_bytes(
            total=655_000, largest=276_000,
            cause="shared union definitions; the requester CTE carries no scope",
        )
        # The full-profile host adds about four statements to the local rail.
        read.assert_statements(60)
        read.assert_audit_inserts(2)


def test_timeline_rail_document_statement_count(timeline_rail_reads) -> None:
    one, five = timeline_rail_reads
    five.assert_flat_from(one)


@pytest.mark.xfail(strict=True, reason="Provenance ChangeKind: record history still audits")
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
            total=70_000,
            largest=40_000,
            cause="REBAC L8 shared actor-set proof for one and many candidate rows",
        )
        assert read.statement_count == 4, (count, read.statement_count)
        read.assert_no_audit_writes()
    assert reads[5].statement_count == reads[1].statement_count == 4
    reads[10].assert_flat_from(reads[5])
    reads[20].assert_flat_from(reads[10])


def test_record_labels_have_no_bypass_audits(composed_tables: None, capture_graphql_document) -> None:
    """Selected label scalars add no read or audit per returned record."""

    del composed_tables
    owner = create_user("document-budget-labels")
    vault = vault_for(owner)
    for index in range(5):
        with actor_context(owner):
            Page.objects.create_in(vault, title=f"Budget label {index}")
    schema = addon_schema(knowledge_schema.schemas, "public")
    document = """
        query RecordLabels($limit: Int!) {
          pages(limit: $limit) { id created_by_label updated_by_label }
        }
    """
    one = capture_graphql_document(schema, document, {"limit": 1}, actor=owner)
    five = capture_graphql_document(schema, document, {"limit": 5}, actor=owner)
    assert len(one.data["pages"]) == 1
    assert len(five.data["pages"]) == 5
    for read in (one, five):
        assert all(row["created_by_label"] == owner.username for row in read.data["pages"])
        assert all(row["updated_by_label"] == owner.username for row in read.data["pages"])
        read.assert_bytes(total=22_000, largest=20_000)
        read.assert_statements(3)
        read.assert_no_audit_writes()
    five.assert_flat_from(one)


def test_relation_sort_and_filter_aliases_have_no_bypass_audits(
    composed_tables: None, capture_graphql_document,
) -> None:
    """Relation aliases retain the related Project read guard at one and five rows."""

    del composed_tables
    owner = create_user("document-budget-relation-alias")
    with system_context(reason="test.document_budget.relation_alias"):
        project = Project.objects.create(title="Budget alias project", owner=owner)
        for index in range(5):
            Task.objects.create(title=f"Budget alias task {index}", owner=owner, project=project)
    schema = addon_schema(projects_schema.schemas, "console")
    document = """
        query RelationAliases($limit: Int!) {
          project_tasks(
            limit: $limit,
            where: {project: {_is_null: false}},
            order_by: [{project: asc}]
          ) { id }
        }
    """
    one = capture_graphql_document(schema, document, {"limit": 1}, actor=owner)
    five = capture_graphql_document(schema, document, {"limit": 5}, actor=owner)
    assert len(one.data["project_tasks"]) == 1
    assert len(five.data["project_tasks"]) == 5
    for read in (one, five):
        read.assert_bytes(total=65_100, largest=63_200)
        read.assert_statements(3)
        read.assert_no_audit_writes()
    five.assert_flat_from(one)


def test_decision_concern_labels_batch_across_the_list(
    composed_tables: None,
    capture_graphql_document,
) -> None:
    del composed_tables
    owner = create_user("document-budget-concern-labels")
    schema = addon_schema(decisions_schema.schemas, "console")
    document = "query Concerns($limit: Int!) { decisions(limit: $limit) { id concerns_label } }"
    reads = []
    records = []
    for index in range(5):
        record = vault_for(owner, name=f"Concern {index}")
        records.append(record)
        Decision.objects.ask(
            DecisionRequest(
                kind=f"concern_{index}",
                records=(record,),
                requester=owner,
                assignees=(),
                proposal=DecisionProposal(alternatives=[{"key": "accept", "label": "Accept", "outcome": "done"}]),
            ),
            actor=owner,
        )
        if index in (0, 4):
            read = capture_graphql_document(schema, document, {"limit": index + 1}, actor=owner)
            assert len(read.data["decisions"]) == index + 1
            assert {row["concerns_label"] for row in read.data["decisions"]} == {str(item) for item in records}
            reads.append(read)
    one, five = reads
    five.assert_flat_from(one)
    for read in (one, five):
        read.assert_statements(7)
        read.assert_bytes(total=55_000, largest=45_000)
        read.assert_no_audit_writes()


def test_project_node_display_name_stays_flat_at_one_and_five(
    composed_tables: None,
    capture_graphql_document,
) -> None:
    del composed_tables
    owner = create_user("document-budget-node-label")
    with system_context(reason="test.document_budget.node_label"):
        projects = [Project.objects.create(title=f"Project label {index}", owner=owner) for index in range(5)]
    schema = addon_schema(projects_schema.schemas, "console")
    document = "query Labels($limit: Int!) { projects(limit: $limit) { id display_name } }"
    one = capture_graphql_document(schema, document, {"limit": 1}, actor=owner)
    five = capture_graphql_document(schema, document, {"limit": 5}, actor=owner)
    assert [row["display_name"] for row in one.data["projects"]] == [projects[0].title]
    assert [row["display_name"] for row in five.data["projects"]] == [project.title for project in projects]
    five.assert_flat_from(one)
    for read in (one, five):
        read.assert_statements(4)
        read.assert_bytes(total=15_000, largest=10_000)
        read.assert_no_audit_writes()


def test_knowledge_binding_roots_batch_selected_pages(
    composed_permissions: None,
    capture_graphql_document,
) -> None:
    del composed_permissions
    owner = create_user("document-budget-knowledge-bindings")
    source = vault_for(owner, name="Knowledge source")
    targets = [vault_for(owner, name=f"Knowledge target {index}") for index in range(5)]
    schema = addon_schema(knowledge_schema.schemas, "console")
    documents = {
        "record": """query Bindings($id: ID!) {
          record_knowledge_bindings(model_label: "knowledge.Vault", record_id: $id, role: "notes") {
            id page_title
          }
        }""",
        "page": """query Bindings($id: ID!) {
          page_record_bindings(page: $id, role: "notes") {
            id page_title model_label record_id
          }
        }""",
        "vault": """query Bindings($id: ID!) {
          vault_record_bindings(vault: $id, role: "reference") {
            id page_title model_label record_id
          }
        }""",
    }
    roots = {"record": "record_knowledge_bindings", "page": "page_record_bindings", "vault": "vault_record_bindings"}
    reads = {name: [] for name in documents}
    first_page = None
    for index, target in enumerate(targets):
        with actor_context(owner):
            page = Page.objects.create_in(source, title=f"Binding page {index}")
            RecordBinding.objects.upsert(page=page, target=targets[0], role="notes")
            RecordBinding.objects.upsert(vault=source, target=target, role="reference")
            if first_page is None:
                first_page = page
            else:
                RecordBinding.objects.upsert(page=first_page, target=target, role="notes")
        if index in (0, 4):
            ids = {"record": targets[0].sqid, "page": first_page.sqid, "vault": source.sqid}
            for name, document in documents.items():
                read = capture_graphql_document(schema, document, {"id": str(ids[name])}, actor=owner)
                assert len(read.data[roots[name]]) == index + 1
                reads[name].append(read)
    for one, five in reads.values():
        five.assert_flat_from(one)
        for read in (one, five):
            read.assert_statements(5)
            read.assert_bytes(total=225_000, largest=140_000)
            read.assert_no_audit_writes()


def test_node_labels_batch_stored_deferred_and_related_sources(
    composed_tables: None,
    capture_graphql_document,
) -> None:
    del composed_tables
    owner = create_user("document-budget-label-sources")
    with system_context(reason="test.document_budget.label_sources"):
        project = Project.objects.create(title="Milestone parent", owner=owner)
        parties = [PartyModel.objects.create(display_name=f"Party {index}", created_by=owner) for index in range(5)]
        milestones = [Milestone.objects.create(project=project, name=f"Phase {index}") for index in range(5)]
        agent = Agent.objects.create(name="Assistant", owner=owner)
        sessions = [AgentSession.objects.create(agent=agent, user=owner) for _ in range(5)]
    cases = (
        (parties_schema.schemas, "parties", [str(row) for row in parties], 5),
        (projects_schema.schemas, "project_milestones", [str(row) for row in milestones], 6),
        (agents_schema.schemas, "agent_sessions", [str(row) for row in sessions], 7),
    )
    for schemas, root, expected, statement_limit in cases:
        schema = addon_schema(schemas, "console")
        document = f"query Labels($limit: Int!) {{ {root}(limit: $limit) {{ id display_name }} }}"
        one = capture_graphql_document(schema, document, {"limit": 1}, actor=owner)
        five = capture_graphql_document(schema, document, {"limit": 5}, actor=owner)
        assert len(one.data[root]) == 1
        assert one.data[root][0]["display_name"] in expected
        assert sorted(row["display_name"] for row in five.data[root]) == sorted(expected)
        five.assert_flat_from(one)
        for read in (one, five):
            read.assert_statements(statement_limit)
            read.assert_bytes(total=25_000, largest=20_000)
            read.assert_no_audit_writes()


def test_agent_session_label_falls_back_when_agent_is_unreadable(
    composed_tables: None,
    capture_graphql_document,
) -> None:
    """A caller can read their sessions without reading the shared agent."""

    del composed_tables
    owner = create_user("document-budget-shared-agent-owner")
    caller = create_user("document-budget-shared-agent-caller")
    with system_context(reason="test.document_budget.shared_agent"):
        agent = Agent.objects.create(name="Shared assistant", owner=owner)
        write_relationships([RelationshipTuple(to_object_ref(agent), "caller", to_subject_ref(caller))])
        untitled = AgentSession.objects.create(agent=agent, user=caller)
        titled = AgentSession.objects.create(agent=agent, user=caller, title="Quarterly close chat")
    assert not agent.with_actor(caller).has_access("read")
    schema = addon_schema(agents_schema.schemas, "console")
    document = "{ agent_sessions { id display_name } }"
    read = capture_graphql_document(schema, document, {}, actor=caller)
    assert {row["id"]: row["display_name"] for row in read.data["agent_sessions"]} == {
        str(untitled.sqid): "agent session",
        str(titled.sqid): "Quarterly close chat",
    }
    read.assert_no_audit_writes()


@pytest.fixture
def knowledge_authored_reads(composed_permissions: None, capture_graphql_document):
    """Read the authored pane at 1/5/10 and the notes at 1/5/10/20 bindings."""

    del composed_permissions
    owner = create_user("document-budget-knowledge-authored")
    source = vault_for(owner, name="Knowledge authored source")
    record = vault_for(owner, name="Knowledge authored record")
    schema = addon_schema(knowledge_schema.schemas, "console")
    reads = {"pages": [], "notes": []}
    for index in range(20):
        with actor_context(owner):
            if index < 10:
                page = Page.objects.create_in(source, title=f"Related page {index}")
                RecordBinding.objects.upsert(page=page, target=record, role="related")
            RecordBinding.objects.create_note(target=record, vault=source, body=f"Note {index}", role="notes")
        if index in (0, 4, 9, 19):
            for name, document, role in (
                ("pages", KNOWLEDGE_PAGES, "related"),
                ("notes", KNOWLEDGE_NOTES, "notes"),
            ):
                if index == 19 and name == "pages":
                    continue
                read = capture_graphql_document(
                    schema,
                    document,
                    {"modelLabel": record._meta.label, "recordId": str(record.sqid), "role": role},
                    actor=owner,
                )
                assert len(read.data["record_knowledge_bindings"]) == index + 1
                reads[name].append(read)
    return reads


def test_knowledge_authored_documents(knowledge_authored_reads) -> None:
    pages = knowledge_authored_reads["pages"]
    notes = knowledge_authored_reads["notes"]
    for read, statements, total in zip(pages, (9, 13, 18), (360_000, 580_000, 850_000), strict=True):
        read.assert_statements(statements)
        read.assert_bytes(total=total, largest=140_000)
        read.assert_no_audit_writes()
    for read in notes:
        read.assert_statements(10)
        read.assert_bytes(total=420_000, largest=140_000)
        read.assert_no_audit_writes()
    for read in notes[1:]:
        read.assert_flat_from(notes[0])


@pytest.mark.xfail(strict=True, reason="P2b: page_can_write checks the related page once per row")
def test_knowledge_record_pages_page_write_growth(knowledge_authored_reads) -> None:
    knowledge_authored_reads["pages"][1].assert_flat_from(knowledge_authored_reads["pages"][0])
