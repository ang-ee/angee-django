"""Django app contracts for composed Angee addons."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, TypeAlias, cast

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.functional import cached_property
from django.utils.module_loading import module_has_submodule

ResourceManifest: TypeAlias = Mapping[object, object]
"""Resource files keyed by enum tiers or string shorthand."""

SchemaParts: TypeAlias = dict[str, tuple[object, ...]]
"""GraphQL merge buckets for one schema name, each a tuple of contributions."""

SCHEMA_PART_KEYS: tuple[str, ...] = (
    "query",
    "mutation",
    "subscription",
    "types",
    "extensions",
)
"""Merge buckets an addon may contribute to a named schema, in merge order."""


def _running_angee_build() -> bool:
    """Return true while the ``angee build`` command is composing the runtime.

    ``import_models()`` runs during ``apps.populate()``, before any management
    command handler executes, so argv is the only signal available about the
    invocation. The build regenerates and re-imports the runtime models itself;
    importing the previous output here would double-register them in Django's
    app registry, so the build invocation skips the import.
    """

    return sys.argv[1:3] == ["angee", "build"]


def _module_exists(dotted_path: str) -> bool:
    """Return true when ``dotted_path`` and all its parents are importable.

    ``importlib.util.find_spec`` raises when an intermediate parent package is
    missing, so each ancestor is verified before its child is probed. This lets
    the optional runtime-model import stay free of try/except: an absent
    ``runtime`` tree (no build yet) simply reports false.
    """

    parent, _, _ = dotted_path.rpartition(".")
    if parent and not _module_exists(parent):
        return False
    return importlib.util.find_spec(dotted_path) is not None


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

    def get_resource_manifest(self) -> dict[str, tuple[dict[str, Any], ...]]:
        """Return normalized resource entries by tier.

        Keys are normalized tier values; values are tuples of normalized entry
        dicts (``path``/``url``/``model``/``kind``/``encoding``/``depends_on``
        keys). Result is cached for the lifetime of this config instance.
        """

        return self._resource_manifest

    @cached_property
    def _resource_manifest(self) -> dict[str, tuple[dict[str, Any], ...]]:
        """Validate and cache the declared resource manifest."""

        from angee.base.resources.models import Resource

        raw = self.resources or {}
        manifest: dict[str, tuple[dict[str, Any], ...]] = {
            tier: () for tier in Resource.Tier.values
        }
        for raw_tier, entries in raw.items():
            tier = Resource.Tier.from_value(raw_tier)
            manifest[tier] = self._resource_entries(entries)
        return manifest

    def _resource_entries(
        self,
        value: object,
    ) -> tuple[dict[str, Any], ...]:
        """Return normalized entry dicts from one resource manifest value."""

        if value is None:
            return ()
        if isinstance(value, str | Path | Mapping):
            return (self._resource_entry(value),)
        if not isinstance(value, Iterable):
            raise ImproperlyConfigured(
                f"{value!r} is not a resource entry or iterable of entries"
            )
        return tuple(self._resource_entry(item) for item in value)

    def _resource_entry(self, value: object) -> dict[str, Any]:
        """Return one normalized resource entry dict.

        A bare path/string becomes ``{"path": ...}``. A mapping must set just
        one of ``path``/``url``; ``path`` is held to the addon root and
        ``depends_on`` is coerced to a tuple. Unknown keys are preserved for
        future extension addons but ignored by base.
        """

        if isinstance(value, str | Path):
            return {"path": self._relative_path(value)}
        if not isinstance(value, Mapping):
            raise ImproperlyConfigured(
                f"{value!r} is not a resource path or entry mapping"
            )
        entry: dict[str, Any] = {
            key: value[key] for key in value if key not in {"path", "url"}
        }
        path = value.get("path")
        url = value.get("url")
        if (path is None) == (url is None):
            raise ImproperlyConfigured(
                f"resource entry {dict(value)!r} must set exactly one of "
                "'path' or 'url'"
            )
        if path is not None:
            entry["path"] = self._relative_path(path)
        else:
            entry["url"] = str(url)
        if "depends_on" in entry:
            entry["depends_on"] = tuple(entry["depends_on"])
        return entry

    def get_dependencies(self) -> tuple[str, ...]:
        """Return dependency aliases used to order addon composition."""

        return tuple(str(dep) for dep in self.depends_on)

    def get_rebac_schema_path(self) -> Path | None:
        """Return the existing django-zed-rebac schema path, when declared."""

        return self._rebac_schema_path

    @cached_property
    def _rebac_schema_path(self) -> Path | None:
        """Return the cached django-zed-rebac schema path."""

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

        return self._source_models_module

    @cached_property
    def _source_models_module(self) -> ModuleType | None:
        """Return the cached source ``models.py`` module."""

        if self.models_module is not None:
            return self.models_module
        return self.import_optional_module("models")

    def get_model_classes(self) -> tuple[type[models.Model], ...]:
        """Return abstract source models owned by this addon."""

        return self._model_contributions[0]

    def get_model_extensions(self) -> tuple[type[models.Model], ...]:
        """Return abstract source models that extend another source model."""

        return self._model_contributions[1]

    @cached_property
    def _model_contributions(
        self,
    ) -> tuple[tuple[type[models.Model], ...], tuple[type[models.Model], ...]]:
        """Return cached source model contributions declared by this addon."""

        module = self.get_source_models_module()
        if module is None:
            return (), ()
        models_owned: list[type[models.Model]] = []
        extensions: list[type[models.Model]] = []
        package_prefix = self.name + "."
        for _name, value in inspect.getmembers(module, inspect.isclass):
            if not self._belongs_to_source_module(
                value, self.name, package_prefix
            ):
                continue
            if not self._is_source_model(value):
                continue
            from angee.base.mixins.models import AngeeModel

            model_class = cast(type[AngeeModel], value)
            target = model_class.get_extension_target()
            if target:
                extensions.append(model_class)
                continue
            models_owned.append(model_class)
        return (
            tuple(sorted(models_owned, key=lambda cls: cls._meta.object_name)),
            tuple(
                sorted(
                    extensions,
                    key=lambda cls: (
                        cast(type[AngeeModel], cls).get_extension_target()
                        or "",
                        cls._meta.object_name,
                    ),
                )
            ),
        )

    def get_graphql_module(self) -> ModuleType | None:
        """Return the addon ``graphql.py`` module, when present."""

        return self._graphql_module

    @cached_property
    def _graphql_module(self) -> ModuleType | None:
        """Return the cached addon ``graphql.py`` module."""

        return self.import_optional_module("graphql")

    def get_schema_parts(self) -> dict[str, SchemaParts]:
        """Return GraphQL schema contributions declared by this addon, by name.

        An addon ``graphql.py`` may export a ``schemas`` mapping of schema name
        to a parts mapping. Each part is normalized to a tuple keyed by the
        merge buckets in ``SCHEMA_PART_KEYS``; absent buckets default to empty.
        The collector asks this owner instead of re-scanning the module, so the
        parts contract lives in one place.
        """

        return self._schema_parts

    @cached_property
    def _schema_parts(self) -> dict[str, SchemaParts]:
        """Validate and cache this addon's declared schema parts."""

        module = self.get_graphql_module()
        if module is None:
            return {}
        schemas = getattr(module, "schemas", None)
        if schemas is None:
            return {}
        if not isinstance(schemas, Mapping):
            raise ImproperlyConfigured(
                f"{module.__name__}.schemas must be a mapping"
            )
        parts: dict[str, SchemaParts] = {}
        for raw_name, raw_entry in schemas.items():
            name = str(raw_name)
            if not isinstance(raw_entry, Mapping):
                raise ImproperlyConfigured(
                    f"{module.__name__}.schemas[{name!r}] must be a mapping"
                )
            unknown = set(raw_entry) - set(SCHEMA_PART_KEYS)
            if unknown:
                listed = ", ".join(sorted(str(key) for key in unknown))
                raise ImproperlyConfigured(
                    f"{module.__name__}.schemas[{name!r}] has unknown keys: "
                    f"{listed}"
                )
            parts[name] = {
                key: self._schema_part_values(
                    module, name, key, raw_entry.get(key)
                )
                for key in SCHEMA_PART_KEYS
            }
        return parts

    def _schema_part_values(
        self,
        module: ModuleType,
        name: str,
        key: str,
        value: object,
    ) -> tuple[object, ...]:
        """Normalize one schema part to a tuple, rejecting unordered sets."""

        if value is None:
            return ()
        if isinstance(value, set | frozenset):
            raise ImproperlyConfigured(
                f"{module.__name__}.schemas[{name!r}][{key!r}] must be a "
                "sequence, not a set"
            )
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            return tuple(value)
        return (value,)

    def import_optional_module(self, module_name: str) -> ModuleType | None:
        """Import one addon submodule without hiding nested import errors."""

        if not module_has_submodule(self.module, module_name):
            return None
        return importlib.import_module(f"{self.name}.{module_name}")

    def import_models(self) -> None:
        """Import emitted concrete models for this source app if they exist."""

        super().import_models()
        if _running_angee_build():
            return
        runtime_module = settings.ANGEE_RUNTIME_MODULE
        target = f"{runtime_module}.{self.label}.models"
        if _module_exists(target):
            importlib.import_module(target)

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

        from angee.base.mixins.models import AngeeModel

        return (
            issubclass(value, AngeeModel)
            and value is not AngeeModel
            and value._meta.abstract
        )


class BaseConfig(BaseAddonConfig):
    """Django config for the framework base addon."""

    default = True
    name = "angee.base"
    label = "base"

    def ready(self) -> None:
        """Register composed models for revision tracking once they load."""

        super().ready()
        from angee.base.mixins.models import register_revision_models

        register_revision_models()
