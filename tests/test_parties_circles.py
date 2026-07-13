"""Tests for parties circles, relationships, and identity confirm/dismiss."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from rebac import system_context

from angee.parties.models import RelationshipKind as AbstractRelationshipKind
from tests.conftest import _clear_model_tables, _create_missing_tables
from tests.test_messaging import (
    MESSAGING_TEST_MODELS,
    Circle,
    CircleMember,
    Handle,
    Party,
    Relationship,
    RelationshipKind,
)
from tests.test_parties_graphql import PartyHandle

User = get_user_model()

CIRCLES_TEST_MODELS = (*MESSAGING_TEST_MODELS, PartyHandle)


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
        strong = PartyHandle.objects.link(alice, handle, confidence=0.9, source="import", owner_id=owner.pk)
        weak = PartyHandle.objects.link(alicia, handle, confidence=0.4, source="llm", owner_id=owner.pk)

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
        PartyHandle.objects.link(alice, handle, confidence=0.95, source="import", owner_id=owner.pk)
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
        assert strong.source == "manual"

        # Low-confidence, undecided links are exactly the review-queue shape.
        review = PartyHandle.objects.filter(is_confirmed=False, is_dismissed=False, confidence__lt=0.5)
        assert list(review.values_list("pk", flat=True)) == [weak.pk]


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
    for row in rows:
        assert row["category"] in valid_categories
        assert row["name"]
        assert row["xref"] == row["slug"]
    by_slug = {row["slug"]: row for row in rows}
    assert by_slug["parent"]["inverse_name"] == "Child"
    assert "inverse_name" not in by_slug["friend"], "friend is symmetric"
