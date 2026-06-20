"""Django config for the notes addon."""

from __future__ import annotations

from django.apps import AppConfig


class NotesConfig(AppConfig):
    """Source app manifest for the notes addon."""

    default = True
    angee_addon = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "example.notes"
    label = "notes"
    depends_on = (
        "angee.iam",
        "angee.integrate",
        "angee.iam_integrate_oidc",
        "angee.agents",
        "angee.agents_integrate_anthropic",
        "angee.mcp",
    )
    schemas = "schema.schemas"
    permissions = "permissions.zed"
    mcp_tools = "mcp_tools.register"
    """Mount a ``notes`` MCP server whose tools read/write notes for the request actor.

    The module is ``mcp_tools`` (not ``mcp``) so it never shadows the third-party
    ``mcp`` package when a test runner adds the addon directory to ``sys.path``.
    """
    resources = {
        "install": ({"path": "resources/install/010_integrate.vendor.yaml", "adopt": "slug"},),
        "demo": (
            {
                "path": "resources/demo/020_notes.note.yaml",
                "depends_on": "iam:resources/demo/010_iam.user.yaml",
            },
            # Local template source: discover the repo's templates/ as integrate.Template
            # rows via the `local` VCS backend, ordered so each FK resolves before its use.
            {
                "path": "resources/demo/080_integrate.credential.yaml",
                "depends_on": "iam:resources/demo/010_iam.user.yaml",
                "adopt": ("user", "name"),
            },
            {"path": "resources/demo/081_integrate.vendor.yaml", "adopt": "slug"},
            {
                "path": "resources/demo/083_integrate.vcsbridge.yaml",
                "depends_on": (
                    "resources/demo/080_integrate.credential.yaml",
                    "resources/demo/081_integrate.vendor.yaml",
                ),
            },
            {
                "path": "resources/demo/084_integrate.repository.yaml",
                "depends_on": "resources/demo/083_integrate.vcsbridge.yaml",
                "adopt": ("vcs_bridge", "name"),
            },
            {
                "path": "resources/demo/085_integrate.source.yaml",
                "depends_on": "resources/demo/084_integrate.repository.yaml",
            },
            {
                "path": "resources/demo/094_integrate.template.yaml",
                "depends_on": "resources/demo/085_integrate.source.yaml",
                "adopt": ("source", "path"),
            },
            {
                "path": "resources/demo/095_agents.agent.yaml",
                "depends_on": (
                    "resources/demo/094_integrate.template.yaml",
                    "agents:resources/demo/020_agents.mcpserver.yaml",
                    "agents_integrate_anthropic:resources/demo/040_agents.inferencemodel.yaml",
                ),
            },
        ),
    }
