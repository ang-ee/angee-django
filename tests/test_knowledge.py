"""Tests for knowledge models, manager factories, and REBAC scoping."""

from __future__ import annotations

from typing import Any

import pytest
import reversion
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from rebac import (
    MissingActorError,
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.roles import grant

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.knowledge import schema as knowledge_schema
from angee.knowledge.models import (
    SectionNotFoundError,
    StaleBodyError,
    UnsupportedPageKindError,
    parse_wikilinks,
)
from angee.mcp.graphql import execute_under_actor
from tests.conftest import Link, MarkdownPage, Page, SchemaAddon, Vault, create_user, vault_for


def test_create_for_sets_owner_and_audit_stamps(knowledge_tables: None) -> None:
    """The vault factory persists ownership and stamps the creating actor."""

    alice = create_user("alice")

    with actor_context(alice):
        vault = Vault.objects.create_for(alice, name="Research")

    assert vault.owner == alice
    assert vault.created_by == alice
    assert str(vault.sqid).startswith("vlt_")


def test_factory_rows_stay_gated_after_creation(knowledge_tables: None) -> None:
    """The create-time elevation must not survive on the returned instance."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)

    assert getattr(vault, "_rebac_sudo_reason", None) is None
    with actor_context(bob), pytest.raises(PermissionDenied):
        vault.as_user(bob).save(update_fields=("name",))


def test_create_for_refuses_foreign_owner(knowledge_tables: None) -> None:
    """A vault may only be created for the acting user."""

    alice = create_user("alice")
    bob = create_user("bob")

    with actor_context(alice), pytest.raises(PermissionDenied):
        Vault.objects.create_for(bob, name="Gift")


def test_create_vault_requires_an_authenticated_actor(knowledge_tables: None) -> None:
    """Anonymous and actor-less calls cannot create vaults."""

    anonymous = AnonymousUser()

    with actor_context(anonymous), pytest.raises(PermissionDenied):
        Vault.objects.create_for(anonymous, name="Nope")
    with pytest.raises(MissingActorError):
        Vault.objects.create_for(None, name="Nope")


def test_vault_scope_hides_other_owners(knowledge_tables: None) -> None:
    """Actor-scoped vault reads only return the actor's own grants."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault_for(alice, name="Research")

    assert [vault.name for vault in Vault.objects.as_user(alice)] == ["Research"]
    assert list(Vault.objects.as_user(bob)) == []


def test_create_in_requires_vault_write(knowledge_tables: None) -> None:
    """Only actors who can write the vault may add pages to it."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)

    with actor_context(bob), pytest.raises(PermissionDenied):
        Page.objects.create_in(vault, title="Intruder")

    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Reading list")

    assert page.kind == Page.Kind.NOTE
    assert page.created_by == alice
    assert [row.title for row in Page.objects.as_user(alice)] == ["Reading list"]
    assert list(Page.objects.as_user(bob)) == []


def test_create_in_refuses_cross_vault_parent(knowledge_tables: None) -> None:
    """A parent from another vault would leak access across the boundary."""

    alice = create_user("alice")
    research = vault_for(alice, name="Research")
    journal = vault_for(alice, name="Journal")
    with actor_context(alice):
        folder = Page.objects.create_in(journal, title="Projects", kind=Page.Kind.FOLDER)
        with pytest.raises(ValueError, match="same vault"):
            Page.objects.create_in(research, parent=folder, title="Escapee")


def test_page_inherits_vault_read(knowledge_tables: None) -> None:
    """A vault viewer grant reaches every page through ``vault->read``."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)
    with actor_context(alice):
        Page.objects.create_in(vault, title="Shared notes")

    _grant(vault, "viewer", bob)

    assert [row.title for row in Page.objects.as_user(bob)] == ["Shared notes"]
    assert [row.name for row in Vault.objects.as_user(bob)] == ["Research"]


def test_page_inherits_parent_read(knowledge_tables: None) -> None:
    """A grant on a folder page cascades to its children via ``parent->read``."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)
    with actor_context(alice):
        folder = Page.objects.create_in(vault, title="Projects", kind=Page.Kind.FOLDER)
        Page.objects.create_in(vault, parent=folder, title="Roadmap")
        Page.objects.create_in(vault, title="Private")

    _grant(folder, "viewer", bob)

    assert {row.title for row in Page.objects.as_user(bob)} == {"Projects", "Roadmap"}


def test_folder_delete_cascades_past_foreign_children(knowledge_tables: None) -> None:
    """Deleting a folder passes the gate on children other users created."""

    alice = create_user("alice")
    editor = create_user("editor")
    vault = vault_for(alice)
    _grant(vault, "editor", editor)
    with actor_context(alice):
        folder = Page.objects.create_in(vault, title="Projects", kind=Page.Kind.FOLDER)
    with actor_context(editor):
        Page.objects.create_in(vault, parent=folder, title="Editor child")

    with actor_context(alice):
        Page.objects.as_user(alice).get(title="Projects").delete()

    with actor_context(alice):
        assert not Page.objects.as_user(alice).exists()


def test_roles_grant_cross_vault_reach(knowledge_tables: None) -> None:
    """The shipped knowledge roles cover every vault without per-row grants."""

    alice = create_user("alice")
    viewer = create_user("viewer")
    editor = create_user("editor")
    vault_admin = create_user("vault-admin")
    knowledge_admin = create_user("knowledge-admin")
    vault = vault_for(alice)
    with actor_context(alice):
        Page.objects.create_in(vault, title="Handbook")

    grant(actor=viewer, role="knowledge/role:vault_viewer")
    grant(actor=editor, role="knowledge/role:vault_editor")
    grant(actor=vault_admin, role="knowledge/role:vault_admin")
    grant(actor=knowledge_admin, role="knowledge/role:knowledge_admin")

    assert [row.name for row in Vault.objects.as_user(viewer)] == ["Research"]
    assert [row.title for row in Page.objects.as_user(viewer)] == ["Handbook"]
    with actor_context(viewer), pytest.raises(PermissionDenied):
        _rename(Vault.objects.as_user(viewer).get(), "Viewer rename")

    with actor_context(editor):
        _rename(Vault.objects.as_user(editor).get(), "Editor rename")
    with actor_context(editor), pytest.raises(PermissionDenied):
        Vault.objects.as_user(editor).get().delete()

    assert [row.name for row in Vault.objects.as_user(knowledge_admin)] == ["Editor rename"]

    with actor_context(vault_admin):
        Vault.objects.as_user(vault_admin).get().delete()
    assert list(Vault.objects.as_user(alice)) == []


def test_write_body_creates_updates_and_guards(knowledge_tables: None) -> None:
    """Body writes upsert the sidecar, derive hash facts, and reject stale tokens."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Draft")
        markdown = MarkdownPage.objects.write_body(page, "one two three")

    assert markdown.word_count == 3
    assert markdown.body_hash == MarkdownPage.hash_body("one two three")
    assert markdown.created_by == alice

    with actor_context(alice):
        with pytest.raises(StaleBodyError):
            MarkdownPage.objects.write_body(page, "other", expected_hash="stale")
        updated = MarkdownPage.objects.write_body(
            page,
            "rewritten",
            expected_hash=markdown.body_hash,
        )
    assert updated.body == "rewritten"
    assert updated.word_count == 1

    _grant(vault, "viewer", bob)
    with actor_context(bob), pytest.raises(PermissionDenied):
        MarkdownPage.objects.write_body(page, "vandalised")


def test_write_body_rejects_bodyless_kinds(knowledge_tables: None) -> None:
    """Folder pages carry no markdown body sidecar."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        folder = Page.objects.create_in(vault, title="Projects", kind=Page.Kind.FOLDER)
        with pytest.raises(UnsupportedPageKindError):
            MarkdownPage.objects.write_body(folder, "nope")


def test_body_revisions_roll_back(knowledge_tables: None) -> None:
    """Body edits snapshot through django-reversion and revert cleanly."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Versioned")
        with reversion.create_revision():
            markdown = MarkdownPage.objects.write_body(page, "first body")
        with reversion.create_revision():
            MarkdownPage.objects.write_body(page, "second body")

        markdown.refresh_from_db()
        versions = list(markdown.revisions)
        assert [version.field_dict["body"] for version in versions] == [
            "second body",
            "first body",
        ]

        markdown.revert_to(versions[-1])
    markdown.refresh_from_db()
    assert markdown.body == "first body"
    assert markdown.word_count == 2


def test_excerpt_truncates_long_bodies(knowledge_tables: None) -> None:
    """The excerpt keeps short bodies intact and ellipsizes long ones."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Long read")
        markdown = MarkdownPage.objects.write_body(page, "word " * 100)

    assert markdown.excerpt.endswith("…")
    assert len(markdown.excerpt) <= MarkdownPage.excerpt_chars + 1
    assert MarkdownPage(body="short").excerpt == "short"


def test_parse_wikilinks_extracts_target_and_display() -> None:
    """Wikilinks split on ``|`` for display and drop ``#fragment`` from targets."""

    assert parse_wikilinks("see [[Target|the target]] and [[Other#heading]]") == {
        "Target": "the target",
        "Other": "",
    }


def test_body_save_builds_resolved_and_dangling_links(knowledge_tables: None) -> None:
    """Saving a body indexes its wikilinks, resolved against same-vault titles."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        target = Page.objects.create_in(vault, title="Target")
        source = Page.objects.create_in(vault, title="Source")
        MarkdownPage.objects.write_body(source, "see [[Target]] and [[Ghost]]")

    links = {link.target_text: link for link in Link._base_manager.filter(source_page=source)}
    assert links["Target"].is_resolved
    assert links["Target"].target_page_id == target.pk
    assert not links["Ghost"].is_resolved
    assert links["Ghost"].target_page_id is None


def test_editing_body_replaces_stale_links(knowledge_tables: None) -> None:
    """A re-saved body fully replaces the page's previous link set."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Source")
        MarkdownPage.objects.write_body(page, "[[Alpha]]")
        MarkdownPage.objects.write_body(page, "[[Beta]]")

    assert [link.target_text for link in Link._base_manager.filter(source_page=page)] == ["Beta"]


def test_backlinks_respect_source_page_read(knowledge_tables: None) -> None:
    """A backlink is visible only to actors who can read its source page."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)
    with actor_context(alice):
        target = Page.objects.create_in(vault, title="Target")
        linker = Page.objects.create_in(vault, title="Linker")
        MarkdownPage.objects.write_body(linker, "ref [[Target]]")

    assert Link.objects.as_user(alice).filter(target_page=target).count() == 1

    _grant(target, "viewer", bob)  # bob reads the target, not the linking source
    assert list(Link.objects.as_user(bob).filter(target_page=target)) == []


def test_patch_section_splices_guards_and_reindexes(knowledge_tables: None) -> None:
    """A section patch CAS-guards, records a revision, and rebuilds backlinks."""

    alice = create_user("alice")
    vault = vault_for(alice)
    body = "# Doc\n\nintro\n\n## Usage\n\nold usage\n\n## Notes\n\nkeep me\n"
    with actor_context(alice):
        target = Page.objects.create_in(vault, title="Target")
        page = Page.objects.create_in(vault, title="Guide")
        markdown = MarkdownPage.objects.write_body(page, body)

        with pytest.raises(StaleBodyError):
            MarkdownPage.objects.patch_section(page, ["Usage"], "replace", "blocked", expected_hash="stale")
        with pytest.raises(SectionNotFoundError):
            MarkdownPage.objects.patch_section(page, ["Missing"], "replace", "nope")

        with reversion.create_revision():
            patched = MarkdownPage.objects.patch_section(
                page,
                ["Usage"],
                "replace",
                "see [[Target]]",
                expected_hash=markdown.body_hash,
            )

    assert "see [[Target]]" in patched.body
    assert "old usage" not in patched.body
    assert "keep me" in patched.body  # sibling section untouched

    versions = list(patched.revisions)
    assert len(versions) == 1
    assert "see [[Target]]" in versions[0].field_dict["body"]

    links = {link.target_text: link for link in Link._base_manager.filter(source_page=page)}
    assert links["Target"].is_resolved
    assert links["Target"].target_page_id == target.pk


def test_replace_unique_swaps_exact_text_with_guard(knowledge_tables: None) -> None:
    """An exact-string replace CAS-guards and swaps the single occurrence."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Doc")
        markdown = MarkdownPage.objects.write_body(page, "alpha beta gamma")

        with pytest.raises(StaleBodyError):
            MarkdownPage.objects.replace_unique(page, "beta", "BETA", expected_hash="stale")
        updated = MarkdownPage.objects.replace_unique(page, "beta", "BETA", expected_hash=markdown.body_hash)

    assert updated.body == "alpha BETA gamma"


def test_append_and_prepend_grow_body_and_record_revisions(knowledge_tables: None) -> None:
    """Whole-page append/prepend join one blank apart, CAS-guard, and version."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Log")
        first = MarkdownPage.objects.write_body(page, "first line")

        with pytest.raises(StaleBodyError):
            MarkdownPage.objects.append(page, "blocked", expected_hash="stale")

        with reversion.create_revision():
            appended = MarkdownPage.objects.append(page, "second line", expected_hash=first.body_hash)
        with reversion.create_revision():
            prepended = MarkdownPage.objects.prepend(page, "header", expected_hash=appended.body_hash)

    assert appended.body == "first line\n\nsecond line"
    assert prepended.body == "header\n\nfirst line\n\nsecond line"

    versions = list(prepended.revisions)
    assert versions[0].field_dict["body"] == "header\n\nfirst line\n\nsecond line"


def test_mcp_body_write_records_a_revision(knowledge_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A section patch driven through the MCP engine is versioned like an HTTP edit.

    The MCP path runs GraphQL with ``request=None``, so ``RevisionMiddleware`` never
    opens the ``create_revision()`` block an HTTP write rides — the engine itself must.
    Drive ``patch_page_section`` through :func:`execute_under_actor` (the MCP engine
    entry point, not the HTTP schema) and assert a ``Version`` lands, bound to the actor,
    mirroring :func:`test_patch_section_splices_guards_and_reindexes`.
    """

    parts = {key: tuple(knowledge_schema.schemas["public"].get(key, ())) for key in SCHEMA_PART_KEYS}
    schemas = GraphQLSchemas([SchemaAddon({"public": parts})])
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Guide")
        # Written outside a revision block, so the only Version can come from the MCP edit.
        markdown = MarkdownPage.objects.write_body(page, "# Doc\n\nintro\n\n## Usage\n\nold usage\n")

    document = (
        "mutation ($page: ID!, $heading_path: [String!]!, $op: SectionOp!, $content: String!) "
        "{ patch_page_section(page: $page, heading_path: $heading_path, op: $op, content: $content) "
        "{ ok error_code markdown { body_hash } } }"
    )
    variables = {
        "page": str(page.sqid),
        "heading_path": ["Usage"],
        "op": "REPLACE",
        "content": "new usage through mcp",
    }
    with actor_context(alice):
        data = async_to_sync(execute_under_actor)("public", document, variables)

    assert data["patch_page_section"]["ok"] is True
    markdown.refresh_from_db()
    assert "new usage through mcp" in markdown.body

    versions = list(markdown.revisions)
    assert len(versions) == 1
    assert versions[0].field_dict["body"] == markdown.body
    assert versions[0].revision.user_id == alice.pk  # the MCP actor bound as the revision user


def test_lexical_retrieval_matches_title_and_body_scoped_to_actor(knowledge_tables: None) -> None:
    """The lexical provider matches title or body and row-scopes to the actor."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)
    with actor_context(alice):
        recipes = Page.objects.create_in(vault, title="Recipes")
        MarkdownPage.objects.write_body(recipes, "uses paprika and cumin")
        shopping = Page.objects.create_in(vault, title="Shopping")
        MarkdownPage.objects.write_body(shopping, "milk eggs bread")

    with actor_context(alice):
        assert [page.title for page in vault.retrieval.search("paprika")] == ["Recipes"]  # body match
        assert [page.title for page in vault.retrieval.search("shopping")] == ["Shopping"]  # title match

    with actor_context(bob):
        assert list(vault.retrieval.search("paprika")) == []  # bob reads nothing here


def _grant(resource: Any, relation: str, user: Any) -> None:
    """Write one direct relationship tuple for ``user`` on ``resource``."""

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(resource),
                relation=relation,
                subject=to_subject_ref(user),
            )
        ]
    )


def _rename(vault: Any, name: str) -> None:
    """Rename ``vault`` through the ordinary gated save path."""

    vault.name = name
    vault.save(update_fields=("name",))
