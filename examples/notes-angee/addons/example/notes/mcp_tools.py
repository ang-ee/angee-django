"""Notes tools for the MCP server.

``read_note``/``update_note``/``create_note`` gate on a REBAC permission through
rebac's ``rebac_mcp_tool`` decorator — the authenticated actor (bracketed around the
call by ``angee.mcp.middleware.ActorMiddleware`` and read through rebac's ambient
``current_actor``) is checked against the note (read/write) or the ``create``
permission before the body runs, and the body executes inside that actor's context.
``list_notes`` has no single target resource, so it runs under the same ambient actor
and filters by queryset scoping. The agents addon owns mounting and bearer→actor
authentication; this module owns only the notes shape. The bodies run directly on the
event loop using rebac's row-scoped async ORM (``async for`` / ``afrom_public_id`` /
``acreate`` / ``asave``) — no thread hop.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from fastmcp import FastMCP
from rebac import current_actor
from rebac.mcp import rebac_mcp_tool

from angee.base.mixins import actor_user_id


def register(server: FastMCP) -> None:
    """Register the notes tools on the MCP server."""

    @server.tool()
    async def list_notes(limit: int = 20) -> list[dict[str, Any]]:
        """List the caller's most recently updated notes (up to ``limit``)."""

        note = apps.get_model("notes", "Note")
        return [_summary(row) async for row in note.objects.all()[: max(1, min(limit, 100))]]

    @server.tool()
    @rebac_mcp_tool(resource_type="notes/note", action="read", id_arg="sqid")
    async def read_note(sqid: str) -> dict[str, Any]:
        """Return one note in full by its public id (read-gated for the caller)."""

        note = apps.get_model("notes", "Note")
        row = await note.objects.afrom_public_id(sqid)
        if row is None:
            raise ValueError(f"Note {sqid!r} was not found.")
        return _detail(row)

    @server.tool()
    @rebac_mcp_tool(resource_type="notes/note", action="write", id_arg="sqid")
    async def update_note(
        sqid: str,
        title: str | None = None,
        body: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        is_starred: bool | None = None,
    ) -> dict[str, Any]:
        """Update the given fields of a note the caller may write, and return it."""

        note = apps.get_model("notes", "Note")
        applied = {
            field: value
            for field, value in (
                ("title", title),
                ("body", body),
                ("status", status),
                ("tags", tags),
                ("is_starred", is_starred),
            )
            if value is not None
        }
        row = await note.objects.afrom_public_id(sqid)
        if row is None:
            raise ValueError(f"Note {sqid!r} was not found.")
        for field, value in applied.items():
            setattr(row, field, value)
        row.updated_by_id = actor_user_id(current_actor())
        await row.asave(update_fields=[*applied, "updated_by", "updated_at"])
        return _detail(row)

    @server.tool()
    @rebac_mcp_tool(resource_type="notes/note", action="create")
    async def create_note(
        title: str,
        body: str = "",
        status: str = "draft",
        tags: list[str] | None = None,
        is_starred: bool = False,
    ) -> dict[str, Any]:
        """Create a note attributed to the calling user and return it.

        Gated by ``create = authenticated`` (the decorator preflights it, and rebac's
        ``pre_save`` create gate authorises it too — both via the built-in actor
        term), so the insert runs scoped, with no elevation. The row is owned by its
        creator via the ``created_by``-backed owner relation, so they read it
        straight back; a non-user actor (a bare agent without a user grant) is
        rejected here so the row is always attributable.
        """

        note = apps.get_model("notes", "Note")
        user_id = actor_user_id(current_actor())
        if user_id is None:
            raise PermissionError("Only a user actor may create notes.")
        row = await note.objects.acreate(
            title=title,
            body=body,
            status=status,
            tags=list(tags or []),
            is_starred=is_starred,
            created_by_id=user_id,
        )
        return _detail(row)


def _summary(row: Any) -> dict[str, Any]:
    """Return the list projection of a note (no body)."""

    return {
        "sqid": str(row.sqid),
        "title": row.title,
        "status": str(row.status),
        "word_count": row.word_count,
        "is_starred": row.is_starred,
    }


def _detail(row: Any) -> dict[str, Any]:
    """Return the full projection of a note."""

    return {**_summary(row), "body": row.body, "tags": list(row.tags or [])}
