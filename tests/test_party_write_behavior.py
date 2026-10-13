"""Party write behavior."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Address, Handle, Party


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
def test_stale_owner_repair_unowns_a_handle_a_suggestion_decided(composed_tables: None) -> None:
    """A handle a suggestion owned under the old resolution rule is re-resolved once."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="stale-owner-repair-owner")
    with system_context(reason="stale owner fixture"):
        party = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        handle = Handle._base_manager.create(platform="phone", value="+14155552671", created_by=owner)
        Party.objects.assert_handle(party, handle, asserted_by=owner, confidence=0.3)
        Handle._base_manager.filter(pk=handle.pk).update(party=party)
        Party._base_manager.filter(pk=party.pk).update(handle_count=1)

        repaired = Handle.objects.resolve_stale_owners()
        repeated = Handle.objects.resolve_stale_owners()
        handle.refresh_from_db()
        party.refresh_from_db()
    assert (repaired, repeated) == (1, 0)
    assert handle.party_id is None
    assert party.handle_count == 0


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
