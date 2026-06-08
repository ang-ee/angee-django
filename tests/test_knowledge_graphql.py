"""Tests for the knowledge addon's GraphQL surfaces."""

from __future__ import annotations

import importlib
from typing import Any

from rebac import actor_context
from strawberry import relay

from tests.conftest import (
    Page,
    Vault,
    addon_schema,
    create_user,
    execute_schema,
    result_data,
    vault_for,
)

knowledge_schema = importlib.import_module("angee.knowledge.schema")


def test_create_vault_and_page_flow(knowledge_tables: None) -> None:
    """The custom create mutations persist actor-owned rows."""

    alice = create_user("alice")
    schema = _schema("public")

    vault = result_data(
        execute_schema(
            schema,
            """
            mutation {
              createVault(data: {name: "Research", icon: "book"}) {
                id name icon owner ownerLabel
              }
            }
            """,
            user=alice,
        )
    )["createVault"]
    assert vault["name"] == "Research"
    assert vault["ownerLabel"] == "alice"

    page = result_data(
        execute_schema(
            schema,
            """
            mutation CreatePage($vault: ID!) {
              createPage(data: {vault: $vault, title: "Reading list"}) {
                id title kind vault vaultLabel parent createdByLabel
              }
            }
            """,
            {"vault": vault["id"]},
            user=alice,
        )
    )["createPage"]
    assert page["title"] == "Reading list"
    assert page["kind"] == "note"
    assert page["vaultLabel"] == "Research"
    assert page["parent"] is None


def test_anonymous_create_vault_is_denied_with_a_code(knowledge_tables: None) -> None:
    """Anonymous mutation calls surface the standard permission code."""

    result = execute_schema(
        _schema("public"),
        'mutation { createVault(data: {name: "x"}) { id } }',
    )

    assert result.errors is not None
    assert result.errors[0].extensions["code"] == "PERMISSION_DENIED"


def test_pages_query_is_actor_scoped_and_vault_filtered(knowledge_tables: None) -> None:
    """The pages connection narrows to the actor's scope and one vault."""

    alice = create_user("alice")
    bob = create_user("bob")
    research = vault_for(alice, name="Research")
    journal = vault_for(alice, name="Journal")
    with actor_context(alice):
        Page.objects.create_in(research, title="Reading list")
        Page.objects.create_in(journal, title="Monday")
    schema = _schema("public")

    titles = _page_titles(schema, alice)
    assert titles == ["Monday", "Reading list"]
    assert _page_titles(schema, bob) == []

    filtered = result_data(
        execute_schema(
            schema,
            """
            query PagesIn($vault: ID!) {
              pages(filters: {vault: $vault}) { results { title } }
            }
            """,
            {"vault": _global_id("VaultType", research)},
            user=alice,
        )
    )["pages"]["results"]
    assert [row["title"] for row in filtered] == ["Reading list"]


def test_update_page_body_round_trip_and_stale_guard(knowledge_tables: None) -> None:
    """Body writes return the sidecar facts and reject stale hashes."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Draft")
    schema = _schema("public")
    page_id = _global_id("PageType", page)

    written = result_data(
        execute_schema(
            schema,
            """
            mutation Write($page: ID!) {
              updatePageBody(page: $page, body: "one two three") {
                ok errorCode
                markdown { bodyHash wordCount excerpt page }
              }
            }
            """,
            {"page": page_id},
            user=alice,
        )
    )["updatePageBody"]
    assert written["ok"] is True
    assert written["markdown"]["wordCount"] == 3
    assert written["markdown"]["excerpt"] == "one two three"

    stale = result_data(
        execute_schema(
            schema,
            """
            mutation Stale($page: ID!) {
              updatePageBody(page: $page, body: "other", expectedHash: "stale") {
                ok errorCode markdown { id }
              }
            }
            """,
            {"page": page_id},
            user=alice,
        )
    )["updatePageBody"]
    assert stale["ok"] is False
    assert stale["errorCode"] == "STALE_BODY"

    detail = result_data(
        execute_schema(
            schema,
            """
            query Detail($id: ID!) {
              page(id: $id) { title markdown { wordCount } }
            }
            """,
            {"id": page_id},
            user=alice,
        )
    )["page"]
    assert detail["markdown"]["wordCount"] == 3


def test_update_page_body_reports_unsupported_kind(knowledge_tables: None) -> None:
    """Bodyless kinds surface a typed error code, not a server fault."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        folder = Page.objects.create_in(vault, title="Projects", kind=Page.Kind.FOLDER)

    payload = result_data(
        execute_schema(
            _schema("public"),
            """
            mutation Write($page: ID!) {
              updatePageBody(page: $page, body: "nope") { ok errorCode }
            }
            """,
            {"page": _global_id("PageType", folder)},
            user=alice,
        )
    )["updatePageBody"]
    assert payload["ok"] is False
    assert payload["errorCode"] == "UNSUPPORTED_KIND"


def test_crud_update_is_row_scoped(knowledge_tables: None) -> None:
    """The generated update mutation denies actors outside the row scope."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)
    schema = _schema("public")

    denied = execute_schema(
        schema,
        """
        mutation Rename($id: ID!) {
          updateVault(data: {id: $id, name: "Taken over"}) { id }
        }
        """,
        {"id": _global_id("VaultType", vault)},
        user=bob,
    )
    assert denied.errors is not None

    renamed = result_data(
        execute_schema(
            schema,
            """
            mutation Rename($id: ID!) {
              updateVault(data: {id: $id, name: "Lab notes"}) { name }
            }
            """,
            {"id": _global_id("VaultType", vault)},
            user=alice,
        )
    )["updateVault"]
    assert renamed["name"] == "Lab notes"


def test_delete_vault_previews_blast_radius(knowledge_tables: None) -> None:
    """Vault deletion previews the cascade before a confirmed delete."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        Page.objects.create_in(vault, title="Reading list")
    schema = _schema("public")
    vault_id = _global_id("VaultType", vault)

    preview = result_data(
        execute_schema(
            schema,
            """
            mutation Preview($id: ID!) {
              deleteVault(id: $id) {
                totalDeletedCount hasBlockers deleted { label count }
              }
            }
            """,
            {"id": vault_id},
            user=alice,
        )
    )["deleteVault"]
    assert preview["totalDeletedCount"] >= 2
    assert preview["hasBlockers"] is False
    assert {group["label"] for group in preview["deleted"]} >= {"vaults", "pages"}
    assert Vault.objects.as_user(alice).exists()

    result_data(
        execute_schema(
            schema,
            """
            mutation Confirm($id: ID!) {
              deleteVault(id: $id, confirm: true) { totalDeletedCount }
            }
            """,
            {"id": vault_id},
            user=alice,
        )
    )
    assert not Vault.objects.as_user(alice).exists()


def test_schema_exposes_revisions_and_subscriptions(knowledge_tables: None) -> None:
    """The SDL carries the revision query and console change subscriptions."""

    public_sdl = _schema("public").as_str()
    console_sdl = _schema("console").as_str()

    assert "markdownPageRevisions(" in public_sdl
    assert "pageChanged" not in public_sdl
    assert "pageChanged" in console_sdl
    assert "markdownPageChanged" in console_sdl


def _schema(name: str) -> Any:
    """Build one knowledge-only GraphQL schema bucket."""

    return addon_schema(knowledge_schema.schemas, name)


def _page_titles(schema: Any, user: Any) -> list[str]:
    """Return the page titles visible to ``user`` through the connection."""

    rows = result_data(
        execute_schema(
            schema,
            "query { pages { results { title } } }",
            user=user,
        )
    )["pages"]["results"]
    return [row["title"] for row in rows]


def _global_id(type_name: str, instance: Any) -> str:
    """Return the relay global id for one node instance."""

    return str(relay.GlobalID(type_name=type_name, node_id=str(instance.sqid)))
