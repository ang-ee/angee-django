"""Discover and order abstract Django declarations for concrete composition.

Django owns fields, managers and class construction. This module owns the two
Angee declarations, ``runtime`` and ``extends``, and the additive donor policy.
It retains native model classes, never copies their metadata into another model.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Iterable
from graphlib import CycleError, TopologicalSorter

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.utils import make_model_tuple
from django.utils.module_loading import module_has_submodule

from angee.base.transitions import revalidate_transition_metadata


def extension_target(model: type[models.Model]) -> str | None:
    """Return this class's own normalized target; markers are not inherited."""

    target = model.__dict__.get("extends")
    if target is None:
        return None
    if not isinstance(target, str):
        raise ImproperlyConfigured(f"{model.__module__}.{model.__name__}.extends must be a string.")
    try:
        app_label, model_name = make_model_tuple(target)
    except ValueError as error:
        raise ImproperlyConfigured(
            f"{model.__module__}.{model.__name__}.extends must be an 'app_label.ModelName' reference."
        ) from error
    if not app_label or not model_name:
        raise ImproperlyConfigured(
            f"{model.__module__}.{model.__name__}.extends must be an 'app_label.ModelName' reference."
        )
    return f"{app_label}.{model_name}"


class ModelComposition:
    """An ordered collection of native source models and their donor relationships."""

    def __init__(
        self,
        sources_by_label: dict[str, tuple[type[models.Model], ...]],
        extensions: dict[str, tuple[type[models.Model], ...]],
    ) -> None:
        self.sources_by_label = dict(sorted(sources_by_label.items()))
        self.extensions = extensions
        self.models_by_label: dict[str, type[models.Model]] = {}
        for sources in self.sources_by_label.values():
            for source in sources:
                label = source._meta.label_lower
                previous = self.models_by_label.setdefault(label, source)
                if previous is not source:
                    raise ImproperlyConfigured(
                        f"Runtime composes duplicate source model label {label!r}: "
                        f"{previous.__module__}.{previous.__name__} and {source.__module__}.{source.__name__}"
                    )
        for target, donors in extensions.items():
            if target not in self.models_by_label:
                donor = donors[0]
                raise ImproperlyConfigured(f"{donor.__module__}.{donor.__name__} extends unknown model {target!r}")
        graph: dict[str, tuple[str, ...]] = {}
        for label, source in sorted(self.models_by_label.items()):
            target = extension_target(source)
            if target is not None and target not in self.models_by_label:
                raise ImproperlyConfigured(f"{source.__module__}.{source.__name__} extends unknown model {target!r}")
            graph[label] = (target,) if target is not None else ()
        try:
            order = tuple(TopologicalSorter(graph).static_order())
        except CycleError as error:
            cycle = " → ".join(error.args[1])
            raise ImproperlyConfigured(f"Cyclic materialized model parents: {cycle}") from error
        # Each app is emitted as one Python module. Even an acyclic model graph
        # can produce mutually importing modules whose classes are only partly
        # defined; reject that unsupported declaration before writing output.
        app_graph = {label: set() for label in self.sources_by_label}
        for label, targets in graph.items():
            app_label = self.models_by_label[label]._meta.app_label
            for target in targets:
                parent_label = self.models_by_label[target]._meta.app_label
                if parent_label != app_label:
                    app_graph[app_label].add(parent_label)
        try:
            tuple(TopologicalSorter({label: sorted(targets) for label, targets in app_graph.items()}).static_order())
        except CycleError as error:
            cycle = " → ".join(error.args[1])
            raise ImproperlyConfigured(
                f"Cyclic generated model modules: {cycle}. Move the shared parent models "
                "to a dependency app so concrete-parent imports point in one direction."
            ) from error
        self._parents = {
            self.models_by_label[label]: self.models_by_label[targets[0]] if targets else None
            for label, targets in graph.items()
        }
        self.ordered_models = tuple(self.models_by_label[label] for label in order)
        self.labels = tuple(self.sources_by_label)
        declarations = (
            *self.ordered_models,
            *(donor for donors in self.extensions.values() for donor in donors),
        )
        for model in dict.fromkeys(declarations):
            self._validate_import(model)
        self._validate_fields()

    @classmethod
    def discover(cls, app_configs: Iterable[AppConfig]) -> ModelComposition:
        """Import source modules during Django phase 2 and collect own declarations.

        App order supplies donor precedence; module namespace order supplies the
        order of multiple donors from one addon. Foreign re-exports and abstract
        helpers without an own declaration do not participate.
        """

        sources_by_label: dict[str, list[type[models.Model]]] = {}
        extensions: dict[str, list[type[models.Model]]] = {}
        for config in app_configs:
            source = config.models_module
            if source is None and module_has_submodule(config.module, "models"):
                source = importlib.import_module(f"{config.name}.models")
            if source is None:
                continue
            seen: set[type[models.Model]] = set()
            for model in vars(source).values():
                if not inspect.isclass(model) or not issubclass(model, models.Model) or model is models.Model:
                    continue
                if model in seen:
                    continue
                if model.__module__ != config.name and not model.__module__.startswith(f"{config.name}."):
                    continue
                seen.add(model)
                if not model._meta.abstract:
                    if model.__dict__.get("extends") is not None or model.__dict__.get("runtime", False):
                        raise ImproperlyConfigured(
                            f"{model.__module__}.{model.__name__} declares composition on a concrete model; "
                            "runtime sources and extension donors must be abstract."
                        )
                    continue
                materialized = model.__dict__.get("runtime", False)
                target = extension_target(model)
                if not isinstance(materialized, bool):
                    raise ImproperlyConfigured(f"{model.__module__}.{model.__name__}.runtime must be a boolean.")
                if not materialized and target is None:
                    continue
                if model._meta.app_label != config.label:
                    raise ImproperlyConfigured(
                        f"{model.__module__}.{model.__name__} has app_label {model._meta.app_label!r}; "
                        f"expected {config.label!r}"
                    )
                if materialized:
                    sources_by_label.setdefault(config.label, []).append(model)
                else:
                    extensions.setdefault(target, []).append(model)
        return cls(
            {
                label: tuple(sorted(sources, key=lambda model: model._meta.object_name))
                for label, sources in sources_by_label.items()
            },
            {target: tuple(donors) for target, donors in extensions.items()},
        )

    @staticmethod
    def _validate_import(model: type[models.Model]) -> None:
        """Reject exported classes whose declared import resolves to another object."""

        module = sys.modules.get(model.__module__)
        if module is not None and getattr(module, model.__name__, None) is not model:
            raise ImproperlyConfigured(
                f"{model.__name__!r} does not bind in {model.__module__!r}; "
                "source models must be importable by their declared module and name. "
                "For role_anchor wrappers, pass module=__name__ explicitly."
            )

    def parent(self, source: type[models.Model]) -> type[models.Model] | None:
        """Return the abstract source of this materialized model's concrete parent."""

        return self._parents[source]

    def donors(self, source: type[models.Model]) -> tuple[type[models.Model], ...]:
        """Return the donor classes themselves in addon/namespace precedence order."""

        return self.extensions.get(source._meta.label_lower, ())

    def _validate_fields(self) -> None:
        """Reject competing additive fields and parent columns redeclared by children.

        Django deep-copies abstract fields while preserving their creation counter.
        Shared abstract ancestry therefore contributes one declaration; two fields
        independently declared under one name are an ambiguous additive extension.
        """

        fields_by_label: dict[str, dict[str, models.Field]] = {}
        for source in self.ordered_models:
            owners: dict[str, tuple[models.Field, type[models.Model]]] = {}
            parent = self.parent(source)
            inherited = fields_by_label[parent._meta.label_lower] if parent is not None else {}
            for base in (*self.donors(source), source):
                for field in (*base._meta.local_fields, *base._meta.local_many_to_many, *base._meta.private_fields):
                    if field.name in inherited:
                        raise ImproperlyConfigured(
                            f"{source._meta.label} redeclares parent field {field.name!r} from {parent._meta.label}; "
                            "use a narrow abstract child/donor with only its contributed fields."
                        )
                    previous = owners.setdefault(field.name, (field, base))
                    if previous[0].creation_counter != field.creation_counter:
                        raise ImproperlyConfigured(
                            f"{source._meta.label_lower} composes field {field.name!r} from "
                            f"both {previous[1]._meta.label} and {base._meta.label}"
                        )
            fields_by_label[source._meta.label_lower] = {
                **inherited,
                **{name: field for name, (field, _) in owners.items()},
            }

    def validate_concrete(self, concrete_models: Iterable[type[models.Model]]) -> None:
        """Validate transition declarations against final Django classes after import."""

        for model in concrete_models:
            if model._meta.label_lower in self.models_by_label:
                revalidate_transition_metadata(model)
