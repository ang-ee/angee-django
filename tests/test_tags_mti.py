"""A multi-table child's ``tags`` read the edges keyed on its canonical ancestor.

``rebac.generic_target`` stores a child row's tag edge against its topmost
typed ancestor, which declares ``tag_assignments``; Django's generic relation
would key the child's rows on the child's own content type. The knowledge
fixture's ``MarkdownPage`` (``knowledge/markdown_page``) is such a child of
``Page`` (``knowledge/page``).
"""

from __future__ import annotations

import pytest
import strawberry_django
from rebac import actor_context, system_context

from angee.base.refs import canonical_link_path
from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.tags.schema import TaggedNode
from angee.tags.testing.models import Tag, TagAssignment
from tests.conftest import MarkdownPage, Page, addon_schema, create_user, execute_schema, result_data, vault_for

pytestmark = pytest.mark.usefixtures("composed_permissions")


@strawberry_django.type(MarkdownPage)
class MarkdownPageTagsType(TaggedNode, AngeeNode):
    """A console node over the child, composing ``TaggedNode`` as an owner would."""

    title: str


def test_a_child_reaches_its_canonical_ancestor_through_its_parent_links() -> None:
    assert canonical_link_path(MarkdownPage) == ("page_ptr",)
    assert canonical_link_path(Page) == ()


def test_an_mti_child_reads_the_tags_keyed_on_its_canonical_ancestor() -> None:
    owner = create_user("tags-mti-owner")
    vault = vault_for(owner)
    with actor_context(owner):
        pages = [Page.objects.create_in(vault, title=title, body="notes") for title in ("Plan", "Retro")]
    with system_context(reason="test.tags.mti.seed"):
        tag = Tag.objects.create(name="Roadmap", color="")
    with actor_context(owner):
        TagAssignment.objects.attach("knowledge/page", pages[0].sqid, [tag.sqid])
    resource = hasura_model_resource(
        MarkdownPageTagsType,
        model=MarkdownPage,
        name="markdown_tag_rows",
        filterable=("id",),
        sortable=("title",),
        aggregatable=("id",),
        insert=False,
        update=False,
        delete=False,
    )
    schema = addon_schema({"console": {"query": [resource.query], "types": list(resource.types)}}, "console")

    data = result_data(execute_schema(
        schema, "{ markdown_tag_rows(order_by: [{title: asc}]) { title tags { name } } }", user=owner,
    ))

    assert data["markdown_tag_rows"] == [
        {"title": "Plan", "tags": [{"name": "Roadmap"}]},
        {"title": "Retro", "tags": []},
    ]


def test_an_mti_child_save_keys_its_tags_on_its_canonical_ancestor() -> None:
    owner = create_user("tags-mti-saver")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Plan", body="notes")
    child = MarkdownPage._base_manager.get(pk=page.pk)
    with system_context(reason="test.tags.mti.save.seed"):
        tag = Tag.objects.create(name="Roadmap", color="")

    with actor_context(owner):
        child.apply_input_extensions(tags=[tag.sqid])

    with system_context(reason="test.tags.mti.save.check"):
        edges = list(TagAssignment.objects.for_record(page))
    assert [edge.tag_id for edge in edges] == [tag.pk]
    assert {edge.content_type.model_class() for edge in edges} == {Page}
