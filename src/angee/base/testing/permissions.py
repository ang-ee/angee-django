"""Compose installed permission declarations for source-model tests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest
from django.apps import AppConfig, apps
from django_compose.artifacts import atomic_write


def installed_field_owners(app_configs: Iterable[AppConfig]) -> dict[str, dict[str, str]]:
    """Use source composition's field ownership in installed-schema test callers."""

    # Pytest imports plugins before Django populates each worker's app registry.
    from django_compose.model_composition import ModelComposition

    from angee.base.build import field_gate_owners

    return field_gate_owners(ModelComposition.discover(app_configs))


def emit_permissions(
    app_configs: Iterable[AppConfig],
    runtime: Path,
    *,
    sources: Mapping[Path, str] | None = None,
) -> Mapping[Path, str]:
    """Bind merged inputs under ``runtime`` through the native AppConfig schema seam.

    ``sources`` defaults to the installed addons' merged permission fragments, the
    inputs the compiler emits for a composed host; the bound sources are returned.
    """

    from angee.base.permissions import bind_rebac_schema, declared_schema_path, merged_schema_relpath

    configs = tuple(app_configs)
    if sources is None:
        # Permission compilation reads Django models; callers run after setup.
        from angee.base.build import extension_source_map

        sources = extension_source_map(configs, field_owners=installed_field_owners(configs))
    for relative, content in sorted(sources.items()):
        atomic_write(runtime / relative, content.encode("utf-8"))
    for config in configs:
        relative = merged_schema_relpath(config.name)
        path = runtime / relative if relative in sources else declared_schema_path(config)
        bind_rebac_schema(config, path)
    return sources


@pytest.fixture
def composed_permissions(restore_composed_permission_bindings: None, tmp_path: Path) -> None:
    """Bind a test-owned copy of the merged permission inputs, for a test that edits one on disk.

    The session already binds and synchronizes these inputs; a test that rewrites a
    bound ``.zed`` edits this copy instead of the session's, and synchronizes the
    edit itself.
    """

    emit_permissions(apps.get_app_configs(), tmp_path / "permissions")
