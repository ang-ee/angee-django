"""Native record-access events keep chatter follows transactional."""

from typing import Any

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import models
from rebac import RelationshipTuple, actor_context, delete_relationship, to_object_ref, to_subject_ref

from angee.messaging.managers import ThreadFollowerManager
from angee.messaging.models import ThreadedModelMixin
from angee.messaging.testing.models import Message, ThreadAttachment, ThreadFollower
from angee.projects.testing.models import Project
from tests.test_record_sharing import record_access_target as record_access_target


class PlainChatterRecord(ThreadedModelMixin):
    """A Django host with no Angee identity or REBAC model mixin."""

    title = models.CharField(max_length=100)

    class Meta:
        app_label = "messaging"
        db_table = "test_messaging_plain_chatter_record"

    def __str__(self) -> str:
        return self.title


def _followed_record(target: tuple[Project, Any, Any]) -> tuple[Project, Any, Any, ThreadFollower]:
    record, owner, reader = target
    record.with_actor(owner).grant_record_access("reader", reader)
    with actor_context(reader):
        follower = ThreadFollower.objects.subscribe(record, user=reader)
    return record, owner, reader, follower


def test_direct_record_revoke_ends_unreadable_follows(record_access_target: tuple[Project, Any, Any]) -> None:
    record, owner, reader, follower = _followed_record(record_access_target)

    record.with_actor(owner).revoke_record_access("reader", reader)

    assert not record.with_actor(reader).has_access("read")
    assert not ThreadFollower._base_manager.filter(pk=follower.pk).exists()


def test_absent_record_grant_still_rechecks_follows(record_access_target: tuple[Project, Any, Any]) -> None:
    record, owner, reader, follower = _followed_record(record_access_target)
    delete_relationship(RelationshipTuple(to_object_ref(record), "reader", to_subject_ref(reader)))
    assert ThreadFollower._base_manager.filter(pk=follower.pk).exists()

    record.with_actor(owner).revoke_record_access("reader", reader)

    assert not ThreadFollower._base_manager.filter(pk=follower.pk).exists()


def test_follow_cleanup_failure_rolls_back_grant_and_follow(
    record_access_target: tuple[Project, Any, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    record, owner, reader, follower = _followed_record(record_access_target)
    cleanup = ThreadFollowerManager.end_unreadable_for_record

    def fail_after_cleanup(self: ThreadFollowerManager, target: models.Model, *, role: str = "chatter") -> int:
        assert cleanup(self, target, role=role) == 1
        raise RuntimeError("Receiver failed after deleting the follow")

    monkeypatch.setattr(ThreadFollowerManager, "end_unreadable_for_record", fail_after_cleanup)
    with pytest.raises(RuntimeError, match="Receiver failed"):
        record.with_actor(owner).revoke_record_access("reader", reader)

    assert record.with_actor(reader).has_access("read")
    assert ThreadFollower._base_manager.filter(pk=follower.pk).exists()


def test_plain_django_host_can_save_post_and_delete_chatter(composed_tables: None) -> None:
    del composed_tables
    record = PlainChatterRecord.objects.create(title="Plain host")

    message = record.message_post("No Angee model base required")

    assert message.threaded_record() == record
    assert message.preview == "No Angee model base required"
    record.delete()
    assert not Message._base_manager.filter(pk=message.pk).exists()


def test_missing_chatter_target_is_rejected_at_the_attachment(composed_tables: None) -> None:
    del composed_tables
    edge = ThreadAttachment(
        content_type=ContentType.objects.get_for_model(PlainChatterRecord), object_id=1,
    )

    with pytest.raises(ValueError, match="surviving threaded record"):
        edge.require_threaded_target()
