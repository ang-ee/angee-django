"""Chatter comments project `messaging.comment` events; trashing removes them."""

from __future__ import annotations

import pytest
from django.apps import apps
from django.test import override_settings
from rebac import actor_context

from angee.identity import public_id_of
from tests.messaging_campaign import comment_record as comment_record  # noqa: F401 - fixture

KINDS = ["angee.messaging.timeline.timeline_kinds"]


@pytest.mark.django_db
@override_settings(ANGEE_TIMELINE_KINDS=KINDS)
def test_comment_events_follow_the_message(comment_record) -> None:  # noqa: ANN001 - fixture namespace
    record, author = comment_record.record, comment_record.author
    with actor_context(author):
        message = record.message_post("A comment on the record")
    event_model = apps.get_model("timeline", "Event")
    key = f"messaging.comment:{public_id_of(message)}:1"
    event = event_model.objects.for_record(record).get(key=key)
    assert event.kind == "messaging.comment"
    assert event.target_model == "messaging.Message"
    assert event.target_id == public_id_of(message)
    assert event.title == message.preview
    assert event.actor_label
    with actor_context(comment_record.writer):
        record.message_trash(message)
    assert not event_model.objects.filter(key=key).exists()
