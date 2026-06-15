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
    depends_on = ("angee.iam", "angee.integrate", "angee.agents", "angee.mcp")
    schemas = "schema.schemas"
    permissions = "permissions.zed"
    mcp_tools = "mcp_tools.register"
    """Mount a ``notes`` MCP server whose tools read/write notes for the request actor.

    The module is ``mcp_tools`` (not ``mcp``) so it never shadows the third-party
    ``mcp`` package when a test runner adds the addon directory to ``sys.path``.
    """
    resources = {
        "install": ("resources/install/010_integrate.vendor.yaml",),
        "demo": (
            "resources/demo/010_iam.user.yaml",
            "resources/demo/020_notes.note.yaml",
            "resources/demo/030_iam.oauth_client.yaml",
            "resources/demo/050_knowledge.vault.yaml",
            "resources/demo/060_knowledge.page.yaml",
            "resources/demo/070_knowledge.markdown_page.yaml",
            # Local template source: discover the repo's templates/ as integrate.Template
            # rows via the `local` VCS backend, ordered so each FK resolves before its use.
            "resources/demo/080_iam.credential.yaml",
            "resources/demo/081_integrate.vendor.yaml",
            "resources/demo/082_integrate.integration.yaml",
            "resources/demo/083_integrate.vcsintegration.yaml",
            "resources/demo/084_integrate.repository.yaml",
            "resources/demo/085_integrate.source.yaml",
            # A ready-to-provision demo agent and its inference chain (placeholder
            # credential -> integration -> provider -> model -> templates -> agent).
            "resources/demo/090_iam.credential.yaml",
            "resources/demo/091_integrate.integration.yaml",
            "resources/demo/092_agents.inferenceprovider.yaml",
            "resources/demo/093_agents.inferencemodel.yaml",
            "resources/demo/094_integrate.template.yaml",
            "resources/demo/0945_agents.mcpserver.yaml",
            "resources/demo/095_agents.agent.yaml",
        ),
    }
