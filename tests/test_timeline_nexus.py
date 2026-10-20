"""Correspondence shares the ledger, including live identity and circle changes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import override_settings
from rebac import actor_context, system_context, to_subject_ref

from angee.identity import public_id_of
from angee.nexus.timeline import CORRESPONDENCE_KIND, CorrespondenceKind
from angee.refs import generic_pointer_target
from angee.timeline.events import sync_events
from angee.timeline.kinds import timeline_kinds

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
        user = get_user_model().objects.create_user(username="correspondence")
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
            parent=parent, circle=circle, member=member, events=apps.get_model("timeline", "Event").objects,
        )


def test_nexus_message_projects_once_per_party_and_circle(correspondence) -> None:  # noqa: ANN001
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
    assert data.events.filter(kind=kind.id).count() == 3
    for record in (data.party, data.circle, data.parent):
        event = data.events.for_record(record).get(kind=kind.id)
        assert event.occurrence == "message"
        assert (event.target_model, event.target_id) == ("messaging.Message", public_id_of(data.message))
        assert event.actor == str(to_subject_ref(data.handle))
        assert event.actor_label == "Ada"
        assert event.title == "Greeting" and event.at == AT
        assert event.detail == "email · inbound · Hello"
    before = list(data.events.order_by("pk").values())
    data.message.save(update_fields=["preview"])
    assert list(data.events.order_by("pk").values()) == before
    data.message.preview = "Edited"
    data.message.save(update_fields=["preview"])
    assert set(data.events.filter(kind=kind.id).values_list("detail", flat=True)) == {"email · inbound · Edited"}
    data.message.is_trashed = True
    data.message.save(update_fields=["is_trashed"])
    assert not data.events.filter(kind=kind.id).exists()


def test_nexus_uses_the_ledger_identity_contract(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    event = data.events.for_record(data.party).get(kind=CORRESPONDENCE_KIND)
    assert event.key == (
        f"nexus.correspondence:messaging.Message:{data.message.pk}:"
        f"{event.record_content_type_id}:{data.party.pk}:message"
    )
    assert event.target_pk == data.message.pk
    assert event.target_id == public_id_of(data.message)


@override_settings(ANGEE_TIMELINE_KINDS=["angee.nexus.timeline.timeline_kinds"])
def test_nexus_correspondence_keeps_the_message_read_gate(composed_tables) -> None:  # noqa: ANN001
    events = apps.get_model("timeline", "Event").objects
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
        assert events.for_record(party).count() == 1
    with actor_context(reader):
        assert not events.for_record(party).for_reader(reader).exists()
    with system_context(reason="test nexus target grant"):
        thread.owner = reader
        thread.save(update_fields=["owner"])
    with actor_context(reader):
        assert events.for_record(party).for_reader(reader).get().detail.endswith("Private")


def test_nexus_empty_dependency_projections_remove_stale_lines(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    data.member.delete()
    assert not data.events.for_record(data.circle).exists()
    assert not data.events.for_record(data.parent).exists()
    assert data.events.for_record(data.party).count() == 1
    data.handle.party = None
    data.handle.save(update_fields=["party"])
    assert not data.events.filter(kind=CORRESPONDENCE_KIND).exists()
    data.handle.party = data.other
    data.handle.save(update_fields=["party"])
    assert data.events.for_record(data.other).count() == 1
    data.participant.delete()
    assert not data.events.filter(kind=CORRESPONDENCE_KIND).exists()


def test_nexus_correspondence_excludes_record_chatter(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    content_type, object_id = generic_pointer_target(data.party)
    attachment = apps.get_model("messaging", "ThreadAttachment")._base_manager.create(
        thread=data.thread, content_type=content_type, object_id=object_id, role="chatter",
    )
    assert not data.events.filter(kind=CORRESPONDENCE_KIND).exists()
    assert list(CorrespondenceKind().rows()) == []
    attachment.delete()
    assert data.events.filter(kind=CORRESPONDENCE_KIND).count() == 3


def test_nexus_backfill_is_idempotent(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    before = {event.key for event in data.events.all()}
    data.events.all().delete()
    rows = list(CorrespondenceKind().rows())
    assert rows == [data.message]
    for row in rows:
        sync_events(row)
        snapshot = list(data.events.order_by("pk").values())
        sync_events(row)
        assert list(data.events.order_by("pk").values()) == snapshot
    assert {event.key for event in data.events.all()} == before


def test_nexus_only_confirmed_memberships_project_and_reparent_moves_ancestors(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    for confirmed, dismissed in ((False, False), (True, True), (True, False)):
        data.member.is_confirmed, data.member.is_dismissed = confirmed, dismissed
        data.member.save(update_fields=["is_confirmed", "is_dismissed"])
        assert data.events.for_record(data.circle).exists() == (confirmed and not dismissed)
        assert data.events.for_record(data.parent).exists() == (confirmed and not dismissed)
    data.circle.parent = None
    data.circle.save(update_fields=["parent"])
    assert not data.events.for_record(data.parent).exists()
    assert data.events.for_record(data.circle).exists()


def test_nexus_ignores_unread_fields_before_target_discovery(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    with patch.object(CorrespondenceKind, "targets", side_effect=AssertionError("Unrelated change")) as targets:
        data.circle.name = "Renamed"
        data.circle.save(update_fields=["name"])
        data.thread.message_count = 10000
        data.thread.save(update_fields=["message_count"])
        data.handle.save(update_fields=["updated_at"])
    targets.assert_not_called()


def test_nexus_commit_deduplication_preserves_savepoint_retries(correspondence) -> None:  # noqa: ANN001
    data = correspondence
    with patch("angee.timeline.events.sync_events", wraps=sync_events) as sync:
        with transaction.atomic():
            with pytest.raises(ValueError):
                with transaction.atomic():
                    data.message.preview = "Rolled back"
                    data.message.save(update_fields=["preview"])
                    raise ValueError("rollback")
            for preview in ("First", "Final"):
                data.message.preview = preview
                data.message.save(update_fields=["preview"])
            assert not sync.called
        assert sync.call_count == 1
    assert data.events.for_record(data.party).get().detail.endswith("Final")


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
    assert data.events.for_record(source).exists()
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
    assert not data.events.for_record(source).exists()
    source.is_trashed = False
    source.save(update_fields=["is_trashed"])
    assert data.events.for_record(source).exists()
    # Exercise the actual merge owner, including its bulk handle moves.
    party = apps.get_model("parties", "Party")
    with actor_context(data.parent.owner):
        party.objects.get(pk=survivor.pk).merge(records=[public_id_of(party.objects.get(pk=source.pk))])
    assert not data.events.for_record(source).exists()
    assert data.events.for_record(survivor).exists()
