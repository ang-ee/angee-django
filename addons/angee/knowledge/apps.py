"""Django config for Angee's knowledge addon."""

from __future__ import annotations

from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    """Source app manifest for the knowledge addon."""

    default = True
    angee_addon = True
    name = "angee.knowledge"
    label = "knowledge"
    depends_on = ("angee.iam",)
    schemas = "schema.schemas"
    permissions = "permissions.zed"
    resources = {
        "demo": (
            {
                "path": "resources/demo/010_knowledge.vault.yaml",
                "depends_on": "iam:resources/demo/010_iam.user.yaml",
                "adopt": ("owner", "name"),
            },
            {
                "path": "resources/demo/020_knowledge.page.yaml",
                "depends_on": "resources/demo/010_knowledge.vault.yaml",
                "adopt": ("vault", "title"),
            },
            {
                "path": "resources/demo/030_knowledge.markdown_page.yaml",
                "depends_on": "resources/demo/020_knowledge.page.yaml",
                "adopt": "page",
            },
        ),
    }

    def ready(self) -> None:
        """Register the backlink index signal after app population."""

        super().ready()
        # App-populate phase 1 imports this config before models are ready;
        # importing signals here registers the post_save receiver.
        from angee.knowledge import signals  # noqa: F401
