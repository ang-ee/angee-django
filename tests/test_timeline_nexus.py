"""Correspondence shares the timeline, including live identity and circle changes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from angee.graphql.publishing import mute_changes, publish_change
from angee.base.identity import public_id_of
from angee.nexus.timeline import CORRESPONDENCE_KIND, CorrespondenceKind
from angee.base.refs import generic_pointer_target
from angee.timeline.kinds import timeline_kinds
from tests.conftest import create_platform_admin
from tests.test_timeline import timeline_entries

pytestmark = pytest.mark.django_db(transaction=True)
AT = datetime(2026, 10, 19, 12, tzinfo=UTC)


@pytest.fixture
def correspondence(composed_tables):  # noqa: ANN001, ANN201 - fixture namespace
    with (
        override_settings(ANGEE_TIMELINE_KINDS=["angee.nexus.timeline.timeline_kinds"]),
        system_context(reason="test nexus timeline"),
    ):
        party_model = apps.get_model("parties", "Party")
        handle_model = apps.get_model("parties", "Handle")
        message_model = apps.get_model("messaging", "Message")
        participant_model = apps.get_model("messaging", "Participant")
        circle_model = apps.get_model("parties", "Circle")
        user = create_platform_admin("correspondence")
        party = party_model._base_manager.create(display_name="Ada")
        other = party_model._base_manager.create(display_name="Grace")
        handle = handle_model._base_manager.create(
            party=party, platform="email", value="ada@example.com", display_name="Ada",
        )
        thread = apps.get_model("messaging", "Thread")._base_manager.create(platform="email")
        message = message_model._base_manager.create(
            sender=handle, thread=thread, platform="email", direction="inbound", sent_at=AT, preview="Hello",
        )
        title = apps.get_model("messaging", "Fragment").objects.upsert(text="Greeting")
        apps.get_model("messaging", "Part")._base_manager.create(message=message, role="title", fragment=title)
        participant = participant_model._base_manager.create(message=message, thread=thread, handle=handle, role="from")
        parent = circle_model._base_manager.create(owner=user, name="People")
        circle = circle_model._base_manager.create(owner=user, name="Friends", parent=parent)
        member = apps.get_model("parties", "CircleMember")._base_manager.create(
            party=party, circle=circle, is_confirmed=True,
        )
        yield SimpleNamespace(
            party=party, other=other, handle=handle, thread=thread, message=message, participant=participant,
            parent=parent, circle=circle, member=member, actor=user,
        )


def test_nexus_message_appears_once_per_party_and_circle(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    kind, = timeline_kinds()
    assert kind.id == CORRESPONDENCE_KIND and kind.sequence == 60
    assert kind.eligible(type(data.party)) and kind.eligible(type(data.circle))
    assert kind.eligible(apps.get_model("parties", "Person"))
    assert not kind.eligible(type(data.message))
    # A second address for the same party does not duplicate a line on any record.
    handle = apps.get_model("parties", "Handle")._base_manager.create(
        party=data.party, platform="email", value="other@example.com",
    )
    apps.get_model("messaging", "Participant")._base_manager.create(message=data.message, handle=handle, role="to")
    for record in (data.party, data.circle, data.parent):
        event, = timeline_entries(record, data.actor)
        assert (event.target_model, event.target_id) == ("messaging.Message", public_id_of(data.message))
        assert event.actor == "Ada"
        assert event.title == "Greeting" and event.at == AT
        assert event.detail == "email · inbound · Hello"
    before = timeline_entries(data.party, data.actor)
    data.message.save(update_fields=["preview"])
    assert timeline_entries(data.party, data.actor) == before
    data.message.preview = "Edited"
    data.message.save(update_fields=["preview"])
    assert {line.detail for record in (data.party, data.circle, data.parent)
            for line in timeline_entries(record, data.actor)} == {"email · inbound · Edited"}
    data.message.is_trashed = True
    data.message.save(update_fields=["is_trashed"])
    assert all(timeline_entries(record, data.actor) == [] for record in (data.party, data.circle, data.parent))


def test_nexus_uses_the_timeline_identity_contract(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    event, = timeline_entries(data.party, data.actor)
    content_type, _ = generic_pointer_target(data.party)
    assert event.key == (
        f"nexus.correspondence:messaging.Message:{data.message.pk}:"
        f"{content_type.pk}:{data.party.pk}:message"
    )
    assert event.target_id == public_id_of(data.message)


@override_settings(ANGEE_TIMELINE_KINDS=["angee.nexus.timeline.timeline_kinds"])
def test_nexus_correspondence_keeps_the_message_read_gate(composed_tables) -> None:  # noqa: ANN001
    with system_context(reason="test nexus target visibility"):
        owner = get_user_model().objects.create_user(username="message-owner")
        reader = get_user_model().objects.create_user(username="party-reader")
        party = apps.get_model("parties", "Party")._base_manager.create(display_name="Shared", created_by=reader)
        handle = apps.get_model("parties", "Handle")._base_manager.create(party=party, value="shared@example.com")
        thread = apps.get_model("messaging", "Thread")._base_manager.create(owner=owner)
        message = apps.get_model("messaging", "Message")._base_manager.create(
            sender=handle, thread=thread, preview="Private",
        )
        apps.get_model("messaging", "Participant")._base_manager.create(message=message, handle=handle, role="from")
        assert len(timeline_entries(party, owner)) == 1
    with actor_context(reader):
        assert timeline_entries(party, reader) == []
    with system_context(reason="test nexus target grant"):
        thread.owner = reader
        thread.save(update_fields=["owner"])
    with actor_context(reader):
        assert timeline_entries(party, reader)[0].detail.endswith("Private")


def test_nexus_dependency_removals_are_immediately_visible(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    data.member.delete()
    assert timeline_entries(data.circle, data.actor) == []
    assert timeline_entries(data.parent, data.actor) == []
    assert len(timeline_entries(data.party, data.actor)) == 1
    data.handle.party = None
    data.handle.save(update_fields=["party"])
    assert all(timeline_entries(record, data.actor) == []
               for record in (data.party, data.other, data.circle, data.parent))
    data.handle.party = data.other
    data.handle.save(update_fields=["party"])
    assert len(timeline_entries(data.other, data.actor)) == 1
    data.participant.delete()
    assert all(timeline_entries(record, data.actor) == []
               for record in (data.party, data.other, data.circle, data.parent))


def test_nexus_correspondence_excludes_record_chatter(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    content_type, object_id = generic_pointer_target(data.party)
    attachment = apps.get_model("messaging", "ThreadAttachment")._base_manager.create(
        thread=data.thread, content_type=content_type, object_id=object_id, role="chatter",
    )
    assert all(timeline_entries(record, data.actor) == []
               for record in (data.party, data.other, data.circle, data.parent))
    attachment.delete()
    assert all(len(timeline_entries(record, data.actor)) == 1 for record in (data.party, data.circle, data.parent))


def test_nexus_only_confirmed_memberships_appear_and_reparent_moves_ancestors(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    for confirmed, dismissed in ((False, False), (True, True), (True, False)):
        data.member.is_confirmed, data.member.is_dismissed = confirmed, dismissed
        data.member.save(update_fields=["is_confirmed", "is_dismissed"])
        assert bool(timeline_entries(data.circle, data.actor)) == (confirmed and not dismissed)
        assert bool(timeline_entries(data.parent, data.actor)) == (confirmed and not dismissed)
    data.circle.parent = None
    data.circle.save(update_fields=["parent"])
    assert timeline_entries(data.parent, data.actor) == []
    assert timeline_entries(data.circle, data.actor)


def test_nexus_ignores_unread_fields_before_target_discovery(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    with patch.object(CorrespondenceKind, "targets", side_effect=AssertionError("Unrelated change")) as targets:
        data.circle.name = "Renamed"
        data.circle.save(update_fields=["name"])
        data.thread.message_count = 10000
        data.thread.save(update_fields=["message_count"])
        data.handle.save(update_fields=["updated_at"])
    targets.assert_not_called()


@pytest.mark.parametrize("subtype", ["Person", "Organization"])
def test_nexus_observes_party_descendants_and_merge_redirects(correspondence, subtype, monkeypatch) -> None:  # noqa: ANN001
    data = correspondence
    model = apps.get_model("parties", subtype)
    source = model._base_manager.create(display_name="Source", owner=data.parent.owner, created_by=data.parent.owner)
    survivor = model._base_manager.create(
        display_name="Survivor", owner=data.parent.owner, created_by=data.parent.owner,
    )
    broadcasts = []
    monkeypatch.setattr("angee.graphql.publishing._broadcast", lambda model, payload: broadcasts.append(payload))
    data.handle.party = source
    data.handle.save(update_fields=["party"])
    assert timeline_entries(source, data.actor)
    # Inherited record panes use their canonical Party feed; they gain no
    # undeclared Person/Organization transport just by contributing a kind.
    assert any(
        payload["model"] == "parties.Party" and payload["id"] == public_id_of(source)
        and {"model": "parties.Party", "id": public_id_of(source)} in payload.get("related_records", ())
        for payload in broadcasts
    ), broadcasts
    assert all(payload["model"] != model._meta.label for payload in broadcasts)
    source.is_trashed = True
    source.save(update_fields=["is_trashed"])
    assert timeline_entries(source, data.actor) == []
    source.is_trashed = False
    source.save(update_fields=["is_trashed"])
    assert timeline_entries(source, data.actor)
    # Exercise the actual merge owner, including its bulk handle moves.
    party = apps.get_model("parties", "Party")
    with actor_context(data.parent.owner):
        party.objects.get(pk=survivor.pk).merge(records=[public_id_of(party.objects.get(pk=source.pk))])
    assert timeline_entries(source, data.actor) == []
    assert timeline_entries(survivor, data.actor)


def test_reassignment_invalidates_old_and_new_records_after_commit(correspondence, monkeypatch):
    data = correspondence
    observed = []

    def broadcast(model, payload):
        if model._meta.label == "parties.Party":
            assert not connection.in_atomic_block
            observed.append(payload["id"])

    monkeypatch.setattr("angee.graphql.publishing._broadcast", broadcast)
    with transaction.atomic():
        data.handle.party = data.other
        data.handle.save(update_fields=["party"])
        assert observed == []
    assert {public_id_of(data.party), public_id_of(data.other)} <= set(observed)
    assert timeline_entries(data.party, data.actor) == []
    assert len(timeline_entries(data.other, data.actor)) == 1


def test_thread_invalidation_does_not_walk_every_message(correspondence):
    data = correspondence
    publish_change(data.thread, action="update", update_fields=["merged_into"])
    with CaptureQueriesContext(connection) as baseline:
        publish_change(data.thread, action="update", update_fields=["merged_into"])
    with mute_changes():
        for index in range(20):
            message = type(data.message)._base_manager.create(
                thread=data.thread, sender=data.handle, preview=str(index),
            )
            type(data.participant)._base_manager.create(message=message, handle=data.handle, role="from")
    with CaptureQueriesContext(connection) as grown:
        publish_change(data.thread, action="update", update_fields=["merged_into"])
    assert len(grown) <= len(baseline) + 1
    assert len(timeline_entries(data.party, data.actor)) == 21
