"""Runtime source rendering and emission for composed Angee addons."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.functional import cached_property

from angee.base.apps import BaseAddonConfig
from angee.base.discovery import discover_addons
from angee.base.graphql.schema import GraphQLSchemas
from angee.base.mixins import HistoryMixin
from angee.base.models import AngeeModel
from angee.compose.rebac import render_permissions

GENERATED_SENTINEL = "# ANGEE GENERATED RUNTIME - DO NOT EDIT"
"""Sentinel line required before deleting generated runtime files."""


class AngeeRuntime:
    """Owner for rendering, checking, emitting, and cleaning runtime output."""

    def __init__(
        self,
        *,
        addons: Iterable[BaseAddonConfig],
        runtime_dir: Path,
        configured_runtime_dir: Path | None = None,
    ) -> None:
        """Create a runtime for ``addons`` rooted at ``runtime_dir``."""

        self.addons = tuple(addons)
        self.runtime_dir = Path(runtime_dir)
        self.configured_runtime_dir = Path(
            configured_runtime_dir or runtime_dir
        )

    @classmethod
    def from_settings(cls) -> AngeeRuntime:
        """Return a runtime from Django settings and addon discovery."""

        runtime_dir = Path(settings.ANGEE_RUNTIME_DIR)
        return cls(
            addons=discover_addons(),
            runtime_dir=runtime_dir,
            configured_runtime_dir=runtime_dir,
        )

    @classmethod
    def from_addons(
        cls,
        addons: Iterable[BaseAddonConfig],
        *,
        runtime_dir: Path | None = None,
    ) -> AngeeRuntime:
        """Return a runtime from explicit addons."""

        selected_dir = (
            Path(settings.ANGEE_RUNTIME_DIR)
            if runtime_dir is None
            else Path(runtime_dir)
        )
        return cls(
            addons=tuple(addons),
            runtime_dir=selected_dir,
            configured_runtime_dir=selected_dir,
        )

    @cached_property
    def labels(self) -> tuple[str, ...]:
        """Return runtime app labels that emit concrete model modules."""

        return tuple(
            addon.label for addon in self.addons if addon.model_classes
        )

    @cached_property
    def extensions(self) -> dict[str, tuple[type[AngeeModel], ...]]:
        """Return model extensions grouped by target composition label."""

        known_targets = {
            cast(type[AngeeModel], model).get_composition_label()
            for addon in self.addons
            for model in addon.model_classes
        }
        grouped: dict[str, list[type[AngeeModel]]] = {}
        for extension in self._all_extensions():
            extension_model = cast(type[AngeeModel], extension)
            target = extension_model.get_extension_target()
            if target is None:
                continue
            if target not in known_targets:
                raise ImproperlyConfigured(
                    f"{extension.__module__}.{extension.__name__} extends "
                    f"unknown model {target!r}"
                )
            grouped.setdefault(target, []).append(extension_model)
        return {
            target: tuple(
                sorted(values, key=lambda cls: cls._meta.object_name)
            )
            for target, values in grouped.items()
        }

    def render_sources(self) -> dict[Path, str]:
        """Return generated source files keyed by runtime-relative path."""

        self._check_field_collisions()
        sources: dict[Path, str] = {
            Path("__init__.py"): self._runtime_init_source(),
            Path("permissions.zed"): render_permissions(self.addons),
        }
        for addon in self.addons:
            if not addon.model_classes:
                continue
            sources[Path(addon.label) / "__init__.py"] = ""
            sources[Path(addon.label) / "migrations" / "__init__.py"] = ""
            sources[Path(addon.label) / "models.py"] = self._models_source(
                addon
            )
        return sources

    def emit(self) -> None:
        """Write generated runtime source files to disk."""

        self.reset()
        for relative_path, content in self.render_sources().items():
            _write_if_changed(self.runtime_dir / relative_path, content)

    def check(self) -> None:
        """Raise when generated source files differ from disk."""

        expected = self.render_sources()
        actual_paths = self._existing_source_paths(expected)
        drift = sorted(
            (set(expected) ^ actual_paths)
            | {
                path
                for path in set(expected) & actual_paths
                if (self.runtime_dir / path).read_text(encoding="utf-8")
                != expected[path]
            }
        )
        if drift:
            rendered = ", ".join(str(path) for path in drift)
            raise RuntimeError(f"generated runtime is stale: {rendered}")

    def reset(self) -> None:
        """Delete generated files before emitting sources."""

        self._assert_generated_or_empty()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._delete_generated_files()

    def clean(self) -> None:
        """Delete generated runtime files while preserving migrations."""

        self._assert_generated_or_empty()
        if self.runtime_dir.exists():
            self._delete_generated_files()

    def render_schema_sdl(self) -> dict[str, str]:
        """Return printed GraphQL SDL by schema name."""

        schemas = GraphQLSchemas.from_addons(self.addons)
        return {name: schemas.build(name).as_str() for name in schemas.names()}

    def write_schema_sdl(self) -> None:
        """Write printed GraphQL SDL files under ``runtime/schemas``."""

        schemas_dir = self.runtime_dir / "schemas"
        for name, sdl in self.render_schema_sdl().items():
            _write_if_changed(schemas_dir / f"{name}.graphql", sdl)

    def check_schema_sdl(self) -> None:
        """Raise when rendered GraphQL SDL differs from disk."""

        expected = self.render_schema_sdl()
        schemas_dir = self.runtime_dir / "schemas"
        actual = (
            {
                path.stem: path.read_text(encoding="utf-8")
                for path in schemas_dir.glob("*.graphql")
            }
            if schemas_dir.exists()
            else {}
        )
        drift = sorted(
            (set(expected) ^ set(actual))
            | {
                name
                for name in expected.keys() & actual.keys()
                if expected[name] != actual[name]
            }
        )
        if drift:
            rendered = ", ".join(f"schemas/{name}.graphql" for name in drift)
            raise RuntimeError(f"generated GraphQL SDL is stale: {rendered}")

    def _models_source(self, addon: BaseAddonConfig) -> str:
        """Return concrete model source for one addon."""

        imports: list[str] = []
        plans: list[
            tuple[
                type[AngeeModel],
                str,
                tuple[tuple[type[models.Model], str], ...],
            ]
        ] = []
        for raw_model in addon.model_classes:
            model = cast(type[AngeeModel], raw_model)
            source_alias = f"Abstract{model.__name__}"
            imports.append(_class_import(model, source_alias))
            if issubclass(model, HistoryMixin):
                imports.append(
                    "from simple_history.models import HistoricalRecords"
                )
            aliased_extensions: list[tuple[type[models.Model], str]] = []
            for index, extension_base in enumerate(
                self._extension_bases(model),
                start=1,
            ):
                alias = f"{model.__name__}Extension{index}"
                imports.append(_class_import(extension_base, alias))
                aliased_extensions.append((extension_base, alias))
            plans.append((model, source_alias, tuple(aliased_extensions)))

        lines = [
            '"""Concrete Django models generated by Angee."""',
            "",
            "from __future__ import annotations",
            "",
            *sorted(set(imports)),
            "",
        ]
        for model, source_alias, extension_aliases in plans:
            lines.extend(
                self._model_class_source(
                    addon,
                    model,
                    source_alias,
                    extension_aliases,
                )
            )
        return "\n".join(lines).rstrip() + "\n"

    def _model_class_source(
        self,
        addon: BaseAddonConfig,
        model: type[AngeeModel],
        source_alias: str,
        aliased_extensions: tuple[tuple[type[models.Model], str], ...],
    ) -> list[str]:
        """Return source lines for one concrete model class."""

        meta_name = f"_{model.__name__}Meta"
        base_names = [alias for _base, alias in aliased_extensions]
        base_names.append(source_alias)
        body = self._history_source(addon, model)
        meta_lines = [
            "        abstract = False",
            f'        app_label = "{addon.label}"',
            *self._db_table_source(model),
            *self._rebac_meta_source(model),
        ]
        return [
            f"{meta_name} = getattr({source_alias}, 'Meta', object)",
            "",
            f"class {model.__name__}({', '.join(base_names)}):",
            f'    """Concrete {model.__name__} model."""',
            "",
            *body,
            f"    class Meta({meta_name}):",
            *meta_lines,
            "",
        ]

    def _history_source(
        self,
        addon: BaseAddonConfig,
        model: type[models.Model],
    ) -> list[str]:
        """Return simple-history field source for a concrete model."""

        if not issubclass(model, HistoryMixin):
            return []
        args = f'app="{addon.label}"'
        excluded = self._history_excluded_fields(model)
        if excluded:
            args += f", excluded_fields={excluded!r}"
        return [f"    history = HistoricalRecords({args})", ""]

    def _extension_bases(
        self,
        model: type[AngeeModel],
    ) -> tuple[type[models.Model], ...]:
        """Return extension bases targeting ``model``."""

        target = model.get_composition_label()
        return tuple(
            base
            for extension in self.extensions.get(target, ())
            for base in extension.get_extension_bases()
        )

    def _all_extensions(self) -> tuple[type[models.Model], ...]:
        """Return every model extension declared by the addons."""

        return tuple(
            extension
            for addon in self.addons
            for extension in addon.model_extensions
        )

    def _check_field_collisions(self) -> None:
        """Fail when composed bases declare the same local field."""

        for addon in self.addons:
            for raw_model in addon.model_classes:
                model = cast(type[AngeeModel], raw_model)
                owners: dict[str, type[models.Model]] = {}
                for base in (*self._extension_bases(model), model):
                    for field_name in _declared_composition_fields(base):
                        previous = owners.setdefault(field_name, base)
                        if previous is base:
                            continue
                        raise ImproperlyConfigured(
                            f"{model.get_composition_label()} composes field "
                            f"{field_name!r} from both "
                            f"{previous._meta.label} and {base._meta.label}"
                        )

    def _runtime_init_source(self) -> str:
        """Return generated runtime package metadata source."""

        return (
            '"""Generated Angee runtime package."""\n'
            f"{GENERATED_SENTINEL}\n\n"
            f"RUNTIME_APPS = {list(self.labels)!r}\n"
        )

    def _assert_generated_or_empty(self) -> None:
        """Raise unless the configured runtime dir is empty or generated."""

        if self.runtime_dir.resolve() != self.configured_runtime_dir.resolve():
            raise RuntimeError(
                f"{self.runtime_dir} is not the configured runtime directory"
            )
        if not self.runtime_dir.exists():
            return
        if not any(self.runtime_dir.iterdir()):
            return
        init_path = self.runtime_dir / "__init__.py"
        if (
            GENERATED_SENTINEL not in init_path.read_text(encoding="utf-8")
            if init_path.exists()
            else True
        ):
            raise RuntimeError(
                f"{self.runtime_dir} is not an Angee runtime directory"
            )

    def _delete_generated_files(self) -> None:
        """Delete generated source files and keep migration directories."""

        labels = set(self.labels) | set(self._read_runtime_apps())
        for relative in (Path("__init__.py"), Path("permissions.zed")):
            path = self.runtime_dir / relative
            if path.exists():
                path.unlink()

        schemas_dir = self.runtime_dir / "schemas"
        if schemas_dir.exists():
            for path in sorted(schemas_dir.glob("*.graphql")):
                path.unlink()
            _remove_empty_dirs(schemas_dir)

        for label in sorted(labels):
            app_dir = self.runtime_dir / label
            for relative in (Path("__init__.py"), Path("models.py")):
                path = app_dir / relative
                if path.exists():
                    path.unlink()
            migrations_init = app_dir / "migrations" / "__init__.py"
            if migrations_init.exists():
                migrations_init.unlink()
            _remove_empty_dirs(app_dir)

    def _read_runtime_apps(self) -> tuple[str, ...]:
        """Return previous runtime app labels by reading ``__init__.py``."""

        init_path = self.runtime_dir / "__init__.py"
        if not init_path.exists():
            return ()
        try:
            module = ast.parse(init_path.read_text(encoding="utf-8"))
        except SyntaxError:
            return ()
        for node in module.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "RUNTIME_APPS"
                for target in node.targets
            ):
                continue
            value = ast.literal_eval(node.value)
            if isinstance(value, list | tuple):
                return tuple(str(item) for item in value)
        return ()

    def _existing_source_paths(
        self,
        expected: dict[Path, str],
    ) -> set[Path]:
        """Return generated source paths currently on disk."""

        paths = {
            path for path in expected if (self.runtime_dir / path).exists()
        }
        paths.update(
            path
            for path in self._generated_disk_paths()
            if path not in expected
        )
        return paths

    def _generated_disk_paths(self) -> set[Path]:
        """Return checked generated files currently known on disk."""

        labels = set(self.labels) | set(self._read_runtime_apps())
        paths: set[Path] = set()
        for relative in (Path("__init__.py"), Path("permissions.zed")):
            if (self.runtime_dir / relative).exists():
                paths.add(relative)
        for label in labels:
            for relative in (
                Path(label) / "__init__.py",
                Path(label) / "models.py",
                Path(label) / "migrations" / "__init__.py",
            ):
                if (self.runtime_dir / relative).exists():
                    paths.add(relative)
        return paths

    @staticmethod
    def _history_excluded_fields(model: type[models.Model]) -> list[str]:
        """Return virtual fields simple-history cannot mirror."""

        return sorted(
            field.name
            for field in model._meta.get_fields()
            if getattr(field, "concrete", True) is False
            and not field.is_relation
            and not getattr(field, "auto_created", False)
        )

    @staticmethod
    def _db_table_source(model: type[models.Model]) -> list[str]:
        """Return explicit db_table source lines."""

        original = getattr(model._meta, "original_attrs", {})
        if "db_table" not in original:
            return []
        return [f"        db_table = {str(original['db_table'])!r}"]

    @staticmethod
    def _rebac_meta_source(model: type[models.Model]) -> list[str]:
        """Return concrete Meta lines for REBAC options."""

        lines: list[str] = []
        for attr in (
            "rebac_resource_type",
            "rebac_id_attr",
            "rebac_default_action",
        ):
            value = getattr(model._meta, attr, None)
            if value is not None:
                lines.append(f"        {attr} = {value!r}")
        return lines


def _class_import(model: type[models.Model], alias: str) -> str:
    """Return the import line for one source model class."""

    return f"from {model.__module__} import {model.__name__} as {alias}"


def _declared_composition_fields(
    model: type[models.Model],
) -> tuple[str, ...]:
    """Return local fields directly contributed by ``model``."""

    meta = model._meta
    local = {
        field.name for field in (*meta.local_fields, *meta.local_many_to_many)
    }
    inherited: set[str] = set()
    for base in model.__mro__[1:]:
        base_meta = getattr(base, "_meta", None)
        if (
            not issubclass(base, models.Model)
            or base_meta is None
            or not base_meta.abstract
        ):
            continue
        inherited.update(
            field.name
            for field in (
                *base_meta.local_fields,
                *base_meta.local_many_to_many,
            )
        )
    return tuple(sorted(local - inherited))


def _remove_empty_dirs(path: Path) -> None:
    """Remove ``path`` and empty parents while they stay empty."""

    current = path
    while current.exists() and current.is_dir():
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _write_if_changed(path: Path, content: str) -> None:
    """Write ``content`` when the target bytes would change."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")
