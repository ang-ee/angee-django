"""Opt-in concrete changeset storage and principal loading for source-addon tests.

Install after ``angee.provenance``. Django's native test database setup owns
the ``provenance.Changeset`` table, just as it owns the resource testing ledger.
Production settings and serving code must not depend on this composition.
"""

from django.apps import apps as django_apps
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings


def load_statement_sources(*addon_labels: str) -> None:
    """Load the install-tier statement principals, and their grants, of installed addons.

    Without labels, every installed addon whose install tier declares a
    ``provenance.Rule`` or ``Seed`` loads, as a composed host's install tier would.
    Only the principals and the addon's grants load, never its other install rows;
    an addon whose principals are already in the ledger is skipped.
    """

    from rebac import backend, system_context

    from angee.resources.entries import ResourceEntry, resource_manifest_for

    if "provenance" not in django_apps.app_configs:
        return
    resources = django_apps.get_model("resources", "Resource")
    principal_models = [django_apps.get_model("provenance", name)._meta.label for name in ("Rule", "Seed")]
    principal_labels = {label.lower() for label in principal_models}
    addons = (
        [django_apps.get_app_config(label) for label in addon_labels if label in django_apps.app_configs]
        if addon_labels else django_apps.get_app_configs()
    )
    for addon in addons:
        manifest = resource_manifest_for(addon)
        install = [ResourceEntry.from_declaration(addon, "install", declaration) for declaration in manifest["install"]]
        others = [
            ":".join(entry.key) for entry in install
            if entry.kind != "grants" and (entry.model or entry.infer_model_label()).lower() not in principal_labels
        ]
        if len(others) == sum(entry.kind != "grants" for entry in install) or resources._base_manager.filter(
            source_addon=addon.name, tier="install", target_model__in=principal_models,
        ).exists():
            continue
        if backend().schema().get_definition("provenance/role") is None:
            call_command("rebac", "sync", verbosity=0)
        excluded = [
            ":".join(ResourceEntry.from_declaration(addon, "master", declaration).key)
            for declaration in manifest["master"]
        ]
        with (
            system_context(reason=f"{addon.label}.testing.statement_sources"),
            override_settings(
                ANGEE_RESOURCE_EXCLUDED_ENTRIES=(*settings.ANGEE_RESOURCE_EXCLUDED_ENTRIES, *excluded, *others),
            ),
        ):
            resources.objects.load_addons((addon,), tiers=("install",))
