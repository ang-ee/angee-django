"""Sender and party avatars cost the same per page however many rows the page shows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from angee.base.identity import public_id_of
from angee.messaging.testing.models import Channel, Handle, Message, Party, Person
from tests.conftest import File, execute_schema, make_integration, result_data
from tests.test_nexus import _schema
from tests.test_storage import PNG_BYTES, drive, image_drive  # noqa: F401 -- the image drive avatars land on

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("composed_tables", "image_drive", "composed_permissions"),
]

SENDER = "id display_name value party_link_confirmed avatar { id } party { id display_name display_avatar { id } }"
PAGES = {
    "messages": f"{{ messages {{ id sender {{ {SENDER} }} }} }}",
    "inbox_navigator": f"{{ inbox_navigator {{ rows {{ id handle {{ {SENDER} }} party {{ id display_name }} }} }} }}",
    "same_as_pairs": "{ same_as_pairs { left { id display_avatar { id } } right { id display_avatar { id } } } }",
}
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_message_lists_the_inbox_navigator_and_same_as_pairs_batch_their_avatars() -> None:
    owner = get_user_model().objects.create_user(username="sender-avatar-owner")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    schema = _schema()
    request = RequestFactory().post("/graphql/console/")
    request.user = owner
    shown: dict[str, str] = {}

    def seed(start: int, count: int) -> None:
        """Each sender's person shows its own avatar (even rows) or its handle's, and has a duplicate twin."""

        with system_context(reason="test sender avatar seed"):
            for index in range(start, start + count):
                own = File.objects.ingest_image(PNG_BYTES + bytes([index])) if index % 2 == 0 else None
                person = Person._base_manager.create(display_name=f"Sender {index}", avatar=own, created_by=owner)
                sender = Handle.objects.upsert(
                    platform="whatsapp", value=f"+1858342{index:04d}", created_by_id=owner.pk
                )
                Party.objects.assert_handle(person, sender, asserted_by=owner)
                picture = File.objects.ingest_image(PNG_BYTES + bytes([100 + index]))
                Handle.objects.set_avatar(sender, picture)
                shown[public_id_of(person)] = public_id_of(own or picture)
                twin = Person._base_manager.create(display_name=f"Twin {index}", created_by=owner)
                for party, value in ((person, f"+1858342{index:04d}"), (twin, f"+1 858 342 {index:04d}")):
                    phone = Handle.objects.upsert(platform="phone", value=value, created_by_id=owner.pk)
                    Party.objects.assert_handle(party, phone, asserted_by=owner)
                Message._base_manager.create(
                    channel=channel,
                    created_by=owner,
                    sender=sender,
                    status="synced",
                    direction="inbound",
                    sent_at=T0 + timedelta(minutes=index),
                )
            # Twins share a normalized phone: the matching rule states the same_as pair.
            Party.objects.reconcile_same_as()

    def read(query: str) -> tuple[int, Any]:
        execute_schema(schema, query, request=request)  # warm the schema and permission caches
        with CaptureQueriesContext(connection) as queries:
            data = result_data(execute_schema(schema, query, request=request))
        return len(queries.captured_queries), data

    seed(0, 1)
    one = {name: read(query)[0] for name, query in PAGES.items()}
    seed(1, 3)
    four = {name: read(query) for name, query in PAGES.items()}
    assert {name: count for name, (count, _data) in four.items()} == one

    messages = four["messages"][1]["messages"]
    assert {row["sender"]["party"]["id"]: row["sender"]["party"]["display_avatar"]["id"] for row in messages} == shown
    navigator = four["inbox_navigator"][1]["inbox_navigator"]["rows"]
    assert {row["handle"]["party"]["id"]: row["handle"]["party"]["display_avatar"]["id"] for row in navigator} == shown
    pairs = four["same_as_pairs"][1]["same_as_pairs"]
    assert {
        side["id"]: side["display_avatar"]["id"]
        for pair in pairs
        for side in (pair["left"], pair["right"])
        if side["display_avatar"] is not None
    } == shown
