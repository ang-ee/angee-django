"""Activity authority and bounded recipient evaluation use the messaging owners."""

from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection, transaction
from django.db.models.deletion import ProtectedError
from django.test.utils import CaptureQueriesContext
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.roles import grant as grant_role

from angee.graphql.publishing import mute_changes
from angee.messaging.testing.models import ActivityType, Message, ThreadActivity, ThreadAttachment, ThreadNotification
from tests.chatterdemo.models import ChatterDoc
from tests.conftest import execute_schema, result_data
from tests.messaging_campaign import grant
from tests.t3_campaign import campaign_access as campaign_access
from tests.t3_campaign import campaign_user as campaign_user
from tests.t3_campaign import messaging_access_schema as messaging_access_schema
from tests.test_messaging_graphql import _schema


@pytest.mark.parametrize("size", (100, 1500))
def test_fanout_chunks_account_read_gates_and_delivers_exactly_the_readable_half(
    campaign_access,
    campaign_user,
    size,
):
    author = campaign_user("author")
    user_model = get_user_model()
    with system_context(reason="tests.t3.large_audience"), mute_changes():
        accounts = user_model._base_manager.bulk_create(
            [
                user_model(username=f"{author.username}-{index}", email=f"{author.username}-{index}@example.test")
                for index in range(size)
            ]
        )
        record = ChatterDoc.objects.create(title="Large audience")
        attachment = record.message_thread_attachment(create=False)
        write_relationships(
            [RelationshipTuple(to_object_ref(record), "reader", to_subject_ref(account)) for account in accounts[::2]]
        )
        message = Message.objects.create(thread=attachment.thread, message_type="comment", created_by=author)
    expected = {account.pk for account in accounts[::2]}
    # Warm only static schema/content-type metadata; recipient checks themselves are measured.
    record.thread_reader_ids(accounts[:1])
    with system_context(reason="tests.t3.gate_under_system"), CaptureQueriesContext(connection) as gate_queries:
        assert record.thread_reader_ids([*accounts, accounts[0], to_subject_ref(accounts[0])]) == expected
    chunks = [q for q in gate_queries if q["sql"].startswith("SELECT") and "EXISTS(" in q["sql"]]
    assert len(chunks) == size // 50
    assert len(gate_queries) <= size // 50 + 5
    with (
        system_context(reason="tests.t3.fanout_under_system"),
        patch.object(ChatterDoc, "thread_reader_allowed", side_effect=AssertionError("Per-account read gate")),
        CaptureQueriesContext(connection) as fanout_queries,
    ):
        count = ThreadNotification.objects.fanout_for_message(
            message,
            attachment=attachment,
            created_by_id=author.pk,
            recipient_user_ids=tuple(account.pk for account in accounts),
        )
    assert count == size // 2
    assert set(ThreadNotification._base_manager.filter(message=message).values_list("user_id", flat=True)) == expected
    assert len(fanout_queries) <= size // 50 + 9  # Handover: 11 at 100; 39 at 1,500.


@pytest.mark.parametrize("actor_role", ("reader", "writer", "admin", "anonymous"))
def test_activity_catalog_is_authenticated_read_and_admin_only_write(campaign_access, campaign_user, actor_role):
    actor = AnonymousUser() if actor_role == "anonymous" else campaign_user(actor_role)
    with system_context(reason="tests.t3.catalog"):
        if actor_role == "admin":
            grant_role(actor=actor, role="angee/role:admin")
        row = ActivityType.objects.create(key="exchange", name="Exchange", glyph="phone")
    assert ActivityType.objects.with_actor(actor).filter(pk=row.pk).exists() is (actor_role != "anonymous")
    with actor_context(actor):
        candidate = ActivityType._base_manager.get(pk=row.pk).with_actor(actor)
        candidate.name = "Renamed exchange"
        if actor_role == "admin":
            candidate.save(update_fields=("name",))
            created = ActivityType.objects.create(key="meeting", name="Meeting")
            created.delete()
        else:
            for operation in (
                lambda: candidate.save(update_fields=("name",)),
                lambda: ActivityType.objects.create(key="meeting", name="Meeting"),
                candidate.delete,
            ):
                with pytest.raises(PermissionDenied), transaction.atomic():
                    operation()


def test_activity_log_preserves_note_uses_key_fk_and_posts_no_completion_message(campaign_access, campaign_user):
    writer, reader = campaign_user("writer"), campaign_user("reader")
    with system_context(reason="tests.t3.activity"):
        kind = ActivityType.objects.create(key="exchange", name="Exchange")
        record = ChatterDoc.objects.create(title="Activity record")
    grant(record, "writer", writer)
    grant(record, "reader", reader)
    note = "  " + "A" * 280 + "  \n  Second line stays indented.\n"
    instant = datetime(2026, 9, 20, 12, tzinfo=UTC)
    before = Message._base_manager.count()
    with actor_context(writer), patch("angee.messaging.managers.timezone.now", return_value=instant):
        activity = record.with_actor(writer).activity_log("exchange", date(2026, 9, 19), note)
    activity.refresh_from_db()
    assert activity.status == "done"
    assert activity.user_id == activity.created_by_id == writer.pk
    assert activity.due_date == date(2026, 9, 19) and activity.completed_at == instant
    assert activity.note == note and activity.summary == "A" * 256
    assert activity.activity_type_id == "exchange" and activity.activity_type.pk == kind.pk
    assert Message._base_manager.count() == before
    with actor_context(reader), pytest.raises(PermissionDenied):
        record.with_actor(reader).activity_log("exchange", date(2026, 9, 19), "Forbidden")
    with system_context(reason="tests.t3.protected_catalog"), pytest.raises(ProtectedError):
        kind.delete()
    field = ThreadActivity._meta.get_field("activity_type")
    assert field.target_field.name == "key" and field.column == "activity_type"


def test_record_activity_action_denies_reader_but_record_payload_reveals_logged_exchange(
    campaign_access, campaign_user
):
    writer, reader, outsider = (campaign_user(role) for role in ("writer", "reader", "outsider"))
    with system_context(reason="tests.t3.activity_graphql"):
        ActivityType.objects.create(key="exchange", name="Exchange")
        record = ChatterDoc.objects.create(title="Logged record")
    grant(record, "writer", writer)
    grant(record, "reader", reader)
    schema = _schema()
    mutation = """mutation($id: ID!) {
      log_record_activity(input: {model_label: "chatterdemo.ChatterDoc", record_id: $id,
        activity_type: "exchange", occurred_on: "2026-09-01", note: "Recorded exchange"}) {
        error_code activity { activity_type note status }
      }
    }"""
    variables = {"id": record.sqid}
    logged = result_data(execute_schema(schema, mutation, variables, user=writer))["log_record_activity"]
    assert logged["error_code"] is None
    denied = result_data(execute_schema(schema, mutation, variables, user=reader))["log_record_activity"]
    assert denied["error_code"] == "PERMISSION_DENIED" and denied["activity"] is None
    query = """query($id: ID!) {
      record_thread(input: {model_label: "chatterdemo.ChatterDoc", record_id: $id}) {
        error_code activities { activity_type note status }
      }
    }"""
    readable = result_data(execute_schema(schema, query, variables, user=reader))["record_thread"]
    assert readable["error_code"] is None and readable["activities"] == [logged["activity"]]
    hidden = result_data(execute_schema(schema, query, variables, user=outsider))["record_thread"]
    assert hidden["activities"] == [] and hidden["error_code"] is not None


@pytest.mark.parametrize("attachment_role", ("chatter", "source"))
@pytest.mark.parametrize("actor_role", ("reader", "writer", "owner", "outsider", "other_reader", "anonymous"))
def test_record_arm_is_read_only_and_role_filtered(campaign_access, campaign_user, attachment_role, actor_role):
    actor = AnonymousUser() if actor_role == "anonymous" else campaign_user(actor_role)
    with system_context(reason="tests.t3.thread_matrix"):
        record = ChatterDoc.objects.create(title="Record A")
        other = ChatterDoc.objects.create(title="Record B")
        attachment = ThreadAttachment.objects.ensure_for_record(record, role=attachment_role)
    if actor_role in ("reader", "writer", "owner"):
        grant(record, actor_role, actor)
    elif actor_role == "other_reader":
        grant(other, "reader", actor)
    expected = attachment_role == "chatter" and actor_role in ("reader", "writer", "owner")
    thread = attachment.thread
    for permission in ("read", "write", "delete", "share", "transfer"):
        allowed = expected and permission == "read"
        assert thread.with_actor(actor).has_access(permission) is allowed
        assert type(thread).objects.with_actor(actor).with_action(permission).filter(pk=thread.pk).exists() is allowed
