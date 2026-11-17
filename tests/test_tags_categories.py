"""Tests for tag categories: shared, ordered groups of tags with an optional per-record maximum.

Categories are vocabulary like tags, so their rows are created under
``system_context`` and curated by tag administrators. The maximum is one owner
check, ``TagAssignmentManager.require_category_maximum``. Every edge insert runs
it for its record and its tag's category, a refiling or a lowered maximum runs it
when the stored value changes, and a merge runs it after its moves. Each refusal
rolls its write back. ``bulk_create`` stands in for the documented bypass when a
test needs a record already over a maximum. ``parties.Party`` stands in for a
taggable record.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from rebac import actor_context, system_context
from rebac.roles import grant as grant_role

from angee.base.models.identity import public_id_for, public_id_of
from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging.testing.models import Party
from angee.provenance.context import changeset
from angee.resources.testing.models import Resource
from angee.tags import schema as tags_schema
from angee.tags.testing.models import Tag, TagAssignment, TagCategory
from tests.conftest import addon_schema, create_user


def _tags_admin(username: str) -> Any:
    """A tag curator: an effective member of ``tags/role:tags_admin`` and nothing else."""

    user = create_user(username)
    with system_context(reason="tags categories test curator"):
        grant_role(actor=user, role="tags/role:tags_admin")
    return user


def _held(record: Any) -> set[str]:
    with system_context(reason="tags categories test read"):
        return set(TagAssignment.objects.for_record(record).values_list("tag__name", flat=True))


def _over(record: Any, *tags: Any) -> None:
    """Give ``record`` ``tags`` through ``bulk_create``, the bypass the check leaves unjudged."""

    content_type = ContentType.objects.get_for_model(type(record))
    with system_context(reason="tags categories test bypass"):
        TagAssignment.objects.bulk_create(
            [TagAssignment(tag=tag, content_type=content_type, object_id=record.pk) for tag in tags],
        )


@pytest.fixture()
def capped(db: None) -> SimpleNamespace:
    """A record writer's party, a category capped at one tag holding two tags, and a free tag."""

    del db
    owner = create_user("tags-categories-owner")
    with system_context(reason="tags categories test vocabulary"):
        relationship = TagCategory.objects.create(name="Relationship", max_per_record=1)
        prospect = Tag.objects.create(name="Prospect", category=relationship)
        partner = Tag.objects.create(name="Partner", category=relationship)
        loose = Tag.objects.create(name="Loose")
    with actor_context(owner):
        party = Party.objects.create(display_name="Epsilon Ltd")
    address = ("parties/party", public_id_for(Party, party.pk))
    return SimpleNamespace(
        owner=owner,
        relationship=relationship,
        prospect=prospect,
        partner=partner,
        loose=loose,
        party=party,
        address=address,
    )


def test_a_category_is_shared_vocabulary_only_tag_administrators_curate(db: None) -> None:
    """Every signed-in actor reads a category; a tags admin creates one and a plain user cannot."""

    del db
    reader = create_user("tags-categories-reader")
    with system_context(reason="tags categories test vocabulary"):
        category = TagCategory.objects.create(name="Segment")
    with actor_context(reader):
        assert TagCategory.objects.filter(pk=category.pk).exists()
        assert not category.with_actor(reader).has_access("write")
    with actor_context(AnonymousUser()):
        assert not TagCategory.objects.exists()

    schema = addon_schema(tags_schema.schemas, "console")
    create = """
      mutation { insert_tag_categories_one(object: {name: "Region", max_per_record: 1}) { name max_per_record } }
    """
    assert execute_schema(schema, create, user=reader).errors
    created = result_data(execute_schema(schema, create, user=_tags_admin("tags-categories-curator")))
    assert created["insert_tag_categories_one"] == {"name": "Region", "max_per_record": 1}


def test_categories_append_in_order_and_uncategorised_tags_stay_valid(db: None) -> None:
    """An omitted position appends after the last category; a tag needs no category."""

    del db
    with system_context(reason="tags categories test vocabulary"):
        first = TagCategory.objects.create(name="Zeta")
        second = TagCategory.objects.create(name="Alpha")
        loose = Tag.objects.create(name="Loose")
        assert second.position > first.position
        assert list(TagCategory.objects.values_list("name", flat=True)) == ["Zeta", "Alpha"]
    assert loose.category_id is None


def test_a_maximum_below_one_is_refused(db: None) -> None:
    """``max_per_record`` is unset or at least one: zero would forbid the category's own tags."""

    del db
    with pytest.raises(ValidationError) as refused:
        TagCategory(name="Empty", max_per_record=0).full_clean()
    assert refused.value.messages == ["A category's maximum per record is at least one tag."]


def test_attach_refuses_a_tag_over_its_category_maximum_and_attaches_nothing(capped: SimpleNamespace) -> None:
    """The verb judges the record's result: the second relationship tag refuses the whole attach."""

    with actor_context(capped.owner):
        TagAssignment.objects.attach(*capped.address, [capped.prospect.sqid])
        with pytest.raises(ValidationError) as refused:
            TagAssignment.objects.attach(*capped.address, [capped.loose.sqid, capped.partner.sqid])
    assert refused.value.message_dict == {"tags": ["A record may hold at most 1 tag from Relationship."]}
    assert _held(capped.party) == {"Prospect"}


def test_set_for_refuses_over_the_maximum_and_keeps_the_records_tags(capped: SimpleNamespace) -> None:
    """The record's save path refuses the over-maximum set and leaves its tags as they were."""

    with actor_context(capped.owner):
        capped.party.apply_input_extensions(tags=[capped.prospect.sqid, capped.loose.sqid])
        with pytest.raises(ValidationError) as refused:
            capped.party.apply_input_extensions(tags=[capped.prospect.sqid, capped.partner.sqid])
        assert "tags" in refused.value.message_dict
        # Swapping one relationship tag for the other stays within the maximum.
        capped.party.apply_input_extensions(tags=[capped.partner.sqid, capped.loose.sqid])
    assert _held(capped.party) == {"Partner", "Loose"}


def test_the_tag_verb_reports_the_maximum_as_a_validation_error(capped: SimpleNamespace) -> None:
    """The record writer sees the category's maximum named in the verb's error."""

    schema = addon_schema(tags_schema.schemas, "console")
    document = """mutation Tag($type: String!, $id: ID!, $tags: [ID!]!) {
      tag(target_type: $type, target_id: $id, tag_ids: $tags) { id }
    }"""
    target_type, target_id = capped.address
    variables = {"type": target_type, "id": target_id, "tags": [capped.prospect.sqid, capped.partner.sqid]}
    [error] = execute_schema(schema, document, variables, user=capped.owner).errors
    assert error.message == "A record may hold at most 1 tag from Relationship."
    assert error.extensions["code"] == "VALIDATION"
    assert _held(capped.party) == set()


def test_filing_a_tag_into_a_full_category_is_refused(capped: SimpleNamespace) -> None:
    """Moving a record's free tag into its full category is refused and the tag stays where it was."""

    with actor_context(capped.owner):
        TagAssignment.objects.attach(*capped.address, [capped.prospect.sqid, capped.loose.sqid])
    with system_context(reason="tags categories test refile"):
        capped.loose.category = capped.relationship
        with pytest.raises(ValidationError) as refused:
            capped.loose.save()
        assert refused.value.message_dict == {"category": ["A record may hold at most 1 tag from Relationship."]}
        assert Tag.objects.get(pk=capped.loose.pk).category_id is None


def test_lowering_a_maximum_below_a_records_tags_is_refused(capped: SimpleNamespace) -> None:
    """A maximum a record already exceeds is refused with its own message; raising or removing one is not."""

    with system_context(reason="tags categories test widen"):
        capped.relationship.max_per_record = 2
        capped.relationship.save(update_fields=["max_per_record"])
    with actor_context(capped.owner):
        TagAssignment.objects.attach(*capped.address, [capped.prospect.sqid, capped.partner.sqid])
    with system_context(reason="tags categories test narrow"):
        capped.relationship.max_per_record = 1
        with pytest.raises(ValidationError) as refused:
            capped.relationship.save(update_fields=["max_per_record"])
        assert refused.value.message_dict == {
            "max_per_record": ["Records already hold more than 1 tag from Relationship."],
        }
        assert TagCategory.objects.get(pk=capped.relationship.pk).max_per_record == 2
        capped.relationship.max_per_record = None
        capped.relationship.save()
    assert _held(capped.party) == {"Prospect", "Partner"}


def test_edits_beside_a_record_already_over_the_maximum_are_not_judged(capped: SimpleNamespace) -> None:
    """Only a changed category or a lowered maximum is judged, so unrelated edits stay possible."""

    _over(capped.party, capped.prospect, capped.partner)
    with system_context(reason="tags categories test unrelated edits"):
        segment = TagCategory.objects.create(name="Segment", max_per_record=1)
        partner = Tag.objects.get(pk=capped.partner.pk)
        partner.name = "Partner (renamed)"
        partner.save()
        category = TagCategory.objects.get(pk=capped.relationship.pk)
        category.description = "Edited."
        category.save()
        category.max_per_record = 3
        category.save()
        # A tag no over-maximum record holds files under the category freely.
        retail = Tag.objects.create(name="Retail", category=segment)
        retail.category = capped.relationship
        retail.save()
    with actor_context(capped.owner):
        TagAssignment.objects.attach(*capped.address, [capped.loose.sqid])
    assert _held(capped.party) == {"Prospect", "Partner (renamed)", "Loose"}


def test_a_raw_edge_create_is_judged_and_needs_its_record(capped: SimpleNamespace) -> None:
    """``objects.create`` is an insert like the verbs'; an edge on a missing row is refused."""

    content_type = ContentType.objects.get_for_model(Party)
    with system_context(reason="tags categories test raw edges"):
        TagAssignment.objects.create(tag=capped.prospect, content_type=content_type, object_id=capped.party.pk)
        with pytest.raises(ValidationError, match="at most 1 tag from Relationship"):
            TagAssignment.objects.create(tag=capped.partner, content_type=content_type, object_id=capped.party.pk)
        with pytest.raises(ValueError, match="tag target not found"):
            TagAssignment.objects.create(tag=capped.loose, content_type=content_type, object_id=capped.party.pk + 999)
    assert _held(capped.party) == {"Prospect"}


def test_merging_a_duplicate_into_a_capped_tag_collapses_it(capped: SimpleNamespace) -> None:
    """A record holding a tag and its duplicate keeps one tag of the category after the merge."""

    curator = _tags_admin("tags-categories-merge-curator")
    with actor_context(capped.owner):
        TagAssignment.objects.attach(*capped.address, [capped.prospect.sqid, capped.loose.sqid])
    with actor_context(curator):
        Tag.objects.get(pk=capped.prospect.pk).merge(records=[public_id_of(capped.loose)])
    assert _held(capped.party) == {"Prospect"}


def test_a_tag_merge_that_leaves_a_record_over_the_maximum_is_refused(capped: SimpleNamespace) -> None:
    """The fold is judged after its moves: a record gaining a second capped tag rolls the merge back."""

    curator = _tags_admin("tags-categories-over-merge-curator")
    with actor_context(capped.owner):
        TagAssignment.objects.attach(*capped.address, [capped.partner.sqid, capped.loose.sqid])
    with actor_context(curator):
        with pytest.raises(ValidationError, match="at most 1 tag from Relationship"):
            Tag.objects.get(pk=capped.prospect.pk).merge(records=[public_id_of(capped.loose)])
    with system_context(reason="tags categories test read"):
        assert Tag.objects.filter(pk=capped.loose.pk).exists()
    assert _held(capped.party) == {"Partner", "Loose"}


def test_a_record_merge_that_leaves_the_survivor_over_the_maximum_is_refused(capped: SimpleNamespace) -> None:
    """A merged record's tags move to the survivor, which is judged in the categories it gained."""

    with actor_context(capped.owner):
        duplicate = Party.objects.create(display_name="Epsilon Limited")
        TagAssignment.objects.attach(*capped.address, [capped.prospect.sqid])
        TagAssignment.objects.attach("parties/party", public_id_for(Party, duplicate.pk), [capped.partner.sqid])
        with pytest.raises(ValidationError, match="at most 1 tag from Relationship"):
            Party.objects.get(pk=capped.party.pk).merge(records=[public_id_of(duplicate)])
    assert _held(capped.party) == {"Prospect"}
    assert _held(duplicate) == {"Partner"}


def test_a_record_merge_within_the_maximum_moves_the_tags(capped: SimpleNamespace) -> None:
    """A survivor already over in one category still merges a record that adds none of it."""

    _over(capped.party, capped.prospect, capped.partner)
    with actor_context(capped.owner):
        duplicate = Party.objects.create(display_name="Epsilon Limited")
        TagAssignment.objects.attach("parties/party", public_id_for(Party, duplicate.pk), [capped.loose.sqid])
        Party.objects.get(pk=capped.party.pk).merge(records=[public_id_of(duplicate)])
    assert _held(capped.party) == {"Prospect", "Partner", "Loose"}


def test_a_merge_into_an_uncapped_category_moves_tags_across_categories(capped: SimpleNamespace) -> None:
    """Without a maximum on the survivor's category, tags merge in from any category."""

    curator = _tags_admin("tags-categories-free-merge-curator")
    with system_context(reason="tags categories test segment"):
        segment = TagCategory.objects.create(name="Segment")
        retail = Tag.objects.create(name="Retail", category=segment)
    with actor_context(capped.owner):
        TagAssignment.objects.attach(*capped.address, [capped.prospect.sqid, capped.loose.sqid])
    with actor_context(curator):
        Tag.objects.get(pk=retail.pk).merge(records=[public_id_of(capped.loose), public_id_of(capped.partner)])
    assert _held(capped.party) == {"Prospect", "Retail"}


def test_deleting_a_category_leaves_its_tags_uncategorised(capped: SimpleNamespace) -> None:
    """A category's tags outlive it, uncategorised and still attached."""

    with actor_context(capped.owner):
        TagAssignment.objects.attach(*capped.address, [capped.prospect.sqid])
    with system_context(reason="tags categories test delete"):
        capped.relationship.delete()
        assert Tag.objects.get(pk=capped.prospect.pk).category_id is None
    assert _held(capped.party) == {"Prospect"}


def test_the_console_files_tags_by_category_public_id_and_filters_on_it(db: None) -> None:
    """A tag's category writes by public id, projects as a node and narrows the tag list."""

    curator = _tags_admin("tags-categories-console-curator")
    with system_context(reason="tags categories test vocabulary"):
        segment = TagCategory.objects.create(name="Segment")
        Tag.objects.create(name="Loose")
    schema = addon_schema(tags_schema.schemas, "console")
    filed = result_data(
        execute_schema(
            schema,
            """
      mutation File($category: ID!) {
        insert_tags_one(object: {name: "Retail", category: $category}) { name category { name } }
      }
    """,
            {"category": segment.sqid},
            user=curator,
        )
    )
    assert filed["insert_tags_one"] == {"name": "Retail", "category": {"name": "Segment"}}
    narrowed = result_data(
        execute_schema(
            schema,
            """
      query Narrow($category: String!) { tags(where: {category: {_eq: $category}}) { name } }
    """,
            {"category": segment.sqid},
            user=curator,
        )
    )
    assert narrowed["tags"] == [{"name": "Retail"}]
    loose = result_data(
        execute_schema(
            schema,
            "{ tags(where: {category: {_is_null: true}}) { name } }",
            user=curator,
        )
    )
    assert loose["tags"] == [{"name": "Loose"}]


@pytest.mark.django_db(transaction=True)
def test_reverting_an_untag_over_the_maximum_is_refused() -> None:
    """A changeset revert restores an edge through its save, which judges the record like any insert."""

    owner = create_user("tags-categories-revert-owner")
    with system_context(reason="tags categories test vocabulary"):
        relationship = TagCategory.objects.create(name="Relationship", max_per_record=1)
        prospect = Tag.objects.create(name="Prospect", category=relationship)
        partner = Tag.objects.create(name="Partner", category=relationship)
    with actor_context(owner):
        party = Party.objects.create(display_name="Zeta Ltd")
        address = ("parties/party", public_id_for(Party, party.pk))
        [edge] = TagAssignment.objects.attach(*address, [prospect.sqid])
        with changeset(fresh=True):
            TagAssignment.objects.detach(*address, [prospect.sqid])
        TagAssignment.objects.attach(*address, [partner.sqid])
    with system_context(reason="tags categories test changeset"):
        removal = TagAssignment.history.filter(id=edge.pk, history_type="-").order_by("-history_id").first()
        assert removal is not None
        boundary = removal.changeset
    with actor_context(owner), pytest.raises(ValidationError, match="at most 1 tag from Relationship"):
        boundary.with_actor(owner).revert()
    assert _held(party) == {"Partner"}


def test_demo_resources_load_categories_and_file_tags_idempotently(db: None) -> None:
    """The demo tier seeds neutral categories before the tags it files, and a replay changes nothing."""

    del db
    addon = apps.get_app_config("tags")
    first = Resource.objects.load_addons((addon,), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    assert first.created == 6
    with system_context(reason="tags categories test demo"):
        filed = dict(Tag.objects.values_list("name", "category__name"))
        assert filed == {
            "VIP": "Segment",
            "Prospect": "Relationship",
            "Wholesale": "Segment",
            "Partner": "Relationship",
        }
        assert TagCategory.objects.get(name="Relationship").max_per_record == 1
    replay = Resource.objects.load_addons((addon,), tiers=[Resource.Tier.DEMO], allow_non_dev=True)
    assert (replay.created, replay.updated) == (0, 0)
