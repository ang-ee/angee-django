"""Django config for the MCP addon's generated-tool contract check."""

from __future__ import annotations

from functools import partial

from django.apps import AppConfig
from django.core import checks

from angee.base.hooks import check_hook

check_settings = partial(check_hook, "ANGEE_MCP_ACTOR_VERIFIER")
_CHECKS_REGISTERED = False


class MCPConfig(AppConfig):
    """Register process hooks owned by the MCP addon."""

    default = True
    name = "angee.mcp"

    def compose_asgi(self):
        """Contribute FastMCP's mount and native lifespan as one capability."""

        from django_compose.asgi import AsgiContribution

        from angee.mcp.asgi import http_mounts, lifespans

        return AsgiContribution(http_mounts=dict(http_mounts()), lifespans=tuple(lifespans()))

    def ready(self) -> None:
        """Register the console-resource tool compiler as a Django system check."""

        super().ready()
        global _CHECKS_REGISTERED
        if _CHECKS_REGISTERED:
            return
        from angee.mcp.resource_tools import check_resource_tool_specs

        checks.register(check_settings, checks.Tags.models)

        checks.register(check_resource_tool_specs)
        _CHECKS_REGISTERED = True
