"""Record merge: the shared template, its declared referent policies and the shared verb.

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

from angee.base.identity import public_id_of
from angee.base.merge import MergePolicy, referents
from angee.graphql import records
from angee.messaging.testing.models import Party
from angee.tags import schema as tags_schema
from angee.tags.testing.models import Tag, TagAssignment
from tests.conftest import addon_schema, create_user, execute_schema, result_data
from tests.test_record_ref_import_campaign import CustomColumnEdge

pytestmark = pytest.mark.usefixtures("composed_permissions")

_MERGE = """mutation Merge($type: String!, $survivor: ID!, $ids: [ID!]!) {
  merge_records(target_type: $type, survivor_id: $survivor, ids: $ids, confirm: true) {
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


def test_merging_a_tag_moves_its_assignments_and_collapses_a_duplicate() -> None:
    """Ada keeps one Mobile, Bob gains it, and Mobil is gone."""

    mobile, mobil, ada, bob = _vocabulary()
    curator = _curator("merge-curator")
    with actor_context(curator):
        Tag.objects.get(pk=mobile.pk).absorb(records=[public_id_of(mobil)])

    with system_context(reason="merge test read"):
        assert not Tag._base_manager.filter(pk=mobil.pk).exists()
        assert TagAssignment._base_manager.count() == 2
    assert _tagged_parties(mobile) == sorted([ada.pk, bob.pk])


def test_merging_needs_write_on_the_survivor_and_delete_on_each_merged_record() -> None:
    """Someone who may not curate tags merges nothing."""

    mobile, mobil, _ada, _bob = _vocabulary()
    outsider = create_user("merge-outsider")
    with actor_context(outsider), pytest.raises(PermissionDenied):
        Tag._base_manager.get(pk=mobile.pk).with_actor(outsider).absorb(records=[public_id_of(mobil)])

    with system_context(reason="merge test read"):
        assert Tag._base_manager.filter(pk__in=(mobile.pk, mobil.pk)).count() == 2
        assert TagAssignment._base_manager.count() == 3


def test_a_merge_refuses_itself_and_records_that_are_gone() -> None:
    mobile, _mobil, _ada, _bob = _vocabulary()
    curator = _curator("merge-refusals")
    with actor_context(curator):
        survivor = Tag.objects.get(pk=mobile.pk)
        with pytest.raises(ValidationError, match="into itself"):
            survivor.absorb(records=[public_id_of(mobile)])
        with pytest.raises(ValidationError, match="no longer exist"):
            survivor.absorb(records=["tag_missing"])
        with pytest.raises(ValidationError, match="Choose the records"):
            survivor.absorb()


def test_an_edge_without_a_merge_policy_blocks_the_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """An edge that declares nothing pointing at the merged record refuses the whole merge."""

    mobile, mobil, _ada, _bob = _vocabulary()
    with system_context(reason="merge test edge on a tag"):
        TagAssignment.objects.create(
            tag=mobile, content_type=ContentType.objects.get_for_model(Tag), object_id=mobil.pk
        )
    monkeypatch.setattr(TagAssignment, "merge_policy", None)
    curator = _curator("merge-blocked")
    with actor_context(curator), pytest.raises(ValidationError, match="tag assignments"):
        Tag.objects.get(pk=mobile.pk).absorb(records=[public_id_of(mobil)])

    with system_context(reason="merge test read"):
        assert Tag._base_manager.filter(pk=mobil.pk).exists()
    assert _tagged_parties(mobil) != []


def test_an_edge_model_without_a_table_holds_nothing_to_merge() -> None:
    """A registered edge whose table this database lacks (another module's fixture) is skipped."""

    assert all(referent.model is not CustomColumnEdge for referent in referents(Tag))
    mobile, mobil, ada, bob = _vocabulary()
    curator = _curator("merge-tableless")
    with actor_context(curator):
        Tag.objects.get(pk=mobile.pk).absorb(records=[public_id_of(mobil)])
    assert _tagged_parties(mobile) == sorted([ada.pk, bob.pk])


def test_each_edge_declares_what_a_merge_does_to_it() -> None:
    """Moves for attachments and bindings, history kept for runs and evidence, sync links block."""

    edges = {referent.model._meta.label: referent.policy for referent in referents(Tag) if referent.pointer}
    assert edges["tags.TagAssignment"] == MergePolicy.MOVE
    assert edges["storage.FileAttachment"] == MergePolicy.MOVE
    assert edges["knowledge.RecordBinding"] == MergePolicy.MOVE
    assert edges["workflows.StepWatch"] == MergePolicy.MOVE
    assert edges["workflows.WorkflowRun"] == MergePolicy.KEEP
    assert edges["integrate.RecordLink"] == MergePolicy.BLOCK
    # Thread attachments block until they merge the two records' threads.
    assert edges["messaging.ThreadAttachment"] == MergePolicy.BLOCK
    concrete = {
        (referent.model._meta.label, referent.field.name): referent.policy
        for referent in referents(Tag)
        if referent.field is not None
    }
    assert concrete[("tags.TagAssignment", "tag")] == MergePolicy.MOVE


def test_the_shared_verb_merges_a_mergeable_type_and_refuses_another() -> None:
    mobile, mobil, ada, bob = _vocabulary()
    curator = _curator("merge-verb")
    schema = addon_schema(tags_schema.schemas, "console", records.schemas)

    refused = execute_schema(
        schema, _MERGE, {"type": "parties/party", "survivor": public_id_of(ada), "ids": [public_id_of(bob)]},
        user=curator,
    )
    assert result_data(refused)["merge_records"]["ok"] is False

    merged = execute_schema(
        schema, _MERGE, {"type": "tags/tag", "survivor": public_id_of(mobile), "ids": [public_id_of(mobil)]},
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
