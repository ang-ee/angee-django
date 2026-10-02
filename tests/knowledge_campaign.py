"""Small shared knowledge fixtures over the suite's existing composition."""

from types import SimpleNamespace

import pytest
from rebac import actor_context

from tests.conftest import MarkdownPage, Page, Vault, create_user
from tests.test_knowledge import _grant


@pytest.fixture
def clone_template(composed_tables):
    author = create_user("template-author")
    actor = create_user("clone-creator")
    reader = create_user("template-reader")
    with actor_context(author):
        source = Vault.objects.create_for(
            author, name="Template", description="Shared instructions", icon="book", accent="blue",
        )
        root = Page.objects.create_in(source, title="Folder", kind=Page.PageKind.FOLDER, icon="folder")
        nested = Page.objects.create_in(source, title="Nested", parent=root, kind=Page.PageKind.FOLDER)
        note = Page.objects.create_in(source, title="Guide", parent=nested, icon="note")
        template = Page.objects.create_in(source, title="Outline", kind=Page.PageKind.TEMPLATE)
        MarkdownPage.objects.write_body(note, "# Guide\n\n[[Outline]] [[Guide]] [[Missing]]")
        MarkdownPage.objects.write_body(template, "# Outline\n\nOriginal instructions")
    _grant(source, "viewer", actor)
    _grant(source, "viewer", reader)
    _grant(note, "viewer", reader)
    return SimpleNamespace(author=author, actor=actor, reader=reader, source=source, note=note)
