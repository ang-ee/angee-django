"""Build-time runtime source rendering and emission."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

from angee.base.apps import BaseAddonConfig
from angee.base.discovery import discover_addons
from angee.base.mixins import HistoryMixin
from angee.base.models import AngeeModel
from angee.compose.rebac import render_permissions
from angee.resources.models import Resource

GENERATED_SENTINEL = "# ANGEE GENERATED RUNTIME - DO NOT EDIT"
"""Sentinel required before destructive runtime cleanup."""


class AngeeRuntime:
    """Owner for Angee runtime rendering, emission, checks, and cleanup."""

    def __init__(
        self,
        addons: Iterable[BaseAddonConfig],
        *,
        runtime_dir: Path,
    ) -> None:
        """Create a runtime renderer for ``addons`` and ``runtime_dir``."""

        self.addons = tuple(addons)
        self.runtime_dir = runtime_dir
        self.sources_by_label = self._sources_by_label()
        self.extensions = self._extensions_for()
        self._check_field_collisions()
        self.labels = tuple(sorted(self.sources_by_label))

    @classmethod
    def from_settings(cls) -> AngeeRuntime:
        """Return a runtime using discovered addons and Django settings."""

        return cls.from_addons(
            discover_addons(),
            runtime_dir=Path(settings.ANGEE_RUNTIME_DIR),
        )

    @classmethod
    def from_addons(
        cls,
        addons: Iterable[BaseAddonConfig],
        *,
        runtime_dir: Path,
    ) -> AngeeRuntime:
        """Return a runtime for explicit addon configs."""

        return cls(addons, runtime_dir=runtime_dir)

    def render_sources(self) -> dict[Path, str]:
        """Return generated runtime source files keyed by relative path."""

        sources: dict[Path, str] = {
            Path("__init__.py"): self._runtime_init_source(),
            Path("permissions.zed"): render_permissions(self.addons),
        }
        for label, source_models in self.sources_by_label.items():
            root = Path(label)
            sources[root / "__init__.py"] = ""
            sources[root / "migrations" / "__init__.py"] = ""
            sources[root / "models.py"] = self._models_source(
                label,
                source_models,
            )
        return sources

    def emit(self) -> None:
        """Write generated runtime sources to disk."""

        self.reset()
        for relative_path, text in self.render_sources().items():
            self._write(self.runtime_dir / relative_path, text)

    def check(self) -> None:
        """Raise when generated runtime sources differ from disk."""

        expected = self.render_sources()
        actual_paths = self._actual_source_paths()
        expected_paths = set(expected)
        drift = sorted(
            (expected_paths ^ actual_paths)
            | {
                path
                for path in expected_paths & actual_paths
                if (self.runtime_dir / path).read_text(encoding="utf-8")
                != expected[path]
            }
        )
        if drift:
            rendered = ", ".join(str(path) for path in drift)
            raise RuntimeError(f"generated runtime is stale: {rendered}")

    def reset(self) -> None:
        """Clear generated runtime sources while preserving migrations."""

        self._ensure_cleanable()
        self.clean()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    def clean(self) -> None:
        """Delete generated runtime files while preserving migrations."""

        self._ensure_cleanable()
        if not self.runtime_dir.exists():
            return
        for path in sorted(self.runtime_dir.rglob("*"), reverse=True):
            if self._is_preserved_migration_path(path):
                continue
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass

    def _models_source(
        self,
        label: str,
        source_models: tuple[type[AngeeModel], ...],
    ) -> str:
        """Return concrete model source for one target label."""

        lines = [
            '"""Concrete Django models emitted by Angee."""',
            "",
            "from __future__ import annotations",
            "",
        ]
        imports: list[str] = []
        render_plans: list[
            tuple[
                type[AngeeModel],
                str,
                tuple[tuple[type[models.Model], str], ...],
            ]
        ] = []
        for model_class in source_models:
            source_alias = self._source_alias(model_class)
            imports.extend(self._class_import(model_class, source_alias))
            if issubclass(model_class, HistoryMixin):
                imports.append(
                    "from simple_history.models import HistoricalRecords"
                )
            extension_bases = tuple(
                base
                for extension in self.extensions.get(
                    model_class.get_composition_label(),
                    (),
                )
                for base in extension.get_extension_bases()
            )
            aliased_extensions: list[tuple[type[models.Model], str]] = []
            for index, extension_base in enumerate(extension_bases, start=1):
                alias = f"{model_class.__name__}Extension{index}"
                aliased_extensions.append((extension_base, alias))
                imports.extend(self._class_import(extension_base, alias))
            render_plans.append(
                (model_class, source_alias, tuple(aliased_extensions))
            )

        lines.extend(sorted(set(imports)))
        lines.append("")
        for model_class, source_alias, extension_aliases in render_plans:
            meta_name = f"_{model_class.__name__}Meta"
            base_names = [alias for _extension, alias in extension_aliases] + [
                source_alias
            ]
            meta_lines = [
                "        abstract = False",
                f'        app_label = "{label}"',
            ]
            db_table = self._db_table_source(model_class)
            if db_table is not None:
                meta_lines.append(f"        db_table = {db_table}")
            meta_lines.extend(self._rebac_meta_source(model_class))
            body_lines = self._history_source(label, model_class)
            lines.extend(
                [
                    f"{meta_name} = getattr({source_alias}, 'Meta', object)",
                    "",
                    f"class {model_class.__name__}({', '.join(base_names)}):",
                    f'    """Concrete {model_class.__name__} model."""',
                    "",
                    *body_lines,
                    f"    class Meta({meta_name}):",
                    *meta_lines,
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    def _runtime_init_source(self) -> str:
        """Return the generated runtime package source."""

        return (
            '"""Generated Angee runtime package."""\n'
            f"{GENERATED_SENTINEL}\n\n"
            f"RUNTIME_APPS = {list(self.labels)!r}\n"
        )

    def _history_source(
        self,
        label: str,
        model_class: type[models.Model],
    ) -> list[str]:
        """Return simple-history declarations for a concrete model."""

        if not issubclass(model_class, HistoryMixin):
            return []
        args = f'app="{label}"'
        excluded = self._history_excluded_fields(model_class)
        if excluded:
            args += f", excluded_fields={excluded!r}"
        return [f"    history = HistoricalRecords({args})", ""]

    def _extensions_for(
        self,
    ) -> dict[str, tuple[type[AngeeModel], ...]]:
        """Return model extensions grouped by target composition label."""

        known_targets = {
            model.get_composition_label()
            for source_models in self.sources_by_label.values()
            for model in source_models
        }
        grouped: dict[str, list[type[AngeeModel]]] = {}
        for extension in (
            extension
            for addon in self.addons
            for extension in addon.model_extensions
        ):
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
                sorted(classes, key=lambda cls: cls._meta.object_name)
            )
            for target, classes in grouped.items()
        }

    def _check_field_collisions(self) -> None:
        """Raise when composed bases declare the same direct field."""

        for source_models in self.sources_by_label.values():
            for model_class in source_models:
                label = model_class.get_composition_label()
                owners: dict[str, type[models.Model]] = {}
                bases = (
                    *(
                        base
                        for extension in self.extensions.get(label, ())
                        for base in extension.get_extension_bases()
                    ),
                    model_class,
                )
                for base in bases:
                    for field_name in self._declared_fields(base):
                        previous = owners.setdefault(field_name, base)
                        if previous is base:
                            continue
                        raise ImproperlyConfigured(
                            f"{label} composes field {field_name!r} from "
                            f"both {previous._meta.label} and "
                            f"{base._meta.label}"
                        )

    def _sources_by_label(self) -> dict[str, tuple[type[AngeeModel], ...]]:
        """Return source models grouped by emitted runtime app label."""

        grouped: dict[str, list[type[AngeeModel]]] = {}
        for addon in self.addons:
            models_for_label = grouped.setdefault(addon.label, [])
            models_for_label.extend(
                cast(type[AngeeModel], model)
                for model in addon.model_classes
            )
        grouped.setdefault("base", []).append(Resource)
        return {
            label: tuple(source_models)
            for label, source_models in sorted(grouped.items())
            if source_models
        }

    def _actual_source_paths(self) -> set[Path]:
        """Return generated source paths currently on disk."""

        if not self.runtime_dir.exists():
            return set()
        return {
            path.relative_to(self.runtime_dir)
            for path in self.runtime_dir.rglob("*")
            if path.is_file() and self._is_checked_source(path)
        }

    def _ensure_cleanable(self) -> None:
        """Raise if the runtime path is not configured generated output."""

        configured = getattr(settings, "ANGEE_RUNTIME_DIR", None)
        if configured is not None:
            configured_path = Path(configured).resolve()
            if self.runtime_dir.resolve() != configured_path:
                raise RuntimeError(
                    f"{self.runtime_dir} is not the configured runtime dir"
                )
        if not self.runtime_dir.exists():
            return
        children = list(self.runtime_dir.iterdir())
        if not children:
            return
        init_path = self.runtime_dir / "__init__.py"
        if init_path.exists() and GENERATED_SENTINEL in init_path.read_text(
            encoding="utf-8"
        ):
            return
        remaining_files = [
            path for path in self.runtime_dir.rglob("*") if path.is_file()
        ]
        if remaining_files and all(
            self._is_preserved_migration_path(path) for path in remaining_files
        ):
            return
        raise RuntimeError(
            f"{self.runtime_dir} is not an Angee runtime directory"
        )

    def _is_checked_source(self, path: Path) -> bool:
        """Return whether ``path`` participates in source drift checks."""

        if "__pycache__" in path.parts:
            return False
        if self._is_numbered_migration(path):
            return False
        return True

    def _is_preserved_migration_path(self, path: Path) -> bool:
        """Return whether cleanup must preserve ``path`` under migrations."""

        return "migrations" in path.relative_to(self.runtime_dir).parts

    def _is_numbered_migration(self, path: Path) -> bool:
        """Return whether ``path`` is a Django numbered migration file."""

        return (
            path.parent.name == "migrations"
            and path.name[:4].isdigit()
            and path.suffix == ".py"
        )

    def _write(self, path: Path, text: str) -> None:
        """Write ``text`` to ``path``, creating parents first."""

        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return
        path.write_text(text, encoding="utf-8")

    def _source_alias(self, model_class: type[models.Model]) -> str:
        """Return the import alias for an abstract source model."""

        return f"Abstract{model_class.__name__}"

    def _class_import(
        self,
        model_class: type[models.Model],
        alias: str,
    ) -> list[str]:
        """Return import lines needed to reference ``model_class``."""

        return [
            f"from {model_class.__module__} import "
            f"{model_class.__name__} as {alias}"
        ]

    def _rebac_meta_source(self, model_class: type[models.Model]) -> list[str]:
        """Return concrete Meta lines for REBAC model options."""

        lines: list[str] = []
        for attr in (
            "rebac_resource_type",
            "rebac_id_attr",
            "rebac_default_action",
        ):
            value = getattr(model_class._meta, attr, None)
            if value is not None:
                lines.append(f"        {attr} = {value!r}")
        return lines

    def _db_table_source(self, model_class: type[models.Model]) -> str | None:
        """Return an explicit source ``db_table`` override."""

        original = getattr(model_class._meta, "original_attrs", {})
        if "db_table" in original:
            return repr(str(original["db_table"]))
        return None

    def _history_excluded_fields(
        self,
        model_class: type[models.Model],
    ) -> list[str]:
        """Return source fields simple-history cannot mirror."""

        return sorted(
            field.name
            for field in model_class._meta.get_fields()
            if getattr(field, "concrete", True) is False
            and not field.is_relation
            and not getattr(field, "auto_created", False)
        )

    def _declared_fields(
        self,
        model_class: type[models.Model],
    ) -> tuple[str, ...]:
        """Return fields directly declared by one abstract composition base."""

        meta = model_class._meta
        local = {
            field.name
            for field in (*meta.local_fields, *meta.local_many_to_many)
        }
        inherited: set[str] = set()
        for base in model_class.__mro__[1:]:
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
