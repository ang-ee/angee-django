"""Party write behavior."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from rebac import actor_context, system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Address, Channel, Fragment, Handle, Message, Part, Party, PartyHandle
from angee.parties.mixins import LinkSource
from angee.parties.tasks import refresh_handle_suggestions
from tests.conftest import make_integration


@pytest.mark.django_db(transaction=True)
def test_handle_upsert_collision_refreshes_existing_row(composed_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Handle upsert collision refreshes existing row."""
    del composed_tables
    with system_context(reason="handle refresh fixture"):
        source = Handle.objects.upsert(platform="email", value="old@example.test", external_id="stable-account")
        existing = Handle.objects.upsert(platform="email", value="new@example.test", display_name="Before")
        owner = Handle.objects
        refreshed = owner.upsert(
            platform="email", value="new@example.test", external_id="stable-account", display_name="After"
        )
        existing.refresh_from_db()
        source.refresh_from_db()
    assert refreshed.pk == existing.pk
    assert existing.display_name == "After"
    assert source.value == "old@example.test"


@pytest.mark.django_db(transaction=True)
def test_display_name_suggestions_need_a_personal_full_name_the_party_bears(composed_tables: None) -> None:
    """Only a full name the party itself bears is suggested, once and for review only."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="routed-suggestion-owner")
    with system_context(reason="suggestion fixture"):

        def handle(platform: str, value: str, name: str, party: Party | None = None) -> Handle:
            row = Handle._base_manager.create(platform=platform, value=value, display_name=name, created_by=owner)
            if party is not None:
                PartyHandle.objects.link(party, row, source=LinkSource.CARDDAV, created_by_id=owner.pk)
            return row

        ada = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        brand = Party._base_manager.create(display_name="Customer", created_by=owner)
        grab_bag = Party._base_manager.create(display_name="Unknown", created_by=owner)
        handle("email", "ada@example.test", "Ada Lovelace", ada)
        handle("email", "customer@example.test", "Customer", brand)
        handle("phone", "+420777000001", "Grace Hopper", grab_bag)
        candidate = handle("phone", "+420777123456", "Ada Lovelace")
        handle("phone", "+420777123457", "Customer")
        handle("email", "grace@example.test", "Grace Hopper")
        notifications = handle("email", "notifications@example.test", "Ada Lovelace")

        created = PartyHandle.objects.suggest_from_display_names(shared_handle_ids=[notifications.pk])
        repeated = PartyHandle.objects.suggest_from_display_names(shared_handle_ids=[notifications.pk])
        suggested = set(PartyHandle._base_manager.filter(source=LinkSource.RULE).values_list("party_id", "handle_id"))
        candidate.refresh_from_db()
        ada.refresh_from_db()
    assert (created, repeated) == (1, 0)
    assert suggested == {(ada.pk, candidate.pk)}
    assert candidate.party_id is None
    assert ada.handle_count == 1


@pytest.mark.django_db(transaction=True)
def test_stale_owner_repair_unowns_a_handle_a_suggestion_decided(composed_tables: None) -> None:
    """A handle a suggestion owned under the old resolution rule is re-resolved once."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="stale-owner-repair-owner")
    with system_context(reason="stale owner fixture"):
        party = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        handle = Handle._base_manager.create(platform="phone", value="+14155552671", created_by=owner)
        PartyHandle.objects.link(party, handle, confidence=0.3, source=LinkSource.RULE, created_by_id=owner.pk)
        Handle._base_manager.filter(pk=handle.pk).update(party=party)
        Party._base_manager.filter(pk=party.pk).update(handle_count=1)

        repaired = PartyHandle.objects.resolve_stale_owners()
        repeated = PartyHandle.objects.resolve_stale_owners()
        handle.refresh_from_db()
        party.refresh_from_db()
    assert (repaired, repeated) == (1, 0)
    assert handle.party_id is None
    assert party.handle_count == 0


@pytest.mark.django_db(transaction=True)
def test_signature_phones_are_mined_only_from_a_senders_own_signature(composed_tables: None) -> None:
    """Only a fragment one sender alone signs with is mined, and only for real phone numbers."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="signature-mining-owner")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="signature mining fixture"):
        ada = Party._base_manager.create(display_name="Ada", created_by=owner)
        senders = {
            name: Handle._base_manager.create(platform="email", value=f"{name}@example.test", created_by=owner)
            for name in ("ada", "carol")
        }
        PartyHandle.objects.link(ada, senders["ada"], source=LinkSource.CARDDAV, created_by_id=owner.pk)
        own = Fragment.objects.upsert(text="Ada Lovelace\n+1 415 555 2671\nIssue 1721429715", created_by_id=owner.pk)
        shared = Fragment.objects.upsert(text="Monica Alvarez\n+1 787 523 6508", created_by_id=owner.pk)
        for sender, fragment in (("ada", own), ("ada", shared), ("carol", shared)):
            message = Message._base_manager.create(
                channel=channel,
                created_by=owner,
                sender=senders[sender],
                direction="inbound",
                status="synced",
                sent_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            Part._base_manager.create(created_by=owner, message=message, role="signature", fragment=fragment)
        refresh_handle_suggestions(lookback_hours=None)
        mined = set(
            PartyHandle._base_manager.filter(source=LinkSource.RULE, handle__platform="phone").values_list(
                "party_id", "handle__normalized_value"
            )
        )
    assert mined == {(ada.pk, "+14155552671")}


@pytest.mark.django_db(transaction=True)
def test_retracting_suggestions_keeps_reviewed_links(composed_tables: None) -> None:
    """Retraction deletes only unreviewed rule links of its evidence kind; the confirmed one still owns."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="signature-retraction-owner")
    with system_context(reason="signature retraction fixture"):
        party = Party._base_manager.create(display_name="Ada", created_by=owner)
        PartyHandle.objects.suggest_from_signature(
            text="+1 415 555 2671\n+1 415 555 2672\n+1 415 555 2673",
            party_id=party.pk,
            fragment_hash="ada-signature",
            owner_id=owner.pk,
        )
        confirmed, dismissed, unreviewed = PartyHandle._base_manager.filter(party=party).order_by(
            "handle__normalized_value"
        )
        with actor_context(owner):
            confirmed.confirm()
            dismissed.dismiss()
        retracted = PartyHandle.objects.retract_suggestions(evidence_kind="signature_phone")
        remaining = set(PartyHandle._base_manager.filter(party=party).values_list("pk", flat=True))
        handle = Handle._base_manager.get(pk=unreviewed.handle_id)
        party.refresh_from_db()
    assert retracted == 1
    assert remaining == {confirmed.pk, dismissed.pk}
    assert handle.party_id is None
    assert party.handle_count == 1


@pytest.mark.django_db(transaction=True)
def test_primary_address_save_demotes_previous_primary(composed_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Primary address save demotes previous primary."""
    del composed_tables
    with system_context(reason="primary address fixture"):
        party = Party._base_manager.create(display_name="Customer")
        previous = Address._base_manager.create(party=party, is_primary=True)
        selected = Address._base_manager.create(party=party)
        selected.is_primary = True
        selected.save(update_fields=["is_primary"])
        previous.refresh_from_db()
        selected.refresh_from_db()
    assert selected.is_primary is True
    assert previous.is_primary is False


@pytest.mark.django_db(transaction=True)
def test_party_link_delete_repairs_after_commit(composed_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Party link delete repairs after commit."""
    del composed_tables
    with system_context(reason="delete repair fixture"):
        party = Party._base_manager.create(display_name="Customer")
        handle = Handle.objects.upsert(platform="email", value="repair@example.test")
        link = PartyHandle.objects.link(party, handle, source=LinkSource.MANUAL)
        with transaction.atomic():
            link.delete()
            assert Handle._base_manager.get(pk=handle.pk).party_id == party.pk
        handle.refresh_from_db()
        party.refresh_from_db()
    assert handle.party_id is None
    assert party.handle_count == 0
