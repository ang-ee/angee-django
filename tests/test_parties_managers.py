"""Exact party-name candidates and confirmed handle read scopes."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test.utils import isolate_apps
from rebac import system_context

from angee.base.mixins import ArchiveMixin, ArchiveQuerySet
from angee.base.models import AngeeManager
from angee.messaging.testing.models import Handle, Organization, Party, PartyHandle
from angee.parties.managers import PartyQuerySet
from angee.parties.mixins import LinkSource
from tests.tables import model_tables

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def owner(composed_tables: None) -> Any:
    """Create an ordinary actor after the parties test graph is prepared."""

    del composed_tables
    return get_user_model().objects.create_user(username="party-lookup-owner")


@pytest.mark.parametrize("name", ["Alex Example", "alex example", "ALEX EXAMPLE", " \tAlex\n  Example\t "])
def test_named_matches_case_and_whitespace_on_both_sides(owner: Any, name: str) -> None:
    """Stored and supplied whitespace normalize to the same exact name."""

    with system_context(reason="test exact party names"):
        party = Party.objects.create(display_name="\t Alex  \n Example  ", created_by=owner)
        Party.objects.create(display_name="Alex Exampleton", created_by=owner)
        Party.objects.create(display_name="Other Alex Example", created_by=owner)

    candidates = Party.objects.with_actor(owner).named(name)

    assert isinstance(candidates, PartyQuerySet)
    assert list(candidates.values_list("pk", flat=True)) == [party.pk]


def test_named_matches_legal_name_and_keeps_multiple_candidates(owner: Any) -> None:
    """A legal name and a display name can independently identify candidates."""

    with system_context(reason="test legal party names"):
        organization = Organization.objects.create(
            display_name="Northstar",
            legal_name=" Northstar\t  Studio ",
            created_by=owner,
        )
        party = Party.objects.create(display_name="NORTHSTAR STUDIO", created_by=owner)

    candidates = Party.objects.with_actor(owner).named("northstar studio")

    assert set(candidates.values_list("pk", flat=True)) == {organization.pk, party.pk}
    assert list(candidates.filter(pk=organization.pk).values_list("pk", flat=True)) == [organization.pk]
    assert list(Organization.objects.with_actor(owner).named(" NORTHSTAR  STUDIO ")) == [organization]


def test_named_treats_pattern_characters_literally(owner: Any) -> None:
    """Punctuation cannot broaden exact matching into a regular expression."""

    with system_context(reason="test literal party name"):
        party = Party.objects.create(display_name="A. (Studio)+ [One]", created_by=owner)
        Party.objects.create(display_name="Ax Studiooooo One", created_by=owner)

    assert list(Party.objects.with_actor(owner).named("a. (studio)+ [one]")) == [party]


@pytest.mark.parametrize("name", ["", " \t\n "])
def test_named_empty_name_has_no_candidates(owner: Any, name: str) -> None:
    """An absent name never matches empty display or legal names."""

    with system_context(reason="test empty party name"):
        Organization.objects.create(display_name="", legal_name="", created_by=owner)

    assert not Party.objects.with_actor(owner).named(name).exists()


def test_named_excludes_merged_display_and_legal_names(owner: Any) -> None:
    """Neither name of a merged row appears among canonical candidates."""

    with system_context(reason="test merged party name"):
        canonical = Party.objects.create(display_name="Northstar Studio", created_by=owner)
        merged = Organization.objects.create(
            display_name="Northstar Studio",
            legal_name="Northstar Legal",
            merged_into=canonical,
            created_by=owner,
        )

    candidates = Party.objects.with_actor(owner).named("northstar studio")
    assert list(candidates.values_list("pk", flat=True)) == [canonical.pk]
    assert not Party.objects.with_actor(owner).named(merged.legal_name).exists()


def test_named_preserves_filters_and_actor_scope(owner: Any) -> None:
    """Name matching cannot expose another actor's identically named party."""

    other = get_user_model().objects.create_user(username="party-lookup-other")
    with system_context(reason="test scoped party names"):
        visible = Party.objects.create(display_name="Alex Example", created_by=owner)
        hidden = Party.objects.create(display_name="Alex Example", created_by=other)

    assert list(Party.objects.with_actor(owner).named("alex example")) == [visible]
    assert not Party.objects.with_actor(owner).filter(pk=hidden.pk).named("alex example").exists()


def test_named_respects_the_legal_name_owners_read_scope(owner: Any) -> None:
    """Sharing a parent identity does not expose an unreadable child's legal name."""

    other = get_user_model().objects.create_user(username="legal-name-owner")
    with system_context(reason="test scoped legal name"):
        organization = Organization.objects.create(
            display_name="Northstar",
            legal_name="Northstar Studio",
            created_by=other,
        )
        parent = Party.objects.get(pk=organization.pk)
        parent.grant_record_access("reader", owner)

    assert list(Party.objects.with_actor(owner).named("Northstar")) == [parent]
    assert not Party.objects.with_actor(owner).named("Northstar Studio").exists()


def test_named_excludes_archived_rows_in_an_archivable_composition(owner: Any) -> None:
    """An archive-aware party queryset composes its native archive predicate."""

    with isolate_apps():

        class ArchivablePartyQuerySet(ArchiveQuerySet, PartyQuerySet):
            """A party composition that adds the shared archive lifecycle."""

        class ArchivableParty(ArchiveMixin, Party):
            """Alternate concrete party composition for archive-scope coverage."""

            objects = AngeeManager.from_queryset(ArchivablePartyQuerySet)()

            class Meta:
                """Keep this alternate test table outside installed app setup."""

                app_label = "party_lookup"
                rebac_resource_type = "parties/party"

        with model_tables((ArchivableParty,)), system_context(reason="test archived party names"):
            active = ArchivableParty.objects.create(display_name="Alex Example", created_by=owner)
            ArchivableParty.objects.create(display_name="Alex Example", is_archived=True, created_by=owner)
            assert list(ArchivableParty.objects.named("alex example")) == [active]


@pytest.mark.parametrize(
    ("platform", "stored", "query"),
    [
        ("email", "Alex.Example+team@gmail.com", " alexexample@GMAIL.COM "),
        ("phone", "+420 777 123 456", "+420777123456"),
    ],
)
def test_confirmed_uses_platform_normalization(owner: Any, platform: str, stored: str, query: str) -> None:
    """A confirmed party link is found through the handle normalization owner."""

    with system_context(reason="test confirmed handle lookup"):
        party = Party.objects.create(display_name="Alex Example", created_by=owner)
        handle = Handle.objects.create(platform=platform, value=stored, created_by=owner)
        PartyHandle.objects.link(party, handle, source=LinkSource.MANUAL, is_confirmed=True, created_by_id=owner.pk)

    candidates = Handle.objects.with_actor(owner).confirmed(platform, query)

    assert list(candidates) == [handle]
    assert candidates.get().party_id == party.pk
    assert not Handle.objects.with_actor(owner).filter(pk=-1).confirmed(platform, query).exists()
    assert not Handle.objects.with_actor(owner).confirmed("other", query).exists()


def test_confirmed_ignores_unconfirmed_and_unlinked_handles(owner: Any) -> None:
    """Verification alone does not establish a confirmed party identity."""

    with system_context(reason="test unconfirmed handle lookup"):
        party = Party.objects.create(display_name="Alex Example", created_by=owner)
        handle = Handle.objects.create(platform="email", value="alex@example.test", is_verified=True, created_by=owner)
        PartyHandle.objects.link(party, handle, source=LinkSource.MANUAL, created_by_id=owner.pk)
        Handle.objects.create(
            platform="email",
            value="unlinked@example.test",
            party_link_confirmed=True,
            created_by=owner,
        )

    assert not Handle.objects.with_actor(owner).confirmed("email", handle.value).exists()
    assert not Handle.objects.with_actor(owner).confirmed("email", "unlinked@example.test").exists()


def test_confirmed_preserves_actor_scope(owner: Any) -> None:
    """A confirmed foreign handle stays hidden from the pinned actor."""

    other = get_user_model().objects.create_user(username="handle-lookup-other")
    with system_context(reason="test scoped confirmed handle"):
        party = Party.objects.create(display_name="Morgan Example", created_by=other)
        handle = Handle.objects.create(platform="email", value="morgan@example.test", created_by=other)
        PartyHandle.objects.link(party, handle, source=LinkSource.MANUAL, is_confirmed=True, created_by_id=other.pk)

    assert not Handle.objects.with_actor(owner).confirmed("email", handle.value).exists()
