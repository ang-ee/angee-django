"""Compose installed permission declarations for bare and generated test hosts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import copy
from importlib import import_module
from pathlib import Path

from django.apps import AppConfig
from django_compose.artifacts import atomic_write


def installed_field_owners(app_configs: Iterable[AppConfig]) -> dict[str, dict[str, str]]:
    """Use source declarations for field ownership in bare and composed test hosts."""

    # Pytest imports plugins before Django populates each worker's app registry.
    from django_compose.model_composition import ModelComposition

    from angee.iam.build import field_gate_owners

    # Generated app configs expose runtime models. The compiler discovers donor
    # ownership from each addon's source exports, including cross-addon donors.
    source_configs = []
    for config in app_configs:
        module = config.models_module
        if module is not None and (module.__name__ == config.name or module.__name__.startswith(f"{config.name}.")):
            source_configs.append(config)
            continue
        source_config = copy(config)
        module_name = f"{config.name}.models"
        try:
            source_config.models_module = import_module(module_name)
        except ModuleNotFoundError as error:
            if error.name != module_name:
                raise
            source_config.models_module = None
        source_configs.append(source_config)
    return field_gate_owners(ModelComposition.discover(source_configs))


def emit_permissions(
    app_configs: Iterable[AppConfig],
    runtime: Path,
    *,
    sources: Mapping[Path, str] | None = None,
) -> None:
    """Bind installed merged inputs through the native AppConfig schema seam."""

    configs = tuple(app_configs)
    if sources is None:
        from angee.iam.build import extension_source_map

        sources = extension_source_map(configs, field_owners=installed_field_owners(configs))

    from angee.iam.permissions import bind_rebac_schema, declared_schema_path, merged_schema_relpath

    for relative, content in sorted(sources.items()):
        atomic_write(runtime / relative, content.encode("utf-8"))
    for config in configs:
        relative = merged_schema_relpath(config.name)
        path = runtime / relative if relative in sources else declared_schema_path(config)
        bind_rebac_schema(config, path)
