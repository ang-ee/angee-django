"""Django config for the agents addon."""

from __future__ import annotations

from django.apps import AppConfig


class AgentsConfig(AppConfig):
    """Source app manifest for the agent catalogue."""

    default = True
    angee_addon = True
    angee_web_package = "@angee/agents"
    name = "angee.agents"
    label = "agents"
    # operator: agent provisioning drives the daemon over its REST bridge server-side.
    # mcp: the agents addon owns the MCP catalogue and supplies the bearer→actor verifier.
    depends_on = ("angee.integrate", "angee.operator", "angee.mcp")
    schemas = "schema.schemas"
    permissions = "permissions.zed"
    resources = {
        "demo": (
            {
                "path": "resources/demo/010_integrate.credential.yaml",
                "depends_on": "iam:resources/demo/010_iam.user.yaml",
                "adopt": ("user", "name"),
            },
            {
                "path": "resources/demo/020_agents.mcpserver.yaml",
                "depends_on": "resources/demo/010_integrate.credential.yaml",
                "adopt": "name",
            },
        ),
    }
