"""Discover and order abstract Django declarations for concrete composition.

Django owns fields, managers and class construction. This module owns
``runtime``, ``extends``, and checked ``requires_parent`` declarations and the
additive donor policy.
It retains native model classes, never copies their metadata into another model.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Iterable
from graphlib import CycleError, TopologicalSorter
from typing import cast

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.utils import make_model_tuple

from angee.compose.declarations import AppDeclarationSource


def _model_reference(model: type[models.Model], attribute: str) -> str | None:
    target = model.__dict__.get(attribute)
    if target is None:
        return None
    if not isinstance(target, str):
        raise ImproperlyConfigured(f"{model.__module__}.{model.__name__}.{attribute} must be a string.")
    try:
        app_label, model_name = make_model_tuple(target)
    except ValueError as error:
        raise ImproperlyConfigured(
            f"{model.__module__}.{model.__name__}.{attribute} must be an 'app_label.ModelName' reference."
        ) from error
    if not app_label or not model_name:
        raise ImproperlyConfigured(
            f"{model.__module__}.{model.__name__}.{attribute} must be an 'app_label.ModelName' reference."
        )
    return f"{app_label}.{model_name}"


def extension_target(model: type[models.Model]) -> str | None:
    """Return this class's own normalized target; markers are not inherited."""

    return _model_reference(model, "extends")


class ModelComposition:
    """An ordered collection of native source models and their donor relationships."""

    def __init__(
        self,
        sources_by_label: dict[str, tuple[type[models.Model], ...]],
        extensions: dict[str, tuple[type[models.Model], ...]],
        model_owners: dict[type[models.Model], str] | None = None,
    ) -> None:
        self.sources_by_label = dict(sorted(sources_by_label.items()))
        self.extensions = extensions
        self._model_owners = model_owners or {}
        self._contributed_fields: dict[
            type[models.Model], tuple[tuple[models.Field, type[models.Model] | None], ...]
        ] = {}
        missing_owners = [
            donor
            for donors in extensions.values()
            for donor in donors
            if donor not in self._model_owners
        ]
        if missing_owners:
            names = ", ".join(
                sorted(f"{donor.__module__}.{donor.__name__}" for donor in missing_owners)
            )
            raise ImproperlyConfigured(
                f"Addon ownership is required for extension donors: {names}. "
                "Use ModelComposition.discover() or pass model_owners."
            )
        self.models_by_label: dict[str, type[models.Model]] = {}
        for sources in self.sources_by_label.values():
            for source in sources:
                label = cast(str, source._meta.label_lower)
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
            parent_target = extension_target(source)
            if parent_target is not None and parent_target not in self.models_by_label:
                raise ImproperlyConfigured(
                    f"{source.__module__}.{source.__name__} extends unknown model {parent_target!r}"
                )
            graph[label] = (parent_target,) if parent_target is not None else ()
        try:
            order = tuple(TopologicalSorter(graph).static_order())
        except CycleError as error:
            cycle = " → ".join(error.args[1])
            raise ImproperlyConfigured(f"Cyclic materialized model parents: {cycle}") from error
        # Each app is emitted as one Python module. Even an acyclic model graph
        # can produce mutually importing modules whose classes are only partly
        # defined; reject that unsupported declaration before writing output.
        app_graph: dict[str, set[str]] = {label: set() for label in self.sources_by_label}
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
        self.required_parents = self._validate_required_parents()
        self._validate_fields()
        self._validate_donor_managers()

    @classmethod
    def discover(
        cls, app_configs: Iterable[AppConfig], *, declarations: AppDeclarationSource | None = None,
    ) -> ModelComposition:
        """Collect declarations from model modules loaded by native AppConfig hooks.

        App order supplies donor precedence; module namespace order supplies the
        order of multiple donors from one addon. Foreign re-exports and abstract
        helpers without an own declaration do not participate.
        """

        sources_by_label: dict[str, list[type[models.Model]]] = {}
        extensions: dict[str, list[type[models.Model]]] = {}
        model_owners: dict[type[models.Model], str] = {}
        declarations = declarations if declarations is not None else AppDeclarationSource.configured()
        configs = tuple(app_configs)
        migration_targets = {
            item.get("app_label")
            for config in configs
            for item in declarations.migrations(config)
        }
        for config in configs:
            if config.label in migration_targets:
                # A selected addon may contribute an upgrade for another installed
                # app after its last model retires. Keep that native target label.
                sources_by_label.setdefault(config.label, [])
            source = config.models_module
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
                    extensions.setdefault(cast(str, target), []).append(model)
                model_owners[model] = config.name
        return cls(
            {
                label: tuple(sorted(sources, key=lambda model: model.__name__))
                for label, sources in sources_by_label.items()
            },
            {target: tuple(donors) for target, donors in extensions.items()},
            model_owners,
        )

    @staticmethod
    def _validate_import(model: type[models.Model]) -> None:
        """Reject exported classes whose declared import resolves to another object."""

        module = sys.modules.get(model.__module__)
        if module is not None and getattr(module, model.__name__, None) is not model:
            raise ImproperlyConfigured(
                f"{model.__name__!r} does not bind in {model.__module__!r}; "
                "source models must be importable by their declared module and name. "
                "Declare the source module explicitly when using a model factory."
            )

    def parent(self, source: type[models.Model]) -> type[models.Model] | None:
        """Return the abstract source of this materialized model's concrete parent."""

        return self._parents[source]

    def donors(self, source: type[models.Model]) -> tuple[type[models.Model], ...]:
        """Return the donor classes themselves in addon/namespace precedence order."""

        return self.extensions.get(source._meta.label_lower, ())

    def contributed_field_origins(self, source: type[models.Model]) -> tuple[tuple[str, str], ...]:
        """Return ``(field name, addon name)`` for fields supplied by source donors."""

        return tuple(
            (field.name, self._model_owners[owner])
            for field, owner in self._contributed_fields.get(source, ())
            if owner is not None
        )

    def contributed_fields(self, source: type[models.Model]) -> tuple[models.Field, ...]:
        """Return native donor declarations, including shared abstract ancestry.

        Validation resolves repeated abstract copies to the first declaration.
        Its native ``field.model`` identifies the donor exposing that field;
        shared ancestry deliberately has no exclusive addon gate owner.
        """

        return tuple(field for field, _ in self._contributed_fields.get(source, ()))

    def _validate_required_parents(self) -> dict[type[models.Model], type[models.Model]]:
        """Validate abstract capabilities against each materialized parent chain.

        The requirement adds no fields or Python bases. The declared parent is
        still the only owner of MTI layout and inherited persistence behavior.
        """
        requirements: dict[type[models.Model], type[models.Model]] = {}
        for source in self.ordered_models:
            parents: set[type[models.Model]] = set()
            parent = self.parent(source)
            while parent is not None:
                parents.add(parent)
                parent = self.parent(parent)
            for contributor in (*self.donors(source), source):
                for declaration in contributor.__mro__:
                    if not issubclass(declaration, models.Model):
                        continue
                    reference = _model_reference(declaration, "requires_parent")
                    if reference is None:
                        continue
                    required = self.models_by_label.get(reference)
                    if required is None:
                        raise ImproperlyConfigured(
                            f"{declaration._meta.label}.requires_parent references unknown model {reference!r}."
                        )
                    if required not in parents:
                        raise ImproperlyConfigured(
                            f"{source._meta.label} requires parent {required._meta.label} through "
                            f"{declaration._meta.label}; declare it in the materialized model's extends chain."
                        )
                    requirements[declaration] = required
        return requirements

    def _validate_fields(self) -> None:
        """Reject competing additive fields and parent columns redeclared by children.

        Django deep-copies abstract fields while preserving their creation counter.
        Shared abstract ancestry therefore contributes one declaration; two fields
        independently declared under one name are an ambiguous additive extension.
        """

        fields_by_label: dict[str, dict[str, models.Field]] = {}
        for source in self.ordered_models:
            owners: dict[str, tuple[models.Field, type[models.Model] | None]] = {}
            parent = self.parent(source)
            inherited = fields_by_label[cast(str, parent._meta.label_lower)] if parent is not None else {}
            for base in (*self.donors(source), source):
                for field in (*base._meta.local_fields, *base._meta.local_many_to_many, *base._meta.private_fields):
                    if field.name in inherited:
                        parent_model = cast(type[models.Model], parent)
                        raise ImproperlyConfigured(
                            f"{source._meta.label} redeclares parent field {field.name!r} "
                            f"from {parent_model._meta.label}; "
                            "use a narrow abstract child/donor with only its contributed fields."
                        )
                    previous = owners.setdefault(field.name, (field, base))
                    if previous[0].creation_counter != field.creation_counter:
                        raise ImproperlyConfigured(
                            f"{source._meta.label_lower} composes field {field.name!r} from "
                            f"both {previous[1]._meta.label if previous[1] is not None else 'shared ancestry'} "
                            f"and {base._meta.label}"
                        )
                    if previous[0] is not field:
                        owners[field.name] = (previous[0], source if base is source else None)
            fields_by_label[source._meta.label_lower] = {
                **inherited,
                **{name: field for name, (field, _) in owners.items()},
            }
            self._contributed_fields[source] = tuple(
                (field, base)
                for _, (field, base) in sorted(owners.items())
                if base is not source and (field.concrete or field.many_to_many)
            )

    def _validate_donor_managers(self) -> None:
        """Reject independently declared managers with one name on a target."""

        for source in self.ordered_models:
            owners: dict[str, tuple[models.Manager, type[models.Model]]] = {}
            for donor in self.donors(source):
                for manager in donor._meta.local_managers:
                    if manager.auto_created:
                        continue
                    previous = owners.setdefault(manager.name, (manager, donor))
                    if previous[0].creation_counter != manager.creation_counter:
                        raise ImproperlyConfigured(
                            f"{source._meta.label_lower} composes manager {manager.name!r} from "
                            f"both {previous[1]._meta.label} and {donor._meta.label}"
                        )
