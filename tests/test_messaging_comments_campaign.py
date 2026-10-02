"""The message kind, authorship, and provider-attribution contract."""

from dataclasses import replace

import pytest
from rebac import PermissionDenied, actor_context, system_context

from angee.messaging.backends import ParsedThread
from angee.messaging.testing.models import Handle, Message, Part, ThreadAttachment, ThreadNotification, TrackingValue
from tests.chatterdemo.models import ChatterDoc
from tests.messaging_campaign import comment_record as comment_record
from tests.messaging_campaign import make_user
from tests.test_messaging import _AT, _ingest, _parsed
from tests.test_messaging import channel as channel


@pytest.mark.parametrize("kind", list(Message.MessageKind))
@pytest.mark.parametrize("tracked", [False, True])
def test_only_untracked_comments_can_be_deleted_through_projection_and_locked_owner(comment_record, kind, tracked):
    case = comment_record
    with system_context(reason="test.comments.kind"):
        attachment = ThreadAttachment.objects.ensure_for_record(case.record)
        message = Message.objects.create(thread=attachment.thread, message_type=kind,
                                         direction="internal", created_by=case.author,
                                         sender=Handle.objects.for_user(case.author))
        if tracked:
            TrackingValue.objects.create(message=message, field_name="status")
    allowed = kind == Message.MessageKind.COMMENT and not tracked
    with actor_context(case.writer):
        assert message.can_delete(post_access=True, moderate_access=True, actor_id=case.writer.pk) is allowed
        if allowed:
            case.record.message_unlink(message)
        else:
            with pytest.raises(ValueError, match="comment|tracking"):
                case.record.message_unlink(message)
            # The manager repeats the invariant under its row lock.
            with pytest.raises(ValueError, match="comment|tracking"):
                Message.objects.unlink_from_thread(message, thread=attachment.thread)
    assert Message._base_manager.filter(pk=message.pk).exists() is not allowed


@pytest.mark.parametrize("verb", ["edit", "delete"])
def test_comment_writes_refresh_stale_tracking_projection_under_lock(comment_record, verb):
    """Tracking added after a list read still blocks the owning mutation."""

    case = comment_record
    with actor_context(case.author):
        message = case.record.message_post("Before tracking")
    with system_context(reason="test.comments.stale_tracking"):
        message = Message.objects.annotate(
            _has_tracking_values=Message.has_tracking_values_expression(),
        ).get(pk=message.pk)
        assert message.content_edit_error() is None
        assert message.delete_error() is None
        TrackingValue.objects.create(message=message, field_name="status")
    with actor_context(case.writer), pytest.raises(ValueError, match="tracking values"):
        if verb == "edit":
            case.record.message_update_content(message, body="Must be refused")
        else:
            case.record.message_unlink(message)
    assert Message._base_manager.get(pk=message.pk).preview == "Before tracking"


@pytest.mark.parametrize("seat", ["author", "peer", "writer", "outsider"])
def test_comment_mutations_match_author_post_access_or_record_moderation(comment_record, seat):
    case = comment_record
    with actor_context(case.author):
        message = case.record.message_post("Original comment")
    actor = getattr(case, seat)
    expected = seat in {"author", "writer"}
    with actor_context(actor):
        assert message.can_edit(post_access=case.record.can_post(), moderate_access=case.record.can_moderate(),
                                actor_id=actor.pk) is expected
        assert message.can_delete(post_access=case.record.can_post(), moderate_access=case.record.can_moderate(),
                                  actor_id=actor.pk) is expected
        if expected:
            edited = case.record.message_update_content(message, body="Revised comment")
            assert edited.created_by_id == case.author.pk
            assert edited.edit_history[0]["edited_by_id"] == str(actor.pk)
            case.record.message_unlink(edited)
        else:
            with pytest.raises(PermissionDenied):
                case.record.message_update_content(message, body="Unauthorized edit")
            with pytest.raises(PermissionDenied):
                case.record.message_unlink(message)
    assert Message._base_manager.filter(pk=message.pk).exists() is not expected


def test_author_who_loses_post_access_cannot_edit_or_delete_their_comment(comment_record, monkeypatch):
    case = comment_record
    with actor_context(case.author):
        message = case.record.message_post("Before permission change")
    # Tighten the declaration to the existing writer permission; the author still reads.
    monkeypatch.setattr(ChatterDoc, "thread_post_access", "write")
    with actor_context(case.author):
        assert case.record.has_access("read")
        assert not case.record.can_post()
        with pytest.raises(PermissionDenied):
            case.record.message_update_content(message, body="After permission change")
        with pytest.raises(PermissionDenied):
            case.record.message_unlink(message)
    assert Message._base_manager.get(pk=message.pk).preview == "Before permission change"


@pytest.mark.parametrize("seat", ["author", "peer", "writer", "outsider"])
def test_senderless_comment_grants_nobody_edit_or_delete(comment_record, seat):
    case = comment_record
    with actor_context(case.author):
        message = case.record.message_post("Unattributed comment")
        assert message.sender_id is not None
    with system_context(reason="test.comments.missing_sender"):
        Message._base_manager.filter(pk=message.pk).update(sender_id=None)
    message.refresh_from_db()
    actor = getattr(case, seat)
    with actor_context(actor):
        access = dict(post_access=case.record.can_post(), moderate_access=case.record.can_moderate(), actor_id=actor.pk)
        assert not message.can_edit(**access)
        assert not message.can_delete(**access)
        with pytest.raises(PermissionDenied):
            case.record.message_update_content(message, body="Unauthorized edit")
        with pytest.raises(PermissionDenied):
            case.record.message_unlink(message)


def test_record_membership_is_required_even_for_a_writer(comment_record):
    case = comment_record
    with system_context(reason="test.comments.foreign"):
        other = ChatterDoc.objects.create(title="Other document")
        attachment = ThreadAttachment.objects.ensure_for_record(other)
        foreign = Message.objects.create(thread=attachment.thread, direction="internal", message_type="comment")
    with actor_context(case.writer):
        with pytest.raises(ValueError, match="does not belong"):
            case.record.message_update_content(foreign, body="Foreign edit")
        with pytest.raises(ValueError, match="does not belong"):
            case.record.message_unlink(foreign)
    assert Message._base_manager.filter(pk=foreign.pk).exists()


def test_provider_edits_record_no_local_editor_even_with_an_ambient_user(channel, composed_tables):
    actor = make_user("sync-operator")
    initial = _parsed("provider-campaign", text="First provider body", sent_at=_AT)
    with actor_context(actor):
        _ingest([initial], channel=channel)
    message = Message._base_manager.get(external_id="provider-campaign")
    initial_hash = Part._base_manager.get(message=message, role="body", fragment__isnull=False).fragment.hash
    for text in ("Second provider body", "Third provider body"):
        with actor_context(actor):
            _ingest([replace(initial, body=replace(initial.body, text=text))], channel=channel)
    message.refresh_from_db()
    assert len(message.edit_history) == 2
    assert all("edited_by_id" not in entry for entry in message.edit_history)
    assert initial_hash in message.edit_history[-1]["prev_fragment_hashes"]
    assert message.created_by_id == channel.owner_id
    with actor_context(actor):
        _ingest([replace(initial, body=replace(initial.body, text="Third provider body"))], channel=channel)
    message.refresh_from_db()
    assert len(message.edit_history) == 2
    assert not ThreadNotification._base_manager.filter(message=message).exists()


@pytest.mark.parametrize("named", [False, True])
def test_email_and_named_provider_conversations_stay_ownerless_on_repeated_ingest(channel, composed_tables, named):
    parsed = _parsed("ownerless-ingest", sent_at=_AT)
    if named:
        parsed = replace(parsed, platform="slack", thread=ParsedThread(external_id="named-conversation"))
    _ingest([parsed], channel=channel)
    _ingest([parsed], channel=channel)
    message = Message._base_manager.select_related("thread").get(external_id=parsed.external_id)
    assert message.thread.owner_id is None
    assert message.thread.created_by_id == channel.owner_id
    assert message.created_by_id == channel.owner_id


@pytest.mark.parametrize("metadata", [{}, {"webform": None}, {"webform": []}, {"webform": {"answers": []}}])
def test_non_webform_messages_have_no_submission_envelope(metadata):
    assert Message(metadata=metadata).webform_submission() is None


def test_webform_accessor_keeps_claimed_email_unverified_and_returns_a_copy():
    answers = {"summary": "A question"}
    message = Message(metadata={"webform": {"answers": answers, "unverified_submitter_email": "claim@example.com"}})
    envelope = message.webform_submission()
    assert envelope.answers == answers
    assert envelope.unverified_submitter_email == "claim@example.com"
    envelope.answers["summary"] = "Changed by caller"
    assert answers == {"summary": "A question"}


@pytest.mark.parametrize("operation", ["edit", "delete"])
def test_record_moderator_cannot_change_comment_with_tracking_hidden_from_generic_scope(comment_record, operation):
    case = comment_record
    with system_context(reason="test.comments.tracking-retention"):
        attachment = ThreadAttachment.objects.ensure_for_record(case.record)
        message = Message.objects.create(thread=attachment.thread, message_type="comment",
                                         direction="internal", created_by=case.author)
        TrackingValue.objects.create(message=message, field_name="status")
    with actor_context(case.writer):
        assert case.record.can_moderate()
        assert not message.has_access("read")
        with pytest.raises(ValueError, match="tracking"):
            if operation == "edit":
                Message.objects.update_content(message, body="Tampered", edited_by_id=case.writer.pk)
            else:
                Message.objects.unlink_from_thread(message, thread=attachment.thread)
    assert Message._base_manager.filter(pk=message.pk).exists()
