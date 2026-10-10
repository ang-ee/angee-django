"""Record merge: the shared template, its declared reference policies and the shared verb.

Tags are the first mergeable model: merging one tag into another moves its
assignments, a record tagged with both keeps one, and the merged tag goes away.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from rebac import PermissionDenied, actor_context, system_context
from rebac.roles import grant as grant_role

from angee.base import merge as merge_module
from angee.base.identity import public_id_of
from angee.base.merge import references
from angee.base.refs import MergePolicy
from angee.graphql import records
from angee.messaging.testing.models import Party
from angee.tags import schema as tags_schema
from angee.tags.testing.models import Tag, TagAssignment
from tests.conftest import addon_schema, create_user, execute_schema, result_data

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


def _vocabulary() -> tuple[Any, Any, Any, Any]:
    """Two tags for one meaning: Ada carries both, Bob only the one to merge away."""

    with system_context(reason="merge test vocabulary"):
        mobile = Tag.objects.create(name="Mobile")
        mobil = Tag.objects.create(name="Mobil")
        ada = Party.objects.create(display_name="Ada")
        bob = Party.objects.create(display_name="Bob")
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
    """Ada keeps one Mobile, Bob gains it, and Mobil is gone."""

    mobile, mobil, ada, bob = _vocabulary()
    with actor_context(_curator("merge-curator")):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil)])

    with system_context(reason="merge test read"):
        assert not Tag._base_manager.filter(pk=mobil.pk).exists()
        assert TagAssignment._base_manager.filter(tag__in=(mobile, mobil)).count() == 2
    assert _tagged_parties(mobile) == sorted([ada.pk, bob.pk])


def test_several_records_merge_into_one_survivor() -> None:
    mobile, mobil, ada, bob = _vocabulary()
    with system_context(reason="merge test third tag"):
        cell = Tag.objects.create(name="Cell")
    with actor_context(_curator("merge-several")):
        Tag.objects.get(pk=mobile.pk).merge(records=[public_id_of(mobil), public_id_of(cell)])

    with system_context(reason="merge test read"):
        assert not Tag._base_manager.filter(pk__in=(mobil.pk, cell.pk)).exists()
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
    monkeypatch.setattr(TagAssignment, "merge_policy", None)
    blocked = {
        tag.pk
        for condition, _message in Tag.merge_blockers()
        for tag in Tag._base_manager.filter(condition)
    }
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

    party_fields = {(ref.model._meta.label, ref.field.name) for ref in references(Party) if ref.field is not None}
    assert ("messaging.ThreadFollower", "party") in party_fields
    edges = {ref.model._meta.label: ref.policy for ref in references(Tag) if ref.pointer is not None}
    assert edges["tags.TagAssignment"] == MergePolicy.MOVE
    assert edges["storage.FileAttachment"] == MergePolicy.MOVE
    assert edges["workflows.StepWatch"] == MergePolicy.MOVE
    assert edges["workflows.WorkflowRun"] == MergePolicy.KEEP
    assert edges["integrate.RecordLink"] == MergePolicy.BLOCK
    assert edges["messaging.ThreadAttachment"] == MergePolicy.BLOCK


def test_the_shared_verb_merges_a_mergeable_type_and_refuses_another() -> None:
    mobile, mobil, ada, bob = _vocabulary()
    curator = _curator("merge-verb")
    schema = addon_schema(tags_schema.schemas, "console", records.schemas)

    refused = execute_schema(
        schema, _MERGE, {"type": "parties/party", "id": public_id_of(ada), "records": [public_id_of(bob)]},
        user=curator,
    )
    assert result_data(refused)["merge_records"]["ok"] is False

    merged = execute_schema(
        schema, _MERGE, {"type": "tags/tag", "id": public_id_of(mobile), "records": [public_id_of(mobil)]},
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
