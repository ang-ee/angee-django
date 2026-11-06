"""Elevated collections retain the pinned or ambient requester for derived reads."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db.models import OuterRef, Subquery
from rebac import actor_context, system_context, to_subject_ref

from angee.iam.permissions import instance_actor
from angee.messaging.inbox import MessageInbox
from angee.messaging.testing.models import (
    Channel,
    Fragment,
    Handle,
    Message,
    Organization,
    Participant,
    Party,
    Relationship,
    RelationshipKind,
    Thread,
)
from angee.nexus.testing.models import Tie
from tests.conftest import create_platform_admin, make_integration

pytestmark = pytest.mark.django_db(transaction=True)


def _users() -> tuple[Any, Any]:
    user_model = get_user_model()
    return (
        user_model.objects.create_user(username="sudo-actor-owner"),
        user_model.objects.create_user(username="sudo-actor-outsider"),
    )


def test_readable_scalar_subquery_uses_ambient_actor_under_sudo(composed_tables: None) -> None:
    owner, outsider = _users()
    with system_context(reason="test sudo scalar seed"):
        visible = Party._base_manager.create(display_name="Visible", created_by=owner)
        hidden = Party._base_manager.create(display_name="Hidden", created_by=outsider)

    with actor_context(owner):
        scalar = (
            Party.objects.sudo(reason="test sudo scalar")
            .filter(pk=OuterRef("pk"))
            .readable_scalar_subquery("display_name", default="redacted")
        )
        names = dict(
            Party._base_manager.filter(pk__in=(visible.pk, hidden.pk)).annotate(name=scalar).values_list("pk", "name")
        )

    assert names == {visible.pk: "Visible", hidden.pk: "redacted"}


def test_keyset_cursor_scope_names_ambient_actor_under_sudo(composed_tables: None) -> None:
    del composed_tables
    owner, _ = _users()
    with actor_context(owner):
        scope = Party.objects.sudo(reason="test sudo cursor")._keyset_cursor_scope(("parties",))
    assert scope == ("parties", str(to_subject_ref(owner)))


def test_handle_sender_name_scopes_elevated_rows_to_ambient_actor(composed_tables: None) -> None:
    owner, outsider = _users()
    with system_context(reason="test sudo handle seed"):
        visible = Party._base_manager.create(display_name="Visible sender", created_by=owner)
        hidden = Party._base_manager.create(display_name="Hidden sender", created_by=outsider)
        own_handle = Handle._base_manager.create(
            platform="email", value="own@example.test", party=visible, party_link_confirmed=True, created_by=owner
        )
        other_handle = Handle._base_manager.create(
            platform="email", value="other@example.test", party=hidden, party_link_confirmed=True, created_by=outsider
        )

    with actor_context(owner):
        names = dict(
            Handle.objects.sudo(reason="test sudo sender")
            .with_sender_name()
            .filter(pk__in=(own_handle.pk, other_handle.pk))
            .values_list("pk", "_sender_name")
        )

    assert names == {own_handle.pk: "Visible sender"}


def test_sender_name_expression_keeps_the_party_read_gate_under_sudo(composed_tables: None) -> None:
    owner, outsider = _users()
    with system_context(reason="test sudo sender expression seed"):
        visible = Party._base_manager.create(display_name="Visible sender", created_by=owner)
        hidden = Party._base_manager.create(display_name="Hidden sender", created_by=outsider)
        own_handle = Handle._base_manager.create(
            platform="email",
            value="own@example.test",
            display_name="Envelope own",
            party=visible,
            party_link_confirmed=True,
            created_by=owner,
        )
        other_handle = Handle._base_manager.create(
            platform="email",
            value="other@example.test",
            display_name="Envelope other",
            party=hidden,
            party_link_confirmed=True,
            created_by=outsider,
        )

    with actor_context(owner), system_context(reason="test ambient sudo sender expression"):
        expression = Handle.objects.sender_name_expression()
        names = dict(
            Handle._base_manager.filter(pk__in=(own_handle.pk, other_handle.pk))
            .annotate(sender_name=expression)
            .values_list("pk", "sender_name")
        )

    assert names == {own_handle.pk: "Visible sender", other_handle.pk: "Envelope other"}


def test_resolved_confidence_expression_keeps_reader_under_sudo(
    composed_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _ = _users()
    seen: list[Any] = []

    def for_reader(rows: Any, actor: Any) -> Any:
        seen.append(actor)
        return rows.none()

    monkeypatch.setattr(type(Party.objects.handle_statements()), "for_reader", for_reader)

    with actor_context(owner):
        expression = Handle.objects.sudo(reason="test sudo confidence").resolved_confidence_expression()

    assert isinstance(expression, Subquery)
    assert seen == [to_subject_ref(owner)]


def test_named_legal_name_uses_reader_under_sudo(composed_tables: None) -> None:
    owner, outsider = _users()
    with system_context(reason="test sudo legal name seed"):
        visible = Organization._base_manager.create(display_name="Visible", legal_name="Shared Legal", created_by=owner)
        Organization._base_manager.create(display_name="Hidden", legal_name="Shared Legal", created_by=outsider)

    with actor_context(owner), system_context(reason="test ambient sudo legal name"):
        matches = list(Party.objects.named("Shared Legal"))

    assert matches == [Party._base_manager.get(pk=visible.pk)]


def test_to_review_uses_reader_under_sudo(composed_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    owner, _ = _users()
    seen: list[Any] = []

    def for_reader(rows: Any, actor: Any) -> Any:
        seen.append(actor)
        return rows.none()

    monkeypatch.setattr(type(Party.objects.handle_statements()), "for_reader", for_reader)

    with actor_context(owner):
        rows = Party.objects.sudo(reason="test sudo review").to_review()

    assert rows.model is Party
    assert seen == [to_subject_ref(owner)]


def test_organization_memberships_scope_both_endpoints_under_sudo(composed_tables: None) -> None:
    owner, outsider = _users()
    with system_context(reason="test sudo membership seed"):
        kind = RelationshipKind._base_manager.create(slug="sudo-member", name="Member")
        visible = Party._base_manager.create(display_name="Visible", created_by=owner)
        hidden = Party._base_manager.create(display_name="Hidden", created_by=outsider)
        own_org = Organization._base_manager.create(display_name="Own org", created_by=owner)
        other_org = Organization._base_manager.create(display_name="Other org", created_by=outsider)
        own_relation = Relationship._base_manager.create(
            party=visible, other_party=own_org, kind=kind, created_by=owner
        )
        Relationship._base_manager.create(party=hidden, other_party=other_org, kind=kind, created_by=outsider)

    with actor_context(owner), system_context(reason="test ambient sudo memberships"):
        matches = list(
            Party.objects.organization_memberships().values_list("pk", flat=True)
        )

    assert matches == [own_relation.pk]


def test_fading_parties_for_scopes_counterparties_under_sudo(composed_tables: None) -> None:
    owner, outsider = _users()
    with system_context(reason="test sudo nexus seed"):
        viewer = Party._base_manager.create(display_name="Viewer", created_by=owner)
        visible = Party._base_manager.create(display_name="Visible", created_by=owner)
        hidden = Party._base_manager.create(display_name="Hidden", created_by=outsider)
        for counterparty in (visible, hidden):
            first, second = sorted((viewer, counterparty), key=lambda party: party.pk)
            Tie._base_manager.create(party_a=first, party_b=second, is_fading=True)

    with actor_context(owner):
        matches = list(
            Tie.objects.sudo(reason="test sudo fading").fading_parties_for(viewer).values_list("pk", flat=True)
        )

    assert matches == [visible.pk]


def test_related_manager_retains_parent_actor_under_ambient_sudo(composed_tables: None) -> None:
    owner, outsider = _users()
    with system_context(reason="test related handle seed"):
        party = Party._base_manager.create(display_name="Visible", created_by=owner)
        Handle._base_manager.create(
            platform="email", value="related@example.test", party=party, party_link_confirmed=True, created_by=owner
        )

    with actor_context(outsider), system_context(reason="test related handle read"):
        related = Party.objects.with_actor(owner).get(pk=party.pk).handles
        assert instance_actor(related.all()) == to_subject_ref(owner)
        assert list(related.with_sender_name().values_list("_sender_name", flat=True)) == ["Visible"]


def _message_relation_rows(*, titled: bool = True) -> tuple[Any, Any, Any, Any, Any, Any]:
    owner = create_platform_admin("sudo-relation-owner")
    outsider = get_user_model().objects.create_user(username="sudo-relation-outsider")
    with system_context(reason="test message relation seed"):
        channel = make_integration("sudo-relation-channel", model=Channel, owner=owner)
        party = Party._base_manager.create(display_name="Visible sender", created_by=owner)
        handle = Handle._base_manager.create(
            platform="email", value="sender@example.test", display_name="Envelope sender",
            party=party, party_link_confirmed=True, created_by=owner,
        )
        thread = Thread._base_manager.create(
            channel=channel, created_by=owner,
            modality=Thread.Modality.DIRECT,
            title=Fragment.objects.upsert(text="Visible thread") if titled else None,
        )
        message = Message._base_manager.create(
            channel=channel, thread=thread, sender=handle, created_by=owner, direction="inbound",
        )
        Participant._base_manager.create(
            thread=thread, message=message, handle=handle, role=Participant.ParticipantRole.FROM,
            created_by=owner,
        )
    return owner, outsider, channel, thread, message, handle


def test_conversation_labels_use_related_channel_actor_under_sudo(composed_tables: None) -> None:
    owner, outsider, channel, thread, _, _ = _message_relation_rows(titled=False)
    with actor_context(outsider), system_context(reason="test related conversation labels"):
        related = Channel.objects.with_actor(owner).get(pk=channel.pk).messaging_threads
        assert related.conversation_labels() == {thread.pk: "Visible sender"}


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("sender_name_expression", "Visible sender"),
        ("thread_title_expression", "Visible thread"),
        ("channel_vendor_name_expression", "Sudo-Relation-Channel"),
    ],
)
def test_message_projections_use_related_thread_actor_under_sudo(
    composed_tables: None, method: str, expected: str
) -> None:
    owner, outsider, _, thread, message, _ = _message_relation_rows()
    with actor_context(outsider), system_context(reason="test related message projection"):
        related = Thread.objects.with_actor(owner).get(pk=thread.pk).messages
        expression = getattr(related, method)()
        value = (
            Message._base_manager.filter(pk=message.pk)
            .annotate(projected=expression)
            .values_list("projected", flat=True)
            .get()
        )
    assert value == expected


def test_message_inbox_uses_related_thread_actor_under_sudo(composed_tables: None) -> None:
    owner, outsider, _, thread, _, handle = _message_relation_rows()
    with actor_context(outsider), system_context(reason="test related message inbox"):
        related = Thread.objects.with_actor(owner).get(pk=thread.pk).messages
        inbox = MessageInbox(related.all())
        assert inbox.actor == to_subject_ref(owner)
        assert inbox.user_id == owner.pk
        assert inbox.handles.filter(pk=handle.pk).exists()


def test_party_graph_identity_uses_queryset_actor_under_sudo(
    composed_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, outsider = _users()
    with system_context(reason="test related party graph seed"):
        root = Party._base_manager.create(display_name="Root", created_by=owner)
    seen: list[Any] = []

    def for_reader(rows: Any, actor: Any) -> Any:
        seen.append(actor)
        return rows.none()

    monkeypatch.setattr(type(Party.objects.handle_statements()), "for_reader", for_reader)
    with actor_context(outsider), system_context(reason="test party graph identity"):
        selected_root = Party.objects.with_actor(owner).with_concrete_kind().get(pk=root.pk)
        graph = Tie.objects.with_actor(owner).party_graph(root=selected_root, lenses=("identity",), depth=0)
    assert graph.nodes[0]["title"] == "Root"
    assert seen == [to_subject_ref(owner)]
