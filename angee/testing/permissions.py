"""Install test permission schemas through REBAC's public backend API."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.apps import apps
from django.core.management import call_command
from rebac.backends import LocalBackend
from rebac.schema import Schema
from rebac.testing import install_schema

from angee.compose.model_composition import ModelComposition
from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.fs import write_atomic


def install_permission_schema(schema: Schema, *, active: LocalBackend | None = None) -> LocalBackend:
    """Install a local test schema through the library's public test hook."""

    return install_schema(schema, backend=active)


@pytest.fixture
def composed_permissions(composed_tables: None, restore_composed_permission_bindings: None, tmp_path: Path) -> None:
    """Bind installed contributions and let native sync persist their schema."""

    configs = list(apps.get_app_configs())
    sources = extension_source_map(configs, field_owners=ModelComposition.discover(configs).field_gate_owners())
    runtime = tmp_path / "permissions"
    for relative, content in sources.items():
        write_atomic(runtime / relative, content)
    apply_schema_paths(configs, runtime, sources=sources)
    call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)
