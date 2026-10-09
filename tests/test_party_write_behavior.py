"""Party write behavior."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from rebac import system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Address, Handle, Party, PartyHandle
from angee.parties.mixins import LinkSource


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
def test_suggestion_sweep_resolves_and_recounts_idempotently(
    composed_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Suggestion sweep resolves and recounts idempotently."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="routed-suggestion-owner")
    with system_context(reason="suggestion fixture"):
        party = Party._base_manager.create(display_name="Customer", created_by_id=owner.pk)
        Handle._base_manager.create(
            platform="email",
            value="customer@example.test",
            display_name="Customer",
            party=party,
            created_by_id=owner.pk,
        )
        candidate = Handle._base_manager.create(
            platform="phone", value="+420777123456", display_name="Customer", created_by_id=owner.pk
        )
        created = PartyHandle.objects.suggest_from_display_names()
        repeated = PartyHandle.objects.suggest_from_display_names()
        candidate.refresh_from_db()
        party.refresh_from_db()
        link = PartyHandle._base_manager.get(handle_id=candidate.pk, party_id=party.pk)
    assert (created, repeated) == (1, 0)
    assert candidate.party_id == party.pk
    assert party.handle_count == 2
    assert link.is_confirmed is False


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
