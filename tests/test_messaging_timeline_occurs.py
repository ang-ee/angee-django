"""Messaging probes follow record attachments without authorizing or hydrating them."""

import pytest
from rebac import actor_context, system_context

from angee.messaging.testing.models import Message, ThreadAttachment
from angee.messaging.timeline import CommentKind, ConversationKind
from angee.projects.testing.models import Task
from tests.messaging_campaign import comment_record as comment_record  # noqa: F401 -- shared fixture
from tests.test_messaging_source_threads import sourced as sourced  # noqa: F401 -- shared fixture
from tests.test_timeline import timeline_entries
from tests.test_timeline_occurs import assert_branch_omitted, assert_occurs


@pytest.mark.django_db(transaction=True)
def test_comments_elsewhere_do_not_compile_a_branch(comment_record):
    record, author = comment_record.record, comment_record.author
    with actor_context(author):
        record.message_post("Present only here")
    with system_context(reason="test.timeline.empty_chatter"):
        absent = type(record).objects.create(title="No comments")
    kind = CommentKind()
    assert_occurs(kind, record, True, comment_record.outsider)
    assert_occurs(kind, absent, False, comment_record.outsider)
    assert_branch_omitted(kind, absent, comment_record.writer, Message)


def test_comment_probe_includes_messages_on_a_redirected_thread(comment_record, settings):
    record, author = comment_record.record, comment_record.author
    with actor_context(author):
        message = record.message_post("Retained on the old physical thread")
    with system_context(reason="test.timeline.chatter_redirect"):
        survivor = type(record).objects.create(title="Surviving chatter")
        survivor.message_thread().fold([record.message_thread()])
    kind = CommentKind()
    for target in (record, survivor):
        assert_occurs(kind, target, True, comment_record.outsider)
    # The structural result grants no access to the survivor.
    settings.ANGEE_TIMELINE_KINDS = ["angee.messaging.timeline.timeline_kinds"]
    assert timeline_entries(record, author) == []
    Message._base_manager.filter(pk=message.pk).update(is_trashed=True)
    assert_occurs(kind, survivor, False, author)


@pytest.mark.django_db(transaction=True)
def test_source_attachments_elsewhere_do_not_compile_a_branch(sourced):
    owner, record, thread = sourced["owner"], sourced["task"], sourced["thread"]
    with actor_context(owner):
        ThreadAttachment.objects.bind_source_thread(record, thread)
        absent = Task.objects.create(title="No source conversation")
    kind = ConversationKind()
    assert_occurs(kind, record, True, sourced["outsider"])
    assert_occurs(kind, absent, False, sourced["outsider"])
    assert_branch_omitted(kind, absent, owner, ThreadAttachment)
