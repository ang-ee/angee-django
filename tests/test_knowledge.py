"""Tests for knowledge models, manager factories, and REBAC scoping."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from rebac import (
    MissingActorError,
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
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


@pytest.mark.parametrize("worker", [False, True])
def test_create_for_sets_owner_and_audit_stamps(composed_tables: None, worker: bool) -> None:
    """The vault factory persists ownership and stamps the creating actor."""

    alice = create_user("alice")

    with system_context(reason="test.knowledge.worker") if worker else nullcontext(), actor_context(alice):
        vault = Vault.objects.create_for(alice, name="Research")

    assert vault.owner == alice
    assert vault.created_by == alice
    assert str(vault.sqid).startswith("vlt_")


def test_factory_rows_stay_gated_after_creation(composed_tables: None) -> None:
    """The create-time elevation must not survive on the returned instance."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)

    assert getattr(vault, "_rebac_sudo_reason", None) is None
    with actor_context(bob), pytest.raises(PermissionDenied):
        vault.as_user(bob).save(update_fields=("name",))


def test_create_for_refuses_foreign_owner(composed_tables: None) -> None:
    """A vault may only be created for the acting user."""

    alice = create_user("alice")
    bob = create_user("bob")

    with actor_context(alice), pytest.raises(PermissionDenied):
        Vault.objects.create_for(bob, name="Gift")


def test_create_vault_requires_an_authenticated_actor(composed_tables: None) -> None:
    """Anonymous and actor-less calls cannot create vaults."""

    anonymous = AnonymousUser()

    with actor_context(anonymous), pytest.raises(PermissionDenied):
        Vault.objects.create_for(anonymous, name="Nope")
    with pytest.raises(MissingActorError):
        Vault.objects.create_for(None, name="Nope")


def test_vault_scope_hides_other_owners(composed_tables: None) -> None:
    """Actor-scoped vault reads only return the actor's own grants."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault_for(alice, name="Research")

    assert [vault.name for vault in Vault.objects.as_user(alice)] == ["Research"]
    assert list(Vault.objects.as_user(bob)) == []


def test_create_in_requires_vault_write(composed_tables: None) -> None:
    """Only actors who can write the vault may add pages to it."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)

    with actor_context(bob), pytest.raises(PermissionDenied):
        Page.objects.create_in(vault, title="Intruder")

    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Reading list")

    assert page.kind == Page.PageKind.NOTE
    assert page.created_by == alice
    assert [row.title for row in Page.objects.as_user(alice)] == ["Reading list"]
    assert list(Page.objects.as_user(bob)) == []


def test_create_in_refuses_cross_vault_parent(composed_tables: None) -> None:
    """A parent from another vault would leak access across the boundary."""

    alice = create_user("alice")
    research = vault_for(alice, name="Research")
    journal = vault_for(alice, name="Journal")
    with actor_context(alice):
        folder = Page.objects.create_in(journal, title="Projects", kind=Page.PageKind.FOLDER)
        with pytest.raises(ValueError, match="same vault"):
            Page.objects.create_in(research, parent=folder, title="Escapee")


@pytest.mark.parametrize("kind", ["folder", "note"])
def test_create_in_reports_duplicate_title_as_validation(composed_tables: None, kind: str) -> None:
    alice = create_user("duplicate-title")
    vault = vault_for(alice)
    with actor_context(alice):
        Page.objects.create_in(vault, title="Existing", kind=kind)
        with pytest.raises(ValidationError):
            Page.objects.create_in(vault, title="Existing", kind=kind)


def test_page_inherits_vault_read(composed_tables: None) -> None:
    """A vault viewer grant reaches every page through ``vault->read``."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)
    with actor_context(alice):
        Page.objects.create_in(vault, title="Shared notes")

    _grant(vault, "viewer", bob)

    assert [row.title for row in Page.objects.as_user(bob)] == ["Shared notes"]
    assert [row.name for row in Vault.objects.as_user(bob)] == ["Research"]


def test_vault_viewer_share_is_managed_by_share_holders(composed_tables: None) -> None:
    """The vault's only grantable relation is ``viewer``, managed under ``share``."""

    alice = create_user("alice")
    editor = create_user("editor")
    bob = create_user("bob")
    vault = vault_for(alice)
    _grant(vault, "editor", editor)

    with actor_context(editor), pytest.raises(PermissionDenied):
        vault.with_actor(editor).grant_record_access("viewer", bob)
    assert list(Vault.objects.as_user(bob)) == []

    with actor_context(alice):
        vault.with_actor(alice).grant_record_access("viewer", bob)
        assert [row.name for row in Vault.objects.as_user(bob)] == ["Research"]
        vault.revoke_record_access("viewer", bob)
        assert list(Vault.objects.as_user(bob)) == []
        with pytest.raises(ValueError):
            vault.grant_record_access("editor", bob)


def test_page_inherits_parent_read(composed_tables: None) -> None:
    """A grant on a folder page cascades to its children via ``parent->read``."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)
    with actor_context(alice):
        folder = Page.objects.create_in(vault, title="Projects", kind=Page.PageKind.FOLDER)
        Page.objects.create_in(vault, parent=folder, title="Roadmap")
        Page.objects.create_in(vault, title="Private")

    _grant(folder, "viewer", bob)

    assert {row.title for row in Page.objects.as_user(bob)} == {"Projects", "Roadmap"}


def test_folder_delete_cascades_past_foreign_children(composed_tables: None) -> None:
    """Deleting a folder passes the gate on children other users created."""

    alice = create_user("alice")
    editor = create_user("editor")
    vault = vault_for(alice)
    _grant(vault, "editor", editor)
    with actor_context(alice):
        folder = Page.objects.create_in(vault, title="Projects", kind=Page.PageKind.FOLDER)
    with actor_context(editor):
        Page.objects.create_in(vault, parent=folder, title="Editor child")

    with actor_context(alice):
        Page.objects.as_user(alice).get(title="Projects").delete()

    with actor_context(alice):
        assert not Page.objects.as_user(alice).exists()


def test_roles_grant_cross_vault_reach(composed_tables: None) -> None:
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


@pytest.mark.parametrize("worker", [False, True])
def test_write_body_creates_updates_and_guards(composed_tables: None, worker: bool) -> None:
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
    with (
        system_context(reason="test.knowledge.worker") if worker else nullcontext(),
        actor_context(bob),
        pytest.raises(PermissionDenied),
    ):
        MarkdownPage.objects.write_body(page, "vandalised")


def test_write_body_saves_title_atomically_and_validates_it(composed_tables: None) -> None:
    """A form save cannot persist its title or body without the other half."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Draft")
        Page.objects.create_in(vault, title="Taken")
        saved = MarkdownPage.objects.write_body(page, "Original", title="Saved")
        assert saved.title == "Saved"
        for title in ("", "Taken", "x" * 513):
            with pytest.raises(ValidationError):
                MarkdownPage.objects.write_body(page, "Must not persist", title=title, expected_hash=saved.body_hash)
        with pytest.raises(StaleBodyError):
            MarkdownPage.objects.write_body(page, "Stale body", title="Stale title", expected_hash="stale")
        stored = MarkdownPage.objects.get(pk=page.pk)
        assert (stored.title, stored.body, stored.body_hash) == ("Saved", "Original", saved.body_hash)


def test_write_body_rejects_bodyless_kinds(composed_tables: None) -> None:
    """Folder pages carry no markdown body sidecar."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        folder = Page.objects.create_in(vault, title="Projects", kind=Page.PageKind.FOLDER)
        with pytest.raises(UnsupportedPageKindError):
            MarkdownPage.objects.write_body(folder, "nope")


def test_excerpt_truncates_long_bodies(composed_tables: None) -> None:
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


def test_native_markdown_wikilinks_index_encoded_titles_and_ignore_code(composed_tables: None) -> None:
    alice = create_user("markdown-links")
    vault = vault_for(alice)
    with actor_context(alice):
        target = Page.objects.create_in(vault, title="Guide # [one]")
        source = Page.objects.create_in(vault, title="Source", body=(
            f"[A **bold** guide](<{target.wikilink_url}>)\n\n"
            "`[[Inline code]]`\n\n```md\n[[Code block]]\n```"
        ))
    link = Link._base_manager.get(source_page=source)
    assert (link.target_text, link.display_text, link.target_page_id) == (target.title, "A bold guide", target.pk)


def test_body_save_builds_resolved_and_dangling_links(composed_tables: None) -> None:
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


def test_editing_body_replaces_stale_links(composed_tables: None) -> None:
    """A re-saved body fully replaces the page's previous link set."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Source")
        MarkdownPage.objects.write_body(page, "[[Alpha]]")
        MarkdownPage.objects.write_body(page, "[[Beta]]")

    assert [link.target_text for link in Link._base_manager.filter(source_page=page)] == ["Beta"]


def test_target_lifecycle_refreshes_exact_title_links_including_bulk_trash(composed_tables: None) -> None:
    owner = create_user("link-lifecycle")
    vault = vault_for(owner)
    with actor_context(owner):
        source = Page.objects.create_in(vault, title="Source", body="[[Target]] [[target]] [[Renamed]]")
        target = Page.objects.create_in(vault, title="Target")

        def resolved() -> dict[str, int | None]:
            return dict(Link.objects.filter(source_page=source).values_list("target_text", "target_page_id"))

        assert resolved() == {"Target": target.pk, "target": None, "Renamed": None}
        target.title = "Renamed"
        target.save(update_fields=["title"])
        assert resolved() == {"Target": None, "target": None, "Renamed": target.pk}
        Page.objects.filter(pk=target.pk).trash()
        assert set(resolved().values()) == {None}
        Page.objects.filter(pk=target.pk).restore()
        assert resolved()["Renamed"] == target.pk


def test_backlinks_respect_source_page_read(composed_tables: None) -> None:
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


def test_patch_section_splices_guards_and_reindexes(composed_tables: None) -> None:
    """A section patch CAS-guards and rebuilds backlinks."""

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

    links = {link.target_text: link for link in Link._base_manager.filter(source_page=page)}
    assert links["Target"].is_resolved
    assert links["Target"].target_page_id == target.pk


def test_replace_unique_swaps_exact_text_with_guard(composed_tables: None) -> None:
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


def test_append_and_prepend_grow_body(composed_tables: None) -> None:
    """Whole-page append/prepend join one blank apart and CAS-guard."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Log")
        first = MarkdownPage.objects.write_body(page, "first line")

        with pytest.raises(StaleBodyError):
            MarkdownPage.objects.append(page, "blocked", expected_hash="stale")

        appended = MarkdownPage.objects.append(page, "second line", expected_hash=first.body_hash)
        prepended = MarkdownPage.objects.prepend(page, "header", expected_hash=appended.body_hash)

    assert appended.body == "first line\n\nsecond line"
    assert prepended.body == "header\n\nfirst line\n\nsecond line"


def test_mcp_body_write_updates_section(composed_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A section patch driven through the MCP engine updates the body."""

    parts = {key: tuple(knowledge_schema.schemas["public"].get(key, ())) for key in SCHEMA_PART_KEYS}
    schemas = GraphQLSchemas([SchemaAddon({"public": parts})])
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Guide")
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


def test_lexical_retrieval_matches_title_and_body_scoped_to_actor(composed_tables: None) -> None:
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
