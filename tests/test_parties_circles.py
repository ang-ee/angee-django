"""Tests for parties circles, relationships, and identity confirm/dismiss."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from rebac import PermissionDenied, actor_context, system_context

from angee.messaging.testing.models import (
    Circle,
    CircleMember,
    Directory,
    Folder,
    Handle,
    Organization,
    Party,
    Person,
    Relationship,
    RelationshipKind,
)
from angee.parties.backends import ParsedContact
from angee.parties.mixins import LinkSource
from angee.parties.models import RelationshipKind as AbstractRelationshipKind
from tests.conftest import make_integration
from tests.test_messaging import _grant

User = get_user_model()


def _user(username: str) -> Any:
    """Create a plain user for ownership fixtures."""

    return User.objects.create_user(username=username, password="x")


@pytest.mark.django_db(transaction=True)
def test_manual_contact_is_unconfirmed_and_dismissed_link_stays_dismissed(
    composed_tables: None,
) -> None:
    """Manual contact entry creates a reviewable claim and never resurrects its anti-link."""

    del composed_tables
    owner = _user("manual-contact-owner")
    with system_context(reason="test manual contact party"):
        party = Party._base_manager.create(display_name="Counterparty", created_by=owner)

    with actor_context(owner):
        with pytest.raises(ValidationError):
            Party.objects.propose_manual_contact(
                party,
                platform="email",
                value="not-an-email",
                actor=owner,
            )
        with pytest.raises(ValidationError):
            Party.objects.propose_manual_contact(
                party,
                platform="phone",
                value="123",
                actor=owner,
            )
        link = Party.objects.propose_manual_contact(
            party,
            platform="email",
            value="contact@example.test",
            label="Contact",
            actor=owner,
        )
        link.dismiss()
        repeated = Party.objects.propose_manual_contact(
            party,
            platform="email",
            value="contact@example.test",
            label="Changed label",
            actor=owner,
        )

    repeated.refresh_from_db()
    repeated.handle.refresh_from_db()
    assert repeated.pk == link.pk
    assert repeated.source_kind == "person"
    assert not repeated.is_confirmed
    assert repeated.is_dismissed
    assert repeated.handle.label == "Contact"
    assert not repeated.handle.is_verified
    assert not repeated.handle.party_link_confirmed


@pytest.mark.django_db(transaction=True)
def test_manual_contact_reuse_preserves_confirmed_owner_and_hides_foreign_handle(
    composed_tables: None,
) -> None:
    """Global Handle reuse neither overwrites its label nor discloses an unreadable match."""

    del composed_tables
    owner = _user("manual-contact-reuse")
    reader = _user("manual-contact-reader")
    foreign = _user("manual-contact-foreign")
    with system_context(reason="test manual contact reuse"):
        target = Party._base_manager.create(display_name="Target counterparty", created_by=owner)
        reader_target = Party._base_manager.create(display_name="Reader target", created_by=reader)
        confirmed_party = Party._base_manager.create(display_name="Confirmed counterparty", created_by=owner)
        shared = Handle._base_manager.create(
            platform=Handle.Platform.EMAIL,
            value="shared@example.test",
            label="Authoritative label",
            created_by=owner,
        )
        confirmed_party.with_actor(owner).confirm(aspect="handle", handle=shared, values={})
        reader_handle = Handle._base_manager.create(
            platform=Handle.Platform.EMAIL,
            value="reader-shared@example.test",
            label="Shared read-only label",
            created_by=owner,
        )
        hidden = Handle._base_manager.create(
            platform=Handle.Platform.PHONE,
            value="+420601123456",
            label="Private label",
            created_by=foreign,
        )
    _grant(reader_handle, "reader", reader)

    with actor_context(owner):
        proposed = Party.objects.propose_manual_contact(
            target,
            platform="email",
            value="shared@example.test",
            label="Replacement label",
            actor=owner,
        )
        with pytest.raises(PermissionDenied, match="cannot add this contact point"):
            Party.objects.propose_manual_contact(
                target,
                platform="phone",
                value=hidden.value,
                actor=owner,
            )
    with actor_context(reader):
        assert reader_handle.with_actor(reader).has_access("read")
        assert not reader_handle.has_access("write")
        reader_proposed = Party.objects.propose_manual_contact(
            reader_target,
            platform="email",
            value="reader-shared@example.test",
            label="Reader replacement label",
            actor=reader,
        )

    shared.refresh_from_db()
    reader_handle.refresh_from_db()
    proposed.refresh_from_db()
    reader_proposed.refresh_from_db()
    assert shared.label == "Authoritative label"
    assert shared.party_id == confirmed_party.pk
    assert shared.party_link_confirmed
    assert proposed.subject_id == target.pk
    assert not proposed.is_confirmed
    assert not proposed.is_dismissed
    assert reader_handle.label == "Shared read-only label"
    # A manual claim awaits review, so it does not decide the shared handle's owner.
    assert reader_handle.party_id is None
    assert not reader_handle.party_link_confirmed
    assert reader_proposed.subject_id == reader_target.pk
    assert not reader_proposed.is_confirmed
    assert not reader_proposed.is_dismissed


@pytest.mark.django_db(transaction=True)
def test_circle_subtree_and_ancestor_scopes(composed_tables: None) -> None:
    """Circle composes HierarchyMixin: subtree/ancestors read off the path index."""

    del composed_tables
    with system_context(reason="test circles"):
        owner = _user("olivia")
        root = Circle._base_manager.create(name="Friends", created_by=owner)
        child = Circle._base_manager.create(name="Climbing", parent=root, created_by=owner)
        grand = Circle._base_manager.create(name="Bleau crew", parent=child, created_by=owner)
        other = Circle._base_manager.create(name="Work", created_by=owner)

        subtree = set(Circle.objects.subtree_of(root).values_list("pk", flat=True))
        assert subtree == {root.pk, child.pk, grand.pk}
        ancestors = set(Circle.objects.ancestors_of(grand).values_list("pk", flat=True))
        assert ancestors == {root.pk, child.pk}
        assert other.pk not in subtree


@pytest.mark.django_db(transaction=True)
def test_circle_tree_never_straddles_owners(composed_tables: None) -> None:
    """hierarchy_scope_fields=("created_by",): a parent from another owner is rejected."""

    del composed_tables
    with system_context(reason="test circles"):
        mine = Circle._base_manager.create(name="Mine", created_by=_user("me"))
        theirs = Circle._base_manager.create(name="Theirs", created_by=_user("them"))
        with pytest.raises(ValidationError):
            Circle._base_manager.create(name="Nested", parent=theirs, created_by=mine.created_by)


@pytest.mark.django_db(transaction=True)
def test_circle_membership_is_unique_per_pair(composed_tables: None) -> None:
    """One row per (circle, party): a re-suggestion updates, never duplicates."""

    del composed_tables
    with system_context(reason="test circles"):
        owner = _user("uma")
        circle = Circle._base_manager.create(name="Family", created_by=owner)
        party = Party._base_manager.create(display_name="Maya", created_by=owner)
        CircleMember._base_manager.create(circle=circle, party=party, created_by=owner)
        with pytest.raises(IntegrityError), transaction.atomic():
            CircleMember._base_manager.create(circle=circle, party=party, created_by=owner)


@pytest.mark.django_db(transaction=True)
def test_relationship_kind_renders_both_directions(composed_tables: None) -> None:
    """One kind row carries both readings via inverse_name; blank means symmetric."""

    del composed_tables
    with system_context(reason="test kinds"):
        friend = RelationshipKind._base_manager.create(slug="friend", name="Friend")
        mother = RelationshipKind._base_manager.create(slug="mother", name="Mother", inverse_name="Child")

    assert friend.is_symmetric
    assert friend.label_for(outbound=True) == "Friend"
    assert friend.label_for(outbound=False) == "Friend"
    assert not mother.is_symmetric
    # On the anchor's card the counterparty is their Mother; on the mother's
    # card the anchor renders as her Child.
    assert mother.label_for(outbound=True) == "Mother"
    assert mother.label_for(outbound=False) == "Child"


@pytest.mark.django_db(transaction=True)
def test_relationship_edge_constraints(composed_tables: None) -> None:
    """A tracked edge is unique per (party, other, kind), never self-referential,
    and every edge names a counterparty (tracked or free-text)."""

    del composed_tables
    with system_context(reason="test relationships"):
        owner = _user("rita")
        kind = RelationshipKind._base_manager.create(slug="sibling", name="Sibling")
        maya = Party._base_manager.create(display_name="Maya", created_by=owner)
        anna = Party._base_manager.create(display_name="Anna", created_by=owner)
        Relationship._base_manager.create(party=maya, other_party=anna, kind=kind, created_by=owner)
        with pytest.raises(IntegrityError), transaction.atomic():
            Relationship._base_manager.create(party=maya, other_party=anna, kind=kind, created_by=owner)
        with pytest.raises(IntegrityError), transaction.atomic():
            Relationship._base_manager.create(party=maya, other_party=maya, kind=kind, created_by=owner)
        with pytest.raises(IntegrityError), transaction.atomic():
            Relationship._base_manager.create(party=maya, kind=kind, created_by=owner)


@pytest.mark.django_db(transaction=True)
def test_relationship_records_untracked_relatives(composed_tables: None) -> None:
    """A relative who is not a directory entry records as free text (health-gaps).

    Two same-kind free-text rows are legitimate (the tracked-pair uniqueness is
    partial), so a family history lists both grandmothers.
    """

    del composed_tables
    with system_context(reason="test relationships"):
        owner = _user("gene")
        kind = RelationshipKind._base_manager.create(slug="grandparent", name="Grandparent", inverse_name="Grandchild")
        maya = Party._base_manager.create(display_name="Maya", created_by=owner)
        first = Relationship._base_manager.create(party=maya, other_name="Rosa K.", kind=kind, created_by=owner)
        second = Relationship._base_manager.create(party=maya, other_name="Vera M.", kind=kind, created_by=owner)

    assert first.other_party_id is None
    assert {first.other_name, second.other_name} == {"Rosa K.", "Vera M."}


def _contested_handle(owner: Any, value: str, *links: tuple[float, bool]) -> tuple[Any, list[Any]]:
    """One handle claimed by one new party per ``(confidence, is_confirmed)``; return it and the links."""

    handle = Handle._base_manager.create(platform="email", value=value, created_by=owner)
    claims = []
    for index, (confidence, is_confirmed) in enumerate(links):
        party = Party._base_manager.create(display_name=f"Party {index}", created_by=owner)
        claims.append(
            (
                party.with_actor(owner).confirm(aspect="handle", handle=handle, values={})
                if is_confirmed
                else Party.objects.assert_handle(party, handle, asserted_by=owner, confidence=confidence)
            )
        )
    return handle, claims


def _in_link_order(links: list[Any]) -> list[Any]:
    """Return ``links`` in the order the model ranks equal links."""

    return list(
        Party.statements.filter(pk__in=[link.pk for link in links]).order_by(*Party.statements.model.trust_ordering)
    )


def _name(link: Any, name: str) -> None:
    party = link.subject
    party.display_name = name
    party.save(update_fields=["display_name", "updated_at"])


def _resolved_owner(handle: Any) -> Any:
    Handle.objects.resolve(handle)
    handle.refresh_from_db()
    return handle.party_id


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("nameless", ["Unknown", "+1 415 555 2671"])
def test_an_equal_tie_resolves_to_the_party_with_a_real_name(composed_tables: None, nameless: str) -> None:
    """A nameless duplicate card first in order loses an equal tie; a bare number is no name either."""

    del composed_tables
    with system_context(reason="test real-name tie"):
        owner = _user(f"tie-{nameless.strip('+').replace(' ', '')}")
        handle, links = _contested_handle(owner, "ada@example.com", (1.0, False), (1.0, False))
        first, second = _in_link_order(links)
        _name(first, nameless)
        _name(second, "Ada Lovelace")
        assert _resolved_owner(handle) == second.subject_id


@pytest.mark.django_db(transaction=True)
def test_a_real_name_never_outranks_a_confirmation_or_a_stronger_score(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="test real-name rank"):
        owner = _user("tie-rank")
        handle, (confirmed, named) = _contested_handle(owner, "confirmed@example.com", (1.0, True), (1.0, False))
        _name(confirmed, "Unknown")
        _name(named, "Ada Lovelace")
        assert _resolved_owner(handle) == confirmed.subject_id

        handle, (stronger, named) = _contested_handle(owner, "stronger@example.com", (1.0, False), (0.9, False))
        _name(stronger, "Unknown")
        _name(named, "Ada Lovelace")
        assert _resolved_owner(handle) == stronger.subject_id


@pytest.mark.django_db(transaction=True)
def test_an_all_nameless_tie_keeps_the_link_order(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="test nameless tie"):
        handle, links = _contested_handle(_user("tie-nameless"), "nobody@example.com", (1.0, False), (1.0, False))
        for link in links:
            _name(link, "Unknown")
        assert _resolved_owner(handle) == _in_link_order(links)[0].subject_id


@pytest.mark.django_db(transaction=True)
def test_confirm_and_dismiss_drive_resolution(composed_tables: None) -> None:
    """Confirm outranks any score; dismiss is a durable anti-link that demotes and recounts."""

    del composed_tables
    with system_context(reason="test identity"):
        owner = _user("ivan")
        alice = Party._base_manager.create(display_name="Alice", created_by=owner)
        alicia = Party._base_manager.create(display_name="Alicia", created_by=owner)
        handle = Handle._base_manager.create(platform="email", value="a@example.com", created_by=owner)
        strong = Party.objects.assert_handle(alice, handle, asserted_by=owner, confidence=0.9)
        weak = Party.objects.assert_handle(alicia, handle, asserted_by=owner, confidence=0.4)

        handle.refresh_from_db()
        alice.refresh_from_db()
        assert handle.party_id == alice.pk
        assert handle.party_link_confirmed is False
        assert alice.handle_count == 1

        # Dismissing the winner leaves only a suggestion awaiting review, which never
        # owns the handle; the demoted owner must not keep a stale count.
        strong.with_actor(owner).dismiss()
        handle.refresh_from_db()
        alice.refresh_from_db()
        alicia.refresh_from_db()
        assert handle.party_id is None
        assert handle.party_link_confirmed is False
        assert alice.handle_count == 0
        assert alicia.handle_count == 0

        # A re-sync re-linking the dismissed pair must not resurrect it: the
        # dismissed row already exists, so resolution still ignores it.
        Party.objects.assert_handle(alice, handle, asserted_by=owner, confidence=0.95)
        handle.refresh_from_db()
        assert handle.party_id is None

        # A human confirm outranks any score and clears the dismissal.
        strong.refresh_from_db()
        strong = strong.with_actor(owner).confirm()
        handle.refresh_from_db()
        strong.refresh_from_db()
        assert handle.party_id == alice.pk
        assert handle.party_link_confirmed is True
        assert strong.is_confirmed and not strong.is_dismissed
        assert strong.confidence == 1.0
        assert strong.source_kind == "person"

        # Low-confidence, undecided links are exactly the review queue.
        assert list(Party.statements.to_review().values_list("pk", flat=True)) == [weak.pk]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("model", [Party, Person])
def test_person_for_user_is_the_one_person_per_user_owner(composed_tables: None, model: type[Any]) -> None:
    """The inherited manager always keys the Person.user O2O — never two rows."""

    del composed_tables
    with system_context(reason="test for_user"):
        user = User.objects.create_user(username="mona", email="mona@example.com", password="x")
        first = model.objects.for_user(user)
        again = Person.objects.for_user(user)

    assert first.pk == again.pk
    assert first.user_id == user.pk
    assert first.display_name


@pytest.mark.django_db(transaction=True)
def test_claim_own_writes_control_and_identity_facts(composed_tables: None) -> None:
    """Handle.claim_own sets the owner (control) and a normal person statement (identity)."""

    del composed_tables
    with system_context(reason="test claim_own"):
        user = User.objects.create_user(username="nils", email="nils@example.com", password="x")
        handle = Handle.objects.claim_own(
            user,
            platform=Handle.Platform.EMAIL,
            value="nils@work.example",
            asserted_by=user,
        )
        again = Handle.objects.claim_own(
            user,
            platform=Handle.Platform.EMAIL,
            value="nils@work.example",
            asserted_by=user,
        )

        handle.refresh_from_db()
        assert again.pk == handle.pk
        assert handle.owner_id == user.pk
        assert set(Handle.objects.owned_by(user).values_list("pk", flat=True)) == {handle.pk}
        person = Person.objects.for_user(user)
        assert handle.party_id == person.pk
        assert handle.party_link_confirmed is False
        link = Party.statements.get(handle=handle, subject=person)
        assert not link.is_confirmed and link.source_kind == "person" and link.confidence == 1.0
        assert Party.statements.filter(handle=handle, subject=person).count() == 1


@pytest.mark.django_db(transaction=True)
def test_claim_own_records_a_contested_identity_without_reassigning_control(composed_tables: None) -> None:
    """A second user's claim stays weak and unconfirmed; the first owner remains in control."""

    del composed_tables
    with system_context(reason="test contested claim_own"):
        first_user = User.objects.create_user(username="first", email="shared@example.com", password="x")
        competing_user = User.objects.create_user(username="competing", email="competing@example.com", password="x")
        handle = Handle.objects.claim_own(
            first_user,
            platform=Handle.Platform.EMAIL,
            value="shared@example.com",
            asserted_by=first_user,
        )
        contested = Handle.objects.claim_own(
            competing_user,
            platform=Handle.Platform.EMAIL,
            value="shared@example.com",
            asserted_by=competing_user,
        )

        first_person = Person.objects.for_user(first_user)
        competing_person = Person.objects.for_user(competing_user)
        first_link = Party.statements.get(handle=handle, subject=first_person)
        competing_link = Party.statements.get(handle=handle, subject=competing_person)

    contested.refresh_from_db()
    assert contested.pk == handle.pk
    assert contested.owner_id == first_user.pk
    assert contested.party_id == first_person.pk
    assert not first_link.is_confirmed and first_link.confidence == 1.0
    assert not competing_link.is_confirmed
    assert competing_link.confidence == 0.3
    assert competing_link.source_kind == "person"


@pytest.mark.django_db(transaction=True)
def test_handle_persists_normalized_value_on_create_and_value_update(composed_tables: None) -> None:
    """Handle owns its indexed normalization, including Gmail local-part collapsing."""

    del composed_tables
    with system_context(reason="test handle normalization"):
        owner = _user("normalized")
        handle = Handle._base_manager.create(
            platform=Handle.Platform.EMAIL,
            value="  A.Lice+News@GMail.Com  ",
            created_by=owner,
        )
        assert handle.normalized_value == "alice@gmail.com"
        assert Handle._meta.get_field("normalized_value").db_index is True

        handle.value = " Alice.Smith+work@googlemail.com "
        handle.save(update_fields=["value"])

    handle.refresh_from_db()
    assert handle.normalized_value == "alicesmith@googlemail.com"


@pytest.mark.django_db(transaction=True)
def test_suggest_for_exact_match_autolinks_at_full_confidence(composed_tables: None) -> None:
    """An exact normalized-value match to a resolved handle auto-links (gmail dot/plus)."""

    del composed_tables
    with system_context(reason="test suggest exact"):
        owner = _user("iris")
        alice = Party._base_manager.create(display_name="Alice", created_by=owner)
        resolved = Handle._base_manager.create(platform="email", value="a.lice@gmail.com", created_by=owner)
        Party.objects.assert_handle(alice, resolved, asserted_by=owner, confidence=1.0)

        fresh = Handle._base_manager.create(platform="email", value="alice+news@gmail.com", created_by=owner)
        link = Party.objects.suggest_for(fresh)

    assert link is not None
    fresh.refresh_from_db()
    assert fresh.party_id == alice.pk
    assert link.source_kind == "rule"
    assert not link.is_confirmed
    assert link.confidence == 1.0


@pytest.mark.django_db(transaction=True)
def test_suggest_for_resolved_handle_returns_without_competing_links(composed_tables: None) -> None:
    """A resolved handle is final input to suggest_for, even when a twin disagrees."""

    del composed_tables
    with system_context(reason="test suggest resolved noop"):
        owner = _user("resolved-suggestion")
        alice = Party._base_manager.create(display_name="Alice", created_by=owner)
        alicia = Party._base_manager.create(display_name="Alicia", created_by=owner)
        resolved = Handle._base_manager.create(
            platform=Handle.Platform.EMAIL,
            value="alice@gmail.com",
            created_by=owner,
        )
        competing_twin = Handle._base_manager.create(
            platform=Handle.Platform.EMAIL,
            value="a.lice+other@gmail.com",
            created_by=owner,
        )
        Party.objects.assert_handle(alice, resolved, asserted_by=owner, confidence=1.0)
        Party.objects.assert_handle(alicia, competing_twin, asserted_by=owner, confidence=1.0)

        resolved.refresh_from_db()
        result = Party.objects.suggest_for(resolved)

    assert result is None
    assert list(Party.statements.filter(handle=resolved).values_list("subject_id", flat=True)) == [alice.pk]


@pytest.mark.django_db(transaction=True)
def test_suggest_for_keeps_dismissed_link_dismissed(composed_tables: None) -> None:
    """A repeated normalized-value suggestion never resurrects its durable anti-link."""

    del composed_tables
    with system_context(reason="test suggest dismissed noop"):
        owner = _user("dismissed-suggestion")
        alice = Party._base_manager.create(display_name="Alice", created_by=owner)
        resolved = Handle._base_manager.create(
            platform=Handle.Platform.EMAIL,
            value="a.lice@gmail.com",
            created_by=owner,
        )
        Party.objects.assert_handle(alice, resolved, asserted_by=owner, confidence=1.0)
        unknown = Handle._base_manager.create(
            platform=Handle.Platform.EMAIL,
            value="alice+unknown@gmail.com",
            created_by=owner,
        )
        dismissed = Party.objects.suggest_for(unknown)
        assert dismissed is not None
        dismissed = dismissed.with_actor(owner).dismiss()

        repeated = Party.objects.suggest_for(unknown)

    dismissed.refresh_from_db()
    unknown.refresh_from_db()
    assert repeated is not None
    assert repeated.subject_id == dismissed.subject_id
    assert dismissed.is_dismissed
    assert not dismissed.is_confirmed
    assert unknown.party_id is None
    assert Party.statements.filter(subject=alice, handle=unknown).count() == 2


@pytest.mark.django_db(transaction=True)
def test_suggest_for_records_conflicting_normalized_twins(composed_tables: None) -> None:
    """Resolved normalized twins create one strong suggestion plus weak competing links."""

    del composed_tables
    with system_context(reason="test suggest conflicts"):
        owner = _user("twins")
        alice = Party._base_manager.create(display_name="Alice", created_by=owner)
        alicia = Party._base_manager.create(display_name="Alicia", created_by=owner)
        first = Handle._base_manager.create(
            platform=Handle.Platform.EMAIL,
            value="a.lice@gmail.com",
            created_by=owner,
        )
        second = Handle._base_manager.create(
            platform=Handle.Platform.EMAIL,
            value="al.ice+home@gmail.com",
            created_by=owner,
        )
        Party.objects.assert_handle(alice, first, asserted_by=owner, confidence=1.0)
        Party.objects.assert_handle(alicia, second, asserted_by=owner, confidence=1.0)
        fresh = Handle._base_manager.create(
            platform=Handle.Platform.EMAIL,
            value="alice+new@gmail.com",
            created_by=owner,
        )

        winner = Party.objects.suggest_for(fresh)
        links = list(Party.statements.filter(handle=fresh).order_by("subject_id"))

    assert winner is not None
    by_party = {link.subject_id: link for link in links}
    assert set(by_party) == {alice.pk, alicia.pk}
    assert by_party[alice.pk].confidence == 1.0
    assert by_party[alicia.pk].confidence == 0.3
    assert all(not link.is_confirmed for link in links)
    assert all(link.source_kind == "rule" for link in links)


@pytest.mark.django_db(transaction=True)
def test_suggest_for_org_domain_suggests_at_low_confidence(composed_tables: None) -> None:
    """An email whose domain matches Organization.domain suggests that org weakly."""

    del composed_tables
    with system_context(reason="test suggest domain"):
        owner = _user("omar")
        acme = Organization._base_manager.create(display_name="Acme", domain="acme.com", created_by=owner)
        handle = Handle._base_manager.create(platform="email", value="bob@acme.com", created_by=owner)
        link = Party.objects.suggest_for(handle)

    assert link is not None
    assert link.subject_id == acme.pk
    assert link.confidence == 0.4
    assert not link.is_confirmed
    assert link.source_kind == "rule"


@pytest.mark.django_db(transaction=True)
def test_suggest_for_no_match_is_a_noop(composed_tables: None) -> None:
    """No twin and no org-domain match leaves the handle unresolved (the Salesforce policy)."""

    del composed_tables
    with system_context(reason="test suggest noop"):
        owner = _user("nadia")
        handle = Handle._base_manager.create(platform="email", value="stranger@nowhere.example", created_by=owner)
        link = Party.objects.suggest_for(handle)

    assert link is None
    handle.refresh_from_db()
    assert handle.party_id is None


@pytest.mark.django_db(transaction=True)
def test_members_of_serves_the_org_membership_query(composed_tables: None) -> None:
    """PartyQuerySet.members_of returns parties whose relationships name the org."""

    del composed_tables
    with system_context(reason="test members_of"):
        owner = _user("pia")
        kind = RelationshipKind._base_manager.create(
            slug="employee",
            name="Employee",
            inverse_name="Employer",
            category="professional",
            other_party_kind="organization",
        )
        acme = Organization._base_manager.create(display_name="Acme", created_by=owner)
        ada = Person._base_manager.create(display_name="Ada", created_by=owner)
        Relationship._base_manager.create(party=ada, other_party=acme, kind=kind, created_by=owner)
        ended = Person._base_manager.create(display_name="Ended", created_by=owner)
        Relationship._base_manager.create(
            party=ended,
            other_party=acme,
            kind=kind,
            ended_at=date(2025, 1, 1),
            created_by=owner,
        )

        members = set(Party.objects.members_of(acme).values_list("pk", flat=True))
    assert members == {ada.pk}


@pytest.mark.django_db(transaction=True)
def test_relationship_kind_end_legality_requires_org_counterparty(
    composed_tables: None,
    django_assert_num_queries: Any,
) -> None:
    """An organization-typed end rejects a person counterparty via clean()/save()."""

    del composed_tables
    with system_context(reason="test legality"):
        owner = _user("quinn")
        kind = RelationshipKind._base_manager.create(
            slug="employee",
            name="Employee",
            inverse_name="Employer",
            category="professional",
            other_party_kind="organization",
        )
        ada = Person._base_manager.create(display_name="Ada", created_by=owner)
        bob = Person._base_manager.create(display_name="Bob", created_by=owner)
        acme = Organization._base_manager.create(display_name="Acme", created_by=owner)

        assert RelationshipKind.PartyKind.PERSON.model() is Person
        assert RelationshipKind.PartyKind.ORGANIZATION.model() is Organization
        with django_assert_num_queries(1):
            kind.validate_ends(ada, acme)

        with pytest.raises(ValidationError):
            Relationship._base_manager.create(party=ada, other_party=bob, kind=kind, created_by=owner)
        # A free-text (untracked) counterparty is unconstrained, and a real org passes.
        Relationship._base_manager.create(party=ada, other_name="Globex", kind=kind, created_by=owner)
        relationship = Relationship._base_manager.create(
            party=bob,
            other_party=acme,
            kind=kind,
            created_by=owner,
        )

        # An unrelated update does not revalidate unchanged ends. This keeps thin
        # patch writes from issuing end-kind queries and follows Django update_fields.
        Relationship._base_manager.filter(pk=relationship.pk).update(other_party=ada)
        relationship.refresh_from_db()
        relationship.notes = "Imported note"
        relationship.save(update_fields=["notes"])


@pytest.mark.django_db(transaction=True)
def test_merged_into_flattens_transitively(composed_tables: None) -> None:
    """Party.merge_into atomically flattens chains; PartyQuerySet.canonical drops merged rows."""

    del composed_tables
    with system_context(reason="test merge"):
        owner = _user("rex")
        a = Party._base_manager.create(display_name="A", created_by=owner)
        b = Party._base_manager.create(display_name="B", created_by=owner)
        c = Party._base_manager.create(display_name="C", created_by=owner)

        a.merge_into(b)
        a.refresh_from_db()
        assert a.merged_into_id == b.pk

        # Merging B into C repoints A forward in the same write — no two-hop chain.
        b.merge_into(c)
        a.refresh_from_db()
        b.refresh_from_db()
        assert a.merged_into_id == c.pk
        assert b.merged_into_id == c.pk
        assert a.canonical().pk == c.pk

        canonical = set(Party.objects.canonical().values_list("pk", flat=True))
    assert c.pk in canonical
    assert a.pk not in canonical and b.pk not in canonical


def test_has_real_name_rejects_placeholder_and_letterless_names() -> None:
    """Blank, placeholder, and letterless display names are not real names."""

    assert Party(display_name="Sofia Khomutova").has_real_name
    assert Party(display_name="Ĝis 千葉").has_real_name
    assert not Party(display_name=Party.PLACEHOLDER_NAME).has_real_name
    assert not Party(display_name="+7 921 384 6620").has_real_name
    assert not Party(display_name="   ").has_real_name


@pytest.mark.django_db(transaction=True)
def test_merge_into_rejects_reversal_and_database_rejects_self_merge(composed_tables: None) -> None:
    """The verb rejects A→B when B→A exists; the database is the direct-write floor."""

    del composed_tables
    with system_context(reason="test merge reversal"):
        owner = _user("reversal")
        a = Party._base_manager.create(display_name="A", created_by=owner)
        b = Party._base_manager.create(display_name="B", created_by=owner)
        b.merge_into(a)

        with pytest.raises(ValidationError, match="reverse"):
            a.merge_into(b)

        a.merged_into = a
        with pytest.raises(IntegrityError), transaction.atomic():
            a.save(update_fields=["merged_into"])


@pytest.mark.django_db(transaction=True)
def test_ingest_contact_upserts_carddav_employment_in_place(composed_tables: None) -> None:
    """ORG/TITLE/ROLE map to one stable CardDAV edge while manual rows survive."""

    del composed_tables
    with system_context(reason="test carddav employment"):
        owner = _user("carddav")
        directory = make_integration(
            owner.username + "-directory", model=Directory, backend_class="carddav", owner=owner
        )
        folder = Folder._base_manager.create(name="Contacts", directory=directory, created_by=owner)
        employee = RelationshipKind._base_manager.create(
            slug="employee",
            name="Employee",
            inverse_name="Employer",
            category="professional",
            other_party_kind="organization",
        )
        parsed = ParsedContact(
            uid="contact-1",
            display_name="Ada Lovelace",
            organization="Analytical Engines",
            title="Mathematician",
            role="Programmer",
        )

        person = Party.objects.ingest_contact(parsed, folder=folder, created_by_id=owner.pk)
        synced = Relationship._base_manager.get(
            party=person,
            kind=employee,
            source=LinkSource.CARDDAV,
            other_party__isnull=True,
        )
        first_pk = synced.pk
        first_sqid = synced.sqid
        first_updated_at = synced.updated_at
        manual = Relationship._base_manager.create(
            party=person,
            kind=employee,
            source=LinkSource.MANUAL,
            other_name="Human-maintained employer",
            created_by=owner,
        )

        again = Party.objects.ingest_contact(parsed, folder=folder, created_by_id=owner.pk)
        unchanged = Relationship._base_manager.get(
            party=again,
            kind=employee,
            source=LinkSource.CARDDAV,
            other_party__isnull=True,
        )

        changed = ParsedContact(
            uid="contact-1",
            display_name="Ada Lovelace",
            organization="Analytical Engines",
            title="Principal Mathematician",
            role="Research lead",
        )
        Party.objects.ingest_contact(changed, folder=folder, created_by_id=owner.pk)
        refreshed = Relationship._base_manager.get(pk=first_pk)

    assert synced.other_name == "Analytical Engines"
    assert synced.title == "Mathematician"
    assert synced.notes == "Programmer"
    assert unchanged.pk == first_pk
    assert unchanged.sqid == first_sqid
    assert unchanged.updated_at == first_updated_at
    assert refreshed.title == "Principal Mathematician"
    assert refreshed.notes == "Research lead"
    assert Relationship._base_manager.filter(pk=manual.pk, source=LinkSource.MANUAL).exists()


@pytest.mark.django_db(transaction=True)
def test_ingest_contact_requires_employee_relationship_kind(composed_tables: None) -> None:
    """Employment sync fails clearly and atomically when its master kind is missing."""

    del composed_tables
    with system_context(reason="test carddav missing kind"):
        owner = _user("missing-kind")
        directory = make_integration(
            owner.username + "-directory", model=Directory, backend_class="carddav", owner=owner
        )
        folder = Folder._base_manager.create(name="Contacts", directory=directory, created_by=owner)
        parsed = ParsedContact(uid="contact-2", display_name="Grace", organization="Navy")

        with pytest.raises(ValidationError, match="employee.*RelationshipKind"):
            Party.objects.ingest_contact(parsed, folder=folder, created_by_id=owner.pk)

    assert not Person._base_manager.filter(source_uid="contact-2").exists()


def test_seed_vocabulary_matches_model_contract() -> None:
    """The master-tier seed file stays consistent with the model's enum and shape."""

    seed_path = (
        Path(__file__).resolve().parent.parent
        / "addons"
        / "angee"
        / "parties"
        / "resources"
        / "master"
        / "010_parties.relationshipkind.yaml"
    )
    rows = yaml.safe_load(seed_path.read_text())
    assert rows, "seed file must not be empty"
    slugs = [row["slug"] for row in rows]
    assert len(slugs) == len(set(slugs)), "seed slugs must be unique"
    valid_categories = {choice.value for choice in AbstractRelationshipKind.RelationshipCategory}
    valid_party_kinds = {choice.value for choice in AbstractRelationshipKind.PartyKind}
    for row in rows:
        assert row["category"] in valid_categories
        assert row["name"]
        assert row["xref"] == row["slug"]
        for end in ("party_kind", "other_party_kind"):
            if end in row:
                assert row[end] in valid_party_kinds
    by_slug = {row["slug"]: row for row in rows}
    assert by_slug["parent"]["inverse_name"] == "Child"
    assert "inverse_name" not in by_slug["friend"], "friend is symmetric"
    # Employment kinds pin the counterparty to an organisation.
    assert by_slug["employee"]["other_party_kind"] == "organization"
