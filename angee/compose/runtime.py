"""Runtime composition discovery and generated-tree lifecycle.

``Runtime`` discovers and validates addon composition, then delegates concrete
Django model source emission to ``angee.compose.rendering.ModelRenderer``. It
owns the complete generated source map and its write, drift, and cleanup
lifecycle under ``runtime/<label>/``.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple, cast

from django.apps import AppConfig, apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.module_loading import module_has_submodule

from angee.base.models import AngeeModel
from angee.compose.dependencies import AddonDependencyGroup, AddonDependencyGroupResult
from angee.compose.migrations import RuntimeMigrations
from angee.compose.permissions import extension_source_map
from angee.compose.rendering import ModelRenderer
from angee.compose.web import WebRuntime
from angee.fs import GENERATED_SENTINEL, GeneratedTree
from angee.project import find_project_dir

_COMPOSER_WEB_SOURCES = {
    Path("web/manifest.json"),
    Path("web/tailwind.sources.css"),
}


def _is_runtime_source(path: Path) -> bool:
    """Return whether Runtime owns an extra on-disk path for drift and pruning."""

    root = path.parts[0] if path.parts else ""
    return (
        root not in {"gql", "schemas"}
        and (root != "web" or path in _COMPOSER_WEB_SOURCES)
        and "__pycache__" not in path.parts
    )


class ModelContributions(NamedTuple):
    """Abstract source models one addon contributes, split by composition role."""

    owned: tuple[type[models.Model], ...]
    """Models emitted as concrete runtime classes under the addon's label."""

    extensions: tuple[type[models.Model], ...]
    """Same-row extensions that merge fields into another addon's model."""


class Runtime:
    """Own composition discovery and the generated runtime tree lifecycle.

    One object owns the whole build-time lifecycle while concrete-model planning
    and rendering stay behind ``ModelRenderer``:

    - ``render_sources`` — the seam: returns ``{relative path: text}`` for the
      whole runtime. Every other entry point renders through it.
    - ``emit`` — write that map to ``runtime_dir`` during the explicit
      ``angee build`` pass (resets, prunes orphans).
    - ``build`` — emit stale sources, project the composed folder addons into
      the host dependency group, then materialize applicable addon-owned
      migrations onto the downstream Django graph.
    - ``is_current`` / ``check`` — disk vs the rendered map.
    - ``reset`` / ``clean`` — delete generated files behind the
      ``GENERATED_SENTINEL`` gate while preserving ``*/migrations/``.

    Construction groups source models by emitted label, resolves ``extends``
    extensions, and fails fast on field collisions, so an invalid composition
    never reaches emission.
    """

    def __init__(
        self,
        addons: Iterable[AppConfig],
        *,
        runtime_dir: Path,
        runtime_module: str = "runtime",
        project_dir: Path | None = None,
    ) -> None:
        """Create a runtime composition for ``addons`` and ``runtime_dir``."""

        self.addons = tuple(addons)
        self.runtime_dir = runtime_dir
        self.runtime_module = runtime_module
        self.project_dir = project_dir
        self._contributions = tuple((addon, self.model_contributions(addon)) for addon in self.addons)
        self.sources_by_label = self._sources_by_label()
        self.source_models_by_composition_label = self._source_models_by_composition_label()
        self._check_runtime_parent_targets()
        self.extensions = self._extensions_for()
        self._model_renderer = ModelRenderer(
            sources_by_label=self.sources_by_label,
            source_models_by_composition_label=self.source_models_by_composition_label,
            extensions=self.extensions,
            runtime_module=self.runtime_module,
        )
        self._check_field_collisions()
        self._model_renderer.validate()
        self.labels = tuple(sorted(self.sources_by_label))

    @classmethod
    def from_django(cls) -> Runtime:
        """Return a runtime using installed addons and Django settings.

        ``ANGEE_RUNTIME_DIR`` is the single owner of where the runtime lives.
        ``angee.compose.settings`` always sets it. A host that installs the
        composer without it is misconfigured, so fail loudly here rather than
        let a caller silently skip emission and surface a cryptic missing-model
        error later in app population.
        """

        runtime_dir = getattr(settings, "ANGEE_RUNTIME_DIR", None)
        runtime_module = getattr(settings, "ANGEE_RUNTIME_MODULE", "runtime")
        if not runtime_dir:
            raise ImproperlyConfigured(
                "angee.compose requires ANGEE_RUNTIME_DIR; angee.compose.settings "
                "sets it. A host installing the composer must configure the "
                "runtime directory."
            )
        return cls(
            apps.get_app_configs(),
            runtime_dir=Path(runtime_dir),
            runtime_module=str(runtime_module),
            project_dir=find_project_dir(),
        ).configure_migration_modules()

    def render_sources(self) -> dict[Path, str]:
        """Return generated runtime source files keyed by relative path.

        The composition seam. The returned map (path relative to
        ``runtime_dir`` → file text) is the single source of truth that
        ``emit`` writes and ``check`` compares against disk. It contains the
        generated package ``__init__`` plus, per label, an empty app/migrations
        ``__init__`` and a ``models.py``, plus (when a consumer addon contributes
        through a ``permissions.extends.zed``) the merged effective zed under
        ``permissions/<package>.zed`` — see ``angee.compose.permissions``.
        Migrations themselves are never rendered here — Django's addon
        materialization and later ``makemigrations`` own
        ``runtime/<label>/migrations/`` (redirected via
        ``MIGRATION_MODULES``), and cleanup preserves it.
        """

        sources: dict[Path, str] = {
            Path("__init__.py"): (
                f'"""Generated Angee runtime package."""\n'
                f"{GENERATED_SENTINEL}\n\n"
                f"RUNTIME_APPS = {list(self.labels)!r}\n"
            ),
        }
        for label, source_models in self.sources_by_label.items():
            root = Path(label)
            sources[root / "__init__.py"] = ""
            sources[root / "migrations" / "__init__.py"] = ""
            sources[root / "models.py"] = self._models_source(
                label,
                source_models,
            )
        sources.update(
            WebRuntime(self.addons, runtime_dir=self.runtime_dir).render_sources()
        )
        sources.update(extension_source_map(self.addons))
        return sources

    def emit(self) -> None:
        """Reset the runtime and write all sources (destructive; explicit).

        Used by the ``angee build`` command: it runs the generated-tree cleanup
        gate and prunes stale files (e.g. a removed addon's leftover label), then
        rewrites.
        """

        tree = self._generated_tree()
        tree.reset()
        tree.reconcile(prune=True)

    def runtime_migrations(self) -> RuntimeMigrations:
        """Return the addon migration materializer for this composed runtime."""

        return RuntimeMigrations(
            self.addons,
            runtime_dir=self.runtime_dir,
            runtime_module=self.runtime_module,
            labels=self.labels,
        )

    @property
    def addon_dependency_group(self) -> AddonDependencyGroup:
        """Return the dependency projector for this runtime's composed host."""

        return AddonDependencyGroup.from_app_configs(self.addons, project_dir=self.project_dir)

    def build(self) -> AddonDependencyGroupResult:
        """Emit stale sources, project addon dependencies, then materialize migrations."""

        if not self.is_current():
            self.emit()
        dependency_result = self.addon_dependency_group.write()
        self.runtime_migrations().materialize()
        return dependency_result

    def import_generated_models(self) -> None:
        """Import generated concrete model modules for all emitted labels."""

        for label in self.labels:
            importlib.import_module(f"{self.runtime_module}.{label}.models")

    def emit_if_stale(self) -> bool:
        """Write the runtime when it drifts from the sources, on every boot.

        Called from the composer's ``import_models`` in app-populate phase 2.
        Write-only and idempotent: it never resets or cleans, so a present-but-
        stale runtime is healed file by file and a corrupted or non-Angee
        directory can never abort app population through the destructive
        cleanup gate. Orphaned files from a removed addon are
        pruned by the explicit ``angee build`` (which calls ``emit``). Returning
        early when current keeps boots fast and avoids churning files the running
        process (and Django's autoreloader) already imported.
        """

        return self._generated_tree().reconcile(prune=False)

    def configure_migration_modules(self) -> Runtime:
        """Redirect migrations for emitted runtime app labels."""

        migration_modules = dict(getattr(settings, "MIGRATION_MODULES", {}))
        for label in self.labels:
            module = f"{self.runtime_module}.{label}.migrations"
            configured = migration_modules.get(label)
            if configured is not None and configured != module:
                raise ImproperlyConfigured(f"Project settings define Runtime-owned MIGRATION_MODULES[{label!r}]")
            migration_modules[label] = module
        settings.MIGRATION_MODULES = migration_modules
        return self

    def is_current(self) -> bool:
        """Return whether the on-disk runtime matches the rendered sources."""

        return not self._generated_tree().drift()

    def check(self) -> None:
        """Raise for generated-source, dependency-group, or migration drift."""

        drift = self._generated_tree().drift()
        if drift:
            rendered = ", ".join(str(path) for path in drift)
            raise RuntimeError(f"generated runtime is stale: {rendered}")
        self.addon_dependency_group.check()
        self.runtime_migrations().check()

    def reset(self) -> None:
        """Clear generated runtime sources while preserving migrations."""

        self._generated_tree().reset()

    def clean(self) -> None:
        """Delete generated runtime files while preserving migrations."""

        self._generated_tree().clean()

    def _generated_tree(self) -> GeneratedTree:
        """Return the synchronizer with Runtime's explicit filesystem policies."""

        configured = getattr(settings, "ANGEE_RUNTIME_DIR", None)
        return GeneratedTree(
            self.runtime_dir,
            self.render_sources(),
            owns=_is_runtime_source,
            sentinel=(Path("__init__.py"), GENERATED_SENTINEL),
            clean_root=Path(configured) if configured is not None else None,
        )

    def _models_source(
        self,
        label: str,
        source_models: tuple[type[AngeeModel], ...],
    ) -> str:
        """Delegate concrete model source rendering to its owning renderer."""

        return self._model_renderer.render(label, source_models)

    def _child_override_removed_fields(self, child_class: type[AngeeModel]) -> tuple[str, ...]:
        """Delegate child-field equivalence validation to its owning renderer."""

        return self._model_renderer.child_override_removed_fields(child_class)

    def _extensions_for(
        self,
    ) -> dict[str, tuple[type[AngeeModel], ...]]:
        """Return same-row model extensions grouped by target composition label."""

        grouped: dict[str, list[type[AngeeModel]]] = {}
        for _addon, contributions in self._contributions:
            for extension in contributions.extensions:
                extension_model = cast(type[AngeeModel], extension)
                target = extension_model.get_extension_target()
                if target is None:
                    continue
                if target not in self.source_models_by_composition_label:
                    raise ImproperlyConfigured(
                        f"{extension.__module__}.{extension.__name__} extends unknown model {target!r}"
                    )
                grouped.setdefault(target, []).append(extension_model)
        return {target: tuple(classes) for target, classes in grouped.items()}

    def _check_runtime_parent_targets(self) -> None:
        """Raise when a materialized child extends an unknown source model."""

        for source_models in self.sources_by_label.values():
            for model_class in source_models:
                target = model_class.get_extension_target()
                if target is None:
                    continue
                if target not in self.source_models_by_composition_label:
                    raise ImproperlyConfigured(
                        f"{model_class.__module__}.{model_class.__name__} extends unknown model {target!r}"
                    )

    def _check_field_collisions(self) -> None:
        """Raise when composed bases declare the same direct field."""

        for source_models in self.sources_by_label.values():
            for model_class in source_models:
                label = model_class._meta.label_lower
                owners: dict[str, type[models.Model]] = {}
                bases = (*self._model_renderer.extension_bases(model_class), model_class)
                for base in bases:
                    for field_name in self._model_renderer.declared_fields(base):
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
        for addon, contributions in self._contributions:
            models_for_label = grouped.setdefault(addon.label, [])
            models_for_label.extend(cast(type[AngeeModel], model) for model in contributions.owned)
        return {label: tuple(source_models) for label, source_models in sorted(grouped.items()) if source_models}

    def _source_models_by_composition_label(self) -> dict[str, type[AngeeModel]]:
        """Return emitted source models keyed by normalized composition label."""

        models_by_label: dict[str, type[AngeeModel]] = {}
        for source_models in self.sources_by_label.values():
            for model_class in source_models:
                label = model_class._meta.label_lower
                previous = models_by_label.setdefault(label, model_class)
                if previous is not model_class:
                    raise ImproperlyConfigured(
                        f"Runtime composes duplicate source model label {label!r}: "
                        f"{previous.__module__}.{previous.__name__} and "
                        f"{model_class.__module__}.{model_class.__name__}"
                    )
        return models_by_label

    def model_contributions(
        self,
        app_config: AppConfig,
    ) -> ModelContributions:
        """Return source models and extensions declared by one Django app config.

        Runtime owns this scan because addons deliberately remain plain Django
        ``AppConfig`` classes with no shared Angee base method to delegate to.
        """

        models_owned: list[type[models.Model]] = []
        extensions: list[type[models.Model]] = []
        seen: set[type] = set()
        source = app_config.models_module
        if source is None and module_has_submodule(app_config.module, "models"):
            source = importlib.import_module(f"{app_config.name}.models")
        if source is None:
            return ModelContributions((), ())
        for value in source.__dict__.values():
            if not inspect.isclass(value):
                continue
            if value in seen:
                continue
            origin = value.__module__
            package_prefix = f"{app_config.name}."
            if origin != app_config.name and not origin.startswith(package_prefix):
                continue
            if not issubclass(value, AngeeModel) or value is AngeeModel:
                continue
            model_class = cast(type[AngeeModel], value)
            if not model_class._meta.abstract:
                continue
            self._validate_source_model_label(app_config, model_class)
            seen.add(value)
            if model_class.get_extension_target() is None:
                if model_class.is_runtime_model():
                    models_owned.append(model_class)
            else:
                if model_class.is_runtime_model():
                    models_owned.append(model_class)
                else:
                    extensions.append(model_class)
        return ModelContributions(
            tuple(sorted(models_owned, key=lambda cls: cls._meta.object_name)),
            tuple(extensions),
        )

    def _validate_source_model_label(
        self,
        app_config: AppConfig,
        model_class: type[AngeeModel],
    ) -> None:
        """Raise when a source model's Django label does not match its addon."""

        if model_class._meta.app_label != app_config.label:
            raise ImproperlyConfigured(
                f"{model_class.__module__}.{model_class.__name__} has app_label "
                f"{model_class._meta.app_label!r}; expected {app_config.label!r}"
            )
