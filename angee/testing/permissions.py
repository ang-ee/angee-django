"""Install test permission schemas through REBAC's public backend API."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from django.apps import AppConfig, apps
from django.core.management import call_command
from rebac.schema import Schema

from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.fs import write_atomic

if TYPE_CHECKING:
    from rebac.backends import LocalBackend


def installed_field_owners(app_configs: Iterable[AppConfig]) -> dict[str, dict[str, str]]:
    """Use source composition's field ownership in installed-schema test callers."""

    # Pytest imports plugins before Django populates each worker's app registry.
    from angee.compose.model_composition import ModelComposition

    return ModelComposition.discover(app_configs).field_gate_owners()


def install_permission_schema(schema: Schema, *, active: LocalBackend | None = None) -> LocalBackend:
    """Install a local test schema through the library's public test hook."""

    # The native backend imports auth models; schema installation runs after setup.
    from rebac.testing import install_schema

    return install_schema(schema, backend=active)


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
