"""Message visibility inherited from user-owned channels."""

from __future__ import annotations

import pytest
from django.apps import apps
from rebac import (
    PermissionDenied,
    actor_context,
    system_context,
)

from angee.messaging.testing.models import Channel, Message
from angee.tags.testing.models import Tag, TagAssignment
from tests.conftest import Vendor


def test_tag_attachment_enforces_annotate_on_a_readable_conversation(db: str) -> None:
    """The tags edge writes through messaging's annotate arm: an annotator who reads may tag, a reader
    may not, and a trashed or foreign message is not a target at all."""

    del db
    with system_context(reason="tests.messaging.annotation_setup"):
        users = apps.get_model("iam", "User").objects
        owner = users.create_user(username="annotation-owner")
        annotator = users.create_user(username="annotation-worker", kind="service")
        vendor = Vendor.objects.create(slug="annotation-access", display_name="Annotation access")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
        other_channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
        message = Message.objects.create(channel=channel)
        hidden = Message.objects.create(channel=channel, is_trashed=True)
        unrelated = Message.objects.create(channel=other_channel)
        tag = Tag.objects.create(name="Review needed")
    channel.with_actor(owner).grant_record_access("reader", annotator)
    with actor_context(annotator), pytest.raises(PermissionDenied):
        TagAssignment.objects.attach("messaging/message", message.sqid, [tag.sqid])
    channel.with_actor(owner).grant_record_access("annotator", annotator)
    with actor_context(annotator):
        assert len(TagAssignment.objects.attach("messaging/message", message.sqid, [tag.sqid])) == 1
        assert TagAssignment.objects.detach("messaging/message", message.sqid, [tag.sqid]) == 1
        for row in (hidden, unrelated):
            with pytest.raises(ValueError):
                TagAssignment.objects.attach("messaging/message", row.sqid, [tag.sqid])
    channel.with_actor(owner).revoke_record_access("annotator", annotator)
    with actor_context(annotator), pytest.raises(PermissionDenied):
        TagAssignment.objects.attach("messaging/message", message.sqid, [tag.sqid])
