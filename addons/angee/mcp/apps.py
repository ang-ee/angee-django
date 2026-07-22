"""Django config for the MCP addon's generated-tool contract check."""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks

_CHECKS_REGISTERED = False


class MCPConfig(AppConfig):
    """Register process hooks owned by the MCP addon."""

    default = True
    name = "angee.mcp"

    def ready(self) -> None:
        """Register the console-resource tool compiler as a Django system check."""

        super().ready()
        global _CHECKS_REGISTERED
        if _CHECKS_REGISTERED:
            return
        from angee.mcp.resource_tools import check_resource_tool_specs

        checks.register(check_resource_tool_specs)
        _CHECKS_REGISTERED = True
