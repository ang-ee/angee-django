"""Compose installed permission declarations for source-model tests."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest
from django.apps import AppConfig, apps
from django.core.management import call_command

from angee.utils.files import write_atomic


def installed_field_owners(app_configs: Iterable[AppConfig]) -> dict[str, dict[str, str]]:
    """Use source composition's field ownership in installed-schema test callers."""

    # Pytest imports plugins before Django populates each worker's app registry.
    from angee.base.build import field_gate_owners
    from angee.compose.model_composition import ModelComposition

    return field_gate_owners(ModelComposition.discover(app_configs))


def emit_permissions(app_configs: Iterable[AppConfig], runtime: Path, *, sources: Mapping[Path, str]) -> None:
    """Execute the actual generated native path module for source-model tests."""

    from angee.base.build import PERMISSION_WIRING, schema_path_wiring

    for relative, content in sources.items():
        write_atomic(runtime / relative, content)
    path = runtime / PERMISSION_WIRING
    write_atomic(path, schema_path_wiring(app_configs, sources=sources))
    spec = importlib.util.spec_from_file_location("angee_test_permission_paths", path)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(importlib.util.module_from_spec(spec))


@pytest.fixture
def composed_permissions(composed_tables: None, restore_composed_permission_bindings: None, tmp_path: Path) -> None:
    """Bind installed contributions and let native sync persist their schema."""

    # Permission compilation reads Django models; fixture execution follows setup.
    from angee.base.build import extension_source_map

    configs = list(apps.get_app_configs())
    sources = extension_source_map(configs, field_owners=installed_field_owners(configs))
    runtime = tmp_path / "permissions"
    emit_permissions(configs, runtime, sources=sources)
    call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)
