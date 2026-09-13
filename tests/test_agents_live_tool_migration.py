"""Historical MCP live-backing migration coverage."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from angee.agents.runtime_migrations.live_tool_backing import (
    populate_grant_ids,
    remove_evidenced_mirrors,
)
from angee.base.fields import SqidField


class _Rows:
    def __init__(self, values: list[tuple[Any, ...]] | None = None) -> None:
        self.values = values or []
        self.deleted: list[dict[str, Any]] = []
        self.pending: dict[str, Any] = {}
        self.requested_fields: tuple[str, ...] = ()

    def using(self, database: str) -> _Rows:
        assert database == "default"
        return self

    def filter(self, **kwargs: Any) -> _Rows:
        self.pending = kwargs
        return self

    def values_list(self, *fields: str) -> _Rows:
        self.requested_fields = fields
        return self

    def iterator(self) -> Any:
        return iter(self.values)

    def delete(self) -> None:
        self.deleted.append(self.pending)


def test_tool_grant_backfill_encodes_server_pk_through_historical_field() -> None:
    """The concrete grant id uses the retired server public id without a virtual field."""

    saved: list[tuple[str, ...]] = []
    tool = SimpleNamespace(
        server_id=7,
        name="search",
        save=lambda *, update_fields: saved.append(update_fields),
    )
    rows = _Rows([(tool,)])
    rows.iterator = lambda: iter((tool,))
    models = {("agents", "MCPTool"): SimpleNamespace(_base_manager=rows)}

    populate_grant_ids(
        SimpleNamespace(get_model=lambda app, model: models[(app, model)]),
        SimpleNamespace(connection=SimpleNamespace(alias="default")),
    )

    assert tool.grant_id == f"{_public_id(7, 'mcp_')}.search"
    assert saved == [("grant_id",)]


def test_tool_cleanup_queries_primary_keys_and_deletes_legacy_public_refs() -> None:
    """Mirror evidence comes from concrete FKs and is encoded only at tuple matching."""

    denormalized = _Rows()
    registry = _Rows()
    tool_edges = _Rows([(17, "mcp_legacy.search")])
    server_edges = _Rows([(18, 19)])
    agent = SimpleNamespace()
    tool = object()
    server = object()
    agent_field = SimpleNamespace(name="agent", remote_field=SimpleNamespace(model=agent))
    tool_field = SimpleNamespace(name="tool", remote_field=SimpleNamespace(model=tool))
    server_field = SimpleNamespace(name="server", remote_field=SimpleNamespace(model=server))
    agent.mcp_tools = SimpleNamespace(
        through=SimpleNamespace(
            _meta=SimpleNamespace(fields=(agent_field, tool_field)),
            _base_manager=tool_edges,
        )
    )
    agent.mcp_servers = SimpleNamespace(
        through=SimpleNamespace(
            _meta=SimpleNamespace(fields=(agent_field, server_field)),
            _base_manager=server_edges,
        )
    )
    models = {
        ("rebac", "Relationship"): SimpleNamespace(_base_manager=denormalized),
        ("rebac", "RelationshipRegistry"): SimpleNamespace(_base_manager=registry),
        ("agents", "Agent"): agent,
        ("agents", "MCPTool"): tool,
        ("agents", "MCPServer"): server,
    }

    remove_evidenced_mirrors(
        SimpleNamespace(get_model=lambda app, model: models[(app, model)]),
        SimpleNamespace(connection=SimpleNamespace(alias="default")),
    )

    assert tool_edges.requested_fields == ("agent__user_id", "tool__grant_id")
    assert server_edges.requested_fields == ("agent_id", "server_id")
    assert denormalized.deleted == [
        {
            "resource_type": "agents/tool_grant",
            "resource_id": "mcp_legacy.search",
            "relation": "grantee",
            "subject_type": "auth/user",
            "subject_id": _public_id(17, "usr_"),
            "optional_subject_relation": "",
            "caveat_name": "",
        },
        {
            "resource_type": "agents/mcp_server",
            "resource_id": _public_id(19, "mcp_"),
            "relation": "agent",
            "subject_type": "agents/agent",
            "subject_id": _public_id(18, "agt_"),
            "optional_subject_relation": "",
            "caveat_name": "",
        },
    ]
    assert len(registry.deleted) == 2


def _public_id(value: int, prefix: str) -> str:
    return SqidField(real_field_name="id", prefix=prefix, min_length=8).public_id_from_value(value)
