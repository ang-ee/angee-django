"""Tests for the tags addon — the polymorphic edge, shared scope, and manager protocol.

The vocabulary is an admin-curated surface, so rows are created under
``system_context``; the scope reads run under ``actor_context`` after
``rebac sync`` loads the schema. Party tagging is exercised against a real
``parties.Party`` row through the polymorphic edge, with no ``parties`` change.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import IntegrityError, connection, models, transaction
from django.test.utils import CaptureQueriesContext
from rebac import (
    actor_context,
    system_context,
)
from rebac.models import active_relationship_model

from angee.base.models import public_id_for
from angee.tags.models import _NEVER_LOADED
from angee.tags.models import Tag as AbstractTag
from angee.tags.models import TagAssignment as AbstractTagAssignment
from angee.tags.models import TagRole as AbstractTagRole
from tests.conftest import _clear_model_tables, _create_missing_tables, create_user
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


class ScopeFlagTag(AbstractTag):
    """Concrete tag whose shared row fact is backed by a local boolean field."""

    shared_marker = models.BooleanField(default=True)
    shared_scope_source_fields = ("shared_marker",)

    class Meta(AbstractTag.Meta):
        """Django model options for the shared-scope fact regression tag."""

        abstract = False
        app_label = "tags"
        db_table = "test_tags_scope_flag_tag"
        rebac_resource_type = "tags/tag"
        rebac_id_attr = "sqid"

    @property
    def is_shared_scope(self) -> bool:
        """Return the row's declared shared-scope fact."""

        return bool(self.shared_marker)


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


TAGS_TEST_MODELS = (Tag, ScopeFlagTag, TagAssignment, Party)
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


def test_base_tag_is_always_shared_and_declares_no_scope_source_fields(tags_tables: None) -> None:
    """The framework tag is shared vocabulary; consumers may extend scope later."""

    del tags_tables

    tag_relation_fields = {
        field.name
        for field in Tag._meta.fields
        if field.is_relation and field.name not in {"created_by", "updated_by"}
    }
    assert Tag.shared_scope_source_fields == ()
    assert tag_relation_fields == set()
    assert Tag(name="Framework").is_shared_scope is True


def test_shared_scope_fact_controls_the_wildcard_reader(tags_tables: None) -> None:
    """The wildcard reader reconciles from ``is_shared_scope``, not a hardcoded FK."""

    del tags_tables
    with system_context(reason="tags test scope fact"):
        shared = ScopeFlagTag.objects.create(name="Everyone", shared_marker=True)
        scoped = ScopeFlagTag.objects.create(name="Local", shared_marker=False)

    assert _shared_reader_exists(shared)
    assert not _shared_reader_exists(scoped)


def test_deleting_a_shared_tag_removes_its_wildcard_tuple(tags_tables: None) -> None:
    """The base REBAC delete seam removes a deleted tag's resource relationships."""

    del tags_tables
    with system_context(reason="tags delete relationship cleanup"):
        tag = ScopeFlagTag.objects.create(name="Temporary")
        resource_id = tag.sqid
        assert _shared_reader_exists(tag)
        tag.delete()

    assert not active_relationship_model().objects.filter(
        resource_type="tags/tag",
        resource_id=resource_id,
    ).exists()


def test_flipping_shared_scope_reconciles_the_wildcard_reader(tags_tables: None) -> None:
    """A same-instance double flip grants and revokes the wildcard every time."""

    del tags_tables
    with system_context(reason="tags test rescope"):
        created = ScopeFlagTag.objects.create(name="Local", shared_marker=False)
        tag = ScopeFlagTag.objects.get(pk=created.pk)
        tag.shared_marker = True
        tag.save()
        assert _shared_reader_exists(tag)

        tag.shared_marker = False
        tag.save()
        assert not _shared_reader_exists(tag)

        tag.shared_marker = True
        tag.save()
        assert _shared_reader_exists(tag)

        tag.shared_marker = False
        tag.save()
        assert not _shared_reader_exists(tag)


def test_shared_scope_fact_controls_actor_scoped_reads(tags_tables: None) -> None:
    """Actor-scoped reads allow shared rows and deny marker-off rows."""

    del tags_tables
    reader = create_user("tags-scope-reader")
    with system_context(reason="tags test actor scope setup"):
        shared = ScopeFlagTag.objects.create(name="Everyone", shared_marker=True)
        scoped = ScopeFlagTag.objects.create(name="Local", shared_marker=False)

    with actor_context(reader):
        assert ScopeFlagTag.objects.filter(pk=shared.pk).exists()
        assert not ScopeFlagTag.objects.filter(pk=scoped.pk).exists()


def test_deferred_scope_source_field_defers_snapshot(tags_tables: None) -> None:
    """A deferred scope source does not evaluate ``is_shared_scope`` in ``from_db``."""

    del tags_tables
    with system_context(reason="tags test deferred scope setup"):
        tag = ScopeFlagTag.objects.create(name="Deferred", shared_marker=False)
        with CaptureQueriesContext(connection) as ctx:
            loaded = ScopeFlagTag.objects.defer("shared_marker").get(pk=tag.pk)

    assert len(ctx.captured_queries) == 1
    assert loaded.get_deferred_fields() == {"shared_marker"}
    assert getattr(loaded, "_loaded_is_shared_scope") is _NEVER_LOADED


def test_deferred_scope_source_field_save_resyncs_idempotently(tags_tables: None) -> None:
    """A row loaded without its scope source falls back to an idempotent resync."""

    del tags_tables
    with system_context(reason="tags test deferred scope save"):
        tag = ScopeFlagTag.objects.create(name="Deferred", shared_marker=True)
        loaded = ScopeFlagTag.objects.defer("shared_marker").get(pk=tag.pk)
        loaded.name = "Deferred renamed"
        loaded.save()

    assert _shared_reader_exists(tag)


def test_loaded_scope_snapshot_skips_unrelated_wildcard_rewrites(tags_tables: None) -> None:
    """An unrelated save with a complete snapshot does not rewrite wildcard rows."""

    del tags_tables
    with system_context(reason="tags test complete scope snapshot"):
        tag = ScopeFlagTag.objects.create(name="Stable", shared_marker=True)
        loaded = ScopeFlagTag.objects.get(pk=tag.pk)
        loaded.name = "Still stable"
        with CaptureQueriesContext(connection) as ctx:
            loaded.save()

    tuple_writes = [
        query
        for query in ctx.captured_queries
        if "rebac" in query["sql"].lower() and ("relationship" in query["sql"].lower())
    ]
    assert tuple_writes == []


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
