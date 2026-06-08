"""Django config for Angee's knowledge addon."""

from __future__ import annotations

from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    """Source app manifest for the knowledge addon."""

    default = True
    angee_addon = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.knowledge"
    label = "knowledge"
    depends_on = ("angee.iam",)
    schemas = "schema.schemas"
    permissions = "permissions.zed"

    def ready(self) -> None:
        """Register the backlink index signal after app population."""

        super().ready()
        # App-populate phase 1 imports this config before models are ready;
        # importing signals here registers the post_save receiver.
        from angee.knowledge import signals  # noqa: F401
