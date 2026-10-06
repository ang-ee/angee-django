"""Record moderators trash and restore comments; nobody else ever sees a trashed one."""

from __future__ import annotations

from typing import Any

import pytest
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
)

from angee.graphql import records
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.messaging.testing.models import Message, Thread, ThreadAttachment, ThreadFollower
from tests.conftest import SchemaAddon, create_platform_admin, execute_schema, result_data
from tests.messaging_campaign import comment_record as comment_record
from tests.messaging_campaign import grant, make_user
from tests.test_messaging_graphql import (
    _request,
    _schema,
    iam_schema,
    integrate_schema,
    messaging_schema,
    parties_schema,
)

SHARED_TRASH = """
mutation Trash($id: ID!) {
  trash_record(target_type: "messaging/message", target_id: $id, reason: "Spam", confirm: true) { ok }
}
"""
SHARED_RESTORE = """
mutation Restore($id: ID!) {
  restore_record(target_type: "messaging/message", target_id: $id, confirm: true) { ok }
}
"""

THREAD = """
query Thread($id: ID!, $search: String!) {
  record_thread(input: {model_label: "chatterdemo.ChatterDoc", record_id: $id, search: $search}) {
    error_code
    message_result_count
    removed_message_count
    unread_count
    thread { message_count }
    messages { preview can_trash can_delete }
  }
}
"""
REMOVED = """
query Removed($id: ID!) {
  record_removed_messages(input: {model_label: "chatterdemo.ChatterDoc", record_id: $id}) {
    error_code
    message_result_count
    messages { id preview is_trashed trash_reason trashed_by_label trashed_at }
  }
}
"""
TRASH = """
mutation Trash($id: ID!, $message: ID!, $reason: String!, $confirm: Boolean!) {
  trash_record_message(input: {
    model_label: "chatterdemo.ChatterDoc", record_id: $id, message_id: $message, reason: $reason, confirm: $confirm
  }) {
    error error_code removed_message_count message_result_count
    messages { preview }
  }
}
"""
RESTORE = """
mutation Restore($id: ID!, $message: ID!) {
  restore_record_message(input: {
    model_label: "chatterdemo.ChatterDoc", record_id: $id, message_id: $message, confirm: true
  }) {
    error error_code removed_message_count message_result_count
  }
}
"""


@pytest.fixture
def discussion(comment_record: Any) -> Any:
    """Two reader comments on a record whose writer moderates its chatter."""

    case = comment_record
    with actor_context(case.peer):
        case.record.message_subscribe()
    with actor_context(case.author):
        case.removed = case.record.message_post("Off topic remark")
        case.kept = case.record.message_post("Useful remark")
    return case


def test_moderator_trash_removes_a_comment_from_every_reader_surface(discussion: Any) -> None:
    case = discussion
    before = _thread(case, case.peer)
    assert _comments(before) == ["Off topic remark", "Useful remark"]
    unread = _unread(case)
    grant(case.removed, "reader", case.peer)
    with actor_context(case.peer):
        assert Message.objects.filter(pk=case.removed.pk).exists()

    trashed = _run(TRASH, case, case.writer, message=case.removed, reason="  Off topic  ")
    assert trashed["error"] is None
    assert trashed["removed_message_count"] == 1
    assert "Off topic remark" not in [row["preview"] for row in trashed["messages"]]

    for reader in (case.peer, case.author):
        thread = _thread(case, reader)
        assert _comments(thread) == ["Useful remark"]
        assert thread["message_result_count"] == before["message_result_count"] - 1
        assert thread["thread"]["message_count"] == before["thread"]["message_count"] - 1
        assert thread["removed_message_count"] is None
        assert _thread(case, reader, search="Off topic")["messages"] == []
        with actor_context(reader):
            assert not Message.objects.filter(pk=case.removed.pk).exists()
        removed = result_data(execute_schema(_schema(), REMOVED, {"id": case.record.sqid}, request=_request(reader)))
        assert removed["record_removed_messages"]["error_code"] == "PERMISSION_DENIED"
        assert removed["record_removed_messages"]["messages"] == []
    assert _unread(case) == unread - 1

    listed = result_data(execute_schema(_schema(), REMOVED, {"id": case.record.sqid}, request=_request(case.writer)))
    rows = listed["record_removed_messages"]["messages"]
    assert listed["record_removed_messages"]["message_result_count"] == 1
    assert [(row["preview"], row["is_trashed"], row["trash_reason"]) for row in rows] == [
        ("Off topic remark", True, "Off topic"),
    ]
    assert rows[0]["trashed_by_label"] and rows[0]["trashed_at"]
    assert _thread(case, case.writer)["removed_message_count"] == 1

    restored = _run(RESTORE, case, case.writer, message=case.removed)
    assert (restored["error"], restored["removed_message_count"]) == (None, 0)
    assert restored["message_result_count"] == before["message_result_count"]
    assert _comments(_thread(case, case.peer)) == ["Off topic remark", "Useful remark"]
    assert _unread(case) == unread
    with actor_context(case.peer):
        assert Message.objects.filter(pk=case.removed.pk).exists()
    stored = Message._base_manager.get(pk=case.removed.pk)
    assert (stored.is_trashed, stored.trashed_by_id, stored.trash_reason) == (False, None, "")


def test_only_record_moderators_get_the_trash_control(discussion: Any) -> None:
    """An author without moderation never trashes, even their own comment."""

    case = discussion
    authored = [row for row in _thread(case, case.author)["messages"] if row["preview"] in _COMMENTS]
    moderated = [row for row in _thread(case, case.writer)["messages"] if row["preview"] in _COMMENTS]
    assert [row["can_trash"] for row in authored] == [False, False]
    assert [row["can_delete"] for row in authored] == [True, True]
    assert [row["can_trash"] for row in moderated] == [True, True]

    refused = _run(TRASH, case, case.author, message=case.removed, reason="")
    assert refused["error_code"] == "PERMISSION_DENIED"
    with actor_context(case.author), pytest.raises(PermissionDenied):
        case.record.message_trash(case.removed)
    unconfirmed = _run(TRASH, case, case.writer, message=case.removed, reason="", confirm=False)
    assert unconfirmed["error_code"] == "CONFIRMATION_REQUIRED"
    oversized = _run(TRASH, case, case.writer, message=case.removed, reason="x" * 1001)
    assert oversized["error"] and oversized["error_code"] != "PERMISSION_DENIED"
    assert not Message._base_manager.get(pk=case.removed.pk).is_trashed

    assert _run(TRASH, case, case.writer, message=case.removed, reason="")["error"] is None
    assert _run(RESTORE, case, case.author, message=case.removed)["error_code"] == "PERMISSION_DENIED"
    assert _run(TRASH, case, case.writer, message=case.removed, reason="")["error_code"] == "BAD_MESSAGE"
    with actor_context(case.author), pytest.raises(ValueError, match="does not belong"):
        case.record.message_update_content(Message._base_manager.get(pk=case.removed.pk), body="Edited")


def test_notes_and_tracked_messages_stay_out_of_the_trash(discussion: Any) -> None:
    case = discussion
    with system_context(reason="test.messaging.trash.note"):
        attachment = ThreadAttachment.objects.ensure_for_record(case.record)
        note = Message.objects.create(thread=attachment.thread, message_type="notification", direction="internal")
    with actor_context(case.writer), pytest.raises(ValueError, match="Only comment messages can be removed"):
        case.record.message_trash(note)


def test_restore_never_regrants_access_revoked_while_trashed(discussion: Any) -> None:
    case = discussion
    assert _run(TRASH, case, case.writer, message=case.removed, reason="")["error"] is None
    with system_context(reason="test.messaging.trash.revoke"):
        delete_relationship(RelationshipTuple(
            resource=to_object_ref(case.record), relation="reader", subject=to_subject_ref(case.peer),
        ))
    assert _run(RESTORE, case, case.writer, message=case.removed)["error"] is None
    assert _thread(case, case.peer)["error_code"] == "NOT_FOUND"
    with actor_context(case.peer):
        assert not Message.objects.filter(pk=case.removed.pk).exists()
    assert _comments(_thread(case, case.author)) == ["Off topic remark", "Useful remark"]


def test_shared_verbs_trash_inbox_messages_but_never_record_chatter(discussion: Any) -> None:
    """Inbox moderation uses the shared verbs; record chatter stays with its record."""

    case = discussion
    owner, admin = make_user("inbox-owner"), create_platform_admin("inbox-trash-admin")
    with system_context(reason="test.messaging.trash.inbox"):
        inbox = Message.objects.create(thread=Thread.objects.create(owner=owner), preview="Inbox spam")
    schema = _console_schema()
    with actor_context(owner):
        assert Message.objects.filter(pk=inbox.pk).exists()

    trashed = result_data(execute_schema(schema, SHARED_TRASH, {"id": inbox.sqid}, request=_request(owner)))
    assert trashed == {"trash_record": {"ok": True}}
    with actor_context(owner):
        assert not Message.objects.filter(pk=inbox.pk).exists()
    with actor_context(admin):
        assert Message.objects.filter(pk=inbox.pk, is_trashed=True).exists()
    restored = result_data(execute_schema(schema, SHARED_RESTORE, {"id": inbox.sqid}, request=_request(owner)))
    assert restored == {"restore_record": {"ok": True}}

    chatter_owner = Thread._base_manager.get(pk=case.removed.thread_id).owner
    for user in (case.writer, *([chatter_owner] if chatter_owner is not None else [])):
        refused = result_data(execute_schema(schema, SHARED_TRASH, {"id": case.removed.sqid}, request=_request(user)))
        assert refused == {"trash_record": {"ok": False}}
    assert not Message._base_manager.get(pk=case.removed.pk).is_trashed


def _console_schema() -> Any:
    """Build the messaging console schema with the shared record verbs."""

    modules = (iam_schema, integrate_schema, parties_schema, messaging_schema, records)
    return GraphQLSchemas([
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in modules
    ]).build("console")


_COMMENTS = ("Off topic remark", "Useful remark")


def _comments(thread: dict[str, Any]) -> list[str]:
    """Return the discussion's comment previews in transcript order."""

    return [row["preview"] for row in thread["messages"] if row["preview"] in _COMMENTS]


def _thread(case: Any, user: Any, *, search: str = "") -> dict[str, Any]:
    """Read the record thread as ``user``."""

    data = result_data(execute_schema(
        _schema(), THREAD, {"id": case.record.sqid, "search": search}, request=_request(user),
    ))
    return data["record_thread"]


def _run(mutation: str, case: Any, user: Any, *, message: Any, **variables: Any) -> dict[str, Any]:
    """Run one moderation mutation as ``user`` and return its payload."""

    variables.setdefault("confirm", True)
    if "reason" not in mutation:
        variables.pop("confirm")
    data = result_data(execute_schema(
        _schema(), mutation, {"id": case.record.sqid, "message": message.sqid, **variables}, request=_request(user),
    ))
    return next(iter(data.values()))


def _unread(case: Any) -> int:
    """Return the subscribed peer's unread count on the record thread."""

    return ThreadFollower.objects.unread_count_for_record(case.record, user=case.peer)
