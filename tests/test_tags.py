"""Tests for the tags addon — the shared vocabulary and the record-authorized edge.

The vocabulary is an admin-curated surface, so tag rows are created under
``system_context``; the scope reads run under ``actor_context`` after
``rebac sync`` loads the schema. A tag assignment is authorized by the record it
tags: each taggable type's app declares its relation on ``tags/tag_assignment``
from its ``permissions.extends.zed``, which ``composed_permissions`` merges.
``parties.Party`` stands in for a taggable record and ``knowledge.Vault`` for a
type no relation names.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import IntegrityError, connection, models, transaction
from django.test.utils import CaptureQueriesContext, isolate_apps
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model
from rebac.roles import grant as grant_role

from angee.base.identity import public_id_for
from angee.messaging.testing.models import Handle, Message, Party, Person, Thread
from angee.projects.testing.models import Project, Task
from angee.tags import schema as tags_schema
from angee.tags.models import TaggedModel
from angee.tags.testing.models import Tag, TagAssignment
from tests.conftest import (
    File,
    Page,
    addon_schema,
    create_platform_admin,
    create_user,
    execute_schema,
    result_data,
    vault_for,
)
from tests.mtidemo.models import MtiParent


def _shared_reader_exists(tag: Any) -> bool:
    """Return whether ``tag`` carries the ``shared@auth/user:*`` wildcard reader."""

    relationship_model = active_relationship_model()
    return relationship_model.objects.filter(
        resource_type="tags/tag",
        resource_id=str(tag.pk),
        relation="shared",
        subject_type="auth/user",
        subject_id="*",
    ).exists()


def test_tag_a_party_through_a_generic_assignment(composed_tables: None) -> None:
    """The polymorphic edge resolves back to the exact party and tag."""

    del composed_tables
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


def test_a_partys_tags_resolve_by_content_type_and_object_id(composed_tables: None) -> None:
    """The reverse query (content_type, object_id) returns the party's tags."""

    del composed_tables
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


def test_the_same_tag_attaches_once_per_target(composed_tables: None) -> None:
    """``unique(tag, content_type, object_id)`` rejects a duplicate edge."""

    del composed_tables
    with system_context(reason="tags test setup"):
        party = Party.objects.create(display_name="Gamma Inc")
        tag = Tag.objects.create(name="Partner")
        content_type = ContentType.objects.get_for_model(Party)
        TagAssignment.objects.create(tag=tag, content_type=content_type, object_id=party.pk)
        with pytest.raises(IntegrityError), transaction.atomic():
            TagAssignment.objects.create(tag=tag, content_type=content_type, object_id=party.pk)


def _assert_authenticated_reads(tag: Any) -> None:
    with actor_context(SubjectRef.of("auth/user", "1")):
        assert type(tag).objects.filter(pk=tag.pk).exists()
    with actor_context(AnonymousUser()):
        assert not type(tag).objects.filter(pk=tag.pk).exists()
    assert not _shared_reader_exists(tag)


def test_base_tag_is_always_shared_and_declares_no_policy_fields(composed_tables: None) -> None:
    """The vocabulary uses authenticated membership without a row-level policy."""

    del composed_tables
    tag_relation_fields = {
        field.name
        for field in Tag._meta.fields
        if field.is_relation and field.name not in {"created_by", "updated_by"}
    }
    assert tag_relation_fields == set()
    with system_context(reason="tags vocabulary setup"):
        tag = Tag.objects.create(name="Framework")
    _assert_authenticated_reads(tag)


def test_consumer_marker_does_not_control_authenticated_reads(composed_tables: None) -> None:
    """An ordinary consumer marker does not restrict shared vocabulary."""

    del composed_tables
    with system_context(reason="tags marker setup"):
        shared = Tag.objects.create(name="Everyone", shared_marker=True)
        scoped = Tag.objects.create(name="Local", shared_marker=False)
    _assert_authenticated_reads(shared)
    _assert_authenticated_reads(scoped)


def test_deleting_a_tag_removes_the_row_without_wildcard_tuples(composed_tables: None) -> None:
    """Shared vocabulary has no per-row grant to reconcile on deletion."""

    del composed_tables
    with system_context(reason="tags delete setup"):
        tag = Tag.objects.create(name="Temporary")
    _assert_authenticated_reads(tag)
    resource_id = str(tag.pk)
    with system_context(reason="tags delete"):
        tag.delete()
    with actor_context(SubjectRef.of("auth/user", "1")):
        assert not Tag.objects.filter(pk=resource_id).exists()
    assert not active_relationship_model().objects.filter(
        resource_type="tags/tag", resource_id=resource_id,
    ).exists()


def test_flipping_consumer_marker_keeps_authenticated_reads(composed_tables: None) -> None:
    """Partial and full saves leave authenticated visibility unchanged."""

    del composed_tables
    with system_context(reason="tags marker setup"):
        tag = Tag.objects.create(name="Local", shared_marker=False)
    for update_fields in ({"shared_marker"}, None):
        for marker in (True, False):
            with system_context(reason="tags marker update"):
                tag.shared_marker = marker
                tag.save(update_fields=update_fields)
            _assert_authenticated_reads(tag)


def test_authenticated_reads_ignore_consumer_marker(composed_tables: None) -> None:
    """A signed-in user reads both marker states; anonymous reads neither."""

    del composed_tables
    reader = create_user("tags-scope-reader")
    with system_context(reason="tags test actor scope setup"):
        shared = Tag.objects.create(name="Everyone", shared_marker=True)
        scoped = Tag.objects.create(name="Local", shared_marker=False)
    with actor_context(reader):
        assert set(Tag.objects.values_list("pk", flat=True)) == {shared.pk, scoped.pk}
    with actor_context(AnonymousUser()):
        assert not Tag.objects.exists()


def test_deferred_marker_stays_deferred_on_load(composed_tables: None) -> None:
    """Loading shared vocabulary preserves Django's deferred-field behavior."""

    del composed_tables
    with system_context(reason="tags test deferred marker setup"):
        tag = Tag.objects.create(name="Deferred", shared_marker=False)
        with CaptureQueriesContext(connection) as ctx:
            loaded = Tag.objects.defer("shared_marker").get(pk=tag.pk)
    assert len(ctx.captured_queries) == 1
    assert loaded.get_deferred_fields() == {"shared_marker"}


def test_deferred_marker_save_preserves_authenticated_reads(composed_tables: None) -> None:
    """Saving a deferred row needs no grant synchronization."""

    del composed_tables
    with system_context(reason="tags test deferred marker save"):
        tag = Tag.objects.create(name="Deferred", shared_marker=True)
        loaded = Tag.objects.defer("shared_marker").get(pk=tag.pk)
        loaded.name = "Deferred renamed"
        loaded.save()
    _assert_authenticated_reads(tag)


def test_unrelated_save_keeps_authenticated_reads_without_tuples(composed_tables: None) -> None:
    """A full save does not materialize authenticated membership."""

    del composed_tables
    with system_context(reason="tags test complete save"):
        tag = Tag.objects.create(name="Stable", shared_marker=True)
        loaded = Tag.objects.get(pk=tag.pk)
        loaded.name = "Still stable"
        loaded.save()
    _assert_authenticated_reads(tag)


def test_targeted_content_save_needs_no_reader_reconciliation(composed_tables: None) -> None:
    """A targeted save avoids tuple reads and writes."""

    del composed_tables
    with system_context(reason="tags test targeted content save"):
        tag = Tag.objects.create(name="Stable", shared_marker=True)
        tag.name = "Renamed"
        with CaptureQueriesContext(connection) as queries:
            tag.save(update_fields={"name"})
    _assert_authenticated_reads(tag)
    assert not [
        query for query in queries.captured_queries
        if "rebac" in query["sql"].lower() and "relationship" in query["sql"].lower()
    ]


def test_marker_bulk_writes_need_no_reader_reconciliation(composed_tables: None) -> None:
    """Bulk edits and inserts preserve authenticated reads without wildcard grants."""

    del composed_tables
    with system_context(reason="tags bulk marker writes"):
        tag = Tag.objects.create(name="Stable", shared_marker=True)
        assert Tag.objects.filter(pk=tag.pk).update(shared_marker=False) == 1
        tag.shared_marker = True
        assert Tag.objects.bulk_update([tag], ["shared_marker"]) == 1
        [bulk] = Tag.objects.bulk_create([Tag(name="Bulk")])
        assert Tag.objects.filter(pk=tag.pk).update(name="Renamed") == 1
        tag.refresh_from_db()
        assert tag.shared_marker is True
        assert tag.name == "Renamed"
        tag.name = "Bulk renamed"
        assert Tag.objects.bulk_update([tag], ["name"]) == 1
        tag.refresh_from_db()
        assert tag.name == "Bulk renamed"
    _assert_authenticated_reads(tag)
    _assert_authenticated_reads(bulk)


@pytest.fixture()
def party_edge(composed_permissions: None) -> SimpleNamespace:
    """Seed a shared tag, a party its owner writes, and an outsider, with every taggable type declared."""

    del composed_permissions
    owner = create_user("tags-party-owner")
    outsider = create_user("tags-outsider")
    with system_context(reason="tags test setup"):
        tag = Tag.objects.create(name="VIP")
    with actor_context(owner):
        party = Party.objects.create(display_name="Delta Co")
    party_address = ("parties/party", public_id_for(Party, party.pk))
    return SimpleNamespace(owner=owner, outsider=outsider, tag=tag, party=party, party_address=party_address)


def _grant(record: Any, relation: str, user: Any) -> None:
    with system_context(reason="tags test grant"):
        write_relationships([RelationshipTuple(to_object_ref(record), relation, to_subject_ref(user))])


def _tags_admin(username: str) -> Any:
    """A tag curator: an effective member of ``tags/role:tags_admin`` and nothing else."""

    user = create_user(username)
    with system_context(reason="tags test curator"):
        grant_role(actor=user, role="tags/role:tags_admin")
    return user


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
    """attach() creates the edge once as the record's writer; re-attaching returns the same row."""

    objects = TagAssignment.objects
    with actor_context(party_edge.owner):
        first = objects.attach(*party_edge.party_address, [party_edge.tag.sqid])
        second = objects.attach(*party_edge.party_address, [party_edge.tag.sqid])

    assert [row.pk for row in first] == [row.pk for row in second]
    with system_context(reason="tags test count"):
        assert objects.count() == 1
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
        # The row-level address resolves the same edge set as the public one.
        assert list(objects.for_record(party_edge.party)) == list(objects.for_target(*party_edge.party_address))
        assert objects.get().has_access("read")
    with actor_context(AnonymousUser()):
        assert not objects.exists()


def test_a_tag_reader_who_cannot_read_the_record_sees_none_of_its_edges(party_edge: SimpleNamespace) -> None:
    """Listing a tag's records drops the records the actor cannot read."""

    with actor_context(party_edge.outsider):
        own = Party.objects.create(display_name="Outsider Co")
    with system_context(reason="tags test edges"):
        TagAssignment.objects.attach(*party_edge.party_address, [party_edge.tag.sqid])
        TagAssignment.objects.attach("parties/party", public_id_for(Party, own.pk), [party_edge.tag.sqid])

    with actor_context(party_edge.outsider):
        assert Tag.objects.filter(pk=party_edge.tag.pk).exists()
        assert not TagAssignment.objects.for_record(party_edge.party).exists()
        assert [edge.object_id for edge in party_edge.tag.assignments.all()] == [own.pk]
    with actor_context(party_edge.owner):
        assert [edge.object_id for edge in party_edge.tag.assignments.all()] == [party_edge.party.pk]


@pytest.fixture()
def curated_vocabulary(party_edge: SimpleNamespace) -> SimpleNamespace:
    """Narrow tag reads to tag administrators, so a record's reader can lack its tag's read."""

    schema = Path(apps.get_app_config("tags").rebac_schema)
    source = schema.read_text(encoding="utf-8")
    shared = "permission read = ((authenticated + admin->member) + manager->effective_member)"
    assert source.count(shared) == 1
    schema.write_text(source.replace(shared, "permission read = (admin->member + manager->effective_member)"))
    call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)
    return party_edge


def test_an_edge_is_read_only_by_readers_of_both_its_tag_and_its_record(
    curated_vocabulary: SimpleNamespace,
) -> None:
    """A record reader without the tag and a tag reader without the record each see nothing."""

    edge = curated_vocabulary
    curator = _tags_admin("tags-curator")
    with system_context(reason="tags test edge"):
        TagAssignment.objects.attach(*edge.party_address, [edge.tag.sqid])

    with actor_context(edge.owner):
        assert Party.objects.filter(pk=edge.party.pk).exists()
        assert not Tag.objects.filter(pk=edge.tag.pk).exists()
        assert not TagAssignment.objects.exists()
    with actor_context(curator):
        assert Tag.objects.filter(pk=edge.tag.pk).exists()
        assert not Party.objects.filter(pk=edge.party.pk).exists()
        assert not TagAssignment.objects.exists()

    _grant(edge.party, "reader", curator)
    with actor_context(curator):
        assert [row.tag_id for row in TagAssignment.objects.for_record(edge.party)] == [edge.tag.pk]


_TAG = """mutation Tag($type: String!, $id: ID!, $tags: [ID!]!) {
  tag(target_type: $type, target_id: $id, tag_ids: $tags) { tag { name } target_type target_id }
}"""
_UNTAG = """mutation Untag($type: String!, $id: ID!, $tags: [ID!]!) {
  untag(target_type: $type, target_id: $id, tag_ids: $tags)
}"""


def _verb_variables(edge: SimpleNamespace) -> dict[str, Any]:
    target_type, target_id = edge.party_address
    return {"type": target_type, "id": target_id, "tags": [edge.tag.sqid]}


def test_a_tags_admin_who_cannot_write_the_record_cannot_tag_or_untag_it(party_edge: SimpleNamespace) -> None:
    """Curating the vocabulary grants no write on records: both verbs refuse a reader of the record."""

    curator = _tags_admin("tags-verb-curator")
    _grant(party_edge.party, "reader", curator)
    schema = addon_schema(tags_schema.schemas, "console")

    assert execute_schema(schema, _TAG, _verb_variables(party_edge), user=curator).errors
    with system_context(reason="tags test verbs"):
        assert not TagAssignment.objects.exists()
        TagAssignment.objects.attach(*party_edge.party_address, [party_edge.tag.sqid])
    assert execute_schema(schema, _UNTAG, _verb_variables(party_edge), user=curator).errors
    with system_context(reason="tags test verbs"):
        assert TagAssignment.objects.count() == 1


def test_a_record_writer_tags_and_untags_through_the_verbs_without_the_tags_admin_role(
    party_edge: SimpleNamespace,
) -> None:
    """The record's writer adds and removes tags; no vocabulary role is involved."""

    schema = addon_schema(tags_schema.schemas, "console")
    tagged = result_data(execute_schema(schema, _TAG, _verb_variables(party_edge), user=party_edge.owner))
    target_type, target_id = party_edge.party_address
    assert tagged["tag"] == [{"tag": {"name": "VIP"}, "target_type": target_type, "target_id": target_id}]
    with actor_context(party_edge.owner):
        assert TagAssignment.objects.for_record(party_edge.party).count() == 1

    untagged = result_data(execute_schema(schema, _UNTAG, _verb_variables(party_edge), user=party_edge.owner))
    assert untagged == {"untag": True}
    with system_context(reason="tags test verbs"):
        assert not TagAssignment.objects.exists()


def test_a_record_writer_saves_tags_with_the_record_and_a_reader_cannot(party_edge: SimpleNamespace) -> None:
    """The form path: the record's save sets its tags under the same actor's write."""

    with actor_context(party_edge.owner):
        party_edge.party.apply_input_extensions(tags=[party_edge.tag.sqid])
        assert [edge.tag_id for edge in TagAssignment.objects.for_record(party_edge.party)] == [party_edge.tag.pk]

    _grant(party_edge.party, "reader", party_edge.outsider)
    with actor_context(party_edge.outsider), pytest.raises(PermissionDenied):
        party_edge.party.apply_input_extensions(tags=[])
    with actor_context(party_edge.owner):
        party_edge.party.apply_input_extensions(tags=[])
        assert not TagAssignment.objects.for_record(party_edge.party).exists()


def test_deleting_a_tagged_record_removes_its_edges_under_its_owner(party_edge: SimpleNamespace) -> None:
    """The record's delete cascades its edges through ``TaggedModel``'s generic relation."""

    with actor_context(party_edge.owner):
        TagAssignment.objects.attach(*party_edge.party_address, [party_edge.tag.sqid])
        Party.objects.get(pk=party_edge.party.pk).delete()
    with system_context(reason="tags test count"):
        assert not TagAssignment.objects.exists()


def test_a_type_no_relation_names_cannot_be_tagged(party_edge: SimpleNamespace) -> None:
    """A vault is not taggable: neither its owner nor a platform admin can tag it."""

    vault = vault_for(party_edge.owner)
    admin = create_platform_admin("tags-untaggable-admin")
    for actor in (party_edge.owner, admin):
        with actor_context(actor), pytest.raises(PermissionDenied):
            TagAssignment.objects.attach("knowledge/vault", vault.sqid, [party_edge.tag.sqid])
    with system_context(reason="tags test count"):
        assert not TagAssignment.objects.exists()


def test_a_handle_carries_its_own_tags_under_its_writer(party_edge: SimpleNamespace) -> None:
    """A handle's writer tags it apart from its party; only its readers see the edge."""

    with system_context(reason="tags test handle"):
        handle = Handle.objects.create(
            platform="email", value="billing@example.test", party=party_edge.party, created_by=party_edge.owner,
        )
    handle_address = ("parties/handle", public_id_for(Handle, handle.pk))
    with actor_context(party_edge.outsider), pytest.raises(ValueError):
        TagAssignment.objects.attach(*handle_address, [party_edge.tag.sqid])
    with actor_context(party_edge.owner):
        TagAssignment.objects.attach(*handle_address, [party_edge.tag.sqid])
        assert [edge.tag_id for edge in TagAssignment.objects.for_record(handle)] == [party_edge.tag.pk]
        assert not TagAssignment.objects.for_record(party_edge.party).exists()
    with actor_context(party_edge.outsider):
        assert not TagAssignment.objects.exists()


def test_attaching_across_mti_levels_shares_one_edge(party_edge: SimpleNamespace) -> None:
    """A tag addressed at a person and at its party resolves to one edge on the party.

    ``attach`` stores the topmost REBAC-typed ancestor (:func:`rebac.generic_target`),
    so a ``parties/person`` and a ``parties/party`` address never split the edge set
    and a query at either level finds the one edge.
    """

    objects = TagAssignment.objects
    with actor_context(party_edge.owner):
        person = Person.objects.create(display_name="Ada")
        person_address = ("parties/person", public_id_for(Person, person.pk))
        party_address = ("parties/party", public_id_for(Party, person.pk))
        via_person = objects.attach(*person_address, [party_edge.tag.sqid])
        via_party = objects.attach(*party_address, [party_edge.tag.sqid])

        assert [row.pk for row in via_person] == [row.pk for row in via_party]
        assert objects.count() == 1
        edge = objects.get()
        assert edge.content_type == ContentType.objects.get_for_model(Party)
        assert edge.object_id == person.pk
        assert objects.for_target(*person_address).count() == 1
        assert objects.for_target(*party_address).count() == 1


def _taggable_test_models() -> set[type[Any]]:
    return {File, Handle, Message, Page, Party, Project, Task, Thread}


def _tag_errors(errors: list[Any]) -> list[Any]:
    """The check's findings about the framework's taggable models and the edge's relations.

    Other test modules register concrete fixtures of taggable sources (a task
    under a demo type) that declare no relation; the check rightly reports them,
    so these tests read only the findings about the models they assert on.
    """

    owned = _taggable_test_models()
    return [
        error for error in errors
        if error.id.startswith("tags.") and (error.obj in owned or error.obj is TagAssignment)
    ]


def test_the_check_accepts_matching_taggable_models_and_relations(composed_permissions: None) -> None:
    """Every model composing TaggedModel has its relation, and every relation its model."""

    del composed_permissions
    assert set(TagAssignment.declared_target_models().values()) == _taggable_test_models()
    assert _tag_errors(TagAssignment.check()) == []


def test_the_check_reports_a_taggable_model_without_a_relation() -> None:
    """Without their owners' fragments, the base schema names no taggable type."""

    errors = _tag_errors(TagAssignment.check())
    assert {error.id for error in errors} == {"tags.E002"}
    assert {error.obj for error in errors} == _taggable_test_models()


def test_the_check_reports_a_relation_without_a_taggable_model(composed_permissions: None) -> None:
    """A relation on a type that does not compose TaggedModel fails the check."""

    del composed_permissions
    schema = Path(apps.get_app_config("tags").rebac_schema)
    source = schema.read_text(encoding="utf-8")
    edge = "relation tag: tags/tag // rebac:field=tag\n"
    assert source.count(edge) == 1
    schema.write_text(source.replace(edge, edge + "    relation stray: mtidemo/parent // rebac:field=target\n"))

    [error] = _tag_errors(TagAssignment.check())
    assert (error.id, error.obj) == ("tags.E003", TagAssignment)
    assert "mtidemo/parent" in error.msg and MtiParent._meta.label in error.msg


def test_the_check_reports_tagged_model_placement(monkeypatch: pytest.MonkeyPatch) -> None:
    """TaggedModel belongs on the canonical model: not on an MTI child, nor on an untyped model."""

    with isolate_apps():

        class MisplacedChild(TaggedModel, MtiParent):
            """An MTI child taking tags its untagged canonical parent would key."""

            class Meta:
                """Django model options for the isolated child."""

                app_label = "mtidemo"

        class Untyped(TaggedModel, models.Model):
            """A model no REBAC type names, whose rows no edge can store."""

            class Meta:
                """Django model options for the isolated untyped model."""

                app_label = "mtidemo"

    strays = (MisplacedChild, Untyped)
    installed = apps.get_models
    monkeypatch.setattr(apps, "get_models", lambda *args, **kwargs: [*installed(*args, **kwargs), *strays])
    placement = [error for error in TagAssignment.check() if error.id == "tags.E001" and error.obj in strays]

    assert [error.obj for error in placement] == [MisplacedChild, Untyped]
    assert f"tagged as {MtiParent._meta.label}" in placement[0].msg
    assert "no REBAC type" in placement[1].msg
