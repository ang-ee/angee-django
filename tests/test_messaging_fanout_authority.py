"""Activity authority and bounded recipient evaluation use the messaging owners."""

from math import ceil
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from rebac import (
    RelationshipTuple,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model

from angee.base.signals import mute_changes
from angee.messaging.testing.models import Message, ThreadAttachment, ThreadNotification
from tests.chatterdemo.models import ChatterDoc
from tests.messaging_campaign import grant
from tests.queries import is_rebac_revision_read, statement_body
from tests.t3_campaign import campaign_access as campaign_access
from tests.t3_campaign import campaign_user as campaign_user
from tests.t3_campaign import messaging_access_schema as messaging_access_schema


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
        # A record's creation is a history line, not a message: the chatter thread exists from its first use.
        attachment = record.message_thread_attachment()
        write_relationships(
            [RelationshipTuple(to_object_ref(record), "reader", to_subject_ref(account)) for account in accounts[::2]]
        )
        message = Message.objects.create(thread=attachment.thread, message_type="comment", created_by=author)
    expected = {account.pk for account in accounts[::2]}
    # Warm only static schema/content-type metadata; recipient checks themselves are measured.
    record.thread_reader_ids(accounts[:1])
    with system_context(reason="tests.t3.gate_under_system"), CaptureQueriesContext(connection) as gate_queries:
        assert record.thread_reader_ids([*accounts, accounts[0], to_subject_ref(accounts[0])]) == expected
    statements = [statement_body(q["sql"]) for q in gate_queries]
    chunks = [sql for sql in statements if sql.startswith("SELECT") and "EXISTS(" in sql]
    # Native check_bulk_permissions decides 50 items per batch: statements per batch, not per user.
    # 0.31 (2 -> 2 or 3) splits a batch's bounds at its statement-size budget; registry items compile wider.
    per_batch = 3 if campaign_access == "registry" else 2
    batches = size // 50
    assert len(chunks) == per_batch * batches
    # 0.31 (1 per batch -> 0): actor memberships are predicates of the bound statements, never a separate read.
    tuple_table = connection.ops.quote_name(active_relationship_model()._meta.db_table)
    memberships = [
        sql for sql in statements if sql.startswith("SELECT DISTINCT") and f"FROM {tuple_table}" in sql
    ]
    assert memberships == []
    witnesses = sum(is_rebac_revision_read(query["sql"]) for query in gate_queries)
    assert witnesses == 1
    assert len(gate_queries) == per_batch * batches + witnesses
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
    # Django owns the INSERT chunk size; the native write gate adds one revision
    # read. The remaining overhead is four row reads and BEGIN/COMMIT.
    fields = [
        field
        for field in ThreadNotification._meta.concrete_fields
        if not field.generated and not isinstance(field, models.AutoField)
    ]
    insert_size = connection.ops.bulk_batch_size(fields, [None] * count)
    # 0.31 (3 -> per_batch gate statements per batch): the audience gate above, without membership reads.
    assert len(fanout_queries) <= per_batch * batches + 8 + ceil(count / insert_size)


@pytest.mark.parametrize("attachment_role", ("chatter", "source"))
@pytest.mark.parametrize("actor_role", ("reader", "writer", "manager", "outsider", "other_reader", "anonymous"))
def test_record_arm_is_read_only_and_role_filtered(campaign_access, campaign_user, attachment_role, actor_role):
    actor = AnonymousUser() if actor_role == "anonymous" else campaign_user(actor_role)
    with system_context(reason="tests.t3.thread_matrix"):
        record = ChatterDoc.objects.create(title="Record A")
        other = ChatterDoc.objects.create(title="Record B")
        attachment = ThreadAttachment.objects.ensure_for_record(record, role=attachment_role)
    if actor_role in ("reader", "writer", "manager"):
        grant(record, actor_role, actor)
    elif actor_role == "other_reader":
        grant(other, "reader", actor)
    expected = attachment_role == "chatter" and actor_role in ("reader", "writer", "manager")
    thread = attachment.thread
    for permission in ("read", "write", "delete", "share", "transfer"):
        allowed = expected and permission == "read"
        assert thread.with_actor(actor).has_access(permission) is allowed
        assert type(thread).objects.with_actor(actor).with_action(permission).filter(pk=thread.pk).exists() is allowed
