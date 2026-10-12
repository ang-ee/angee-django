"""Install declared statement principals in both source and composed test hosts."""

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings
from rebac import backend, system_context

from angee.resources.entries import ResourceEntry, resource_manifest_for


def statement_sources() -> None:
    """Load parties' install resources once per database, including after a flush.

    Test fixtures own their relationship vocabulary. The resource loader's native
    exclusion contract selects only the required install declarations here. Both
    native relationship stores need the declarations because campaigns switch
    storage after bootstrap.
    """

    if "parties" not in apps.app_configs:
        return
    rule = apps.get_model("provenance", "Rule")
    with system_context(reason="tests.statement_sources.bootstrap"):
        if rule._base_manager.filter(key="parties.normalized_twin").exists():
            return
        if backend().schema().get_definition("provenance/role") is None:
            call_command("rebac", "sync", verbosity=0)
        addon = apps.get_app_config("parties")
        masters = tuple(
            ":".join(ResourceEntry.from_declaration(addon, "master", declaration).key)
            for declaration in resource_manifest_for(addon)["master"]
        )
        with override_settings(
            ANGEE_RESOURCE_EXCLUDED_ENTRIES=(*getattr(settings, "ANGEE_RESOURCE_EXCLUDED_ENTRIES", ()), *masters)
        ):
            resource = apps.get_model("resources", "Resource")
            for storage in ("denormalized", "registry"):
                with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
                    resource.objects.load_addons((addon,), tiers=("install",))
