"""Party-handle claims: each source owns its claim, the pair derives its score, people decide."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rebac import actor_context, system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Handle, Party, PartyHandle, PartyHandleClaim
from angee.parties.mixins import LinkSource

pytestmark = pytest.mark.django_db(transaction=True)


def _pair(username: str) -> tuple[Any, Any, Any]:
    owner = get_user_model().objects.create_user(username=username)
    with system_context(reason="claim fixture"):
        party = Party._base_manager.create(display_name="Ada", created_by=owner)
        handle = Handle._base_manager.create(platform="phone", value="+14155552671", created_by=owner)
    return owner, party, handle


def _claims(link: Any) -> dict[str, float]:
    with system_context(reason="claim read"):
        return dict(PartyHandleClaim._base_manager.filter(link=link).values_list("source", "confidence"))


def _retract(link: Any, source: LinkSource) -> int:
    claims = PartyHandleClaim._base_manager.filter(link=link, source=source)
    return PartyHandle.objects.retract(claims)


def test_sources_keep_their_own_claims_and_the_pair_derives_from_them(composed_tables: None) -> None:
    """An import and a card coexist; retracting one re-ranks; the last retraction removes the pair."""

    del composed_tables
    owner, party, handle = _pair("claims-coexist")
    with system_context(reason="claims coexist"):
        PartyHandle.objects.link(party, handle, confidence=0.9, source=LinkSource.IMPORT, created_by_id=owner.pk)
        link = PartyHandle.objects.link(
            party, handle, confidence=0.8, source=LinkSource.CARDDAV, created_by_id=owner.pk
        )
        assert _claims(link) == {LinkSource.IMPORT: 0.9, LinkSource.CARDDAV: 0.8}
        link.refresh_from_db()
        assert (link.source, link.confidence) == (LinkSource.CARDDAV, 0.9)

        assert _retract(link, LinkSource.CARDDAV) == 1
        link.refresh_from_db()
        handle.refresh_from_db()
        assert (link.source, link.confidence) == (LinkSource.IMPORT, 0.9)
        assert handle.party_id == party.pk

        assert _retract(link, LinkSource.IMPORT) == 1
        handle.refresh_from_db()
        assert not PartyHandle._base_manager.filter(pk=link.pk).exists()
        assert handle.party_id is None


def test_a_card_retracting_its_claim_keeps_a_persons_link_and_dismissal(composed_tables: None) -> None:
    """A card dropping a number removes only the card's claim, never a person's entry or decision."""

    del composed_tables
    owner, party, handle = _pair("claims-person")
    with system_context(reason="claims person"):
        other = Handle.objects.upsert(platform="phone", value="+14155550199", created_by_id=owner.pk)
        manual = PartyHandle.objects.link(
            party, handle, confidence=0.4, source=LinkSource.MANUAL, created_by_id=owner.pk
        )
        PartyHandle.objects.link(party, handle, source=LinkSource.CARDDAV, created_by_id=owner.pk)
        dismissed = PartyHandle.objects.link(party, other, source=LinkSource.CARDDAV, created_by_id=owner.pk)
        with actor_context(owner):
            dismissed.dismiss()
        _retract(manual, LinkSource.CARDDAV)
        _retract(dismissed, LinkSource.CARDDAV)
        manual.refresh_from_db()
        dismissed.refresh_from_db()
    assert (manual.source, manual.confidence) == (LinkSource.MANUAL, 0.4)
    assert _claims(manual) == {LinkSource.MANUAL: 0.4}
    assert dismissed.is_dismissed
    assert _claims(dismissed) == {}


def test_a_confirmation_is_the_persons_own_claim(composed_tables: None) -> None:
    del composed_tables
    owner, party, handle = _pair("claims-confirm")
    with system_context(reason="claims confirm"):
        link = PartyHandle.objects.link(party, handle, confidence=0.3, source=LinkSource.RULE, created_by_id=owner.pk)
    with actor_context(owner):
        link.confirm()
    with system_context(reason="claims confirm read"):
        link.refresh_from_db()
    assert (link.is_confirmed, link.source, link.confidence) == (True, LinkSource.MANUAL, 1.0)
    assert _claims(link) == {LinkSource.RULE: 0.3, LinkSource.MANUAL: 1.0}


def test_an_extending_source_sets_its_claim_fields_and_save_arguments(
    composed_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An addon that extends the claim reaches its creation and every save through link()."""

    del composed_tables
    owner, party, handle = _pair("claims-extension")
    saves: list[dict[str, Any]] = []
    original = PartyHandleClaim.save

    def save(self: Any, *args: Any, source_identity: str = "", **kwargs: Any) -> None:
        saves.append({"identity": source_identity, "fields": kwargs.get("update_fields")})
        original(self, *args, **kwargs)

    monkeypatch.setattr(PartyHandleClaim, "save", save)
    with system_context(reason="claims extension"):
        for confidence in (0.6, 0.9):
            link = PartyHandle.objects.link(
                party,
                handle,
                confidence=confidence,
                source=LinkSource.IMPORT,
                metadata={"provenance": "erp"},
                created_by_id=owner.pk,
                claim_defaults={"metadata": {"provenance": "erp", "partner": 7}},
                claim_save_kwargs={"source_identity": "erp:partner:7"},
            )
        claim = PartyHandleClaim._base_manager.get(link=link)
    assert saves == [
        {"identity": "erp:partner:7", "fields": None},
        {"identity": "erp:partner:7", "fields": ["confidence", "updated_at"]},
    ]
    assert (claim.confidence, claim.metadata) == (0.9, {"provenance": "erp", "partner": 7})
