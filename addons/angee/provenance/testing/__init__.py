"""Opt-in concrete changeset storage and principal loading for source-addon tests.

Install after ``angee.provenance``. Django's native test database setup owns
the ``provenance.Changeset`` table, just as it owns the resource testing ledger.
Production settings and serving code must not depend on this composition.
"""

from django.apps import apps as django_apps
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings


def load_statement_sources(addon_label: str) -> None:
    """Load one installed addon's declared statement principals and grants."""

    from rebac import backend, system_context

    from angee.resources.entries import ResourceEntry, resource_manifest_for

    if addon_label not in django_apps.app_configs or "provenance" not in django_apps.app_configs:
        return
    if backend().schema().get_definition("provenance/role") is None:
        call_command("rebac", "sync", verbosity=0)
    addon = django_apps.get_app_config(addon_label)
    resources = django_apps.get_model("resources", "Resource")
    principal_models = [django_apps.get_model("provenance", name)._meta.label for name in ("Rule", "Seed")]
    if resources._base_manager.filter(
        source_addon=addon.name,
        tier="install",
        target_model__in=principal_models,
    ).exists():
        return
    manifest = resource_manifest_for(addon)
    excluded = [
        ":".join(ResourceEntry.from_declaration(addon, "master", declaration).key) for declaration in manifest["master"]
    ]
    # Only the principals and their grants load: the addon's other install rows
    # (and whatever other addons they depend on) are not statement sources.
    principal_labels = {label.lower() for label in principal_models}
    for declaration in manifest["install"]:
        entry = ResourceEntry.from_declaration(addon, "install", declaration)
        if entry.kind != "grants" and (entry.model or entry.infer_model_label()).lower() not in principal_labels:
            excluded.append(":".join(entry.key))
    with (
        system_context(reason=f"{addon_label}.testing.statement_sources"),
        override_settings(
            ANGEE_RESOURCE_EXCLUDED_ENTRIES=(
                *getattr(settings, "ANGEE_RESOURCE_EXCLUDED_ENTRIES", ()),
                *excluded,
            ),
        ),
    ):
        for storage in ("denormalized", "registry"):
            with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
                resources.objects.load_addons((addon,), tiers=("install",))
