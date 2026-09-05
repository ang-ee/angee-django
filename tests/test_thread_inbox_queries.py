"""Native query shape and visibility contracts for the generic thread inbox."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from tests.chatterdemo.models import ChatterDoc
from tests.test_messaging import Fragment, Thread, ThreadAttachment, messaging_tables  # noqa: F401

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("messaging_tables")]


def test_inbox_antijoin_preserves_title_projection_and_null_timestamp_ties() -> None:
    """Attachment absence must not force a left join across every inbox row."""

    at = datetime(2026, 9, 1, tzinfo=UTC)
    with system_context(reason="test.thread.inbox.order"):
        threads = [
            Thread.objects.create(
                last_message_at=timestamp,
                title=Fragment.objects.upsert(text=f"Inbox title {index}"),
            )
            for index, timestamp in enumerate((None, at, at + timedelta(days=1), None, at + timedelta(days=1)))
        ]
        record = ChatterDoc.objects.create(title="Private record")
        attached = Thread.objects.create(last_message_at=at + timedelta(days=2))
        for role in ("chatter", "source"):
            ThreadAttachment.objects.create(
                thread=attached,
                content_type=ContentType.objects.get_for_model(record),
                object_id=record.pk,
                role=role,
            )

        inbox = Thread.objects.inbox().select_related("title").only("id", "last_message_at", "title__text")
        with CaptureQueriesContext(connection) as captured:
            rows = list(inbox)
            titles = [row.title.text for row in rows]

        expected = [threads[index] for index in (2, 4, 1, 0, 3)]
        assert [row.pk for row in rows] == [row.pk for row in expected]
        assert titles == [f"Inbox title {index}" for index in (2, 4, 1, 0, 3)]
        assert inbox.count() == 5
        assert not Thread.objects.inbox().filter(pk=attached.pk).exists()
        assert len(captured) == 1
        # This SQL property is the measured performance regression: a null-filtered
        # left join badly underestimates surviving rows on a populated inbox.
        assert re.search(r"\bNOT\s*\(*\s*EXISTS\s*\(", captured[0]["sql"])


def test_inbox_excludes_owned_thread_when_its_record_is_inaccessible() -> None:
    """Generic thread ownership cannot substitute for the parent record's read."""

    owner = get_user_model().objects.create_user(username="inbox-structural-owner")
    with system_context(reason="test.thread.inbox.record.seed"):
        record = ChatterDoc.objects.create(title="Inaccessible record")
        attached = Thread.objects.create(created_by=owner)
        ordinary = Thread.objects.create(created_by=owner)
        ThreadAttachment.objects.create(
            thread=attached,
            content_type=ContentType.objects.get_for_model(record),
            object_id=record.pk,
        )

    with actor_context(owner):
        assert not ChatterDoc.objects.filter(pk=record.pk).exists()
        assert Thread.objects.filter(pk=attached.pk).exists()
        assert list(Thread.objects.inbox().values_list("pk", flat=True)) == [ordinary.pk]
        assert Thread.objects.inbox().count() == 1
        assert not Thread.objects.inbox().filter(pk=attached.pk).exists()
