"""Party write behavior."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from rebac import system_context

from angee.base.refs import canonical_record_target
from angee.base.serialization import canonical_json_sha256
from tests.extraction_models import Extraction, ExtractionLineage
from tests.test_messaging import Address, Handle, Party, PartyHandle
from tests.test_parties_circles import parties_tables as parties_tables
from tests.test_workflows_extraction_service import extraction_tables as extraction_tables


@pytest.mark.django_db(transaction=True)
def test_handle_upsert_collision_refreshes_existing_row(parties_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Handle upsert collision refreshes existing row."""
    del parties_tables
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
    parties_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Suggestion sweep resolves and recounts idempotently."""
    del parties_tables
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
def test_primary_address_save_demotes_previous_primary(parties_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Primary address save demotes previous primary."""
    del parties_tables
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
def test_extraction_retention_reuses_and_advances_lineage(
    extraction_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extraction retention reuses and advances lineage."""
    del extraction_tables
    schema = {"$id": "tests.routing.v1", "type": "object"}
    with system_context(reason="retained evidence fixture"):
        party = Party._base_manager.create(display_name="Evidence target")
        target = canonical_record_target(party)
        values: dict[str, Any] = {
            "lineage_key": "routing-lineage",
            "reuse_key": "routing-first",
            "status": "succeeded",
            "error_code": "",
            "schema_id": schema["$id"],
            "schema": schema,
            "schema_digest": canonical_json_sha256(schema),
            "profile": "fake_document",
            "result": {},
            "profile_config": {},
            "provenance": {"claims": {}},
            "content_type_id": target.content_type.pk,
            "object_id": str(target.object_id),
        }
        manager = Extraction.objects
        first = manager.create_revision(sources=(), pages=(), page_results=(), parts=(), **values)
        replay = manager.create_revision(sources=(), pages=(), page_results=(), parts=(), **values)
        second = manager.create_revision_from_evidence(
            first, **{**values, "reuse_key": "routing-second", "expected_base_id": first.pk}
        )
        lineage = ExtractionLineage._base_manager.get(pk="routing-lineage")
    assert replay.pk == first.pk
    assert second.revision == first.revision + 1
    assert lineage.head_id == second.pk


@pytest.mark.django_db(transaction=True)
def test_party_link_delete_repairs_after_commit(parties_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Party link delete repairs after commit."""
    del parties_tables
    with system_context(reason="delete repair fixture"):
        party = Party._base_manager.create(display_name="Customer")
        handle = Handle.objects.upsert(platform="email", value="repair@example.test")
        link = PartyHandle.objects.link(party, handle)
        with transaction.atomic():
            link.delete()
            assert Handle._base_manager.get(pk=handle.pk).party_id == party.pk
        handle.refresh_from_db()
        party.refresh_from_db()
    assert handle.party_id is None
    assert party.handle_count == 0
