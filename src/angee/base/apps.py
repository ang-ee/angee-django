"""Django app contracts for composed Angee addons."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
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
"""Resource files keyed by resource tier values."""

SchemaParts: TypeAlias = dict[str, tuple[object, ...]]
"""GraphQL merge buckets for one schema name."""

SCHEMA_PART_KEYS: tuple[str, ...] = (
    "query",
    "mutation",
    "subscription",
    "types",
    "extensions",
)
"""GraphQL merge buckets accepted from addon ``schema.schemas``."""

# Deliberate cross-layer copy of angee.resources.tiers.ResourceTier; base cannot import resources.
RESOURCE_TIER_VALUES: tuple[str, ...] = ("master", "install", "demo")
"""Resource tier values accepted in addon manifests."""


class BaseAddonConfig(AppConfig):
    """Base AppConfig for Django apps that participate in Angee composition."""

    default = False
    default_auto_field = "django.db.models.BigAutoField"

    depends_on: ClassVar[str | tuple[str, ...]] = ()
    """Addon labels or app names that must compose before this addon.

    A bare string names a single dependency (it is not split into characters).
    """

    rebac_schema: ClassVar[str | None] = "permissions.zed"
    """Optional REBAC schema path relative to the addon package root."""

    settings_defaults: ClassVar[Mapping[str, object]] = {}
    """Django setting defaults this addon contributes to a composed host.

    ``compose_defaults`` folds these in beneath framework defaults and host
    overrides (e.g. the IAM addon contributes ``AUTH_USER_MODEL``). Two addons
    contributing the same key with conflicting values is a composition error.
    """

    resources: ClassVar[ResourceManifest] = {}
    """Resource declarations grouped by tier."""

    @cached_property
    def model_classes(self) -> tuple[type[models.Model], ...]:
        """Return abstract source models owned by this addon."""

        return self._model_contributions[0]

    @cached_property
    def model_extensions(self) -> tuple[type[models.Model], ...]:
        """Return abstract source models that extend another model."""

        return self._model_contributions[1]

    @cached_property
    def schema_parts(self) -> dict[str, SchemaParts]:
        """Return normalized GraphQL schema parts declared by this addon."""

        module = self.schema_module
        if module is None:
            return {}
        schemas = getattr(module, "schemas", None)
        if schemas is None:
            return {}
        if not isinstance(schemas, Mapping):
            raise ImproperlyConfigured(f"{module.__name__}.schemas must be a mapping")

        parts: dict[str, SchemaParts] = {}
        for raw_name, raw_entry in schemas.items():
            name = str(raw_name)
            if not isinstance(raw_entry, Mapping):
                raise ImproperlyConfigured(f"{module.__name__}.schemas[{name!r}] must be a mapping")
            unknown = set(raw_entry) - set(SCHEMA_PART_KEYS)
            if unknown:
                listed = ", ".join(sorted(str(key) for key in unknown))
                raise ImproperlyConfigured(f"{module.__name__}.schemas[{name!r}] has unknown keys: {listed}")
            parts[name] = {
                key: self._schema_part_values(
                    module,
                    name,
                    key,
                    raw_entry.get(key),
                )
                for key in SCHEMA_PART_KEYS
            }
        return parts

    @cached_property
    def rebac_schema_path(self) -> Path | None:
        """Return the declared REBAC schema path when it exists."""

        if self.rebac_schema is None:
            return None
        relative_path = self._relative_path(self.rebac_schema)
        path = Path(self.path) / relative_path
        if path.exists():
            return path
        if relative_path == "permissions.zed":
            return None
        raise ImproperlyConfigured(f"{self.name}.rebac_schema references missing file {relative_path!r}")

    @cached_property
    def resource_manifest(self) -> dict[str, tuple[dict[str, Any], ...]]:
        """Return normalized resource declarations keyed by tier."""

        manifest: dict[str, tuple[dict[str, Any], ...]] = {tier: () for tier in RESOURCE_TIER_VALUES}
        for raw_tier, declarations in (self.resources or {}).items():
            tier = self._resource_tier_value(raw_tier)
            manifest[tier] = self._resource_entries(declarations)
        return manifest

    @cached_property
    def dependencies(self) -> tuple[str, ...]:
        """Return dependency aliases used to order addon composition."""

        return _normalize_depends_on(self.depends_on)

    @cached_property
    def source_models_module(self) -> ModuleType | None:
        """Return this addon's conventional source ``models.py`` module."""

        if self.models_module is not None:
            return self.models_module
        return self.import_optional_module("models")

    @cached_property
    def schema_module(self) -> ModuleType | None:
        """Return this addon's optional ``schema.py`` module.

        Addon GraphQL contributions live in ``schema.py`` (not ``graphql.py``):
        a top-level ``graphql.py`` shadows the ``graphql`` core package on
        import and breaks ``manage.py test``. ``schema.py`` is the
        framework-wide convention every addon follows.
        """

        return self.import_optional_module("schema")

    @cached_property
    def _model_contributions(
        self,
    ) -> tuple[tuple[type[models.Model], ...], tuple[type[models.Model], ...]]:
        """Return source models and extensions declared by this addon."""

        # Deferred: AppConfig modules load during app-populate phase 1, before
        # model classes may be imported safely.
        from angee.base.models import AngeeModel

        models_owned: list[type[models.Model]] = []
        extensions: list[type[models.Model]] = []
        seen: set[type] = set()
        for source in self._source_modules():
            for _name, value in inspect.getmembers(
                source,
                inspect.isclass,
            ):
                if value in seen:
                    continue
                if not self._belongs_to_source_module(value):
                    continue
                if not self._is_source_model(value):
                    continue
                seen.add(value)
                model_class = cast(type[AngeeModel], value)
                if model_class.get_extension_target() is None:
                    models_owned.append(model_class)
                else:
                    extensions.append(model_class)
        return (
            tuple(sorted(models_owned, key=lambda cls: cls._meta.object_name)),
            tuple(
                sorted(
                    extensions,
                    key=lambda cls: (
                        cast(type[AngeeModel], cls).get_extension_target() or "",
                        cls._meta.object_name,
                    ),
                )
            ),
        )

    def import_models(self) -> None:
        """Adopt this addon's emitted concrete models for its label.

        This is the composition hook (``docs/composer.md``). The composer's
        ``import_models`` ran earlier in the same phase-2 loop and wrote
        ``runtime/<label>/models.py``; here the source addon imports it so the
        *generated* concrete models register under this addon's own ``label``.
        The source addon lends its label and — via ``MIGRATION_MODULES`` —
        its migration namespace ``runtime.<label>.migrations`` to the emitted
        models. ``super().import_models()`` first imports the addon's own
        ``models.py``, which holds only abstract sources and registers no
        table. An absent runtime reads as "not built yet" (e.g. a host with
        no composer installed) rather than an error.
        """

        super().import_models()
        runtime_module = getattr(settings, "ANGEE_RUNTIME_MODULE", None)
        if not runtime_module:
            return
        target = f"{runtime_module}.{self.label}.models"
        if _module_exists(target):
            importlib.import_module(target)

    def import_optional_module(self, module_name: str) -> ModuleType | None:
        """Import one addon submodule without hiding nested import errors."""

        if not module_has_submodule(self.module, module_name):
            return None
        return importlib.import_module(f"{self.name}.{module_name}")

    def _source_modules(self) -> tuple[ModuleType, ...]:
        """Return modules scanned for this addon's source models."""

        if self.source_models_module is not None:
            return (self.source_models_module,)
        return ()

    def _belongs_to_source_module(
        self,
        value: type,
    ) -> bool:
        """Return whether ``value`` is owned by a scanned source module."""

        origin = value.__module__
        package_prefix = f"{self.name}."
        return origin == self.name or origin.startswith(package_prefix)

    def _is_source_model(self, value: type) -> bool:
        """Return true for abstract Angee source models the composer emits."""

        # Deferred for the same Django app-populate phase as
        # ``_model_contributions``.
        from angee.base.models import AngeeModel

        if not (issubclass(value, AngeeModel) and value is not AngeeModel and value._meta.abstract):
            return False
        return cast(type[AngeeModel], value).is_composer_emitted()

    def _schema_part_values(
        self,
        module: ModuleType,
        name: str,
        key: str,
        value: object,
    ) -> tuple[object, ...]:
        """Return one schema part as a deterministic tuple."""

        if value is None:
            return ()
        if isinstance(value, set | frozenset):
            raise ImproperlyConfigured(f"{module.__name__}.schemas[{name!r}][{key!r}] must be a sequence, not a set")
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            return tuple(value)
        return (value,)

    def _resource_entries(
        self,
        declarations: object,
    ) -> tuple[dict[str, Any], ...]:
        """Return normalized entry dictionaries for one resource tier."""

        if declarations is None:
            return ()
        if isinstance(declarations, str | Path | Mapping):
            return (self._resource_entry(declarations),)
        if not isinstance(declarations, Iterable):
            raise ImproperlyConfigured(f"{declarations!r} is not a resource entry or iterable")
        return tuple(self._resource_entry(entry) for entry in declarations)

    def _resource_entry(self, declaration: object) -> dict[str, Any]:
        """Return one normalized resource entry dictionary."""

        if isinstance(declaration, str | Path):
            return {"path": self._relative_path(declaration)}
        if not isinstance(declaration, Mapping):
            raise ImproperlyConfigured(f"{declaration!r} is not a resource path or mapping")

        entry: dict[str, Any] = {str(key): declaration[key] for key in declaration if key not in {"path", "url"}}
        path = declaration.get("path")
        url = declaration.get("url")
        if (path is None) == (url is None):
            raise ImproperlyConfigured(f"resource entry {dict(declaration)!r} must set exactly one of 'path' or 'url'")
        if path is None:
            entry["url"] = str(url)
        else:
            entry["path"] = self._relative_path(path)
        if "depends_on" in entry:
            entry["depends_on"] = _normalize_depends_on(entry["depends_on"])
        if "adopt" in entry:
            entry["adopt"] = self._resource_adopt_value(entry["adopt"])
        if "model" in entry and entry["model"] is not None:
            entry["model"] = str(entry["model"])
        if "encoding" in entry:
            entry["encoding"] = str(entry["encoding"] or "utf-8")
        return entry

    def _resource_adopt_value(self, value: object) -> str | bool:
        """Return the normalized adoption declaration for one resource entry."""

        if isinstance(value, str):
            return value
        return bool(value)

    def _resource_tier_value(self, value: object) -> str:
        """Return one normalized resource tier value."""

        raw = str(getattr(value, "value", value))
        if raw not in RESOURCE_TIER_VALUES:
            expected = ", ".join(RESOURCE_TIER_VALUES)
            raise ImproperlyConfigured(f"Unknown resource tier {raw!r}; expected one of {expected}")
        return raw

    def _relative_path(self, value: object) -> str:
        """Return one safe addon-relative manifest path."""

        raw = str(value)
        path = Path(raw)
        if not raw or path.is_absolute() or ".." in path.parts:
            raise ImproperlyConfigured(f"Manifest path {raw!r} must be relative and stay inside the addon")
        return raw


class BaseConfig(BaseAddonConfig):
    """Django app configuration for Angee's runtime base addon."""

    default = True
    name = "angee.base"
    label = "base"

    def ready(self) -> None:
        """Wire runtime model registration after Django populates apps."""

        super().ready()
        if not _module_exists("angee.base.signals"):
            return
        # Deferred: ready() runs after app population; signal wiring imports
        # model-dependent modules that are unsafe during phase 1.
        from angee.base.signals import connect_audit_stamping, register_revision_models

        register_revision_models()
        connect_audit_stamping()


def _module_exists(dotted_path: str) -> bool:
    """Return true when ``dotted_path`` and every parent can be imported."""

    parent, _separator, _name = dotted_path.rpartition(".")
    if parent and not _module_exists(parent):
        return False
    return importlib.util.find_spec(dotted_path) is not None


def _normalize_depends_on(value: object) -> tuple[str, ...]:
    """Return dependency keys, treating a bare string as a single key."""

    if isinstance(value, str):
        return (value,)
    if not isinstance(value, Iterable):
        raise ImproperlyConfigured("depends_on must be a string or iterable of strings")
    return tuple(str(item) for item in value)
