"""Tests for parties circles, relationships, and identity confirm/dismiss."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from rebac import system_context

from angee.parties.backends import ParsedContact
from angee.parties.mixins import LinkSource
from angee.parties.models import RelationshipKind as AbstractRelationshipKind
from tests.conftest import _clear_model_tables, _create_missing_tables
from tests.test_messaging import (
    MESSAGING_TEST_MODELS,
    Circle,
    CircleMember,
    Folder,
    Handle,
    Party,
    Relationship,
    RelationshipKind,
)
from tests.test_parties_graphql import Organization, PartyHandle, Person

User = get_user_model()

CIRCLES_TEST_MODELS = (*MESSAGING_TEST_MODELS, Organization)


@pytest.fixture
def parties_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete parties/messaging tables and sync the REBAC schema."""

    del transactional_db
    created_models = _create_missing_tables(CIRCLES_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(CIRCLES_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _user(username: str) -> Any:
    """Create a plain user for ownership fixtures."""

    return User.objects.create_user(username=username, password="x")


@pytest.mark.django_db(transaction=True)
def test_circle_subtree_and_ancestor_scopes(parties_tables: None) -> None:
    """Circle composes HierarchyMixin: subtree/ancestors read off the path index."""

    del parties_tables
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
def test_circle_tree_never_straddles_owners(parties_tables: None) -> None:
    """hierarchy_scope_fields=("created_by",): a parent from another owner is rejected."""

    del parties_tables
    with system_context(reason="test circles"):
        mine = Circle._base_manager.create(name="Mine", created_by=_user("me"))
        theirs = Circle._base_manager.create(name="Theirs", created_by=_user("them"))
        with pytest.raises(ValidationError):
            Circle._base_manager.create(name="Nested", parent=theirs, created_by=mine.created_by)


@pytest.mark.django_db(transaction=True)
def test_circle_membership_is_unique_per_pair(parties_tables: None) -> None:
    """One row per (circle, party): a re-suggestion updates, never duplicates."""

    del parties_tables
    with system_context(reason="test circles"):
        owner = _user("uma")
        circle = Circle._base_manager.create(name="Family", created_by=owner)
        party = Party._base_manager.create(display_name="Maya", created_by=owner)
        CircleMember._base_manager.create(circle=circle, party=party, created_by=owner)
        with pytest.raises(IntegrityError), transaction.atomic():
            CircleMember._base_manager.create(circle=circle, party=party, created_by=owner)


@pytest.mark.django_db(transaction=True)
def test_relationship_kind_renders_both_directions(parties_tables: None) -> None:
    """One kind row carries both readings via inverse_name; blank means symmetric."""

    del parties_tables
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
def test_relationship_edge_constraints(parties_tables: None) -> None:
    """A tracked edge is unique per (party, other, kind), never self-referential,
    and every edge names a counterparty (tracked or free-text)."""

    del parties_tables
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
def test_relationship_records_untracked_relatives(parties_tables: None) -> None:
    """A relative who is not a directory entry records as free text (health-gaps).

    Two same-kind free-text rows are legitimate (the tracked-pair uniqueness is
    partial), so a family history lists both grandmothers.
    """

    del parties_tables
    with system_context(reason="test relationships"):
        owner = _user("gene")
        kind = RelationshipKind._base_manager.create(slug="grandparent", name="Grandparent", inverse_name="Grandchild")
        maya = Party._base_manager.create(display_name="Maya", created_by=owner)
        first = Relationship._base_manager.create(party=maya, other_name="Rosa K.", kind=kind, created_by=owner)
        second = Relationship._base_manager.create(party=maya, other_name="Vera M.", kind=kind, created_by=owner)

    assert first.other_party_id is None
    assert {first.other_name, second.other_name} == {"Rosa K.", "Vera M."}


@pytest.mark.django_db(transaction=True)
def test_confirm_and_dismiss_drive_resolution(parties_tables: None) -> None:
    """Confirm outranks any score; dismiss is a durable anti-link that demotes and recounts."""

    del parties_tables
    with system_context(reason="test identity"):
        owner = _user("ivan")
        alice = Party._base_manager.create(display_name="Alice", created_by=owner)
        alicia = Party._base_manager.create(display_name="Alicia", created_by=owner)
        handle = Handle._base_manager.create(platform="email", value="a@example.com", created_by=owner)
        strong = PartyHandle.objects.link(
            alice,
            handle,
            confidence=0.9,
            source=LinkSource.IMPORT,
            created_by_id=owner.pk,
        )
        weak = PartyHandle.objects.link(
            alicia,
            handle,
            confidence=0.4,
            source=LinkSource.LLM,
            created_by_id=owner.pk,
        )

        handle.refresh_from_db()
        alice.refresh_from_db()
        assert handle.party_id == alice.pk
        assert alice.handle_count == 1

        # Dismissing the winner demotes the handle to the next candidate and
        # recounts BOTH parties (the demoted owner must not keep a stale count).
        strong.dismiss()
        handle.refresh_from_db()
        alice.refresh_from_db()
        alicia.refresh_from_db()
        assert handle.party_id == alicia.pk
        assert alice.handle_count == 0
        assert alicia.handle_count == 1

        # A re-sync re-linking the dismissed pair must not resurrect it: the
        # dismissed row already exists, so resolution still ignores it.
        PartyHandle.objects.link(
            alice,
            handle,
            confidence=0.95,
            source=LinkSource.IMPORT,
            created_by_id=owner.pk,
        )
        handle.refresh_from_db()
        assert handle.party_id == alicia.pk

        # A human confirm outranks any score and clears the dismissal.
        strong.refresh_from_db()
        strong.confirm()
        handle.refresh_from_db()
        strong.refresh_from_db()
        assert handle.party_id == alice.pk
        assert strong.is_confirmed and not strong.is_dismissed
        assert strong.confidence == 1.0
        assert strong.source == LinkSource.MANUAL

        # Low-confidence, undecided links are exactly the review-queue shape.
        review = PartyHandle.objects.filter(is_confirmed=False, is_dismissed=False, confidence__lt=0.5)
        assert list(review.values_list("pk", flat=True)) == [weak.pk]


@pytest.mark.django_db(transaction=True)
def test_person_for_user_is_the_one_person_per_user_owner(parties_tables: None) -> None:
    """PersonManager.for_user get-or-creates keyed on the Person.user O2O — never two rows."""

    del parties_tables
    with system_context(reason="test for_user"):
        user = User.objects.create_user(username="mona", email="mona@example.com", password="x")
        first = Person.objects.for_user(user)
        again = Person.objects.for_user(user)

    assert first.pk == again.pk
    assert first.user_id == user.pk
    assert first.display_name


@pytest.mark.django_db(transaction=True)
def test_claim_own_writes_control_and_identity_facts(parties_tables: None) -> None:
    """Handle.claim_own sets the owner (control) and a confirmed self-link (identity)."""

    del parties_tables
    with system_context(reason="test claim_own"):
        user = User.objects.create_user(username="nils", email="nils@example.com", password="x")
        handle = Handle.objects.claim_own(
            user,
            platform=Handle.Platform.EMAIL,
            value="nils@work.example",
            source=LinkSource.OAUTH,
        )
        again = Handle.objects.claim_own(
            user,
            platform=Handle.Platform.EMAIL,
            value="nils@work.example",
            source=LinkSource.OAUTH,
        )

        handle.refresh_from_db()
        assert again.pk == handle.pk
        assert handle.owner_id == user.pk
        assert set(Handle.objects.owned_by(user).values_list("pk", flat=True)) == {handle.pk}
        person = Person.objects.for_user(user)
        assert handle.party_id == person.pk
        link = PartyHandle.objects.get(handle=handle, party=person)
        assert link.is_confirmed and link.source == LinkSource.OAUTH and link.confidence == 1.0
        assert PartyHandle.objects.filter(handle=handle, party=person).count() == 1


@pytest.mark.django_db(transaction=True)
def test_claim_own_records_a_contested_identity_without_reassigning_control(parties_tables: None) -> None:
    """A second user's claim stays weak and unconfirmed; the first owner remains in control."""

    del parties_tables
    with system_context(reason="test contested claim_own"):
        first_user = User.objects.create_user(username="first", email="shared@example.com", password="x")
        competing_user = User.objects.create_user(username="competing", email="shared@example.com", password="x")
        handle = Handle.objects.claim_own(
            first_user,
            platform=Handle.Platform.EMAIL,
            value="shared@example.com",
            source=LinkSource.OAUTH,
        )
        contested = Handle.objects.claim_own(
            competing_user,
            platform=Handle.Platform.EMAIL,
            value="shared@example.com",
            source=LinkSource.OAUTH,
        )

        first_person = Person.objects.for_user(first_user)
        competing_person = Person.objects.for_user(competing_user)
        first_link = PartyHandle.objects.get(handle=handle, party=first_person)
        competing_link = PartyHandle.objects.get(handle=handle, party=competing_person)

    contested.refresh_from_db()
    assert contested.pk == handle.pk
    assert contested.owner_id == first_user.pk
    assert contested.party_id == first_person.pk
    assert first_link.is_confirmed and first_link.confidence == 1.0
    assert not competing_link.is_confirmed
    assert competing_link.confidence == 0.3
    assert competing_link.source == LinkSource.OAUTH


@pytest.mark.django_db(transaction=True)
def test_handle_persists_normalized_value_on_create_and_value_update(parties_tables: None) -> None:
    """Handle owns its indexed normalization, including Gmail local-part collapsing."""

    del parties_tables
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
def test_suggest_for_exact_match_autolinks_at_full_confidence(parties_tables: None) -> None:
    """An exact normalized-value match to a resolved handle auto-links (gmail dot/plus)."""

    del parties_tables
    with system_context(reason="test suggest exact"):
        owner = _user("iris")
        alice = Party._base_manager.create(display_name="Alice", created_by=owner)
        resolved = Handle._base_manager.create(platform="email", value="a.lice@gmail.com", created_by=owner)
        PartyHandle.objects.link(
            alice,
            resolved,
            confidence=1.0,
            source=LinkSource.MANUAL,
            created_by_id=owner.pk,
        )

        fresh = Handle._base_manager.create(platform="email", value="alice+news@gmail.com", created_by=owner)
        link = PartyHandle.objects.suggest_for(fresh)

    assert link is not None
    fresh.refresh_from_db()
    assert fresh.party_id == alice.pk
    assert link.source == LinkSource.EMAIL_MATCH
    assert not link.is_confirmed
    assert link.confidence == 1.0


@pytest.mark.django_db(transaction=True)
def test_suggest_for_records_conflicting_normalized_twins(parties_tables: None) -> None:
    """Resolved normalized twins create one strong suggestion plus weak competing links."""

    del parties_tables
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
        PartyHandle.objects.link(
            alice,
            first,
            source=LinkSource.MANUAL,
            created_by_id=owner.pk,
        )
        PartyHandle.objects.link(
            alicia,
            second,
            source=LinkSource.MANUAL,
            created_by_id=owner.pk,
        )
        fresh = Handle._base_manager.create(
            platform=Handle.Platform.EMAIL,
            value="alice+new@gmail.com",
            created_by=owner,
        )

        winner = PartyHandle.objects.suggest_for(fresh)
        links = list(PartyHandle.objects.filter(handle=fresh).order_by("party_id"))

    assert winner is not None
    by_party = {link.party_id: link for link in links}
    assert set(by_party) == {alice.pk, alicia.pk}
    assert by_party[alice.pk].confidence == 1.0
    assert by_party[alicia.pk].confidence == 0.3
    assert all(not link.is_confirmed for link in links)
    assert all(link.source == LinkSource.EMAIL_MATCH for link in links)


@pytest.mark.django_db(transaction=True)
def test_suggest_for_org_domain_suggests_at_low_confidence(parties_tables: None) -> None:
    """An email whose domain matches Organization.domain suggests that org weakly."""

    del parties_tables
    with system_context(reason="test suggest domain"):
        owner = _user("omar")
        acme = Organization._base_manager.create(display_name="Acme", domain="acme.com", created_by=owner)
        handle = Handle._base_manager.create(platform="email", value="bob@acme.com", created_by=owner)
        link = PartyHandle.objects.suggest_for(handle)

    assert link is not None
    assert link.party_id == acme.pk
    assert link.confidence == 0.4
    assert not link.is_confirmed
    assert link.source == LinkSource.RULE


@pytest.mark.django_db(transaction=True)
def test_suggest_for_no_match_is_a_noop(parties_tables: None) -> None:
    """No twin and no org-domain match leaves the handle unresolved (the Salesforce policy)."""

    del parties_tables
    with system_context(reason="test suggest noop"):
        owner = _user("nadia")
        handle = Handle._base_manager.create(platform="email", value="stranger@nowhere.example", created_by=owner)
        link = PartyHandle.objects.suggest_for(handle)

    assert link is None
    handle.refresh_from_db()
    assert handle.party_id is None


@pytest.mark.django_db(transaction=True)
def test_members_of_serves_the_org_membership_query(parties_tables: None) -> None:
    """PartyQuerySet.members_of returns parties whose relationships name the org."""

    del parties_tables
    with system_context(reason="test members_of"):
        owner = _user("pia")
        kind = RelationshipKind._base_manager.create(
            slug="employee", name="Employee", inverse_name="Employer", category="professional",
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
    parties_tables: None,
    django_assert_num_queries: Any,
) -> None:
    """An organization-typed end rejects a person counterparty via clean()/save()."""

    del parties_tables
    with system_context(reason="test legality"):
        owner = _user("quinn")
        kind = RelationshipKind._base_manager.create(
            slug="employee", name="Employee", inverse_name="Employer", category="professional",
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
def test_merged_into_flattens_transitively(parties_tables: None) -> None:
    """Party.merge_into atomically flattens chains; PartyQuerySet.canonical drops merged rows."""

    del parties_tables
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


@pytest.mark.django_db(transaction=True)
def test_merge_into_rejects_reversal_and_database_rejects_self_merge(parties_tables: None) -> None:
    """The verb rejects A→B when B→A exists; the database is the direct-write floor."""

    del parties_tables
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
def test_ingest_contact_upserts_carddav_employment_in_place(parties_tables: None) -> None:
    """ORG/TITLE/ROLE map to one stable CardDAV edge while manual rows survive."""

    del parties_tables
    with system_context(reason="test carddav employment"):
        owner = _user("carddav")
        folder = Folder._base_manager.create(name="Contacts", created_by=owner)
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
def test_ingest_contact_requires_employee_relationship_kind(parties_tables: None) -> None:
    """Employment sync fails clearly and atomically when its master kind is missing."""

    del parties_tables
    with system_context(reason="test carddav missing kind"):
        owner = _user("missing-kind")
        folder = Folder._base_manager.create(name="Contacts", created_by=owner)
        parsed = ParsedContact(uid="contact-2", display_name="Grace", organization="Navy")

        with pytest.raises(ValidationError, match="employee.*RelationshipKind"):
            Party.objects.ingest_contact(parsed, folder=folder, created_by_id=owner.pk)

    assert not Person._base_manager.filter(source_uid="contact-2").exists()


@pytest.mark.django_db(transaction=True)
def test_counter_signals_reresolve_on_direct_link_delete(parties_tables: None) -> None:
    """The PartyHandle post_delete receiver re-resolves and recounts a raw link delete.

    A raw ``delete()`` (or a queryset/cascade delete) bypasses ``dismiss()``, so the
    resolved owner and its ``handle_count`` would drift without the signal.
    """

    del parties_tables
    with system_context(reason="test counters"):
        owner = _user("sam")
        alice = Party._base_manager.create(display_name="Alice", created_by=owner)
        bob = Party._base_manager.create(display_name="Bob", created_by=owner)
        handle = Handle._base_manager.create(platform="email", value="a@example.com", created_by=owner)
        strong = PartyHandle.objects.link(
            alice,
            handle,
            confidence=0.9,
            source=LinkSource.IMPORT,
            created_by_id=owner.pk,
        )
        PartyHandle.objects.link(
            bob,
            handle,
            confidence=0.4,
            source=LinkSource.LLM,
            created_by_id=owner.pk,
        )
        alice.refresh_from_db()
        assert alice.handle_count == 1

        # Deleting the winning link demotes the handle to the next candidate and
        # recounts BOTH parties — with no dismiss() call to drive it.
        strong.delete()
        handle.refresh_from_db()
        alice.refresh_from_db()
        bob.refresh_from_db()
        assert handle.party_id == bob.pk
        assert alice.handle_count == 0
        assert bob.handle_count == 1


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
