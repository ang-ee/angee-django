"""Native install resources required by messaging's source-identity scenarios."""

from django.apps import apps
from django.test import override_settings
from rebac import system_context

from angee.messaging.source_copies import SOURCE_IDENTITY_RULE


def load_source_identity() -> None:
    """Load the declared rule and its grants after a fixture host's native REBAC sync."""

    if "messaging" not in apps.app_configs:
        return
    rule = apps.get_model("provenance", "Rule")
    with system_context(reason="messaging.testing.source_identity"):
        if rule._base_manager.filter(key=SOURCE_IDENTITY_RULE).exists():
            return
        resources = apps.get_model("resources", "Resource")
        addon = apps.get_app_config("messaging")
        for storage in ("denormalized", "registry"):
            with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
                resources.objects.load_addons((addon,), tiers=("install",))
