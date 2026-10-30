"""Tests for parties circles, relationships, and identity confirm/dismiss."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context
from rebac.actors import to_subject_ref

from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.messaging.backends import ManualChannelBackend
from angee.messaging.testing.models import (
    Address,
    Channel,
    Circle,
    CircleMember,
    Directory,
    Folder,
    Handle,
    Message,
    Organization,
    Participant,
    Party,
    Person,
    Reaction,
    Relationship,
    RelationshipKind,
    Thread,
)
from angee.parties.backends import ManualDirectoryBackend, ParsedAddress, ParsedContact
from angee.parties.models import RelationshipKind as AbstractRelationshipKind
from angee.provenance.statements import (
    SourceKind,
    StatementSourceMixin,
)
from angee.tags.testing.models import Tag, TagAssignment
from tests.conftest import current_stream, make_integration
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
def test_manual_contact_reuses_a_retained_alias_survivor(composed_tables: None) -> None:
    del composed_tables
    owner = _user("manual-contact-redirect-owner")
    with system_context(reason="test manual contact redirect"):
        party = Party._base_manager.create(display_name="Contact", created_by=owner)
        alias = Handle.objects.upsert(platform="email", value="old@example.test", created_by_id=owner.pk)
        survivor = Handle.objects.upsert(platform="email", value="new@example.test", created_by_id=owner.pk)
        survivor.fold([alias])
    with actor_context(owner):
        stance = Party.objects.propose_manual_contact(
            party, platform="email", value=alias.value, actor=owner,
        )
    assert stance.handle_id == survivor.pk
    with system_context(reason="test manual contact redirect retained statements"):
        assert not Party.statements.filter(handle=alias).exists()


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
    # A manual claim awaits review, so it does not decide the shared handle's party.
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
    """hierarchy_scope_fields=("owner",): a parent from another owner is rejected."""

    del composed_tables
    with system_context(reason="test circles"):
        mine = Circle._base_manager.create(name="Mine", created_by=_user("me"))
        theirs = Circle._base_manager.create(name="Theirs", created_by=_user("them"))
        assert (mine.owner_id, theirs.owner_id) == (mine.created_by_id, theirs.created_by_id)
        with pytest.raises(ValidationError):
            Circle._base_manager.create(name="Nested", parent=theirs, created_by=mine.created_by)


@pytest.mark.django_db(transaction=True)
def test_circle_transfer_moves_a_lone_circle_and_never_splits_a_tree(composed_tables: None) -> None:
    """The owner scopes the tree: a transfer refuses a circle with a parent or children."""

    del composed_tables
    with system_context(reason="test circles"):
        owner, recipient = _user("circle-owner"), _user("circle-recipient")
    with actor_context(owner):
        root = Circle.objects.create(name="Friends")
        child = Circle.objects.create(name="Climbing", parent=root)
        loose = Circle.objects.create(name="Loose")
        for circle in (root, child):
            with pytest.raises(ValidationError):
                Circle.objects.get(pk=circle.pk).transfer_ownership(recipient)
        Circle.objects.get(pk=loose.pk).transfer_ownership(recipient)
        assert not Circle.objects.filter(pk=loose.pk).exists()
    with actor_context(recipient):
        nested = Circle.objects.create(name="Nested", parent=Circle.objects.get(pk=loose.pk))
    with system_context(reason="test circles"):
        rows = Circle._base_manager.in_bulk([root.pk, child.pk, loose.pk, nested.pk])
    assert (rows[root.pk].owner_id, rows[child.pk].owner_id) == (owner.pk, owner.pk)
    assert (rows[loose.pk].owner_id, rows[loose.pk].created_by_id) == (recipient.pk, owner.pk)
    assert rows[nested.pk].owner_id == recipient.pk


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
@pytest.mark.parametrize("decision", ("confirm", "dismiss"))
def test_circle_membership_decisions_preserve_confidence_and_update_timestamp(
    composed_tables: None, decision: str,
) -> None:
    """Review flags and their native timestamp belong to the membership owner."""

    del composed_tables
    with system_context(reason="test membership decision timestamp"):
        owner = _user("membership-reviewer")
        circle = Circle._base_manager.create(name="Team", created_by=owner)
        party = Party._base_manager.create(display_name="Maya", created_by=owner)
        row = CircleMember._base_manager.create(circle=circle, party=party, confidence=0.3, created_by=owner)
        previous = timezone.now() - timedelta(days=1)
        CircleMember._base_manager.filter(pk=row.pk).update(updated_at=previous)
        row.refresh_from_db()

        getattr(row, decision)()
        row.refresh_from_db()

    assert row.confidence == 0.3
    assert row.is_confirmed is (decision == "confirm")
    assert row.is_dismissed is (decision == "dismiss")
    assert row.updated_at > previous


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
def test_moving_handle_statements_retains_each_partys_decisions_and_evidence(composed_tables: None) -> None:
    """A party stating both spellings keeps the stronger current; the weaker stays superseded with its evidence."""

    del composed_tables
    with system_context(reason="test move statements"):
        owner = _user("move-statements")
        alias = Handle._base_manager.create(platform="whatsapp", value="113352894324870@lid", created_by=owner)
        phone = Handle._base_manager.create(platform="whatsapp", value="+18583421935", created_by=owner)
        confirmed, dismissed, stronger = (
            Party._base_manager.create(display_name=name, created_by=owner) for name in ("Bob", "Robert", "Rob")
        )
        confirmed.with_actor(owner).confirm(aspect="handle", handle=alias, values={})
        folded = Party.objects.assert_handle(confirmed, phone, asserted_by=owner, confidence=0.6, evidence=[alias])
        Party.objects.assert_handle(dismissed, alias, asserted_by=owner, confidence=0.6)
        dismissed.with_actor(owner).dismiss(aspect="handle", handle=alias)
        Party.objects.assert_handle(stronger, alias, asserted_by=owner, confidence=0.3)
        Party.objects.assert_handle(stronger, phone, asserted_by=owner, confidence=0.9)
        alias.refresh_from_db()
        assert alias.party_id == confirmed.pk

        phone.fold([alias])

        current = {
            row.subject_id: (row.confidence, row.is_confirmed, row.is_dismissed)
            for row in Party.statements.filter(handle=phone).current()
        }
        superseded = list(Party.statements.filter(handle=phone, superseded__isnull=False).order_by("confidence"))
        for row in (phone, alias, confirmed, folded):
            row.refresh_from_db()
        assert not Party.statements.filter(handle=alias).exists()
        assert alias.party_id is None

    assert current == {
        confirmed.pk: (1.0, True, False),
        dismissed.pk: (1.0, False, True),
        stronger.pk: (0.9, False, False),
    }
    assert [(row.subject_id, row.confidence) for row in superseded] == [(stronger.pk, 0.3), (confirmed.pk, 0.6)]
    assert (folded.superseded_id is not None, folded.evidence) == (True, superseded[1].evidence)
    assert (phone.party_id, phone.party_link_confirmed) == (confirmed.pk, True)
    assert confirmed.handle_count == 1


@pytest.mark.django_db(transaction=True)
def test_moving_a_handles_only_statement_leaves_it_unowned(composed_tables: None) -> None:
    """Nothing is deleted, so the move itself re-resolves the handle it empties and recounts its owner."""

    del composed_tables
    with system_context(reason="test move lone statement"):
        owner = _user("move-lone-statement")
        alias = Handle._base_manager.create(platform="whatsapp", value="113352894324871@lid", created_by=owner)
        phone = Handle._base_manager.create(platform="whatsapp", value="+18583421936", created_by=owner)
        party = Party._base_manager.create(display_name="Bob", created_by=owner)
        Party.objects.assert_handle(party, alias, asserted_by=owner)

        phone.fold([alias])

        for row in (alias, phone, party):
            row.refresh_from_db()
    assert (alias.party_id, phone.party_id, party.handle_count) == (None, party.pk, 1)


@pytest.mark.django_db(transaction=True)
def test_a_folded_statement_stays_inspectable_as_superseded(composed_tables: None) -> None:
    """A source stating both spellings keeps its stronger statement current; the weaker is retained, not deleted."""

    del composed_tables
    with system_context(reason="test move statements fold"):
        owner = _user("move-statements-fold")
        alias = Handle._base_manager.create(platform="whatsapp", value="113352894324872@lid", created_by=owner)
        phone = Handle._base_manager.create(platform="whatsapp", value="+18583421937", created_by=owner)
        party = Party._base_manager.create(display_name="Bob", created_by=owner)
        weaker = Party.objects.assert_handle(party, alias, asserted_by=owner, confidence=0.9)
        stronger = Party.objects.assert_handle(party, phone, asserted_by=owner, confidence=1.0)

        phone.fold([alias])

        weaker.refresh_from_db()
        stronger.refresh_from_db()
        assert (weaker.handle_id, weaker.superseded_id, weaker.applied) == (phone.pk, stronger.pk, False)
        assert (stronger.superseded_id, stronger.applied) == (None, True)
        assert Party.statements.asserted().filter(pk=stronger.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_a_moved_import_statement_stays_beside_the_directorys_and_the_directory_publishes(
    composed_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The statement key carries its source: a takeout's alias and a card's number stay two current stances."""

    del composed_tables
    monkeypatch.setattr(ManualChannelBackend, "source_kind", SourceKind.IMPORT)
    statements = Party.statements.model
    with system_context(reason="test mixed-source move"):
        owner = _user("move-mixed-sources")
        alias = Handle._base_manager.create(platform="whatsapp", value="113352894324875@lid", created_by=owner)
        phone = Handle._base_manager.create(platform="whatsapp", value="+18583421940", created_by=owner)
        party = Party._base_manager.create(display_name="Bob", created_by=owner)
        takeout = make_integration("move-mixed-takeout", model=Channel, backend_class="manual").integration_ptr
        directory = make_integration("move-mixed-directory", model=Directory)
        assert StatementSourceMixin.source_kind_for(to_subject_ref(takeout)) == SourceKind.IMPORT
        imported, carded = (
            statements._base_manager.create(
                subject=party,
                handle=handle,
                aspect="handle",
                asserted_by=str(to_subject_ref(source)),
                source_kind=kind,
                confidence=confidence,
            )
            for handle, source, kind, confidence in (
                (alias, takeout, SourceKind.IMPORT, 1.0),
                (phone, directory, SourceKind.DIRECTORY, 0.9),
            )
        )
        Party.statements.resolve(subject=party, aspect="handle")

        phone.fold([alias])

        for row in (imported, carded, phone):
            row.refresh_from_db()
        published = set(Party.statements.asserted().filter(handle=phone).values_list("pk", flat=True))
    assert [(row.handle_id, row.superseded_id) for row in (imported, carded)] == [(phone.pk, None), (phone.pk, None)]
    assert (carded.applied, imported.applied, phone.party_id) == (True, False, party.pk)
    assert published == {carded.pk}


def _handle_merge_fixture(slug: str, digits: str) -> tuple[Any, Any, Any]:
    """An owner, a WhatsApp ``@lid`` alias and the phone handle it resolves to."""

    owner = _user(slug)
    alias = Handle._base_manager.create(platform="whatsapp", value=f"11335289432{digits}@lid", created_by=owner)
    phone = Handle._base_manager.create(platform="whatsapp", value=f"+1858342{digits}", created_by=owner)
    return owner, alias, phone


@pytest.mark.django_db(transaction=True)
def test_merging_a_handle_fills_only_what_the_kept_handle_lacks_and_moves_its_statements(
    composed_tables: None,
) -> None:
    del composed_tables
    with system_context(reason="test handle merge fill"):
        owner, alias, phone = _handle_merge_fixture("merge-handle-fill", "1941")
        Handle._base_manager.filter(pk=alias.pk).update(
            controller=owner,
            label="work",
            display_name="Bob (LID)",
            is_verified=True,
            metadata={"jid": "a", "lid": "b"},
        )
        Handle._base_manager.filter(pk=phone.pk).update(display_name="Bob", metadata={"jid": "p"})
        party = Party._base_manager.create(display_name="Bob", created_by=owner)
        stance = party.with_actor(owner).confirm(aspect="handle", handle=alias, values={})

        kept = phone.fold([alias])

        stance.refresh_from_db()
    assert kept.pk == phone.pk
    assert Handle._base_manager.get(pk=alias.pk).merged_into_id == phone.pk
    assert (kept.controller_id, kept.label, kept.display_name, kept.is_verified) == (owner.pk, "work", "Bob", True)
    assert kept.metadata == {"jid": "p"}
    assert (stance.handle_id, stance.is_confirmed, kept.party_id, kept.party_link_confirmed) == (
        phone.pk,
        True,
        party.pk,
        True,
    )


@pytest.mark.django_db(transaction=True)
def test_merging_a_handle_moves_what_points_at_it_and_collapses_facts_the_kept_handle_holds(
    composed_tables: None,
) -> None:
    """A participation, reaction or tag the phone handle already has is one fact; the rest moves."""

    del composed_tables
    with system_context(reason="test handle merge references"):
        owner, alias, phone = _handle_merge_fixture("merge-handle-references", "1942")
        channel = make_integration("merge-handle-channel", model=Channel, backend_class="manual")
        thread, other_thread = Thread.objects.create(owner=owner), Thread.objects.create(owner=owner)
        message = Message._base_manager.create(
            channel=channel,
            thread=thread,
            external_id="merge-handle-message",
            direction="inbound",
            status="synced",
            message_type="email",
            sender=alias,
            created_by=owner,
        )
        role = Participant.ParticipantRole
        for handle in (alias, phone):
            Participant._base_manager.create(message=message, thread=thread, handle=handle, role=role.FROM)
            Participant._base_manager.create(thread=thread, handle=handle, role=role.TO)
            Reaction._base_manager.create(message=message, handle=handle, reaction="👍")
        moved_participant = Participant._base_manager.create(thread=other_thread, handle=alias, role=role.TO)
        moved_reaction = Reaction._base_manager.create(message=message, handle=alias, reaction="❤️")
        vip, work = Tag.objects.create(name="VIP"), Tag.objects.create(name="Work")
        handle_type = ContentType.objects.get_for_model(Handle)
        for handle, tag in ((alias, vip), (phone, vip), (alias, work)):
            TagAssignment._base_manager.create(tag=tag, content_type=handle_type, object_id=handle.pk)

        phone.fold([alias])

        message.refresh_from_db()
        moved_participant.refresh_from_db()
        moved_reaction.refresh_from_db()
        participants = Counter(
            Participant._base_manager.filter(handle=phone).values_list("thread_id", "message_id", "role")
        )
        reactions = sorted(Reaction._base_manager.filter(handle=phone).values_list("reaction", flat=True))
        tags = sorted(
            TagAssignment._base_manager.filter(content_type=handle_type, object_id=phone.pk).values_list(
                "tag__name", flat=True
            )
        )
    assert message.sender_id == phone.pk
    assert (moved_participant.handle_id, moved_reaction.handle_id) == (phone.pk, phone.pk)
    assert participants == Counter(
        [(thread.pk, message.pk, role.FROM), (thread.pk, None, role.TO), (other_thread.pk, None, role.TO)]
    )
    assert reactions == sorted(["👍", "❤️"])
    assert tags == ["VIP", "Work"]


@pytest.mark.django_db(transaction=True)
def test_folding_a_handle_redirects_ingestion_to_the_survivor(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="test handle merge vanished"):
        owner, alias, phone = _handle_merge_fixture("merge-handle-vanished", "1943")
        party = Party._base_manager.create(display_name="Bob", created_by=owner)
        Party.objects.assert_handle(party, phone, asserted_by=owner)

        assert phone.fold([alias]).pk == phone.pk
        assert Handle.objects.upsert(platform=alias.platform, value=alias.value).pk == phone.pk
        assert Handle.objects.canonical().filter(pk=alias.pk).exists() is False

        phone.refresh_from_db()
        assert Party.statements.filter(handle=phone).count() == 1
    assert phone.party_id == party.pk


@pytest.mark.django_db(transaction=True)
def test_merging_handles_on_different_platforms_refuses(composed_tables: None) -> None:
    """Two platforms are two contact points, never one's spelling of the other."""

    del composed_tables
    with system_context(reason="test handle merge platforms"):
        owner, alias, _phone = _handle_merge_fixture("merge-handle-platforms", "1944")
        email = Handle._base_manager.create(platform="email", value="bob-1944@example.test", created_by=owner)

        with pytest.raises(ValidationError, match="same platform"):
            email.fold([alias])

        kept = set(Handle._base_manager.filter(pk__in=(alias.pk, email.pk)).values_list("pk", flat=True))
    assert kept == {alias.pk, email.pk}


@pytest.mark.django_db(transaction=True)
def test_a_consumer_host_reaches_the_rule_sources_through_the_parties_test_plugin(statement_sources: None) -> None:
    """A host without the framework's composed tables loads parties' install-tier principals from its plugin."""

    del statement_sources
    for key in ("normalized_twin", "email_domain", "display_name", "signature_phone", "source_sender"):
        assert Party.objects.rule_source(key).key == f"parties.{key}"


@pytest.mark.django_db(transaction=True)
def test_a_confirmation_writes_only_the_decision_and_stays_asserted(composed_tables: None) -> None:
    """Confirming a rule's suggestion writes the person's preferred statement beside the untouched producer.

    A source that owns the suggestion's score (an Odoo import, a matching rule)
    keeps its row, score and kind; the handle leaves review and is asserted.
    """

    del composed_tables
    with system_context(reason="test confirm keeps producer"):
        owner = _user("confirm-producer")
        party = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        handle = Handle._base_manager.create(platform="email", value="ada@example.com", created_by=owner)
        rule = Party.objects.rule_source("display_name")
        producer = Party.objects.assert_handle(party, handle, asserted_by=rule, confidence=0.4)
        assert not Party.statements.asserted().filter(pk=producer.pk).exists()
        assert Party.statements.to_review().filter(pk=producer.pk).exists()
        before = (producer.updated_at, producer.rank, producer.confidence, producer.source_kind)

        decision = producer.with_actor(owner).confirm()

        producer.refresh_from_db()
        handle.refresh_from_db()
        assert (producer.updated_at, producer.rank, producer.confidence, producer.source_kind) == before
        assert (decision.subject_id, decision.handle_id, decision.is_confirmed) == (party.pk, handle.pk, True)
        assert decision.pk != producer.pk
        assert Party.statements.asserted().filter(pk=decision.pk).exists()
        assert not Party.statements.to_review().filter(handle=handle).exists()
        assert handle.party_id == party.pk


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

        # A human confirm outranks any score and clears the dismissal; the link keeps
        # its producer's source and score.
        strong.refresh_from_db()
        strong = strong.with_actor(owner).confirm()
        handle.refresh_from_db()
        strong.refresh_from_db()
        assert handle.party_id == alice.pk
        assert handle.party_link_confirmed is True
        assert strong.is_confirmed and not strong.is_dismissed
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
def test_claim_control_writes_control_and_identity_facts(composed_tables: None) -> None:
    """Handle.claim_control sets the controller (control) and a normal person statement (identity)."""

    del composed_tables
    with system_context(reason="test claim_control"):
        user = User.objects.create_user(username="nils", email="nils@example.com", password="x")
        handle = Handle.objects.claim_control(
            user,
            platform=Handle.Platform.EMAIL,
            value="nils@work.example",
            asserted_by=user,
        )
        again = Handle.objects.claim_control(
            user,
            platform=Handle.Platform.EMAIL,
            value="nils@work.example",
            asserted_by=user,
        )

        handle.refresh_from_db()
        assert again.pk == handle.pk
        assert handle.controller_id == user.pk
        person = Person.objects.for_user(user)
        assert handle.party_id == person.pk
        assert handle.party_link_confirmed is False
        link = Party.statements.get(handle=handle, subject=person)
        assert not link.is_confirmed and link.source_kind == "person" and link.confidence == 1.0
        assert Party.statements.filter(handle=handle, subject=person).count() == 1


@pytest.mark.django_db(transaction=True)
def test_claim_control_records_a_contested_identity_without_reassigning_control(composed_tables: None) -> None:
    """A second user's claim stays weak and unconfirmed; the first controller remains in control."""

    del composed_tables
    with system_context(reason="test contested claim_control"):
        first_user = User.objects.create_user(username="first", email="shared@example.com", password="x")
        competing_user = User.objects.create_user(username="competing", email="competing@example.com", password="x")
        handle = Handle.objects.claim_control(
            first_user,
            platform=Handle.Platform.EMAIL,
            value="shared@example.com",
            asserted_by=first_user,
        )
        contested = Handle.objects.claim_control(
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
    assert contested.controller_id == first_user.pk
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
        assert list(Party.statements.filter(handle=resolved).values_list("subject_id", flat=True)) == [alice.pk]

    assert result is None


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
        assert Party.statements.filter(subject=alice, handle=unknown).count() == 2

    dismissed.refresh_from_db()
    unknown.refresh_from_db()
    assert repeated is not None
    assert repeated.subject_id == dismissed.subject_id
    assert dismissed.is_dismissed
    assert not dismissed.is_confirmed
    assert unknown.party_id is None


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
def test_suggest_for_stays_inside_the_parties_the_handle_creator_owns(composed_tables: None) -> None:
    """The partition follows party ownership, not who first recorded the party."""

    del composed_tables
    with system_context(reason="test suggest owner partition"):
        recorder, owner = _user("rhea"), _user("otto")
        acme = Organization._base_manager.create(
            display_name="Acme",
            domain="acme.com",
            created_by=recorder,
            owner=owner,
        )
        recorded = Handle._base_manager.create(platform="email", value="ann@acme.com", created_by=recorder)
        owned = Handle._base_manager.create(platform="email", value="ben@acme.com", created_by=owner)

        assert Party.objects.suggest_for(recorded) is None
        link = Party.objects.suggest_for(owned)

    assert link is not None and link.subject_id == acme.pk


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
    """Party.merge atomically flattens chains; PartyQuerySet.canonical drops merged rows."""

    del composed_tables
    with system_context(reason="test merge"):
        owner = _user("rex")
        a = Party._base_manager.create(display_name="A", created_by=owner)
        b = Party._base_manager.create(display_name="B", created_by=owner)
        c = Party._base_manager.create(display_name="C", created_by=owner)

        b.with_actor(owner).merge(records=[a.public_id])
        a.refresh_from_db()
        assert a.merged_into_id == b.pk

        # Merging B into C repoints A forward in the same write — no two-hop chain.
        c.with_actor(owner).merge(records=[b.public_id])
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
def test_merge_rejects_reversal_and_database_rejects_self_merge(composed_tables: None) -> None:
    """The verb rejects A→B when B→A exists; the database is the direct-write floor."""

    del composed_tables
    with system_context(reason="test merge reversal"):
        owner = _user("reversal")
        a = Party._base_manager.create(display_name="A", created_by=owner)
        b = Party._base_manager.create(display_name="B", created_by=owner)
        a.with_actor(owner).merge(records=[b.public_id])

        with pytest.raises(ValidationError, match="canonical"):
            b.with_actor(owner).merge(records=[a.public_id])

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
        stream = current_stream(directory, "contacts", kind=StreamKind.RECORD_REPLICA)
        link = RecordLink.objects.observe(stream, "contact-1")
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

        person = Party.objects.ingest_contact(parsed, folder=folder, created_by_id=owner.pk, link=link)
        synced = Relationship._base_manager.get(
            party=person,
            kind=employee,
            pk__in=Relationship.statements.filter(link=link).values("subject_id"),
            other_party__isnull=True,
        )
        first_pk = synced.pk
        first_sqid = synced.sqid
        first_updated_at = synced.updated_at
        manual = Relationship._base_manager.create(
            party=person,
            kind=employee,
            other_name="Human-maintained employer",
            created_by=owner,
        )

        again = Party.objects.ingest_contact(parsed, folder=folder, created_by_id=owner.pk, link=link)
        unchanged = Relationship._base_manager.get(
            party=again,
            kind=employee,
            pk__in=Relationship.statements.filter(link=link).values("subject_id"),
            other_party__isnull=True,
        )

        changed = ParsedContact(
            uid="contact-1",
            display_name="Ada Lovelace",
            organization="Analytical Engines",
            title="Principal Mathematician",
            role="Research lead",
        )
        Party.objects.ingest_contact(changed, folder=folder, created_by_id=owner.pk, link=link, target=person)
        refreshed = Relationship._base_manager.get(pk=first_pk)
        backend = ManualDirectoryBackend(directory)
        local = backend._local_states(stream, ("contact-1",), links=(RecordLink.objects.get(pk=link.pk),))
        assert local["contact-1"][0].pk == person.pk

        foreign, _created = get_user_model()._base_manager.get_or_create(
            pk=person.pk, defaults={"username": "same-primary-key-contact-target"},
        )
        foreign_link = RecordLink.objects.observe(stream, "foreign-contact")
        foreign_link.target = foreign
        foreign_link.save(update_fields=["target_content_type", "target_object_id"])
        foreign_link.refresh_from_db()
        foreign_type = foreign_link.target_content_type_id
        with pytest.raises(ValidationError, match="Party"):
            Party.objects.ingest_contact(
                replace(parsed, uid="foreign-contact", display_name="Wrong identity"),
                folder=folder, created_by_id=owner.pk, link=foreign_link,
            )
        with pytest.raises(ValidationError, match="Party"):
            backend._local_states(stream, ("foreign-contact",), links=(foreign_link,))
        foreign_link.refresh_from_db()
        person.refresh_from_db()
        assert foreign_link.target_content_type_id == foreign_type
        assert foreign_link.target_object_id == str(person.pk)
        assert person.display_name == "Ada Lovelace"

    assert synced.other_name == "Analytical Engines"
    assert synced.title == "Mathematician"
    assert synced.notes == "Programmer"
    assert unchanged.pk == first_pk
    assert unchanged.sqid == first_sqid
    assert unchanged.updated_at == first_updated_at
    assert refreshed.title == "Principal Mathematician"
    assert refreshed.notes == "Research lead"
    assert Relationship._base_manager.filter(pk=manual.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_a_card_pass_keeps_its_addresses_local_facts(composed_tables: None) -> None:
    """A card address updates its matching row and keeps its coordinates and primary flag.

    A new address is created, and only a row the card no longer lists loses the
    card's statement: two equal card addresses are two rows, and listing the
    address once leaves one of them asserted.
    """

    del composed_tables
    home = ParsedAddress(label="home", street="1 Main St", city="Springfield", country="US")
    work = ParsedAddress(label="work", street="2 Side St", city="Shelbyville", country="US")
    with system_context(reason="test carddav addresses"):
        owner = _user("carddav-addresses")
        directory = make_integration(
            owner.username + "-directory", model=Directory, backend_class="carddav", owner=owner
        )
        folder = Folder._base_manager.create(name="Contacts", directory=directory, created_by=owner)
        stream = current_stream(directory, "contacts", kind=StreamKind.RECORD_REPLICA)
        link = RecordLink.objects.observe(stream, "contact-4")
        RelationshipKind._base_manager.create(slug="employee", name="Employee", other_party_kind="organization")
        card = ParsedContact(uid="contact-4", display_name="Ada Lovelace", addresses=(home, work, work))
        person = Party.objects.ingest_contact(card, folder=folder, created_by_id=owner.pk, link=link)
        assert Address._base_manager.filter(party=person, label="work").count() == 2
        located = Address._base_manager.get(party=person, label="home")
        located.latitude, located.longitude, located.is_primary = 39.8, -89.6, True
        located.save(update_fields=["latitude", "longitude", "is_primary", "updated_at"])

        moved = ParsedAddress(label="Home", street="1  MAIN st", city="springfield", country="US")
        office = ParsedAddress(label="office", street="3 Far Rd", city="Ogdenville", country="US")
        Party.objects.ingest_contact(
            replace(card, addresses=(moved, work, office)),
            folder=folder,
            created_by_id=owner.pk,
            link=link,
            target=person,
        )
        rows = list(Address._base_manager.filter(party=person).order_by("pk"))
        asserted = set(
            Address.statements.filter(subject__in=rows, link=link).applied().values_list("subject_id", flat=True)
        )

    kept = next(row for row in rows if row.pk == located.pk)
    assert (kept.label, kept.street, kept.city) == ("Home", "1  MAIN st", "springfield")
    assert (kept.latitude, kept.longitude, kept.is_primary) == (39.8, -89.6, True)
    assert sorted(row.label for row in rows if row.pk in asserted) == ["Home", "office", "work"]
    assert len(rows) == 4


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
        stream = current_stream(directory, "contacts", kind=StreamKind.RECORD_REPLICA)
        link = RecordLink.objects.observe(stream, "contact-2")
        parsed = ParsedContact(uid="contact-2", display_name="Grace", organization="Navy")

        with pytest.raises(ValidationError, match="employee.*RelationshipKind"):
            Party.objects.ingest_contact(parsed, folder=folder, created_by_id=owner.pk, link=link)

    assert not Person._base_manager.filter(display_name="Grace").exists()


def test_seed_vocabulary_matches_model_contract() -> None:
    """The master-tier seed file stays consistent with the model's enum and shape."""

    seed_path = (
        Path(__file__).resolve().parent.parent
        / "src"
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


def _system_of_record_statement(party: Any, source: Any, link: Any, **values: Any) -> Any:
    """A retained SYSTEM_OF_RECORD stance backed by ``link``, as an Odoo companion asserts it."""

    return Party.statements.model._base_manager.create(
        subject=party,
        aspect="identity",
        asserted_by=str(to_subject_ref(source)),
        source_kind=SourceKind.SYSTEM_OF_RECORD,
        link=link,
        **values,
    )


@pytest.mark.django_db(transaction=True)
def test_an_authoritative_stream_outranks_a_persons_edit_but_never_a_decision(composed_tables: None) -> None:
    """Declaring or flipping ``SyncStream.authoritative`` re-derives the subjects; preferred stays on top (§11.2)."""

    del composed_tables
    with system_context(reason="test authoritative stream"):
        owner = _user("authority-owner")
        party = Party._base_manager.create(display_name="Ada (me)", created_by=owner)
        source = make_integration("authority-system-of-record", model=Directory)
        stream = current_stream(source, "partners", kind=StreamKind.RECORD_REPLICA)
        link = RecordLink.objects.observe(stream, "partner-1", target=party)
        party.with_actor(owner).statements.set(aspect="identity", values={"display_name": "Ada (me)"})
        _system_of_record_statement(party, source, link, display_name="Ada (odoo)")
        Party.statements.resolve(subject=party, aspect="identity")
        party.refresh_from_db()
        assert party.display_name == "Ada (me)"

        # The adapter's declaration carries the fact: redeclaring flips the current generation.
        stream = current_stream(source, "partners", kind=StreamKind.RECORD_REPLICA, authoritative=True)
        assert stream.authoritative is True
        party.refresh_from_db()
        assert party.display_name == "Ada (odoo)"
        assert Party.statements.model._base_manager.get(link=link).applied

        SyncStream.objects.set_authoritative(stream, False)
        party.refresh_from_db()
        assert party.display_name == "Ada (me)"

        party.with_actor(owner).confirm(aspect="identity", values={"display_name": "Ada"})
        SyncStream.objects.set_authoritative(stream, True)
        party.refresh_from_db()
        assert party.display_name == "Ada"
        assert SyncStream.objects.bump_generation(stream).authoritative is True


@pytest.mark.django_db(transaction=True)
def test_an_authoritative_stream_outranks_a_persons_later_edit(composed_tables: None) -> None:
    """Authority alone decides between the system of record and a person's normal stance, not row age.

    An upgrade's folds record the system of record first and the person's stance beside it,
    so the person's row is the newer one: once the stream is authoritative, precedence ranks
    the system of record first instead of tying both at the head of the order.
    """

    del composed_tables
    with system_context(reason="test authoritative stream over a later edit"):
        owner = _user("authority-later-owner")
        party = Party._base_manager.create(display_name="Ada (odoo)", created_by=owner)
        source = make_integration("authority-later-system-of-record", model=Directory)
        stream = current_stream(source, "partners", kind=StreamKind.RECORD_REPLICA)
        link = RecordLink.objects.observe(stream, "partner-1", target=party)
        _system_of_record_statement(party, source, link, display_name="Ada (odoo)")
        party.with_actor(owner).statements.set(aspect="identity", values={"display_name": "Ada (me)"})
        party.refresh_from_db()
        assert party.display_name == "Ada (me)"

        SyncStream.objects.set_authoritative(stream, True)
        party.refresh_from_db()
        assert party.display_name == "Ada (odoo)"
        assert Party.statements.model._base_manager.get(link=link).applied


@pytest.mark.django_db(transaction=True)
def test_handle_ownership_ranks_source_precedence_before_confidence_and_recency(composed_tables: None) -> None:
    """A person's older, weaker normal statement outranks a directory's newer full-confidence one."""

    del composed_tables
    with system_context(reason="test handle precedence"):
        owner = _user("precedence-owner")
        handle = Handle._base_manager.create(platform="email", value="shared@example.com", created_by=owner)
        person_party = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        card_party = Party._base_manager.create(display_name="Ada Card", created_by=owner)
        directory = make_integration("precedence-directory", model=Directory)
        Party.objects.assert_handle(person_party, handle, asserted_by=owner, confidence=0.9)
        Party.statements.model._base_manager.create(
            subject=card_party,
            handle=handle,
            aspect="handle",
            asserted_by=str(to_subject_ref(directory)),
            source_kind=SourceKind.DIRECTORY,
            confidence=1.0,
        )
        Party.statements.resolve(subject=card_party, aspect="handle", members={"handle": handle})
        handle.refresh_from_db()
        assert handle.party_id == person_party.pk

        # Within one kind, confidence still decides before recency.
        stronger = Party._base_manager.create(display_name="Ada Stronger", created_by=owner)
        Party.objects.assert_handle(stronger, handle, asserted_by=owner, confidence=1.0)
        handle.refresh_from_db()
        assert handle.party_id == stronger.pk


@pytest.mark.django_db(transaction=True)
def test_dismissing_an_identity_value_clears_it_and_confirming_the_stance_restores_it(composed_tables: None) -> None:
    """A preferred no-value applies: the removal sticks; ``Statement.confirm`` re-applies the retained values."""

    del composed_tables
    with system_context(reason="test identity dismissal"):
        owner = _user("dismiss-owner")
        party = Party._base_manager.create(display_name="Ada", created_by=owner)
        stance = party.with_actor(owner).statements.set(aspect="identity", values={"display_name": "Ada"})
        party.with_actor(owner).dismiss(aspect="identity")
        party.refresh_from_db()
        assert party.display_name == ""
        assert party.statements.provenance("display_name").get().is_dismissed
        restored = stance.with_actor(owner).confirm()
        party.refresh_from_db()
        assert party.display_name == "Ada"
        assert (restored.is_confirmed, restored.applied) == (True, True)
