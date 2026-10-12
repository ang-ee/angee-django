"""Install test permission schemas through REBAC's public backend API."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest
from django.apps import AppConfig, apps
from django.core.management import call_command
from rebac.backends import LocalBackend
from rebac.schema import Schema
from rebac.testing import install_schema

from angee.compose.model_composition import ModelComposition
from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.fs import write_atomic


def installed_field_owners(app_configs: Iterable[AppConfig]) -> dict[str, dict[str, str]]:
    """Use source composition's field ownership in installed-schema test callers."""

    return ModelComposition.discover(app_configs).field_gate_owners()


def install_permission_schema(schema: Schema, *, active: LocalBackend | None = None) -> LocalBackend:
    """Install a local test schema through the library's public test hook."""

    return install_schema(schema, backend=active)


def install_statement_principals() -> None:
    """Install the declared statement principals and their grants into a synced schema.

    Admission is a schema fact fed by seeded install resources (``provenance.Rule``
    rows, role grants); the runtime only reads them. A test host that synced its
    REBAC schema loads those declarations once per database state through the
    resource loader's native install tier, with master-tier declarations excluded
    so fixtures keep their own relationship vocabulary. Both relationship stores
    receive the grants because campaigns switch storage after bootstrap.
    """

    if not apps.is_installed("angee.provenance") or not apps.is_installed("angee.parties"):
        return
    from django.conf import settings
    from django.test import override_settings
    from rebac import backend, system_context

    from angee.resources.entries import ResourceEntry, resource_manifest_for

    rule = apps.get_model("provenance", "Rule")
    if backend().schema().get_definition("provenance/role") is None:
        return
    with system_context(reason="tests.statement_principals.bootstrap"):
        if rule._base_manager.exists():
            return
        addon = apps.get_app_config("parties")
        masters = tuple(
            ":".join(ResourceEntry.from_declaration(addon, "master", declaration).key)
            for declaration in resource_manifest_for(addon)["master"]
        )
        resource = apps.get_model("resources", "Resource")
        with override_settings(
            ANGEE_RESOURCE_EXCLUDED_ENTRIES=(*getattr(settings, "ANGEE_RESOURCE_EXCLUDED_ENTRIES", ()), *masters)
        ):
            for storage in ("denormalized", "registry"):
                with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
                    resource.objects.load_addons((addon,), tiers=("install",))


@pytest.fixture
def composed_permissions(composed_tables: None, restore_composed_permission_bindings: None, tmp_path: Path) -> None:
    """Bind installed contributions and let native sync persist their schema."""

    configs = list(apps.get_app_configs())
    sources = extension_source_map(configs, field_owners=installed_field_owners(configs))
    runtime = tmp_path / "permissions"
    for relative, content in sources.items():
        write_atomic(runtime / relative, content)
    apply_schema_paths(configs, runtime, sources=sources)
    call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)
