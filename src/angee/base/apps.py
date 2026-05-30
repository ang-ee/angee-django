"""Django app contracts for composed Angee addons."""

from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import ClassVar, TypeAlias

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.functional import cached_property
from django.utils.module_loading import module_has_submodule

ResourcePaths: TypeAlias = str | Path | Iterable[str | Path] | None
"""One resource path or a deterministic iterable of resource paths."""

ResourceManifest: TypeAlias = Mapping[object, ResourcePaths]
"""Resource files keyed by enum tiers or string shorthand."""


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelExtension:
    """Abstract model that contributes fields to another source model."""

    target: str
    """Normalized ``app_label.model_name`` target."""

    model_class: type[models.Model]
    """Abstract model class that will be added as an emitted base."""


class BaseAddonConfig(AppConfig):
    """Base class for Django apps that participate in Angee builds.

    Addons stay ordinary Django apps: the app root is ``AppConfig.path``,
    source models live in ``models.py``, and native Strawberry contributions
    live in ``graphql.py``. The class attributes below only name facts Django
    does not already know.
    """

    default = False
    default_auto_field = "django.db.models.BigAutoField"

    depends_on: ClassVar[tuple[str, ...]] = ()
    """Addon labels or app names that must compose before this addon."""

    rebac_schema: ClassVar[str | None] = "permissions.zed"
    """REBAC schema file read by django-zed-rebac, relative to the app root.

    ``None`` means the addon contributes no schema. The default follows the
    library convention and is skipped when the file is absent.
    """

    resources: ClassVar[ResourceManifest] = {}
    """Resource files grouped by tier, relative to ``AppConfig.path``.

    Addons may key the dict with ``Resource.Tier`` values or string shorthand
    such as ``"demo"``. Tiers default to no files, so addons only declare the
    tiers they use. Addons list files explicitly so builds stay deterministic
    and reviews see changes.
    """

    def get_dependencies(self) -> tuple[str, ...]:
        """Return dependency aliases used to order addon composition."""

        return tuple(str(dep) for dep in self.depends_on)

    def get_resource_manifest(self) -> dict[str, tuple[str, ...]]:
        """Return validated resource file paths grouped by tier."""

        from angee.base.models import Resource

        raw = self.resources or {}
        manifest: dict[str, tuple[str, ...]] = {
            tier: () for tier in Resource.Tier.values()
        }
        for raw_tier, paths in raw.items():
            try:
                tier = Resource.Tier.from_value(raw_tier)
            except ImproperlyConfigured as exc:
                raise ImproperlyConfigured(
                    f"{self.name}.resources declares {raw_tier!r}: {exc}"
                ) from exc
            manifest[tier] = self._resource_paths(paths)
        return manifest

    def get_resource_paths(self, tier: object) -> tuple[str, ...]:
        """Return resource file paths declared for one tier."""

        from angee.base.models import Resource

        return self.get_resource_manifest()[Resource.Tier.from_value(tier)]

    def resolve_resource_path(self, relative_path: str) -> Path:
        """Return an absolute path for one declared resource file."""

        return Path(self.path) / self._relative_path(relative_path)

    def get_rebac_schema_path(self) -> Path | None:
        """Return the existing django-zed-rebac schema path, when declared."""

        if self.rebac_schema is None:
            return None
        relative_path = self._relative_path(self.rebac_schema)
        path = Path(self.path) / relative_path
        if path.exists():
            return path
        if relative_path == "permissions.zed":
            return None
        raise ImproperlyConfigured(
            f"{self.name}.rebac_schema references missing file "
            f"{relative_path!r}"
        )

    def get_source_models_module(self) -> ModuleType | None:
        """Return the source ``models.py`` module imported by Django."""

        if self.models_module is not None:
            return self.models_module
        return self.import_optional_module("models")

    @property
    def model_classes(self) -> tuple[type[models.Model], ...]:
        """Abstract source models owned by this addon."""

        return self._model_contributions[0]

    @property
    def model_extensions(self) -> tuple[ModelExtension, ...]:
        """Abstract source models that extend another source model."""

        return self._model_contributions[1]

    @cached_property
    def _model_contributions(
        self,
    ) -> tuple[tuple[type[models.Model], ...], tuple[ModelExtension, ...]]:
        """Return cached source model contributions declared by this addon."""

        module = self.get_source_models_module()
        if module is None:
            return (), ()
        models_owned: list[type[models.Model]] = []
        extensions: list[ModelExtension] = []
        package_prefix = module.__name__ + "."
        for _name, value in inspect.getmembers(module, inspect.isclass):
            if not self._belongs_to_source_module(
                value, module.__name__, package_prefix
            ):
                continue
            if not self._is_source_model(value):
                continue
            target = getattr(value, "extends", None)
            if target:
                extensions.append(
                    ModelExtension(
                        target=self._normalize_model_label(str(target)),
                        model_class=value,
                    )
                )
                continue
            models_owned.append(value)
        return (
            tuple(sorted(models_owned, key=lambda cls: cls._meta.object_name)),
            tuple(
                sorted(
                    extensions,
                    key=lambda item: (
                        item.target,
                        item.model_class._meta.object_name,
                    ),
                )
            ),
        )

    def get_graphql_module(self) -> ModuleType | None:
        """Return the addon ``graphql.py`` module, when present."""

        return self.import_optional_module("graphql")

    def import_optional_module(self, module_name: str) -> ModuleType | None:
        """Import one addon submodule without hiding nested import errors."""

        if not module_has_submodule(self.module, module_name):
            return None
        return importlib.import_module(f"{self.name}.{module_name}")

    def import_models(self) -> None:
        """Import emitted concrete models for this source app if they exist."""

        super().import_models()
        if len(sys.argv) >= 3 and sys.argv[1:3] == ["angee", "build"]:
            return
        runtime_module = getattr(settings, "ANGEE_RUNTIME_MODULE", "runtime")
        target = f"{runtime_module}.{self.label}.models"
        try:
            importlib.import_module(target)
        except ModuleNotFoundError as exc:
            if exc.name in {
                runtime_module,
                f"{runtime_module}.{self.label}",
                target,
            }:
                return
            raise

    def _resource_paths(self, value: ResourcePaths) -> tuple[str, ...]:
        """Return relative resource paths from one manifest value."""

        if value is None:
            return ()
        if isinstance(value, str | Path):
            return (self._relative_path(value),)
        if not isinstance(value, Iterable):
            raise TypeError(f"{value!r} is not a path or iterable of paths")
        paths = tuple(self._relative_path(path) for path in value)
        if any(not path for path in paths):
            raise TypeError("manifest paths must be non-empty strings")
        return paths

    def _relative_path(self, value: object) -> str:
        """Return one safe path relative to the addon root."""

        raw = str(value)
        path = Path(raw)
        if not raw or path.is_absolute() or ".." in path.parts:
            raise ImproperlyConfigured(
                f"Manifest path {raw!r} must be relative and stay inside "
                "the addon"
            )
        return raw

    def _belongs_to_source_module(
        self,
        value: type,
        module_name: str,
        package_prefix: str,
    ) -> bool:
        """Return true when a class is defined by this source addon."""

        origin = value.__module__
        return origin == module_name or origin.startswith(package_prefix)

    def _is_source_model(self, value: type) -> bool:
        """Return true for abstract Angee models declared by this addon."""

        from angee.base.mixins import AngeeModel

        return (
            issubclass(value, AngeeModel)
            and value is not AngeeModel
            and value._meta.abstract
        )

    def _normalize_model_label(self, label: str) -> str:
        """Normalize ``app.Model`` labels for extension lookup."""

        if "." not in label:
            raise ImproperlyConfigured(
                f"Model extension target {label!r} must be "
                "'app_label.ModelName'"
            )
        app_label, model_name = label.split(".", 1)
        return f"{app_label.lower()}.{model_name.lower()}"


class BaseConfig(BaseAddonConfig):
    """Django config for the framework base addon."""

    default = True
    name = "angee.base"
    label = "base"
