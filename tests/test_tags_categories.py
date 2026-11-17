"""Tests for tag categories: shared, ordered groups of tags with an optional per-record maximum.

Categories are vocabulary like tags, so their rows are created under
``system_context`` and curated by tag administrators. The maximum is one owner
check, ``TagAssignmentManager.require_category_maximum``: the edge verbs run it
under the record's lock, and a category edit that would break it (filing a tag,
lowering the maximum) or a merge into a capped category is refused.
``parties.Party`` stands in for a taggable record.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from rebac import actor_context, system_context
from rebac.roles import grant as grant_role

from angee.base.models.identity import public_id_for, public_id_of
from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging.testing.models import Party
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
    """A maximum a record already exceeds is refused; raising or removing one is not."""

    with system_context(reason="tags categories test widen"):
        capped.relationship.max_per_record = 2
        capped.relationship.save(update_fields=["max_per_record"])
    with actor_context(capped.owner):
        TagAssignment.objects.attach(*capped.address, [capped.prospect.sqid, capped.partner.sqid])
    with system_context(reason="tags categories test narrow"):
        capped.relationship.max_per_record = 1
        with pytest.raises(ValidationError) as refused:
            capped.relationship.save(update_fields=["max_per_record"])
        assert "max_per_record" in refused.value.message_dict
        assert TagCategory.objects.get(pk=capped.relationship.pk).max_per_record == 2
        capped.relationship.max_per_record = None
        capped.relationship.save()
    assert _held(capped.party) == {"Prospect", "Partner"}


def test_merging_a_tag_filed_elsewhere_into_a_capped_category_is_refused(capped: SimpleNamespace) -> None:
    """A merge into a capped category accepts only tags already filed there; filing first is judged."""

    curator = _tags_admin("tags-categories-merge-curator")
    with actor_context(capped.owner):
        TagAssignment.objects.attach(*capped.address, [capped.loose.sqid])
    with actor_context(curator):
        with pytest.raises(ValidationError, match="File the merged tags under Relationship"):
            Tag.objects.get(pk=capped.prospect.pk).merge(records=[public_id_of(capped.loose)])
        loose = Tag.objects.get(pk=capped.loose.pk)
        loose.category = capped.relationship
        loose.save()
        Tag.objects.get(pk=capped.prospect.pk).merge(records=[public_id_of(capped.loose)])
    assert _held(capped.party) == {"Prospect"}


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
