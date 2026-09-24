"""Split-router coverage for parties writes and retained extraction evidence."""

from __future__ import annotations

# ruff: noqa: F811 - imported pytest fixture names are intentional test parameters
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import router, transaction
from rebac import system_context

from angee.base.refs import canonical_record_target
from angee.base.serialization import canonical_json_sha256
from tests.extraction_models import Extraction, ExtractionLineage
from tests.test_messaging import Address, Handle, Party, PartyHandle
from tests.test_parties_circles import parties_tables  # noqa: F401 - shared table owner
from tests.test_transitions import (
    TransitionRouter,
    transition_alias,  # noqa: F401 - native secondary-connection fixture
    transition_task_table,  # noqa: F401 - dependency of transition_alias
)
from tests.test_workflows_extraction_service import extraction_tables  # noqa: F401 - shared table owner


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("selection", ["manager", "router"])
def test_handle_upsert_collision_refresh_uses_selected_alias(
    parties_tables: None,
    transition_alias: str,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    """The existing-value collision branch refreshes only the write connection."""

    del parties_tables
    with system_context(reason="split-router handle refresh fixture"):
        source = Handle.objects.db_manager(transition_alias).upsert(
            platform="email", value="old@example.test", external_id="stable-account",
        )
        existing = Handle.objects.db_manager(transition_alias).upsert(
            platform="email", value="new@example.test", display_name="Before",
        )
        routing = TransitionRouter(transition_alias if selection == "router" else "wrong-writer")
        owner = Handle.objects.db_manager(transition_alias) if selection == "manager" else Handle.objects
        with monkeypatch.context() as patch:
            patch.setattr(router, "routers", [routing])
            refreshed = owner.upsert(
                platform="email", value="new@example.test", external_id="stable-account", display_name="After",
            )
        existing.refresh_from_db(using=transition_alias)
        source.refresh_from_db(using=transition_alias)

    assert refreshed.pk == existing.pk
    assert existing.display_name == "After"
    assert source.value == "old@example.test"
    assert routing.writes == ([None] if selection == "router" else [])


@pytest.mark.django_db(transaction=True)
def test_suggestion_sweep_reads_locks_resolves_and_recounts_on_bound_alias(
    parties_tables: None,
    transition_alias: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The maintenance sweep completes its nested identity work after candidate admission."""

    del parties_tables
    owner = get_user_model().objects.create_user(username="routed-suggestion-owner")
    with system_context(reason="split-router suggestion fixture"):
        party = Party._base_manager.using(transition_alias).create(display_name="Customer", created_by_id=owner.pk)
        Handle._base_manager.using(transition_alias).create(
            platform="email", value="customer@example.test", display_name="Customer",
            party=party, created_by_id=owner.pk,
        )
        candidate = Handle._base_manager.using(transition_alias).create(
            platform="phone", value="+420777123456", display_name="Customer", created_by_id=owner.pk,
        )
        routing = TransitionRouter("wrong-writer")
        with monkeypatch.context() as patch:
            patch.setattr(router, "routers", [routing])
            created = PartyHandle.objects.db_manager(transition_alias).suggest_from_display_names()
            repeated = PartyHandle.objects.db_manager(transition_alias).suggest_from_display_names()
        candidate.refresh_from_db(using=transition_alias)
        party.refresh_from_db(using=transition_alias)
        link = PartyHandle._base_manager.using(transition_alias).get(handle_id=candidate.pk, party_id=party.pk)

    assert (created, repeated) == (1, 0)
    assert candidate.party_id == party.pk
    assert party.handle_count == 2
    assert link.is_confirmed is False
    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
def test_primary_address_save_uses_explicit_alias_after_primary_lock(
    parties_tables: None,
    transition_alias: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit save binding covers the locked previous-primary demotion and final save."""

    del parties_tables
    with system_context(reason="split-router primary address fixture"):
        party = Party._base_manager.using(transition_alias).create(display_name="Customer")
        previous = Address._base_manager.using(transition_alias).create(party=party, is_primary=True)
        selected = Address._base_manager.using(transition_alias).create(party=party)
        selected._state.db = "wrong-persisted-alias"
        selected.is_primary = True
        routing = TransitionRouter("wrong-writer")
        with monkeypatch.context() as patch:
            patch.setattr(router, "routers", [routing])
            selected.save(using=transition_alias, update_fields=["is_primary"])
        previous.refresh_from_db(using=transition_alias)
        selected.refresh_from_db(using=transition_alias)

    assert selected.is_primary is True
    assert previous.is_primary is False
    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
def test_extraction_retention_reuse_and_successor_stay_on_bound_alias(
    extraction_tables: None,
    transition_alias: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Head reads, reuse evidence comparisons and successor cloning preserve the alias."""

    del extraction_tables
    schema = {"$id": "tests.routing.v1", "type": "object"}
    with system_context(reason="split-router retained evidence fixture"):
        party = Party._base_manager.using(transition_alias).create(display_name="Evidence target")
        target = canonical_record_target(party, using=transition_alias)
        values: dict[str, Any] = {
            "lineage_key": "routing-lineage", "reuse_key": "routing-first", "status": "succeeded",
            "error_code": "", "schema_id": schema["$id"], "schema": schema,
            "schema_digest": canonical_json_sha256(schema), "profile": "fake_document", "result": {},
            "profile_config": {}, "provenance": {"claims": {}},
            "content_type_id": target.content_type.pk, "object_id": str(target.object_id),
        }
        routing = TransitionRouter("wrong-writer")
        with monkeypatch.context() as patch:
            patch.setattr(router, "routers", [routing])
            manager = Extraction.objects.db_manager(transition_alias)
            first = manager.create_revision(sources=(), pages=(), page_results=(), parts=(), **values)
            replay = manager.create_revision(sources=(), pages=(), page_results=(), parts=(), **values)
            second = manager.create_revision_from_evidence(
                first, **{**values, "reuse_key": "routing-second", "expected_base_id": first.pk},
            )
        lineage = ExtractionLineage._base_manager.using(transition_alias).get(pk="routing-lineage")

    assert replay.pk == first.pk
    assert second.revision == first.revision + 1
    assert lineage.head_id == second.pk
    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
def test_party_link_delete_repairs_after_selected_connection_commit(
    parties_tables: None,
    transition_alias: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The delete signal callback repairs the write database after the transaction commits."""

    del parties_tables
    with system_context(reason="split-router delete repair fixture"):
        party = Party._base_manager.using(transition_alias).create(display_name="Customer")
        handle = Handle.objects.db_manager(transition_alias).upsert(platform="email", value="repair@example.test")
        link = PartyHandle.objects.db_manager(transition_alias).link(party, handle)
        routing = TransitionRouter("wrong-writer")
        with monkeypatch.context() as patch:
            patch.setattr(router, "routers", [routing])
            with transaction.atomic(using=transition_alias):
                link.delete(using=transition_alias)
                assert Handle._base_manager.using(transition_alias).get(pk=handle.pk).party_id == party.pk
        handle.refresh_from_db(using=transition_alias)
        party.refresh_from_db(using=transition_alias)

    assert handle.party_id is None
    assert party.handle_count == 0
    assert routing.writes == []
