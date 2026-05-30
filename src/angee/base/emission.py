"""Emit concrete Django runtime modules from abstract addon models."""

from __future__ import annotations

import filecmp
import importlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import models

from angee.base.apps import BaseAddonConfig, ModelExtension
from angee.base.discovery import discover_addons
from angee.base.rebac import sync_permissions, write_permissions


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Summary of a build command."""

    emitted: int
    """Number of runtime model modules emitted."""

    applied: bool
    """Whether migrations and permission sync were applied."""

    checked: bool
    """Whether the build only checked for generated-source drift."""


class DriftError(RuntimeError):
    """Raised when ``angee build --check`` finds generated-source drift."""


def emit_runtime(
    *,
    addons: tuple[BaseAddonConfig, ...] | None = None,
    apply: bool,
    check: bool = False,
) -> BuildResult:
    """Emit runtime modules and optionally apply migrations."""

    discovered = discover_addons() if addons is None else addons
    runtime_dir = Path(settings.ANGEE_RUNTIME_DIR)
    if check:
        _check_runtime(runtime_dir, discovered)
        return BuildResult(
            emitted=_emitted_count(discovered),
            applied=False,
            checked=True,
        )

    labels = _runtime_labels(discovered)
    _reset_runtime_dir(runtime_dir, active_labels=labels)
    _emit_sources(runtime_dir, discovered)
    _import_runtime_models(labels)
    Path(settings.ANGEE_DATA_DIR).mkdir(parents=True, exist_ok=True)
    if labels:
        call_command("makemigrations", *labels, interactive=False, verbosity=0)
        _normalize_migration_headers(runtime_dir, labels)
    if apply:
        call_command("migrate", interactive=False, verbosity=0)
        sync_permissions()
    return BuildResult(
        emitted=len(labels),
        applied=apply,
        checked=False,
    )


def clean_runtime() -> None:
    """Delete generated runtime files while keeping the runtime root."""

    runtime_dir = Path(settings.ANGEE_RUNTIME_DIR)
    if not (runtime_dir / ".angee-manifest.json").exists():
        raise RuntimeError(f"{runtime_dir} is not an Angee runtime directory")
    for path in sorted(runtime_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()


def _check_runtime(
    runtime_dir: Path,
    addons: tuple[BaseAddonConfig, ...],
) -> None:
    """Emit to a temporary tree and compare generated source files."""

    with tempfile.TemporaryDirectory() as raw_tmp:
        expected_dir = Path(raw_tmp) / "runtime"
        _emit_sources(expected_dir, addons)
        expected = set(_generated_source_files(expected_dir))
        actual = (
            set(_generated_source_files(runtime_dir))
            if runtime_dir.exists()
            else set()
        )
        drift = sorted(
            (expected ^ actual)
            | {
                relative
                for relative in expected & actual
                if not _same_file(
                    expected_dir / relative, runtime_dir / relative
                )
            }
        )
    if drift:
        rendered = ", ".join(str(path) for path in drift)
        raise DriftError(f"generated runtime is stale: {rendered}")


def _emit_sources(
    runtime_dir: Path, addons: tuple[BaseAddonConfig, ...]
) -> None:
    """Write deterministic runtime source files."""

    runtime_dir.mkdir(parents=True, exist_ok=True)
    labels = _runtime_labels(addons)
    extensions = _extensions_for(addons)
    for addon in addons:
        if addon.model_classes:
            _emit_addon(runtime_dir, addon, extensions)
    _write(runtime_dir / "__init__.py", _runtime_init_source(labels))
    _write(runtime_dir / "schema.py", _schema_source())
    write_permissions(runtime_dir, addons)
    _write(
        runtime_dir / ".angee-manifest.json",
        json.dumps(
            {
                "addons": [addon.name for addon in addons],
                "resources": _resource_manifest(addons),
                "runtime_apps": labels,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _emit_addon(
    runtime_dir: Path,
    addon: BaseAddonConfig,
    extensions: dict[str, tuple[type[models.Model], ...]],
) -> None:
    """Write the concrete model module for one addon."""

    label = addon.label
    addon_dir = runtime_dir / label
    migrations_dir = addon_dir / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    _write(addon_dir / "__init__.py", "")
    _write(migrations_dir / "__init__.py", "")
    _write(addon_dir / "models.py", _models_source(addon, extensions))


def _models_source(
    addon: BaseAddonConfig,
    extensions: dict[str, tuple[type[models.Model], ...]],
) -> str:
    """Return concrete model source for one addon."""

    lines = [
        '"""Concrete models. Edit source addons instead."""',
        "",
        "from __future__ import annotations",
        "",
    ]
    imports: list[str] = []
    model_plans: list[
        tuple[type[models.Model], tuple[tuple[type[models.Model], str], ...]]
    ] = []
    for model_class in addon.model_classes:
        target = f"{addon.label}.{model_class._meta.model_name}"
        model_extensions = extensions.get(target, ())
        imports.extend(
            _class_import(model_class, f"Abstract{model_class.__name__}")
        )
        aliased_extensions: list[tuple[type[models.Model], str]] = []
        for index, extension in enumerate(model_extensions, start=1):
            alias = f"{model_class.__name__}Extension{index}"
            aliased_extensions.append((extension, alias))
            imports.extend(_class_import(extension, alias))
        model_plans.append((model_class, tuple(aliased_extensions)))
    lines.extend(sorted(set(imports)))
    lines.append("")
    for model_class, aliased_model_extensions in model_plans:
        abstract_name = f"Abstract{model_class.__name__}"
        meta_name = f"_{model_class.__name__}Meta"
        base_names = [
            alias for _extension, alias in aliased_model_extensions
        ] + [abstract_name]
        meta_lines = [
            "        abstract = False",
            f'        app_label = "{addon.label}"',
        ]
        db_table = _db_table_source(model_class)
        if db_table is not None:
            meta_lines.append(f"        db_table = {db_table}")
        lines.extend(
            [
                f"{meta_name} = getattr({abstract_name}, 'Meta', object)",
                "",
                f"class {model_class.__name__}({', '.join(base_names)}):",
                f'    """Concrete {model_class.__name__} model."""',
                "",
                f"    class Meta({meta_name}):",
                *meta_lines,
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _class_import(model_class: type[models.Model], alias: str) -> list[str]:
    """Return a deterministic class import line."""

    return [
        (
            f"from {model_class.__module__} import "
            f"{model_class.__name__} as {alias}"
        )
    ]


def _db_table_source(model_class: type[models.Model]) -> str | None:
    """Return an explicit source table override, when declared."""

    original = getattr(model_class._meta, "original_attrs", {})
    if "db_table" in original:
        return repr(str(original["db_table"]))
    return None


def _extensions_for(
    addons: tuple[BaseAddonConfig, ...],
) -> dict[str, tuple[type[models.Model], ...]]:
    """Group model extension bases by normalized target label."""

    known_targets = {
        f"{addon.label}.{model_class._meta.model_name}"
        for addon in addons
        for model_class in addon.model_classes
    }
    grouped: dict[str, list[type[models.Model]]] = {}
    for extension in _all_extensions(addons):
        if extension.target not in known_targets:
            raise ImproperlyConfigured(
                f"{extension.model_class.__module__}."
                f"{extension.model_class.__name__} extends unknown model "
                f"{extension.target!r}"
            )
        grouped.setdefault(extension.target, []).append(extension.model_class)
    return {
        target: tuple(sorted(classes, key=lambda cls: cls._meta.object_name))
        for target, classes in grouped.items()
    }


def _all_extensions(
    addons: tuple[BaseAddonConfig, ...],
) -> tuple[ModelExtension, ...]:
    """Flatten extension contributions from all addons."""

    return tuple(
        extension for addon in addons for extension in addon.model_extensions
    )


def _runtime_labels(addons: tuple[BaseAddonConfig, ...]) -> list[str]:
    """Return addon labels that emit at least one concrete model."""

    return [addon.label for addon in addons if addon.model_classes]


def _emitted_count(addons: tuple[BaseAddonConfig, ...]) -> int:
    """Return the number of addons that emit model modules."""

    return len(_runtime_labels(addons))


def _runtime_init_source(labels: list[str]) -> str:
    """Return runtime package metadata source."""

    return (
        '"""Generated Angee runtime package."""\n\n'
        f"RUNTIME_APPS = {labels!r}\n"
    )


def _schema_source() -> str:
    """Return the runtime schema module source."""

    return (
        '"""Generated GraphQL schema entrypoints."""\n\n'
        "from __future__ import annotations\n\n"
        "from angee.base.graphql import build_schema\n\n"
        'schema = build_schema("public")\n'
    )


def _resource_manifest(
    addons: tuple[BaseAddonConfig, ...],
) -> list[dict[str, str]]:
    """Return resource entries for the generated manifest."""

    entries: list[dict[str, str]] = []
    for addon in addons:
        for tier, paths in addon.get_resource_manifest().items():
            for path in paths:
                entries.append(
                    {
                        "addon": addon.name,
                        "path": path,
                        "tier": tier,
                    }
                )
    return entries


def _generated_source_files(root: Path) -> tuple[Path, ...]:
    """Return deterministic source files checked for runtime drift."""

    return tuple(
        sorted(
            path.relative_to(root)
            for path in root.rglob("*")
            if path.is_file() and _is_checked_runtime_source(path)
        )
    )


def _is_checked_runtime_source(path: Path) -> bool:
    """Return true for generated source files except numbered migrations."""

    if "__pycache__" in path.parts:
        return False
    name = path.name
    if (
        path.parent.name == "migrations"
        and name[:4].isdigit()
        and name.endswith(".py")
    ):
        return False
    return True


def _same_file(expected: Path, actual: Path) -> bool:
    """Return true when two files both exist and have identical bytes."""

    return actual.exists() and filecmp.cmp(expected, actual, shallow=False)


def _reset_runtime_dir(runtime_dir: Path, *, active_labels: list[str]) -> None:
    """Make runtime source authoritative while preserving migrations."""

    if runtime_dir.exists():
        children = sorted(runtime_dir.iterdir())
        if children and not (runtime_dir / ".angee-manifest.json").exists():
            raise RuntimeError(
                f"{runtime_dir} is not an Angee runtime directory"
            )
        for path in children:
            if path.name in active_labels and path.is_dir():
                _reset_addon_dir(path)
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    runtime_dir.mkdir(parents=True, exist_ok=True)


def _reset_addon_dir(addon_dir: Path) -> None:
    """Delete generated module files but keep migration history append-only."""

    for path in sorted(addon_dir.iterdir()):
        if path.name == "migrations" and path.is_dir():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def _import_runtime_models(labels: list[str]) -> None:
    """Import concrete model modules so Django can make migrations."""

    runtime_module = settings.ANGEE_RUNTIME_MODULE
    importlib.invalidate_caches()
    for label in labels:
        importlib.import_module(f"{runtime_module}.{label}.models")


def _normalize_migration_headers(runtime_dir: Path, labels: list[str]) -> None:
    """Remove wall-clock timestamps from generated migration headers."""

    replacement = "# Generated by Django during Angee build"
    for label in labels:
        migrations_dir = runtime_dir / label / "migrations"
        for path in sorted(migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            if not lines or not lines[0].startswith("# Generated by Django "):
                continue
            lines[0] = replacement
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write(path: Path, content: str) -> None:
    """Write text only when bytes would change."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")
