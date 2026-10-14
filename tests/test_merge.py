"""Record merge: the shared template, its declared reference policies and the shared verb.

Tags exercise shared reference movement and retained identity: assignments
follow the survivor, collisions keep one live edge, and history keeps aliases.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import DO_NOTHING
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rebac import PermissionDenied, RelationshipTuple, actor_context, system_context, to_object_ref, write_relationships
from rebac.actors import to_subject_ref
from rebac.roles import grant as grant_role

from angee.base import merge as merge_module
from angee.base.changesets import changeset
from angee.base.identity import public_id_of
from angee.base.merge import EdgeReference, ForeignKeyReference, fold_records, references
from angee.base.mixins import MergeContractMixin, SourceKind
from angee.base.refs import MergePolicy, RecordRefMixin
from angee.graphql import records
from angee.messaging.testing.models import Directory, Participant, Party, Reaction
from angee.projects.testing.models import Link, Project, Queue, Task
from angee.resources.testing.models import Resource
from angee.spaces.testing.models import Group
from angee.tags import schema as tags_schema
from angee.tags.testing.models import Tag, TagAssignment
from angee.workflows.testing.models import StepWatch, Workflow, WorkflowRun
from tests.conftest import addon_schema, create_user, execute_schema, make_integration, result_data
from tests.mtidemo.models import MtiChild, MtiParent
from tests.recordrefdemo.models import CustomColumnEdge, SelfLinkedNode, TagSlot
from tests.scopedemo.models import ProjectionDoc
from tests.workflow_steps import document

pytestmark = pytest.mark.usefixtures("composed_permissions")

_MERGE = """mutation Merge($type: String!, $id: ID!, $records: [ID!]!) {
  merge_records(target_type: $type, target_id: $id, records: $records, confirm: true) {
    ok message validation_errors
  }
}"""


def _curator(username: str) -> Any:
    """A tag curator: an effective member of ``tags/role:tags_admin``."""

    user = create_user(username)
    with system_context(reason="merge test curator"):
        grant_role(actor=user, role="tags/role:tags_admin")
    return user


def _vocabulary(*, owner: Any = None) -> tuple[Any, Any, Any, Any]:
    """Two tags for one meaning: Ada carries both, Bob only the one to merge away."""

    with system_context(reason="merge test vocabulary"):
        mobile = Tag.objects.create(name="Mobile")
        mobil = Tag.objects.create(name="Mobil")
        ada = Party.objects.create(display_name="Ada", created_by=owner)
        bob = Party.objects.create(display_name="Bob", created_by=owner)
        party_type = ContentType.objects.get_for_model(Party)
        for tag, party in ((mobile, ada), (mobil, ada), (mobil, bob)):
            TagAssignment.objects.create(tag=tag, content_type=party_type, object_id=party.pk)
    return mobile, mobil, ada, bob


def _tagged_parties(tag: Any) -> list[Any]:
    with system_context(reason="merge test read"):
        return sorted(TagAssignment._base_manager.filter(tag=tag).values_list("object_id", flat=True))


def _tag_on_tag(tag: Any, target: Any) -> Any:
    """An edge row pointing at a tag, to stand in for any edge that references a merged record."""

    with system_context(reason="merge test edge on a tag"):
        return TagAssignment.objects.create(
            tag=tag, content_type=ContentType.objects.get_for_model(Tag), object_id=target.pk
        )


def test_merging_a_tag_moves_its_assignments_and_collapses_a_duplicate() -> None:
    """Ada keeps one Mobile, Bob gains it, and Mobil remains a historical alias."""

    mobile, mobil, ada, bob = _vocabulary()
    with actor_context(_curator("merge-curator")):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])

    with system_context(reason="merge test read"):
        assert Tag._base_manager.get(pk=mobil.pk).merged_into_id == mobile.pk
        assert TagAssignment.history.filter(tag_id=mobil.pk).exists()
        assert not Tag.objects.canonical().filter(pk=mobil.pk).exists()
        assert TagAssignment._base_manager.filter(tag__in=(mobile, mobil)).count() == 2
    assert _tagged_parties(mobile) == sorted([ada.pk, bob.pk])


def test_several_records_merge_into_one_survivor() -> None:
    mobile, mobil, ada, bob = _vocabulary()
    with system_context(reason="merge test third tag"):
        cell = Tag.objects.create(name="Cell")
    with actor_context(_curator("merge-several")):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil), public_id_of(cell)])

    with system_context(reason="merge test read"):
        assert Tag._base_manager.get(pk=mobil.pk).merged_into_id == mobile.pk
        assert not Tag._base_manager.filter(pk=cell.pk).exists()
    assert _tagged_parties(mobile) == sorted([ada.pk, bob.pk])


def test_a_retained_alias_protects_its_survivor_from_deletion() -> None:
    mobile, mobil, ada, bob = _vocabulary()
    with actor_context(_curator("merge-protected")):
        survivor = Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])
        with pytest.raises(ProtectedError):
            survivor.delete()
        assert Tag.objects.get(pk=mobil.pk).canonical().pk == mobile.pk
    assert _tagged_parties(mobile) == sorted([ada.pk, bob.pk])


def test_reverting_a_merge_restores_exact_assignment_identities_and_clears_the_alias() -> None:
    curator = _curator("merge-revert")
    mobile, mobil, _ada, _bob = _vocabulary(owner=curator)
    before = set(TagAssignment._base_manager.values_list("pk", "tag_id", "content_type_id", "object_id"))
    with actor_context(curator):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])
        boundary = Tag.history.filter(id=mobil.pk).order_by("-history_id").first().changeset
        changed_assignments = set(
            TagAssignment.history.filter(changeset=boundary).values_list("id", flat=True)
        )
        assert changed_assignments == {pk for pk, tag, _kind, _record in before if tag == mobil.pk}
        reverted = boundary.with_actor(curator).revert()
    assert reverted.activity == "revert"
    assert Tag._base_manager.get(pk=mobil.pk).merged_into_id is None
    assert set(TagAssignment._base_manager.values_list("pk", "tag_id", "content_type_id", "object_id")) == before


def test_a_second_merge_flattens_aliases_and_revert_restores_the_previous_survivor() -> None:
    curator = _curator("merge-flatten")
    mobile, mobil, ada, bob = _vocabulary(owner=curator)
    with system_context(reason="merge test next survivor"):
        phones = Tag.objects.create(name="Phones")
    with actor_context(curator):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])
        Tag.objects.get(pk=phones.pk).merge(records=[public_id_of(mobile)])
        boundary = Tag.history.filter(id=mobile.pk).order_by("-history_id").first().changeset
        assert Tag._base_manager.get(pk=mobil.pk).merged_into_id == phones.pk
        assert Tag.history.filter(id=mobil.pk, changeset=boundary, merged_into_id=phones.pk).exists()
        assert Tag.objects.get(pk=mobil.pk).canonical().pk == phones.pk
        boundary.with_actor(curator).revert()
        assert Tag.objects.get(pk=mobil.pk).canonical().pk == mobile.pk
    assert Tag._base_manager.get(pk=mobil.pk).merged_into_id == mobile.pk
    assert Tag._base_manager.get(pk=mobile.pk).merged_into_id is None
    assert _tagged_parties(mobile) == sorted([ada.pk, bob.pk])
    assert _tagged_parties(phones) == []


def test_a_later_survivor_write_refuses_merge_revert_before_edges_move() -> None:
    curator = _curator("merge-revert-later-write")
    mobile, mobil, ada, bob = _vocabulary(owner=curator)
    with actor_context(curator):
        survivor = Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])
        boundary = Tag.history.filter(id=mobil.pk).order_by("-history_id").first().changeset
        survivor.name = "Mobile phones"
        survivor.save(update_fields=("name",))
        with pytest.raises(ValidationError, match="later write prevents revert"):
            boundary.with_actor(curator).revert()
    assert Tag._base_manager.get(pk=mobil.pk).merged_into_id == mobile.pk
    assert _tagged_parties(mobile) == sorted([ada.pk, bob.pk])


def test_merging_needs_write_on_the_survivor() -> None:
    """Someone who may not curate tags merges nothing."""

    mobile, mobil, _ada, _bob = _vocabulary()
    outsider = create_user("merge-outsider")
    with actor_context(outsider), pytest.raises(PermissionDenied, match="kept"):
        Tag._base_manager.get(pk=mobile.pk).with_actor(outsider).merge(records=[public_id_of(mobil)])

    with system_context(reason="merge test read"):
        assert Tag._base_manager.filter(pk__in=(mobile.pk, mobil.pk)).count() == 2


def test_merging_needs_delete_on_each_merged_record(monkeypatch: pytest.MonkeyPatch) -> None:
    mobile, mobil, _ada, _bob = _vocabulary()
    allowed = Tag.has_access
    monkeypatch.setattr(
        Tag, "has_access", lambda row, action, **kwargs: action != "delete" and allowed(row, action, **kwargs)
    )
    with actor_context(_curator("merge-no-delete")), pytest.raises(PermissionDenied, match="every record"):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])
    assert _tagged_parties(mobil) != []


def test_a_merge_refuses_itself_too_many_and_records_that_are_gone() -> None:
    mobile, _mobil, _ada, _bob = _vocabulary()
    with actor_context(_curator("merge-refusals")):
        survivor = Tag.objects.get(pk=mobile.pk)
        with pytest.raises(ValidationError, match="into itself"):
            survivor.merge(records=[public_id_of(mobile)])
        with pytest.raises(ValidationError, match="no longer exist"):
            survivor.merge(records=["tag_missing"])
        with pytest.raises(ValidationError, match="Choose the records"):
            survivor.merge(records=[])
        with pytest.raises(ValidationError, match="at most 100"):
            survivor.merge(records=["tag_x"] * 101)


def test_a_survivor_referring_to_a_merged_record_refuses_before_anything_moves() -> None:
    """Moving the survivor's own reference would point it at itself, so the fold refuses and nothing moves."""

    with system_context(reason="merge test self reference"):
        merged = ProjectionDoc.objects.create(title="merged")
        survivor = ProjectionDoc.objects.create(title="survivor", parent=merged)
        child = ProjectionDoc.objects.create(title="child", parent=merged)
    retired: list[Any] = []
    with pytest.raises(ValidationError, match="refers to"):
        fold_records(survivor, [merged], carry=lambda: None, retire=retired.append)
    assert retired == []
    with system_context(reason="merge test read"):
        assert set(ProjectionDoc._base_manager.filter(parent=merged).values_list("pk", flat=True)) == {
            survivor.pk,
            child.pk,
        }


def test_a_record_deleted_while_the_merge_waits_reads_as_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    mobile, mobil, _ada, _bob = _vocabulary()
    resolve = merge_module.instances_from_public_ids

    def resolve_then_lose(*args: Any, **kwargs: Any) -> Any:
        found = resolve(*args, **kwargs)
        with system_context(reason="merge test race"):
            Tag._base_manager.filter(pk=mobil.pk).delete()
        return found

    monkeypatch.setattr(merge_module, "instances_from_public_ids", resolve_then_lose)
    with actor_context(_curator("merge-race")), pytest.raises(ValidationError, match="no longer exist"):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])


def test_an_edge_without_a_merge_policy_blocks_and_its_blockers_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """An undeclared edge pointing at the merged record refuses the merge, and the projection says so."""

    mobile, mobil, _ada, _bob = _vocabulary()
    _tag_on_tag(mobile, mobil)
    monkeypatch.setattr(TagAssignment, "merge_policy", RecordRefMixin.merge_policy)
    blocked = {tag.pk for condition, _message in Tag.merge_blockers() for tag in Tag._base_manager.filter(condition)}
    assert blocked == {mobil.pk}
    with actor_context(_curator("merge-blocked")), pytest.raises(ValidationError, match="tag assignments"):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])
    with system_context(reason="merge test read"):
        assert Tag._base_manager.filter(pk=mobil.pk).exists()


def test_a_kept_edge_stays_on_the_merged_record(monkeypatch: pytest.MonkeyPatch) -> None:
    mobile, mobil, _ada, _bob = _vocabulary()
    history = _tag_on_tag(mobile, mobil)
    monkeypatch.setattr(TagAssignment, "merge_policy", MergePolicy.KEEP)
    with actor_context(_curator("merge-keep")):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])
    with system_context(reason="merge test read"):
        assert TagAssignment._base_manager.get(pk=history.pk).object_id == mobil.pk


def test_a_clash_outside_the_declared_identity_refuses_rather_than_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows that only share a unique slot are different facts: the merge stops instead of dropping one."""

    mobile, mobil, _ada, _bob = _vocabulary()
    monkeypatch.setattr(TagAssignment, "merge_identity", ())
    with actor_context(_curator("merge-clash")), pytest.raises(ValidationError, match="same tag"):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])
    with system_context(reason="merge test read"):
        assert TagAssignment._base_manager.filter(tag__in=(mobile, mobil)).count() == 3


def test_references_include_hidden_foreign_keys_and_each_edges_policy() -> None:
    """A delete would collect ``related_name="+"`` rows too, so a merge must see them."""

    party_fields = {
        (ref.model._meta.label, ref.field.name) for ref in references(Party) if isinstance(ref, ForeignKeyReference)
    }
    assert ("messaging.ThreadFollower", "party") in party_fields
    edges = {ref.model._meta.label: ref.policy for ref in references(Tag) if isinstance(ref, EdgeReference)}
    assert edges["tags.TagAssignment"] == MergePolicy.MOVE
    assert edges["storage.FileAttachment"] == MergePolicy.MOVE
    assert edges["workflows.StepWatch"] == MergePolicy.MOVE
    assert edges["workflows.WorkflowRun"] == MergePolicy.KEEP
    assert edges["integrate.RecordLink"] == MergePolicy.MOVE
    # A merge folds the merged record's chatter into the survivor's (the edge's merge contract).
    assert edges["messaging.ThreadAttachment"] == MergePolicy.MOVE
    assert edges["projects.Task"] == MergePolicy.MOVE


def test_a_generic_pointer_no_edge_declares_blocks() -> None:
    """A plain generic foreign key cannot be moved safely."""

    plain = {ref.model._meta.label: ref.policy for ref in references(Tag) if isinstance(ref, EdgeReference)}
    assert plain["rebac.SchemaOverride"] == MergePolicy.BLOCK


def test_a_record_a_resource_ledger_loaded_refuses_its_merge() -> None:
    """The next load would find no row for the ledger's xref and recreate the merged tag."""

    mobile, mobil, _ada, _bob = _vocabulary()
    with system_context(reason="merge test ledger"):
        Resource.objects.create(
            source_addon="angee.tags",
            source_path="resources/install/tags.yaml",
            tier=Resource.Tier.INSTALL,
            xref="mobil",
            content_hash="sha256:" + "0" * 64,
            target_model=Tag._meta.label,
            target_id=public_id_of(mobil),
        )
    with actor_context(_curator("merge-ledger")), pytest.raises(ValidationError, match="angee.tags resources"):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])
    with system_context(reason="merge test read"):
        assert Tag._base_manager.filter(pk=mobil.pk).exists()


def test_a_share_on_a_multi_table_child_row_is_seen_from_its_parent() -> None:
    """Retiring a parent row deletes its child row, and REBAC cleans the child's shares too."""

    reader = create_user("merge-mti-reader")
    with system_context(reason="merge test mti share"):
        child = MtiChild.objects.create(title="Shared", detail="child")
        parent = MtiParent._base_manager.get(pk=child.pk)
        assert not merge_module.named_in_relationships(parent)
        share = RelationshipTuple(resource=to_object_ref(child), relation="reader", subject=to_subject_ref(reader))
        write_relationships([share])
        assert merge_module.named_in_relationships(parent)


def _links(tag: Any, title: str) -> Any:
    with system_context(reason="merge test link"):
        return Link._base_manager.create(
            content_type=ContentType.objects.get_for_model(Tag),
            object_id=tag.pk,
            url="https://example.test/m",
            title=title,
        )


def test_a_duplicate_that_differs_beyond_its_identity_refuses_unless_its_edge_folds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping the merged record's copy of one URL would lose its title; an edge may fold it instead."""

    mobile, mobil, _ada, _bob = _vocabulary()
    _links(mobile, "Kept title")
    _links(mobil, "Merged title")
    with actor_context(_curator("merge-differs")), pytest.raises(ValidationError, match="different details"):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])

    monkeypatch.setattr(Link, "merge_collapse", lambda self, duplicate: True)
    with actor_context(_curator("merge-folds")):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])
    with system_context(reason="merge test read"):
        assert list(Link._base_manager.filter(url="https://example.test/m").values_list("object_id", "title")) == [
            (mobile.pk, "Kept title")
        ]


def test_a_pending_watch_keeps_its_wake_when_its_duplicate_collapses(monkeypatch: pytest.MonkeyPatch) -> None:
    saves: list[Any] = []
    monkeypatch.setattr(StepWatch, "save", lambda self, **kwargs: saves.append(kwargs["update_fields"]))
    kept, duplicate = StepWatch(pending=False), StepWatch(pending=True)
    assert kept.merge_collapse(duplicate)
    assert kept.pending
    assert "pending" in saves[0]


def test_a_history_key_keeps_pointing_at_the_merged_record() -> None:
    """A ``DO_NOTHING`` key is history Django's delete leaves alone; a merge leaves it too."""

    history = [
        ref
        for ref in references(Task)
        if isinstance(ref, ForeignKeyReference) and ref.field.remote_field.on_delete == DO_NOTHING
    ]
    assert history
    assert {ref.policy for ref in history} == {MergePolicy.KEEP}


def test_a_declared_lineage_key_keeps_pointing_at_the_merged_record() -> None:
    """A pointing model's ``merge_policies`` names a key whose policy its ``on_delete`` does not imply."""

    lineage = [
        ref for ref in references(Task)
        if isinstance(ref, ForeignKeyReference) and ref.model is Project and ref.field.name == "converted_from"
    ]
    assert Project.merge_policies == {"converted_from": MergePolicy.KEEP}
    assert [ref.policy for ref in lineage] == [MergePolicy.KEEP]
    assert Project._meta.get_field("converted_from").remote_field.on_delete is not DO_NOTHING


def test_a_key_to_a_multi_table_child_points_and_moves_by_the_shared_key() -> None:
    """``Task.queue`` points at ``Queue``, a child of ``Group``: a group merge reads and moves it by the shared key."""

    owner = create_user("merge-queue-owner")
    with system_context(reason="merge test queues"):
        kept = Queue.objects.create(name="Kept", key="KEPT", slug="kept", owner=owner)
        merged = Queue.objects.create(name="Merged", key="MERGED", slug="merged", owner=owner)
        plain = Group.objects.create(name="Plain", owner=owner, created_by=owner)
        task = Task.objects.create(title="Moves", queue=merged, owner=owner)
        (reference,) = [
            ref
            for ref in references(Group)
            if isinstance(ref, ForeignKeyReference) and ref.model is Task and ref.field.name == "queue"
        ]
        assert list(Group._base_manager.filter(reference.pointing(Group)).values_list("pk", flat=True)) == [merged.pk]
        survivor = Group._base_manager.get(pk=kept.pk)
        reference.move(reference.rows(Group._base_manager.get(pk=merged.pk)), survivor=survivor)
        task.refresh_from_db()
        assert task.queue_id == kept.pk
        with pytest.raises(ValidationError, match="cannot hold"):
            reference.move(reference.rows(survivor), survivor=plain)


def test_a_record_named_in_stored_relationships_is_retained_by_its_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """A direct share is a stored row the retired record would take with it, so the record stays as a redirect."""

    owner, reader = create_user("merge-share-owner"), create_user("merge-share-reader")
    with system_context(reason="merge test share"):
        project = Project.objects.create(title="Shared", owner=owner)
        assert not merge_module.named_in_relationships(project)
        project.system_grant_record_access("reader", reader)
        assert merge_module.named_in_relationships(project)

    mobile, mobil, _ada, _bob = _vocabulary()
    monkeypatch.setattr(merge_module, "named_in_relationships", lambda record: record.pk == mobil.pk)
    # A record with evidence (`retained()`) is kept as a redirect instead of deleted,
    # so the stored relationship outlives the merge and nothing refuses it.
    with actor_context(_curator("merge-shared")):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])
    with system_context(reason="merge test read"):
        assert Tag._base_manager.get(pk=mobil.pk).merged_into_id == mobile.pk


def test_a_many_to_many_to_its_own_model_moves_the_links_other_records_hold() -> None:
    """A one-way link from another record follows the merge, collapsing onto one the survivor holds.

    A two-way link mirrors the record's own links, so like them it is the record's data.
    """

    follows, peers = SelfLinkedNode.follows.through, SelfLinkedNode.peers.through
    links = [(ref.model, ref.name, ref.policy) for ref in references(SelfLinkedNode) if ref.model in (follows, peers)]
    assert links == [(follows, "to_selflinkednode", MergePolicy.MOVE)]

    follower, other, merged, kept = (SelfLinkedNode.objects.create() for _ in range(4))
    follower.follows.add(merged, kept)
    other.follows.add(merged)
    merged.follows.add(other)
    reference = next(ref for ref in references(SelfLinkedNode) if ref.model is follows)
    reference.move(reference.rows(merged), survivor=kept)

    assert set(follows.objects.values_list("from_selflinkednode_id", "to_selflinkednode_id")) == {
        (follower.pk, kept.pk),
        (other.pk, kept.pk),
        (merged.pk, other.pk),
    }


def test_a_collision_no_field_set_describes_refuses_the_merge() -> None:
    """A key under an expression constraint moves onto a clash the field sets cannot foresee."""

    mobile, mobil, _ada, _bob = _vocabulary()
    with system_context(reason="merge test tag slots"):
        TagSlot.objects.create(tag=mobile, label="Phone")
        TagSlot.objects.create(tag=mobil, label="phone")
    with actor_context(_curator("merge-expression")), pytest.raises(ValidationError, match="breaks one of their"):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])

    with system_context(reason="merge test read"):
        assert Tag._base_manager.filter(pk=mobil.pk).exists()
    assert _tagged_parties(mobil) != []


def test_a_merge_identity_must_be_a_declared_unique_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Edges and plain pointing rows (a message's participants and reactions) declare their identity alike."""

    pointing = [model for model in apps.get_models() if issubclass(model, MergeContractMixin)]
    assert {Participant, Reaction} <= set(pointing)
    assert not [error for model in pointing for error in model.check() if error.id == "angee.E037"]

    monkeypatch.setattr(CustomColumnEdge, "merge_identity", (("target_ct", "target_id"),))
    assert [error.id for error in CustomColumnEdge.check() if error.id == "angee.E037"] == ["angee.E037"]
    monkeypatch.setattr(Participant, "merge_identity", (("message", "handle"),))
    assert [error.id for error in Participant.check() if error.id == "angee.E037"] == ["angee.E037"]


def test_a_moved_stance_keeps_its_recency_so_the_tie_break_keeps_its_winner() -> None:
    """Two directories' equal stances on parties that merge: the older stays older, the newer still wins."""

    statements = Party.statements.model
    with system_context(reason="merge test stance recency"):
        owner = create_user("merge-recency")
        kept = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        merged = Party._base_manager.create(display_name="Ada King", created_by=owner)
        older, newer = (
            statements._base_manager.create(
                subject=party,
                aspect="identity",
                display_name=party.display_name,
                asserted_by=str(to_subject_ref(make_integration(slug, model=Directory))),
                source_kind=SourceKind.DIRECTORY,
            )
            for party, slug in ((merged, "recency-older"), (kept, "recency-newer"))
        )
        statements._base_manager.filter(pk=older.pk).update(updated_at=timezone.now() - timedelta(days=7))
        older.refresh_from_db()
        newer.refresh_from_db()
        before = (older.updated_at, min((older, newer), key=lambda row: row.sort_key()).pk)

        kept.with_actor(owner).merge(records=[merged.public_id])

        older.refresh_from_db()
        newer.refresh_from_db()
        kept.refresh_from_db()
    assert (older.subject_id, older.superseded_id, newer.superseded_id) == (kept.pk, None, None)
    assert (older.updated_at, min((older, newer), key=lambda row: row.sort_key()).pk) == before
    assert (newer.applied, older.applied, kept.display_name) == (True, False, "Ada Lovelace")


@pytest.mark.django_db(transaction=True)
def test_a_reverted_stance_gets_its_recency_back_so_the_tie_break_returns_to_its_winner() -> None:
    """Reverting a changeset that re-dated a stance restores that stance's recency, not the revert's time."""

    statements = Party.statements.model
    with system_context(reason="revert test stance recency"):
        owner = create_user("revert-recency")
        party = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        older, newer = (
            statements._base_manager.create(
                subject=party,
                aspect="identity",
                display_name=name,
                asserted_by=str(to_subject_ref(make_integration(slug, model=Directory))),
                source_kind=SourceKind.DIRECTORY,
            )
            for name, slug in (("Ada King", "revert-older"), ("Ada Lovelace", "revert-newer"))
        )
        statements._base_manager.filter(pk=older.pk).update(updated_at=timezone.now() - timedelta(days=7))
        older.refresh_from_db()
        newer.refresh_from_db()
        before = (older.display_name, older.updated_at)
        assert min((older, newer), key=lambda row: row.sort_key()).pk == newer.pk

        with changeset(fresh=True, change_reason="test.redate"):
            statements._base_manager.filter(pk=older.pk).update(display_name="Ada K.", updated_at=timezone.now())
        older.refresh_from_db()
        assert min((older, newer), key=lambda row: row.sort_key()).pk == older.pk
        redated = statements.history.filter(id=older.pk).order_by("-history_id").first().changeset

        with changeset(fresh=True, change_reason="test.revert"):
            redated.revert()
        older.refresh_from_db()
    assert (older.display_name, older.updated_at) == before
    assert min((older, newer), key=lambda row: row.sort_key()).pk == newer.pk


@pytest.mark.usefixtures("workflow_step_classes")
def test_a_record_an_unfinished_run_works_on_refuses_its_merge(execution: Any) -> None:
    """Retiring it would cancel the run while the run's watches moved to the record that is kept."""

    actor, _ = execution
    mobile, mobil, ada, bob = _vocabulary()
    workflow = Workflow.objects.install_definition(
        key="merge_guard",
        name="Merge guard",
        draft=document("entry"),
        actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=mobil)
    curator = _curator("merge-run-guard")
    with actor_context(curator), pytest.raises(ValidationError, match="Workflow runs still working on"):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])

    WorkflowRun.objects.cancel(run, actor=actor)
    with actor_context(curator):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])
    assert _tagged_parties(mobile) == sorted([ada.pk, bob.pk])


def test_the_shared_verb_merges_a_mergeable_type_and_refuses_another() -> None:
    mobile, mobil, ada, bob = _vocabulary()
    curator = _curator("merge-verb")
    schema = addon_schema(tags_schema.schemas, "console", records.schemas)

    refused = execute_schema(
        schema,
        _MERGE,
        {"type": "parties/party", "id": public_id_of(ada), "records": [public_id_of(bob)]},
        user=curator,
    )
    assert result_data(refused)["merge_records"]["ok"] is False

    merged = execute_schema(
        schema,
        _MERGE,
        {"type": "tags/tag", "id": public_id_of(mobile), "records": [public_id_of(mobil)]},
        user=curator,
    )
    assert result_data(merged)["merge_records"] == {"ok": True, "message": "Merged.", "validation_errors": None}
    assert _tagged_parties(mobile) == sorted([ada.pk, bob.pk])


def test_metadata_marks_a_mergeable_resource() -> None:
    schema = addon_schema(tags_schema.schemas, "console", records.schemas)
    resources = {item.model_label: item for item in schema.angee_resources}
    assert resources["tags.Tag"].mergeable
    wire = {item["modelLabel"]: item for item in schema._schema.extensions["angee"]["resources"]}
    assert wire["tags.Tag"]["mergeable"] is True
