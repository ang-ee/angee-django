"""A vault manager trashes and restores pages; everyone else loses them entirely."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from rebac import actor_context, system_context
from rebac.roles import grant, revoke

from angee.graphql import records
from tests.conftest import Page, Vault, addon_schema, create_user, execute_schema, result_data, vault_for

knowledge_schema = importlib.import_module("angee.knowledge.schema")

pytestmark = pytest.mark.usefixtures("composed_tables")

TRASH = """
mutation Trash($id: ID!, $reason: String!, $confirm: Boolean!) {
  trash_record(target_type: "knowledge/page", target_id: $id, reason: $reason, confirm: $confirm) {
    ok message validation_errors
  }
}
"""
RESTORE = """
mutation Restore($id: ID!) {
  restore_record(target_type: "knowledge/page", target_id: $id, confirm: true) { ok message }
}
"""


@pytest.fixture
def vault() -> Any:
    """A vault its owner manages, read by a viewer and written by an editor."""

    owner, reader, editor = (create_user(f"trash-{role}") for role in ("owner", "reader", "editor"))
    vault = vault_for(owner)
    with system_context(reason="test.knowledge.trash.roles"):
        grant(actor=reader, role="knowledge/role:vault_viewer")
        grant(actor=editor, role="knowledge/role:vault_editor")
    with actor_context(owner):
        folder = Page.objects.create_in(vault, title="Projects", kind=Page.PageKind.FOLDER)
        Page.objects.create_in(vault, title="Plan", parent=folder, body="octopus roadmap")
        Page.objects.create_in(vault, title="Notes", body="octopus sightings")
    vault.owner_user, vault.reader, vault.editor, vault.folder = owner, reader, editor, folder
    return vault


def test_manager_trashes_a_folder_and_readers_lose_its_whole_subtree(vault: Any) -> None:
    """Lists, counts, search and by-id reads drop the trashed folder and the child trashed with it."""

    assert _visible(vault.reader) == ["Notes", "Plan", "Projects"]
    outcome = _run(TRASH, {"id": vault.folder.sqid, "reason": "  Superseded  ", "confirm": True}, vault.owner_user)
    assert outcome == {"ok": True, "message": "Moved to the trash.", "validation_errors": None}

    for user in (vault.reader, vault.editor):
        assert _visible(user) == ["Notes"]
        data = _query(
            """
            query Reads($vault: ID!, $id: String!) {
              pages_aggregate { aggregate { count } }
              search_pages(vault: $vault, query: "octopus") { title }
              pages_by_pk(id: $id) { title }
            }
            """,
            {"vault": vault.sqid, "id": vault.folder.sqid},
            user,
        )
        assert data == {
            "pages_aggregate": {"aggregate": {"count": 1}},
            "search_pages": [{"title": "Notes"}],
            "pages_by_pk": None,
        }

    removed = _query(
        """
        query Removed {
          pages(where: {is_trashed: {_eq: true}}) {
            title is_trashed trash_reason trashed_by_label trashed_at permissions
          }
        }
        """,
        {},
        vault.owner_user,
    )["pages"]
    assert sorted((row["title"], row["is_trashed"], row["trash_reason"]) for row in removed) == [
        ("Plan", True, "Superseded"),
        ("Projects", True, "Superseded"),
    ]
    assert len({row["trashed_at"] for row in removed}) == 1
    assert all(row["trashed_by_label"] and "delete" in row["permissions"] for row in removed)
    search = _query(
        'query Search($vault: ID!) { search_pages(vault: $vault, query: "octopus") { title } }',
        {"vault": vault.sqid},
        vault.owner_user,
    )
    assert search == {"search_pages": [{"title": "Notes"}]}

    assert _run(RESTORE, {"id": vault.folder.sqid}, vault.owner_user)["ok"] is True
    assert _visible(vault.reader) == ["Notes", "Plan", "Projects"]
    restored = Page.objects.as_user(vault.owner_user).get(pk=vault.folder.pk)
    assert (restored.is_trashed, restored.trashed_by_id, restored.trash_reason) == (False, None, "")


def test_only_page_deleters_get_the_trash_verbs(vault: Any) -> None:
    """An editor without delete authority cannot trash, even the page they wrote."""

    with actor_context(vault.editor):
        own = Page.objects.create_in(vault, title="Draft", body="mine")
    readable = _query("query { pages { title permissions } }", {}, vault.editor)["pages"]
    assert all("delete" not in row["permissions"] for row in readable)

    refused = _run(TRASH, {"id": own.sqid, "reason": "", "confirm": True}, vault.editor)
    assert refused["ok"] is False
    unconfirmed = _run(TRASH, {"id": own.sqid, "reason": "", "confirm": False}, vault.owner_user)
    assert unconfirmed["ok"] is False and "confirm" in unconfirmed["validation_errors"]
    oversized = _run(TRASH, {"id": own.sqid, "reason": "x" * 1001, "confirm": True}, vault.owner_user)
    assert oversized["ok"] is False and "reason" in oversized["validation_errors"]
    assert "Draft" in _visible(vault.editor)

    assert _run(TRASH, {"id": own.sqid, "reason": "", "confirm": True}, vault.owner_user)["ok"] is True
    assert _run(RESTORE, {"id": own.sqid}, vault.editor)["ok"] is False
    assert _run(TRASH, {"id": own.sqid, "reason": "", "confirm": True}, vault.owner_user)["ok"] is False


def test_restore_never_regrants_access_revoked_meanwhile(vault: Any) -> None:
    """Restoring brings a page back under today's permissions only."""

    assert _run(TRASH, {"id": vault.folder.sqid, "reason": "", "confirm": True}, vault.owner_user)["ok"] is True
    with system_context(reason="test.knowledge.trash.revoke"):
        revoke(actor=vault.reader, role="knowledge/role:vault_viewer")
    assert _run(RESTORE, {"id": vault.folder.sqid}, vault.owner_user)["ok"] is True
    assert _visible(vault.reader) == []
    assert _visible(vault.editor) == ["Notes", "Plan", "Projects"]


def test_trashed_title_is_free_until_restore(vault: Any) -> None:
    """A trashed page reserves no title; restoring refuses one taken meanwhile."""

    assert _run(TRASH, {"id": vault.folder.sqid, "reason": "", "confirm": True}, vault.owner_user)["ok"] is True
    with actor_context(vault.editor):
        Page.objects.create_in(vault, title="Projects", kind=Page.PageKind.FOLDER)
    refused = _run(RESTORE, {"id": vault.folder.sqid}, vault.owner_user)
    assert refused["ok"] is False
    assert Page.objects.as_user(vault.owner_user).get(pk=vault.folder.pk).is_trashed


def test_folder_restore_brings_back_only_pages_trashed_with_it(vault: Any) -> None:
    """A page trashed on its own stays trashed; a child waits for its folder."""

    plan = Page.objects.as_user(vault.owner_user).get(title="Plan")
    with actor_context(vault.owner_user):
        Page.objects.create_in(vault, title="Archive", parent=plan, kind=Page.PageKind.FOLDER)
    assert _run(TRASH, {"id": plan.sqid, "reason": "first", "confirm": True}, vault.owner_user)["ok"] is True
    assert _run(TRASH, {"id": vault.folder.sqid, "reason": "later", "confirm": True}, vault.owner_user)["ok"] is True
    archive = Page.objects.as_user(vault.owner_user).get(title="Archive")
    assert (archive.is_trashed, archive.trash_reason) == (True, "first")
    assert _run(RESTORE, {"id": archive.sqid}, vault.owner_user)["ok"] is False

    assert _run(RESTORE, {"id": vault.folder.sqid}, vault.owner_user)["ok"] is True
    assert _visible(vault.reader) == ["Notes", "Projects"]
    assert _run(RESTORE, {"id": plan.sqid}, vault.owner_user)["ok"] is True
    assert _visible(vault.reader) == ["Archive", "Notes", "Plan", "Projects"]


def test_readers_keep_deep_pages_while_a_sibling_is_trashed(vault: Any) -> None:
    """Concealment adds no recursion: pages deeper than the permission depth stay readable."""

    parent = vault.folder
    with actor_context(vault.owner_user):
        for depth in range(12):
            parent = Page.objects.create_in(vault, title=f"Level {depth}", parent=parent, kind=Page.PageKind.FOLDER)
    notes = Page.objects.as_user(vault.owner_user).get(title="Notes")
    assert _run(TRASH, {"id": notes.sqid, "reason": "", "confirm": True}, vault.owner_user)["ok"] is True
    visible = _visible(vault.reader)
    assert "Level 11" in visible and "Notes" not in visible


def test_vault_clone_skips_a_trashed_subtree(vault: Any) -> None:
    """A manager's clone copies only pages outside the trash and below no trashed page."""

    assert _run(TRASH, {"id": vault.folder.sqid, "reason": "", "confirm": True}, vault.owner_user)["ok"] is True
    with actor_context(vault.owner_user):
        clone = Vault.objects.create_from(vault, name="Copy")
        titles = sorted(Page.objects.filter(vault=clone).values_list("title", flat=True))
    assert titles == ["Notes"]


def _visible(user: Any) -> list[str]:
    """Return the page titles ``user`` reads through the pages list, sorted."""

    return sorted(row["title"] for row in _query("query { pages { title } }", {}, user)["pages"])


def _query(query: str, variables: dict[str, Any], user: Any) -> dict[str, Any]:
    """Execute against the knowledge schema composed with the shared record verbs."""

    return result_data(execute_schema(_schema(), query, variables, user=user))


def _run(mutation: str, variables: dict[str, Any], user: Any) -> dict[str, Any]:
    """Run one trash verb and return its action result."""

    data = _query(mutation, variables, user)
    return next(iter(data.values()))


def _schema() -> Any:
    return addon_schema(knowledge_schema.schemas, "public", records.schemas)
