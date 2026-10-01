"""Install test permission schemas through REBAC's public backend API."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.apps import apps
from django.core.management import call_command
from rebac.backends import LocalBackend, backend
from rebac.schema import Schema

from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.fs import write_atomic


def install_permission_schema(schema: Schema) -> LocalBackend:
    """Make a local test schema current and rebuild its permission index."""

    active = backend()
    if not isinstance(active, LocalBackend):
        raise TypeError("Manual schema tests require the local REBAC backend")
    active.set_schema(schema)
    call_command("rebac", "index", "rebuild", verbosity=0)
    return active


@pytest.fixture
def composed_permissions(composed_tables: None, restore_composed_permission_bindings: None, tmp_path: Path) -> None:
    """Compose installed permission contributions for source-model tests."""

    configs = list(apps.get_app_configs())
    sources = extension_source_map(configs)
    runtime = tmp_path / "permissions"
    for relative, content in sources.items():
        write_atomic(runtime / relative, content)
    apply_schema_paths(configs, runtime, sources=sources)
    call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)
    call_command("rebac", "index", "rebuild", verbosity=0)
