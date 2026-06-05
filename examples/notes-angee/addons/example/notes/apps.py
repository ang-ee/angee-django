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
    depends_on = ("angee.iam",)
    schemas = "schema.schemas"
    permissions = "permissions.zed"
    resources = {
        "demo": (
            "resources/demo/010_iam.user.yaml",
            "resources/demo/020_notes.note.yaml",
            "resources/demo/030_iam.oauth_client.yaml",
        ),
    }
