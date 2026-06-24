"""Tests for the knowledge addon's GraphQL surfaces."""

from __future__ import annotations

import importlib
from typing import Any

from rebac import actor_context

from tests.conftest import (
    MarkdownPage,
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
    """The Hasura insert mutations persist through Knowledge-owned factories."""

    alice = create_user("alice")
    schema = _schema("public")

    vault = result_data(
        execute_schema(
            schema,
            """
            mutation {
              insert_vaults_one(object: {name: "Research", icon: "book"}) {
                id name icon owner owner_label
              }
            }
            """,
            user=alice,
        )
    )["insert_vaults_one"]
    assert vault["name"] == "Research"
    assert vault["owner_label"] == "alice"

    page = result_data(
        execute_schema(
            schema,
            """
            mutation CreatePage($vault: ID!) {
              insert_pages_one(object: {vault: $vault, title: "Reading list"}) {
                id title kind vault vault_label parent created_by_label
              }
            }
            """,
            {"vault": vault["id"]},
            user=alice,
        )
    )["insert_pages_one"]
    assert page["title"] == "Reading list"
    assert page["kind"] == "note"
    assert page["vault_label"] == "Research"
    assert page["parent"] is None


def test_anonymous_create_vault_is_denied_with_a_code(knowledge_tables: None) -> None:
    """Anonymous mutation calls surface the standard permission code."""

    result = execute_schema(
        _schema("public"),
        'mutation { insert_vaults_one(object: {name: "x"}) { id } }',
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
            query PagesIn($vault: String!) {
              pages(where: {vault: {_eq: $vault}}) { title }
            }
            """,
            {"vault": _public_id(research)},
            user=alice,
        )
    )["pages"]
    assert [row["title"] for row in filtered] == ["Reading list"]


def test_detail_query_resolves_raw_sqid(knowledge_tables: None) -> None:
    """Typed detail fields refetch a public object by raw sqid."""

    alice = create_user("alice")
    vault = vault_for(alice, name="Node vault")

    data = result_data(
        execute_schema(
            _schema("public"),
            """
            query Vault($id: String!) {
              vaults_by_pk(id: $id) {
                id
                name
              }
            }
            """,
            {"id": str(vault.sqid)},
            user=alice,
        )
    )

    assert data["vaults_by_pk"] == {"id": str(vault.sqid), "name": "Node vault"}


def test_update_page_body_round_trip_and_stale_guard(knowledge_tables: None) -> None:
    """Body writes return the sidecar facts and reject stale hashes."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        page = Page.objects.create_in(vault, title="Draft")
    schema = _schema("public")
    page_id = _public_id(page)

    written = result_data(
        execute_schema(
            schema,
            """
            mutation Write($page: ID!) {
              update_page_body(page: $page, body: "one two three") {
                ok error_code
                markdown { body_hash word_count excerpt page }
              }
            }
            """,
            {"page": page_id},
            user=alice,
        )
    )["update_page_body"]
    assert written["ok"] is True
    assert written["markdown"]["word_count"] == 3
    assert written["markdown"]["excerpt"] == "one two three"

    stale = result_data(
        execute_schema(
            schema,
            """
            mutation Stale($page: ID!) {
              update_page_body(page: $page, body: "other", expected_hash: "stale") {
                ok error_code markdown { id }
              }
            }
            """,
            {"page": page_id},
            user=alice,
        )
    )["update_page_body"]
    assert stale["ok"] is False
    assert stale["error_code"] == "STALE_BODY"

    detail = result_data(
        execute_schema(
            schema,
            """
            query Detail($id: String!) {
              pages_by_pk(id: $id) { title markdown { word_count } }
            }
            """,
            {"id": page_id},
            user=alice,
        )
    )["pages_by_pk"]
    assert detail["markdown"]["word_count"] == 3


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
              update_page_body(page: $page, body: "nope") { ok error_code }
            }
            """,
            {"page": _public_id(folder)},
            user=alice,
        )
    )["update_page_body"]
    assert payload["ok"] is False
    assert payload["error_code"] == "UNSUPPORTED_KIND"


def test_page_backlinks_list_resolved_sources(knowledge_tables: None) -> None:
    """The page detail surface exposes resolved incoming wikilinks."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        target = Page.objects.create_in(vault, title="Target")
        linker = Page.objects.create_in(vault, title="Linker")
        MarkdownPage.objects.write_body(linker, "ref [[Target|see target]]")

    detail = result_data(
        execute_schema(
            _schema("public"),
            """
            query Backlinks($id: String!) {
              pages_by_pk(id: $id) { backlinks { title display_text } }
            }
            """,
            {"id": _public_id(target)},
            user=alice,
        )
    )["pages_by_pk"]
    assert detail["backlinks"] == [{"title": "Linker", "display_text": "see target"}]


def test_crud_update_is_row_scoped(knowledge_tables: None) -> None:
    """The generated update mutation denies actors outside the row scope."""

    alice = create_user("alice")
    bob = create_user("bob")
    vault = vault_for(alice)
    schema = _schema("public")

    denied = execute_schema(
        schema,
        """
        mutation Rename($id: String!) {
          update_vaults_by_pk(pk_columns: {id: $id}, _set: {name: "Taken over"}) { id }
        }
        """,
        {"id": _public_id(vault)},
        user=bob,
    )
    assert denied.errors is not None

    renamed = result_data(
        execute_schema(
            schema,
            """
            mutation Rename($id: String!) {
              update_vaults_by_pk(pk_columns: {id: $id}, _set: {name: "Lab notes"}) { name }
            }
            """,
            {"id": _public_id(vault)},
            user=alice,
        )
    )["update_vaults_by_pk"]
    assert renamed["name"] == "Lab notes"


def test_delete_vault_previews_blast_radius(knowledge_tables: None) -> None:
    """Vault deletion previews the cascade before a confirmed delete."""

    alice = create_user("alice")
    vault = vault_for(alice)
    with actor_context(alice):
        Page.objects.create_in(vault, title="Reading list")
    schema = _schema("public")
    vault_id = _public_id(vault)

    preview = result_data(
        execute_schema(
            schema,
            """
            mutation Preview($id: ID!) {
              delete_vault(id: $id) {
                totalDeletedCount hasBlockers deleted { label count }
              }
            }
            """,
            {"id": vault_id},
            user=alice,
        )
    )["delete_vault"]
    assert preview["totalDeletedCount"] >= 2
    assert preview["hasBlockers"] is False
    assert {group["label"] for group in preview["deleted"]} >= {"vaults", "pages"}
    assert Vault.objects.as_user(alice).exists()

    result_data(
        execute_schema(
            schema,
            """
            mutation Confirm($id: ID!) {
              delete_vault(id: $id, confirm: true) { totalDeletedCount }
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
            "query { pages { title } }",
            user=user,
        )
    )["pages"]
    return [row["title"] for row in rows]


def _public_id(instance: Any) -> str:
    """Return the public id for one node instance."""

    return str(instance.sqid)
