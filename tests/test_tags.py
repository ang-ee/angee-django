"""Tests for the tags addon — the polymorphic edge, company scope, and the manager protocol.

The vocabulary is an admin-curated surface, so rows are created under
``system_context``; the scope reads run under ``actor_context`` after
``rebac sync`` loads the schema — emulating the authenticated actor a real
request runs as. Party tagging is exercised against a real ``parties.Party``
row through the polymorphic edge, with no ``parties`` change.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from rebac import (
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model

from angee.base.models import public_id_for
from angee.tags.models import Tag as AbstractTag
from angee.tags.models import TagAssignment as AbstractTagAssignment
from angee.tags.models import TagRole as AbstractTagRole
from tests.conftest import _clear_model_tables, _create_missing_tables, create_user
from tests.iam_models import Company
from tests.mtidemo.models import MtiChild, MtiParent
from tests.test_messaging import Party


class Tag(AbstractTag):
    """Concrete tag used by tags tests."""

    class Meta(AbstractTag.Meta):
        """Django model options for the canonical test tag."""

        abstract = False
        app_label = "tags"
        db_table = "test_tags_tag"
        rebac_resource_type = "tags/tag"
        rebac_id_attr = "sqid"


class TagAssignment(AbstractTagAssignment):
    """Concrete polymorphic tag edge used by tags tests."""

    class Meta(AbstractTagAssignment.Meta):
        """Django model options for the canonical test tag assignment."""

        abstract = False
        app_label = "tags"
        db_table = "test_tags_assignment"
        rebac_resource_type = "tags/tag_assignment"
        rebac_id_attr = "sqid"


class TagRole(AbstractTagRole):
    """Concrete table-less REBAC anchor for the ``tags/role`` namespace.

    The composer emits this anchor in the runtime; the bare test env must
    register it too so the const-backed ``admin`` arm of ``tags/role`` (reached
    on every actor-scoped ``tags/tag`` read through
    ``manager->effective_member``) resolves to a deny instead of raising
    ``SchemaError``. ``managed = False`` — never a table, only a type anchor.
    """

    class Meta(AbstractTagRole.Meta):
        """Django model options for the canonical test tags role anchor."""

        abstract = False
        managed = False
        app_label = "tags"
        rebac_resource_type = "tags/role"


TAGS_TEST_MODELS = (Tag, TagAssignment, Party)
"""Concrete tags models (plus the party target) created on demand by tags fixtures."""


@pytest.fixture()
def tags_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete tags tables and sync the REBAC schema.

    ``Tag.save`` writes a ``shared`` relationship tuple that ``write_relationships``
    validates against the loaded schema, so any test creating a tag must first sync
    it (a per-row tuple needs its resource type registered). The production loop
    syncs before load, so only unit tests carry this setup.
    """

    del transactional_db
    created_models = _create_missing_tables(TAGS_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(TAGS_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _grant_membership(company: Any, user: Any) -> None:
    """Write one direct company-of-record membership tuple."""

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(company),
                relation="direct_member",
                subject=to_subject_ref(user),
            )
        ]
    )


def _shared_reader_exists(tag: Any) -> bool:
    """Return whether ``tag`` carries the ``shared@auth/user:*`` wildcard reader."""

    relationship_model = active_relationship_model()
    return relationship_model.objects.filter(
        resource_type="tags/tag",
        resource_id=tag.sqid,
        relation="shared",
        subject_type="auth/user",
        subject_id="*",
    ).exists()


def test_tag_a_party_through_a_generic_assignment(tags_tables: None) -> None:
    """The polymorphic edge resolves back to the exact party and tag."""

    del tags_tables
    with system_context(reason="tags test setup"):
        party = Party.objects.create(display_name="Acme Corp")
        tag = Tag.objects.create(name="VIP")
        assignment = TagAssignment.objects.create(
            tag=tag,
            content_type=ContentType.objects.get_for_model(Party),
            object_id=party.pk,
        )

    assert assignment.target == party
    assert assignment.tag == tag


def test_a_partys_tags_resolve_by_content_type_and_object_id(tags_tables: None) -> None:
    """The reverse query (content_type, object_id) returns the party's tags."""

    del tags_tables
    with system_context(reason="tags test setup"):
        party = Party.objects.create(display_name="Beta LLC")
        tag_one = Tag.objects.create(name="Prospect")
        tag_two = Tag.objects.create(name="Wholesale")
        content_type = ContentType.objects.get_for_model(Party)
        for tag in (tag_one, tag_two):
            TagAssignment.objects.create(tag=tag, content_type=content_type, object_id=party.pk)

        tagged = set(
            TagAssignment.objects.filter(content_type=content_type, object_id=party.pk).values_list(
                "tag_id", flat=True
            )
        )

    assert tagged == {tag_one.pk, tag_two.pk}


def test_the_same_tag_attaches_once_per_target(tags_tables: None) -> None:
    """``unique(tag, content_type, object_id)`` rejects a duplicate edge."""

    del tags_tables
    with system_context(reason="tags test setup"):
        party = Party.objects.create(display_name="Gamma Inc")
        tag = Tag.objects.create(name="Partner")
        content_type = ContentType.objects.get_for_model(Party)
        TagAssignment.objects.create(tag=tag, content_type=content_type, object_id=party.pk)
        with pytest.raises(IntegrityError), transaction.atomic():
            TagAssignment.objects.create(tag=tag, content_type=content_type, object_id=party.pk)


@pytest.fixture()
def scoped_tags(tags_tables: None) -> SimpleNamespace:
    """Seed one scoped and one shared tag, and two company members."""

    del tags_tables
    with system_context(reason="tags test setup"):
        company_a = Company.objects.create(name="Company A")
        company_b = Company.objects.create(name="Company B")
        scoped_a = Tag.objects.create(name="A-only", company=company_a)
        shared = Tag.objects.create(name="Everyone")

    user_a = create_user("tags-user-a")
    user_b = create_user("tags-user-b")
    _grant_membership(company_a, user_a)
    _grant_membership(company_b, user_b)
    return SimpleNamespace(
        company_a=company_a,
        company_b=company_b,
        scoped_a=scoped_a,
        shared=shared,
        user_a=user_a,
        user_b=user_b,
    )


def _visible(user: Any, tag: Any) -> bool:
    """Return whether ``user`` can read ``tag`` through the scoped queryset."""

    with actor_context(user):
        return Tag.objects.filter(pk=tag.pk).exists()


def test_a_scoped_tag_is_visible_to_its_company_member(scoped_tags: SimpleNamespace) -> None:
    """A member of company A reads company A's scoped tag."""

    assert _visible(scoped_tags.user_a, scoped_tags.scoped_a)


def test_a_scoped_tag_is_invisible_cross_company(scoped_tags: SimpleNamespace) -> None:
    """A member of only company B never reads company A's scoped tag."""

    assert not _visible(scoped_tags.user_b, scoped_tags.scoped_a)


def test_a_shared_tag_is_visible_to_every_actor(scoped_tags: SimpleNamespace) -> None:
    """A null-company tag reads for members of either company."""

    assert _visible(scoped_tags.user_a, scoped_tags.shared)
    assert _visible(scoped_tags.user_b, scoped_tags.shared)


def test_only_the_shared_tag_carries_the_wildcard_reader(scoped_tags: SimpleNamespace) -> None:
    """The wildcard reader is written for the shared tag and not the scoped one."""

    assert _shared_reader_exists(scoped_tags.shared)
    assert not _shared_reader_exists(scoped_tags.scoped_a)


def test_deleting_a_shared_tag_removes_its_wildcard_tuple(tags_tables: None) -> None:
    """The base REBAC delete seam removes a deleted tag's resource relationships."""

    del tags_tables
    with system_context(reason="tags delete relationship cleanup"):
        tag = Tag.objects.create(name="Temporary")
        resource_id = tag.sqid
        assert _shared_reader_exists(tag)
        tag.delete()

    assert not active_relationship_model().objects.filter(
        resource_type="tags/tag",
        resource_id=resource_id,
    ).exists()


def test_flipping_scope_reconciles_the_wildcard_reader(scoped_tags: SimpleNamespace) -> None:
    """save() grants the wildcard when a tag turns shared and revokes it when scoped."""

    with system_context(reason="tags test rescope"):
        scoped_tags.scoped_a.company = None
        scoped_tags.scoped_a.save()
        scoped_tags.shared.company = scoped_tags.company_b
        scoped_tags.shared.save()

    assert _shared_reader_exists(scoped_tags.scoped_a)
    assert not _shared_reader_exists(scoped_tags.shared)
    # And the read scope follows: the now-shared tag reads cross-company.
    assert _visible(scoped_tags.user_b, scoped_tags.scoped_a)
    assert not _visible(scoped_tags.user_a, scoped_tags.shared)


@pytest.fixture()
def party_edge(tags_tables: None) -> SimpleNamespace:
    """Seed a shared tag, an owner user (with a party), and an outsider."""

    del tags_tables
    owner = create_user("tags-party-owner")
    outsider = create_user("tags-outsider")
    with system_context(reason="tags test setup"):
        tag = Tag.objects.create(name="VIP")
    with actor_context(owner):
        party = Party.objects.create(display_name="Delta Co")
    party_address = ("parties/party", public_id_for(Party, party.pk))
    return SimpleNamespace(owner=owner, outsider=outsider, tag=tag, party=party, party_address=party_address)


def test_resolve_target_maps_type_and_public_id_to_the_row(party_edge: SimpleNamespace) -> None:
    """A ``(targetType, targetId)`` pair round-trips to its content type and row."""

    with system_context(reason="tags test resolve"):
        resolved = TagAssignment.objects.resolve_target(*party_edge.party_address)

    assert resolved is not None
    assert resolved.object_id == party_edge.party.pk
    assert resolved.content_type == ContentType.objects.get_for_model(Party)


def test_resolve_target_is_none_for_an_unknown_type(party_edge: SimpleNamespace) -> None:
    """An unknown resource type resolves to ``None`` rather than raising."""

    del party_edge
    assert TagAssignment.objects.resolve_target("nope/nope", "whatever") is None


def test_attach_creates_the_edge_idempotently_under_the_actor(party_edge: SimpleNamespace) -> None:
    """attach() creates the edge once; re-attaching returns the same row."""

    objects = TagAssignment.objects
    with actor_context(party_edge.owner):
        first = objects.attach(*party_edge.party_address, [party_edge.tag.sqid])
        second = objects.attach(*party_edge.party_address, [party_edge.tag.sqid])

    assert [row.pk for row in first] == [row.pk for row in second]
    with system_context(reason="tags test count"):
        assert objects.count() == 1
        # created_by stamps from the ambient actor despite the elevated insert.
        assert objects.get().created_by_id == party_edge.owner.pk


def test_attach_fails_fast_on_a_target_the_actor_cannot_read(party_edge: SimpleNamespace) -> None:
    """The target resolves actor-scoped: an unreadable row is not taggable."""

    with actor_context(party_edge.outsider), pytest.raises(ValueError):
        TagAssignment.objects.attach(*party_edge.party_address, [party_edge.tag.sqid])


def test_attach_fails_fast_on_an_unknown_tag(party_edge: SimpleNamespace) -> None:
    """A missing/unreadable tag id raises instead of silently skipping."""

    with actor_context(party_edge.owner), pytest.raises(ValueError):
        TagAssignment.objects.attach(*party_edge.party_address, ["tag_missing"])


def test_detach_removes_the_edge(party_edge: SimpleNamespace) -> None:
    """detach() deletes the addressed edges and reports the removed count."""

    objects = TagAssignment.objects
    with actor_context(party_edge.owner):
        objects.attach(*party_edge.party_address, [party_edge.tag.sqid])
        removed = objects.detach(*party_edge.party_address, [party_edge.tag.sqid])

    assert removed == 1
    with system_context(reason="tags test count"):
        assert objects.count() == 0


def test_for_target_returns_the_targets_edges(party_edge: SimpleNamespace) -> None:
    """for_target() addresses the edge set of one row; unknown targets are empty."""

    objects = TagAssignment.objects
    with actor_context(party_edge.owner):
        objects.attach(*party_edge.party_address, [party_edge.tag.sqid])
        assert objects.for_target(*party_edge.party_address).count() == 1
        assert objects.for_target("nope/nope", "whatever").count() == 0


def test_attaching_across_mti_levels_shares_one_edge(tags_tables: None) -> None:
    """A tag addressed at an MTI child and at its parent resolve to one canonical edge.

    ``mtidemo``'s gated MTI pair stands in for the ``parties.Person`` IS-A
    ``parties.Party`` shape: ``attach`` canonicalizes both addresses to the topmost
    REBAC-typed ancestor (the parent), so the child and parent never split the edge
    set and a query at either level finds the one edge.
    """

    del tags_tables
    objects = TagAssignment.objects
    with system_context(reason="tags mti test"):
        child = MtiChild.objects.create(title="Acme", detail="org")
        parent = MtiParent.objects.get(pk=child.pk)
        tag = Tag.objects.create(name="VIP")
        child_address = ("mtidemo/child", child.public_id)
        parent_address = ("mtidemo/parent", parent.public_id)
        via_child = objects.attach(*child_address, [tag.sqid])
        via_parent = objects.attach(*parent_address, [tag.sqid])

        # Both addresses converge on one edge keyed to the parent content type.
        assert [row.pk for row in via_child] == [row.pk for row in via_parent]
        assert objects.count() == 1
        edge = objects.get()
        assert edge.content_type == ContentType.objects.get_for_model(MtiParent)
        assert edge.object_id == child.pk
        # Querying at either level finds the same single edge — no split set.
        assert objects.for_target(*child_address).count() == 1
        assert objects.for_target(*parent_address).count() == 1
