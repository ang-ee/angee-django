"""Django config for the agents addon."""

from __future__ import annotations

from django.apps import AppConfig


class AgentsConfig(AppConfig):
    """Source app manifest for the agent catalogue."""

    default = True
    angee_addon = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.agents"
    label = "agents"
    # operator: agent provisioning drives the daemon over its REST bridge server-side.
    # mcp: the agents addon owns the MCP catalogue and supplies the bearer→actor verifier.
    depends_on = ("angee.integrate", "angee.operator", "angee.mcp")
    schemas = "schema.schemas"
    permissions = "permissions.zed"
