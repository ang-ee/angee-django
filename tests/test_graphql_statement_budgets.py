"""Document-level SQL budgets for actor-bound page reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import strawberry_django
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext, isolate_apps
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

from angee.agents import schema as agents_schema
from angee.agents.testing.models import Agent, AgentSession
from angee.base.lineage import concrete_child_models
from angee.base.models.identity import public_id_of
from angee.base.signals import mute_changes
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
from angee.knowledge import schema as knowledge_schema
from angee.messaging import schema as messaging_schema
from angee.messaging.testing.models import Channel, Message, Part
from angee.messaging.testing.models import Party as PartyModel
from angee.nexus import schema as nexus_schema
from angee.parties import schema as parties_schema
from angee.posts import schema as posts_schema
from angee.projects import schema as projects_schema
from angee.projects.testing.models import Milestone, Project, Task
from angee.provenance.context import changeset as open_changeset
from angee.storage import schema as storage_schema
from angee.tags import schema as tags_schema
from angee.tags.testing.models import Tag, TagAssignment
from angee.timeline import schema as timeline_schema
from angee.workflows.testing.drivers import load_workflow
from angee.workflows.testing.models import WorkflowRun
from tests import test_messaging as messaging_models
from tests.conftest import (
    Backend,
    Drive,
    Feed,
    File,
    Integration,
    MimeType,
    Page,
    RecordBinding,
    SchemaAddon,
    addon_schema,
    authored_graphql_document,
    create_platform_admin,
    create_user,
    current_stream,
    make_integration,
    vault_for,
)
from tests.conftest import File as StorageFile
from tests.messaging_campaign import comment_record as comment_record  # noqa: F401 -- fixture
from tests.messaging_campaign import make_user
from tests.test_messaging_graphql import _storage_drive
from tests.test_timeline import KINDS
from tests.workflow_steps import document as workflow_document

pytestmark = [pytest.mark.postgresql, pytest.mark.django_db(transaction=True)]

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = authored_graphql_document(
    ROOT / "src/angee/decisions/web/src/documents.console.ts", "RecordDecisionsDocument"
)
SOURCES = authored_graphql_document(ROOT / "src/angee/integrate/web/src/documents.ts", "RecordSourcesDocument")
RECORD_THREAD = "\n".join(
    authored_graphql_document(ROOT / "src/angee/messaging/web/src/documents.ts", name)
    for name in ("RecordThreadSummaryFields", "RecordFollowerFields", "RecordThreadDocument")
)
TIMELINE = authored_graphql_document(ROOT / "src/angee/timeline/web/src/documents.console.ts", "TimelineDocument")
KNOWLEDGE_DOCUMENTS = ROOT / "src/angee/knowledge/web/src/data/documents.ts"
KNOWLEDGE_PAGES = authored_graphql_document(KNOWLEDGE_DOCUMENTS, "KnowledgeRecordPages")
KNOWLEDGE_NOTES = authored_graphql_document(KNOWLEDGE_DOCUMENTS, "KnowledgeRecordNotes")
MESSAGING_DOCUMENTS = ROOT / "src/angee/messaging/web/src/documents.ts"
NEXUS_DOCUMENTS = ROOT / "src/angee/nexus/web/src/inbox/documents.ts"
MESSAGE_FRAGMENTS = "\n".join(
    authored_graphql_document(path, name)
    for path, name in (
        (NEXUS_DOCUMENTS, "InboxMessageFields"),
        (MESSAGING_DOCUMENTS, "MessageSenderFields"),
        (MESSAGING_DOCUMENTS, "MessageReplyFields"),
        (MESSAGING_DOCUMENTS, "MessagePartFields"),
        (MESSAGING_DOCUMENTS, "MessageReaderFields"),
    )
)
NEXUS_MESSAGE = authored_graphql_document(NEXUS_DOCUMENTS, "InboxMessage") + MESSAGE_FRAGMENTS
MESSAGE_USES = authored_graphql_document(NEXUS_DOCUMENTS, "InboxMessageUses")
SOURCE_COPIES = "\n".join((
    authored_graphql_document(MESSAGING_DOCUMENTS, "MessageSourceCopiesDocument"),
    authored_graphql_document(MESSAGING_DOCUMENTS, "TranscriptMessageFields"),
    authored_graphql_document(MESSAGING_DOCUMENTS, "ReactionGroupFields"),
    *(authored_graphql_document(MESSAGING_DOCUMENTS, name) for name in (
        "MessageSenderFields", "MessageReplyFields", "MessagePartFields",
    )),
))


def test_message_part_document_budgets(composed_tables: None, capture_graphql_document, tmp_path) -> None:
    """The authored message and source-copies reads stay flat as MIME parts grow."""

    del composed_tables
    owner, outsider = make_user("message-budget-owner"), make_user("message-budget-outsider")
    channel = make_integration("message-budget", model=Channel, owner=owner)
    with system_context(reason="test.document_budget.message"):
        drive = _storage_drive(tmp_path, owner=owner)
        message = Message.objects.create(channel=channel, created_by=owner, status="synced")
        hidden_message = Message.objects.create(created_by=outsider)
        hidden_parent = Part.objects.create(message=hidden_message, position=0)
        files = [
            File.objects.create(
                drive=drive, owner=owner if index < 3 else outsider,
                visibility="inherited" if index < 3 else "record",
                filename=f"budget-{index}.txt", content_hash=f"{index + 1:064x}",
            )
            for index in range(4)
        ]
    schema = addon_schema(messaging_schema.schemas, "console", nexus_schema.schemas)
    message_variables = {"id": str(message.sqid), "sender": "", "circle": ""}
    reads = {}
    copies = {}
    previous = None
    for index in range(40):
        with system_context(reason="test.document_budget.message.part"):
            part = Part.objects.create(
                message=message, parent=previous,
                position=index, created_by=owner,
                file=files[{4: 0, 11: 1, 39: 2}[index]] if index in (4, 11, 39) else None,
            )
        previous = part
        if index + 1 in (1, 5, 12, 40):
            count = index + 1
            reads[count] = capture_graphql_document(schema, NEXUS_MESSAGE, message_variables, actor=owner)
            copies[count] = capture_graphql_document(
                schema, SOURCE_COPIES, {"id": str(message.sqid)}, actor=owner,
            )
            assert len(reads[count].data["inbox_message"]["message"]["parts"]) == count
            assert len(copies[count].data["message_source_copies"][0]["parts"]) == count

    for count in (5, 12, 40):
        reads[count].assert_flat_from(reads[1])
        copies[count].assert_flat_from(copies[5])
    for read in reads.values():
        # The merged messages resolver reads the parts once for the document.
        assert read.statement_count == 15
        read.assert_bytes(total=450_000, largest=135_000, cause="bounded actor-scoped part and file reads")
        read.assert_no_audit_writes()
    for count, read in copies.items():
        assert read.statement_count == (12 if count == 1 else 13)
        read.assert_bytes(total=310_000, largest=85_000, cause="batched parts and present files")
        read.assert_no_audit_writes()

    with system_context(reason="test.document_budget.message.hidden_part"):
        Part.objects.create(message=message, parent=hidden_parent, position=40, file=files[3], created_by=owner)
    hidden_read = capture_graphql_document(schema, NEXUS_MESSAGE, message_variables, actor=owner)
    assert hidden_read.statement_count == reads[40].statement_count + 1
    nested_parts = hidden_read.data["inbox_message"]["message"]["parts"]
    assert nested_parts[-1]["parent"] is None
    assert nested_parts[-1]["file"] is None
    assert [part["file"]["id"] for part in nested_parts if part["file"] is not None] == [
        str(file.sqid) for file in files[:3]
    ]
    baseline = capture_graphql_document(
        schema,
        "query Parts($id: String!) { parts(where: {message: {_eq: $id}}) { ...MessagePartFields } }\n"
        + authored_graphql_document(MESSAGING_DOCUMENTS, "MessagePartFields"),
        {"id": str(message.sqid)}, actor=owner,
    )
    baseline_by_id = {part["id"]: part for part in baseline.data["parts"]}
    expected_parts = [baseline_by_id[part["id"]] for part in nested_parts]
    assert json.dumps(nested_parts) == json.dumps(expected_parts)
    hidden_copies = capture_graphql_document(schema, SOURCE_COPIES, {"id": str(message.sqid)}, actor=owner)
    assert json.dumps(hidden_copies.data["message_source_copies"][0]["parts"]) == json.dumps(expected_parts)
    with system_context(reason="test.document_budget.message.shared_use"):
        second = Message.objects.create(channel=channel, created_by=owner, status="synced")
        Part.objects.create(message=message, file=files[0], created_by=owner)
        Part.objects.create(message=second, file=files[0], created_by=owner)
    shared_read = capture_graphql_document(schema, MESSAGE_USES, {"id": str(message.sqid)}, actor=owner)
    assert next(
        use["count"] for use in shared_read.data["inbox_message_uses"]
        if use["target"] == f"file:{files[0].sqid}"
    ) == 2


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
    empty.assert_bytes(total=33_000, largest=31_000, cause="bounded target read protects concern identity")
    empty.assert_statements(4)
    open_one.assert_bytes(total=37_000, largest=31_000, cause="bounded target read protects concern identity")
    open_one.assert_statements(5)
    open_one.assert_no_audit_writes()
    for count, read in answered.items():
        read.assert_bytes(total=44_000, largest=31_000, cause="target read and held_many field gates")
        assert read.statement_count == 7, (count, read.statement_count)
        read.assert_no_audit_writes()
    empty.assert_no_audit_writes()


def test_record_decisions_document_statement_count(record_decisions_reads) -> None:
    _, _, answered = record_decisions_reads
    one, five, ten, twenty = (answered[count] for count in (1, 5, 10, 20))
    assert five.statement_count == one.statement_count == 7
    ten.assert_flat_from(five)
    twenty.assert_flat_from(ten)


@pytest.fixture(params=(Channel, Feed), ids=("channel", "feed"))
def record_sources_reads(request, composed_tables: None, capture_graphql_document):
    """Execute the authored pane at one, five, ten and twenty links from distinct integrations."""

    del composed_tables
    return _record_sources_reads(capture_graphql_document, kind=request.param, slug="document-budget-sources")


def _record_sources_reads(capture_graphql_document, *, kind: type[Integration], slug: str):
    """Execute the authored pane for one integration kind under a distinct slug."""

    options = {"backend_class": "feed", "feed_backend_class": "stub"} if kind is Feed else {}
    bridge = make_integration(slug, model=kind, **options)
    schema = addon_schema(
        iam_schema.schemas, "console", integrate_schema.schemas, integrate_vcs_schema.schemas,
        posts_schema.schemas,
    )
    with system_context(reason="test.document_budget.sources"):
        stream = current_stream(bridge, "records", kind=StreamKind.RECORD_REPLICA)
        many_stream = current_stream(bridge, "records-many", kind=StreamKind.RECORD_REPLICA)
        RecordLink.objects.observe(stream, "record:1", target=stream)
        RecordLink.objects.observe(stream, "record:1-many", target=many_stream)
    one = capture_graphql_document(
        schema, SOURCES, {"modelLabel": stream._meta.label, "recordId": public_id_of(stream)}, actor=bridge.owner,
    )
    assert len(one.data["record_links"]) == 1
    # This schema exposes posts.Feed, while messaging.Channel is not composed here.
    expected_state = "AVAILABLE" if kind is Feed else "UNAVAILABLE"
    assert one.data["record_links"][0]["stream"]["integration"]["concrete_target"]["state"] == expected_state
    reads = {1: one}
    many = {"modelLabel": many_stream._meta.label, "recordId": public_id_of(many_stream)}
    for index in range(2, 21):
        with system_context(reason="test.document_budget.sources.more"):
            another = make_integration(
                f"{slug}-{index}", model=kind, owner=bridge.owner, **options
            )
            source_stream = current_stream(another, "records", kind=StreamKind.RECORD_REPLICA)
            RecordLink.objects.observe(source_stream, f"record:{index}", target=many_stream)
        if index in (5, 10, 20):
            read = capture_graphql_document(schema, SOURCES, many, actor=bridge.owner)
            assert len(read.data["record_links"]) == index
            assert all(row["source_label"] for row in read.data["record_links"])
            reads[index] = read
    return reads, schema, bridge.owner, kind


def test_record_sources_document_budget(record_sources_reads) -> None:
    reads, _, _, kind = record_sources_reads
    # CI's full-profile host composes more addons; retain two statements of headroom.
    maximum = 16 if kind is Feed else 15
    total_bytes = 70_000 if kind is Feed else 62_000
    for read in reads.values():
        read.assert_bytes(
            total=total_bytes, largest=16_000, cause="batched reads bind under 100 bytes of keys per link",
        )
        read.assert_statements(maximum)
        read.assert_no_audit_writes()


def test_record_sources_document_statement_count(record_sources_reads) -> None:
    one, five, ten, twenty = (record_sources_reads[0][count] for count in (1, 5, 10, 20))
    five.assert_flat_from(one)
    ten.assert_flat_from(five)
    twenty.assert_flat_from(ten)


def test_record_sources_document_audit(record_sources_reads) -> None:
    for read in record_sources_reads[0].values():
        read.assert_no_audit_writes()


def test_record_links_without_source_fields_stays_flat(record_sources_reads, capture_graphql_document) -> None:
    """An id-only link list never loads deferred stream ids per row."""

    _, schema, owner, _ = record_sources_reads
    document = "query Links($limit: Int!) { record_links(limit: $limit) { id external_key } }"
    one = capture_graphql_document(schema, document, {"limit": 1}, actor=owner)
    five = capture_graphql_document(schema, document, {"limit": 5}, actor=owner)
    assert len(one.data["record_links"]) == 1
    assert len(five.data["record_links"]) == 5
    five.assert_flat_from(one)
    one.assert_no_audit_writes()
    five.assert_no_audit_writes()


def test_record_link_source_fields_without_stream_stay_flat(record_sources_reads, capture_graphql_document) -> None:
    """Source labels and URLs batch even without the nested stream field."""

    _, schema, owner, _ = record_sources_reads
    document = "query Links($limit: Int!) { record_links(limit: $limit) { id source_label source_url } }"
    one = capture_graphql_document(schema, document, {"limit": 1}, actor=owner)
    five = capture_graphql_document(schema, document, {"limit": 5}, actor=owner)
    assert len(one.data["record_links"]) == 1
    assert len(five.data["record_links"]) == 5
    five.assert_flat_from(one)
    one.assert_no_audit_writes()
    five.assert_no_audit_writes()


def test_record_sources_document_fixed_budgets(record_sources_reads) -> None:
    # One page hook supplies each link's stream and connector; the Feed resource
    # adds one concrete-target read because this schema exposes posts.Feed.
    expected = 14 if record_sources_reads[3] is Feed else 13
    for read in record_sources_reads[0].values():
        assert read.statement_count == expected


def test_record_sources_ignore_registered_unused_concrete_kind(
    record_sources_reads, capture_graphql_document, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registering an unused Integration child adds no read to the Sources pane."""

    reads, _, _, kind = record_sources_reads
    with isolate_apps() as isolated:
        class UnusedIntegration(Integration):
            class Meta:
                app_label = "integrate"
                db_table = "test_unused_sources_integration"
                rebac_resource_type = "tests/unused_sources_integration"

        assert UnusedIntegration._meta.apps is isolated
        assert isolated.all_models["integrate"]["unusedintegration"] is UnusedIntegration

        # Isolated apps hide their models from the global parent. Register this
        # real empty child at the Integration owner's current discovery seam.
        children = Integration.concrete_child_models()
        with connection.schema_editor() as editor:
            editor.create_model(UnusedIntegration)
        try:
            def registered_children(parent):
                return (*children, UnusedIntegration) if parent is Integration else concrete_child_models(parent)

            with monkeypatch.context() as patch:
                patch.setattr(Integration, "concrete_child_models", classmethod(registered_children))
                extra_reads = _record_sources_reads(
                    capture_graphql_document, kind=kind, slug="document-budget-sources-extra"
                )[0]
        finally:
            with connection.schema_editor() as editor:
                editor.delete_model(UnusedIntegration)

    overhead = {}
    for size in (1, 5, 10, 20):
        # The integrity union gains a branch, but no additional read statement.
        expected = 14 if kind is Feed else 13
        assert extra_reads[size].statement_count == reads[size].statement_count == expected
        overhead[size] = extra_reads[size].total_sql_bytes - reads[size].total_sql_bytes
    # The empty branch binds at most 100 bytes of keys per additional link.
    assert overhead[20] - overhead[1] <= 19 * 100


def test_record_sources_document_byte_budget(record_sources_reads) -> None:
    kind = record_sources_reads[3]
    # The page hook removes the second stream read; SQLite reached 53,465
    # Channel and 61,175 Feed bytes at twenty links.
    total = 70_000 if kind is Feed else 62_000
    for read in record_sources_reads[0].values():
        read.assert_bytes(total=total, largest=16_000, cause="one batched source-label projection")


def test_integration_ids_stay_flat_without_concrete_target(record_sources_reads, capture_graphql_document) -> None:
    """An id-only integration list never loads deferred content-type ids per row."""

    _, schema, owner, _ = record_sources_reads
    document = "query Integrations($limit: Int!) { integrations(limit: $limit) { id } }"
    one = capture_graphql_document(schema, document, {"limit": 1}, actor=owner)
    five = capture_graphql_document(schema, document, {"limit": 5}, actor=owner)
    assert len(one.data["integrations"]) == 1
    assert len(five.data["integrations"]) == 5
    five.assert_flat_from(one)
    one.assert_no_audit_writes()
    five.assert_no_audit_writes()


def test_record_thread_document_file_part_growth(
    composed_tables: None, capture_graphql_document, tmp_path: Path,
) -> None:
    """The authored thread state read stays flat as its file attachments grow."""

    del composed_tables
    owner = create_user("document-budget-thread-owner")
    reader = create_user("document-budget-thread-reader")
    with system_context(reason="test.document_budget.thread"), mute_changes():
        backend = Backend._base_manager.create(
            slug="budget-local", label="Local", backend_class="local",
            backend_config={"root": str(tmp_path), "base_url": "/media/"},
        )
        MimeType._base_manager.get_or_create(
            mime_type="text/plain", defaults={"category": "text", "label": "Text"},
        )
        Drive._base_manager.create(backend=backend, slug="assets", name="Assets", prefix="assets", created_by=owner)
        record = Project.objects.create(title="Attachment budget", owner=owner)
        attachment = messaging_models.ThreadAttachment.objects.ensure_for_record(record)
        message = messaging_models.Message.objects.create(
            thread=attachment.thread, direction="internal", message_type="comment", created_by=owner,
        )

        def attach(index: int) -> None:
            file = StorageFile.objects.ingest_bytes(
                f"Attachment {index}".encode(), filename=f"budget-{index}.txt", drive_slug="assets",
            )
            messaging_models.Part.objects.create(
                message=message, position=index, type="text/plain", disposition="attachment", file=file,
                created_by=owner,
            )

        attach(1)
    record.with_actor(owner).grant_record_access("reader", reader)
    with actor_context(reader):
        record.message_subscribe(user=reader)
    with system_context(reason="test.document_budget.thread.followers"), mute_changes():
        for index in range(19):
            party = messaging_models.Party.objects.create(display_name=f"Follower {index}", created_by=owner)
            messaging_models.ThreadFollower.objects.create(
                thread=attachment.thread, party=party, created_by=owner,
            )
    schema = addon_schema(
        iam_schema.schemas, "console", integrate_schema.schemas, parties_schema.schemas, messaging_schema.schemas,
        projects_schema.schemas,
    )
    variables = {"modelLabel": record._meta.label, "recordId": public_id_of(record)}
    with CaptureQueriesContext(connection) as captured:
        one = capture_graphql_document(schema, RECORD_THREAD, variables, actor=reader)
    attachment_table = connection.ops.quote_name(messaging_models.ThreadAttachment._meta.db_table)
    edge_reads = [
        query for query in captured
        if query["sql"].startswith(f"SELECT {attachment_table}.") and f"FROM {attachment_table}" in query["sql"]
    ]
    assert len(edge_reads) == 1
    assert one.data["record_thread"]["attachment_count"] == 1
    assert one.data["record_thread"]["followers"]
    with system_context(reason="test.document_budget.thread.more"), mute_changes():
        for index in range(2, 6):
            attach(index)
    five = capture_graphql_document(schema, RECORD_THREAD, variables, actor=reader)
    assert five.data["record_thread"]["attachment_count"] == 5
    with system_context(reason="test.document_budget.thread.ten"), mute_changes():
        for index in range(6, 11):
            attach(index)
    ten = capture_graphql_document(schema, RECORD_THREAD, variables, actor=reader)
    assert ten.data["record_thread"]["attachment_count"] == 10
    transcript = capture_graphql_document(
        schema,
        """query ThreadParts($modelLabel: String!, $recordId: ID!) {
          record_thread(input: {model_label: $modelLabel, record_id: $recordId}) {
            attachment_count messages { parts { id disposition } }
          }
        }""",
        variables,
        actor=reader,
    ).data["record_thread"]
    assert transcript["attachment_count"] == sum(
        part["disposition"] == "ATTACHMENT"
        for message in transcript["messages"] for part in message["parts"]
    )
    five.assert_flat_from(one)
    ten.assert_flat_from(five)
    # The admitted record owns chatter, follower and notification display facts;
    # those reads have no separate bypass audit. Structural follower and avatar
    # reads keep a fixed overhead independent of the attachment count.
    for read in (one, five, ten):
        read.assert_statements(46)
        read.assert_bytes(
            total=150_000, largest=30_000, cause="the read requirement in the messaging held_permissions check",
        )
        read.assert_audit_inserts(8)


def test_fifty_party_avatars_and_tags_stay_bounded(
    composed_tables: None, composed_permissions: None, capture_graphql_document, tmp_path: Path,
) -> None:
    """Display facts on an admitted party list do not expand file or edge policy per row."""

    del composed_tables, composed_permissions
    owner = create_user("document-budget-party-display")
    with system_context(reason="test.document_budget.party_display"), mute_changes():
        backend = Backend._base_manager.create(
            slug="budget-party", label="Local", backend_class="local",
            backend_config={"root": str(tmp_path), "base_url": "/media/"},
        )
        drive = Drive._base_manager.create(
            backend=backend, slug="party-assets", name="Party assets", prefix="party-assets", created_by=owner,
        )
        avatar = File._base_manager.create(
            drive=drive, filename="avatar.png", content_hash="a" * 64, owner=owner,
            upload_state="ready", created_by=owner,
        )
        tag = Tag._base_manager.create(name="Shown", color="#00f")
        parties = [
            PartyModel._base_manager.create(
                display_name=f"Party {index:02}", avatar=avatar, owner=owner, created_by=owner,
            )
            for index in range(50)
        ]
    with actor_context(owner):
        for party in parties:
            TagAssignment.objects.attach("parties/party", party.sqid, [tag.sqid])
    schema = addon_schema(
        iam_schema.schemas, "console", parties_schema.schemas, storage_schema.schemas, tags_schema.schemas,
    )
    query = """query PartyDisplay($limit: Int!) {
      parties(limit: $limit, order_by: [{display_name: asc}]) {
        id avatar { id url } display_avatar { id url } tags { name }
      }
    }"""
    one = capture_graphql_document(schema, query, {"limit": 1}, actor=owner)
    fifty = capture_graphql_document(schema, query, {"limit": 50}, actor=owner)
    assert len(fifty.data["parties"]) == 50
    assert all(row["avatar"] and row["display_avatar"] and row["tags"] for row in fifty.data["parties"])
    fifty.assert_flat_from(one)
    for read in (one, fifty):
        read.assert_bytes(total=40_000, largest=32_000)
        read.assert_audit_inserts(3)


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


@pytest.mark.django_db(transaction=True)
def test_timeline_rail_document_budget(timeline_rail_reads) -> None:
    for read in timeline_rail_reads:
        read.assert_bytes(
            total=655_000, largest=276_000,
            cause="shared union definitions; the requester CTE carries no scope",
        )
        # The full-profile host adds about four statements to the local rail.
        read.assert_statements(60)
        read.assert_no_audit_writes()


@pytest.mark.django_db(transaction=True)
def test_timeline_rail_document_statement_count(timeline_rail_reads) -> None:
    one, five = timeline_rail_reads
    five.assert_flat_from(one)


@pytest.mark.django_db(transaction=True)
def test_timeline_run_document_requires_run_read_for_linked_status(
    composed_tables: None, workflow_step_classes: None, capture_graphql_document,
) -> None:
    """The authored rail withholds linked runs until their own read gate admits them."""

    del composed_tables, workflow_step_classes
    owner = create_platform_admin("document-budget-run-owner")
    viewer = create_user("document-budget-run-viewer")
    record = vault_for(owner)
    write_relationships([RelationshipTuple(
        resource=to_object_ref(record), relation="viewer", subject=to_subject_ref(viewer),
    )])
    workflow = load_workflow(
        workflow_document("entry"), key="rail-run-budget", actor=owner, subject_model=record._meta.label,
    )
    run = WorkflowRun.objects.start(workflow, actor=owner, subject=record)
    assert not WorkflowRun.objects.with_actor(viewer).scoped().filter(pk=run.pk).exists()
    with override_settings(ANGEE_TIMELINE_KINDS=["angee.workflows.timeline.timeline_kinds"]):
        schema = addon_schema(timeline_schema.schemas, "console")
        variables = {"model": record._meta.label, "id": public_id_of(record)}
        capture_graphql_document(schema, TIMELINE, variables, actor=viewer)
        hidden = capture_graphql_document(schema, TIMELINE, variables, actor=viewer)
        write_relationships([RelationshipTuple(
            resource=to_object_ref(run), relation="reader", subject=to_subject_ref(viewer),
        )])
        shown = capture_graphql_document(schema, TIMELINE, variables, actor=viewer)
    assert hidden.data["timeline"]["entries"] == []
    assert [entry["target_id"] for entry in shown.data["timeline"]["entries"]] == [public_id_of(run)]
    hidden.assert_no_audit_writes()
    hidden.assert_statements(5)
    hidden.assert_bytes(total=130_000, largest=50_000, cause="record-bounded run read denies the outsider")
    shown.assert_no_audit_writes()
    # Hydrating the run through the redacting scope costs one field-scope read.
    shown.assert_statements(9)
    shown.assert_bytes(total=195_000, largest=50_000, cause="record-bounded redacting run read admits the shared run")


def test_timeline_rail_sender_statement_budget(comment_record, capture_graphql_document) -> None:
    """The admitted message's sender label is a small structural join in the rail."""

    record, owner = comment_record.record, comment_record.author
    with system_context(reason="test.document_budget.timeline_sender"), mute_changes():
        attachment = messaging_models.ThreadAttachment.objects.ensure_for_record(record)
        party = PartyModel._base_manager.create(display_name="Rail party", created_by=comment_record.outsider)
        sender = messaging_models.Handle.objects.create(
            platform="email", value="rail-sender@example.test", display_name="Rail handle",
            party=party, party_link_confirmed=True, created_by=owner,
        )
        for index in range(5):
            Message.objects.create(
                thread=attachment.thread, sender=sender, created_by=owner,
                message_type="comment", direction="internal", preview=f"Rail comment {index}",
            )
    with override_settings(ANGEE_TIMELINE_KINDS=KINDS):
        schema = addon_schema(timeline_schema.schemas, "console")
        variables = {"model": record._meta.label, "id": public_id_of(record), "kinds": ["messaging.comment"]}
        capture_graphql_document(schema, TIMELINE, {**variables, "limit": 1}, actor=owner)
        reads = [capture_graphql_document(schema, TIMELINE, {**variables, "limit": size}, actor=owner)
                 for size in (1, 5)]
    for size, read in zip((1, 5), reads, strict=True):
        entries = read.data["timeline"]["entries"]
        assert len(entries) == size
        assert {entry["actor"] for entry in entries} == {"Rail party"}
        message_sql = [sql for sql in read.statements if ' AS "_sender_name"' in sql]
        assert len(message_sql) == 1
        assert 'JOIN "test_parties_handle"' in message_sql[0]
        assert 'JOIN "test_parties_party"' in message_sql[0]
        assert 'FROM "test_parties_handle"' not in message_sql[0]
        assert 'FROM "test_parties_party"' not in message_sql[0]
        assert len(message_sql[0].encode("utf-8")) <= 5_000
        read.assert_statements(27)
        read.assert_bytes(total=175_000, largest=75_000, cause="timeline union plus structural sender join")
    reads[1].assert_flat_from(reads[0])


@pytest.mark.django_db(transaction=True)
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
            cause="shared actor-set proof for one and many rows; the related permission proof is shared",
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


@pytest.mark.xfail(strict=True, reason="page_can_write checks the related page once per row")
def test_knowledge_record_pages_page_write_growth(knowledge_authored_reads) -> None:
    knowledge_authored_reads["pages"][1].assert_flat_from(knowledge_authored_reads["pages"][0])


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
    """A related Project answers four named actions without another read proof."""

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
        read.assert_bytes(total=50_000, largest=28_000, cause="native permission expressions after scope_cte removal")
        read.assert_statements(4)
        read.assert_no_audit_writes()
        assert len(read.data["four_permission_tasks"]) == count
        assert all(
            row["project"]["permissions"] == ["delete", "read", "share", "write"]
            for row in read.data["four_permission_tasks"]
        )
    for count in (5, 10, 20):
        reads[count].assert_flat_from(reads[1])
    reader_rows = capture_graphql_document(schema, document, {"limit": 5}, actor=reader)
    assert [row["project"]["permissions"] for row in reader_rows.data["four_permission_tasks"]] == [["read"]] * 5
    reader_rows.assert_no_audit_writes()


def test_recursive_permission_policy_keeps_inline_proofs(composed_tables: None) -> None:
    """A recursive page policy keeps its native permission expression."""

    del composed_tables
    actor = create_user("document-budget-recursive-page")
    rows = (
        Page.objects.with_actor(actor)
        .with_action("read_granted")
        .filter(pk__in=(1,))
        .annotate(can_write=permission_expression(Page, "write_granted", actor=actor))
    )
    assert "_closure" not in str(rows.query)
    list(rows.values_list("pk", flat=True))


def test_unoptimized_permission_fallback_uses_one_unbypassed_proof(composed_tables: None) -> None:
    """A mutation-style row keeps its permission values without an audit write."""

    del composed_tables
    actor = create_user("document-budget-unoptimized-project")
    with system_context(reason="test.document_budget.unoptimized_project"):
        project = Project.objects.create(title="Unoptimized project", owner=actor)
    with actor_context(actor), CaptureQueriesContext(connection) as queries:
        held = held_permissions(project, ("read", "write", "share", "delete"))
    assert held == {"read", "write", "share", "delete"}
    # Native expressions reuse the caller's actor without a bypass or extra statement.
    assert len(queries) == 2
    assert sum(len(query["sql"].encode("utf-8")) for query in queries) <= 30_000
    assert not any(is_permission_audit_insert(query["sql"], connection) for query in queries)
