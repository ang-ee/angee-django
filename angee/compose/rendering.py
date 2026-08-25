"""Concrete-model source planning, validation, and rendering.

The renderer owns every fact needed to turn Runtime's discovered composition
into concrete Django model source. It receives plain grouped declarations and
never reaches back into Runtime or its generated-tree lifecycle.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
from django.db import models

from angee.base.emission import ModelClassAttribute, ModelDecorator
from angee.base.models import AngeeModel
from angee.base.transitions import revalidate_transition_metadata

_RESOURCE_LOAD_HOOK = "after_resource_load"
"""Model classmethod the resource loader fans in once every selected row loads."""


@dataclass(frozen=True, slots=True)
class RuntimeModelRenderPlan:
    """Named render plan for one concrete runtime model class."""

    model_class: type[AngeeModel]
    source_alias: str
    runtime_parent_alias: str | None
    child_overrides_parent: bool
    """Whether this materialized child emits its abstract source before its concrete parent.

    ``True`` only for a child declaring ``child_overrides_parent`` (F-e); it flips
    the base tuple to ``[donors] → source → parent`` so the source's methods win
    the MRO natively. ``False`` keeps the parent-first status quo.
    """

    override_removed_fields: tuple[str, ...]
    """Parent-shared abstract fields the flipped child re-declares as ``None``.

    Empty unless ``child_overrides_parent``. Child-first emission lists the source
    before the concrete parent, so Django copies the source's fields — including
    the framework fields it flattened from shared abstract ancestors
    (``created_at``/``updated_at``) — as *local* before the MTI parent link would
    dedup them, duplicating the parent's columns. Re-declaring each as ``None``
    drops the copy so the child inherits the parent's column and the emitted schema
    matches the parent-first order.
    """

    extension_bases: tuple[type[models.Model], ...]
    extension_aliases: tuple[tuple[type[models.Model], str], ...]
    decorators: tuple[ModelDecorator, ...]
    attributes: tuple[ModelClassAttribute, ...]
    after_resource_load_aliases: tuple[str, ...]
    """Composed-base aliases whose ``after_resource_load`` the concrete model aggregates.

    Empty for the single-donor status quo — one implementation resolves natively
    through the concrete model's MRO, so the composer emits nothing new.
    """


class ModelRenderer:
    """Plan, validate, and render concrete runtime model sources."""

    def __init__(
        self,
        *,
        sources_by_label: dict[str, tuple[type[AngeeModel], ...]],
        source_models_by_composition_label: dict[str, type[AngeeModel]],
        extensions: dict[str, tuple[type[AngeeModel], ...]],
        runtime_module: str,
    ) -> None:
        """Bind the complete discovered composition needed for rendering."""

        self.sources_by_label = sources_by_label
        self.source_models_by_composition_label = source_models_by_composition_label
        self.extensions = extensions
        self.runtime_module = runtime_module
        self._validated = False

    def validate(self) -> None:
        """Validate render-specific child override contracts once."""

        if self._validated:
            return
        self._check_child_overrides()
        self._validated = True

    def render(
        self,
        label: str,
        source_models: tuple[type[AngeeModel], ...],
    ) -> str:
        """Return concrete model source for one target label.

        This is what makes a source addon's abstract models real. For each
        source model it emits a concrete class that imports the abstract source
        (aliased ``Abstract<Name>``), any same-row ``extends`` extension bases,
        and, for ``runtime = True`` materialized children, the concrete generated
        parent model named by ``extends``. It lists extension bases first, then
        the concrete parent when present, then the source, and pins
        ``Meta.abstract = False`` with ``app_label = label`` — so the generated
        class registers under the source addon's label when the composer imports
        ``runtime.<label>.models``. Django-owned ``Meta`` facts ride along
        through ``class Meta(_SourceMeta)``; REBAC Meta options are re-emitted
        because Django discards non-standard Meta attributes.
        Mixins may contribute model decorators and class-body attributes through
        declared emission seams. Field collisions across the composed bases are
        rejected at construction (``_check_field_collisions``).
        """

        self.validate()
        lines = [
            '"""Concrete Django models emitted by Angee."""',
            "",
            "from __future__ import annotations",
            "",
        ]
        imports: list[str] = []
        render_plans: list[RuntimeModelRenderPlan] = []
        for model_class in self._ordered_source_models(label, source_models):
            source_alias = f"Abstract{model_class.__name__}"
            imports.extend(self._class_import(model_class, source_alias))
            runtime_parent_alias = self._runtime_parent_alias(model_class)
            if runtime_parent_alias is not None:
                runtime_parent_import = self._runtime_parent_import(label, model_class, runtime_parent_alias)
                if runtime_parent_import is not None:
                    imports.append(runtime_parent_import)
            extension_bases = self.extension_bases(model_class)
            aliased_extensions: list[tuple[type[models.Model], str]] = []
            for index, extension_base in enumerate(extension_bases, start=1):
                alias = f"{model_class.__name__}Extension{index}"
                aliased_extensions.append((extension_base, alias))
                imports.extend(self._class_import(extension_base, alias))
            decorators = self._model_decorators(model_class, extension_bases)
            attributes = self._model_attributes(label, model_class, extension_bases)
            imports.extend(self._model_decorator_imports(decorators))
            imports.extend(self._model_attribute_imports(attributes))
            child_overrides_parent = runtime_parent_alias is not None and model_class.overrides_runtime_parent()
            render_plans.append(
                RuntimeModelRenderPlan(
                    model_class=model_class,
                    source_alias=source_alias,
                    runtime_parent_alias=runtime_parent_alias,
                    child_overrides_parent=child_overrides_parent,
                    override_removed_fields=(
                        self.child_override_removed_fields(model_class) if child_overrides_parent else ()
                    ),
                    extension_bases=extension_bases,
                    extension_aliases=tuple(aliased_extensions),
                    decorators=decorators,
                    attributes=attributes,
                    after_resource_load_aliases=self._after_resource_load_aliases(
                        model_class,
                        source_alias=source_alias,
                        runtime_parent_alias=runtime_parent_alias,
                        extension_aliases=tuple(aliased_extensions),
                        child_overrides_parent=child_overrides_parent,
                    ),
                )
            )

        lines.extend(sorted(set(imports)))
        lines.append("")
        for plan in render_plans:
            meta_name = f"_{plan.model_class.__name__}Meta"
            base_names = [alias for _extension, alias in plan.extension_aliases]
            if plan.runtime_parent_alias is None:
                base_names.append(plan.source_alias)
            elif plan.child_overrides_parent:
                # F-e: the abstract source before the concrete parent, so the
                # child's own methods win the MRO natively.
                base_names.extend([plan.source_alias, plan.runtime_parent_alias])
            else:
                base_names.extend([plan.runtime_parent_alias, plan.source_alias])
            meta_lines = [
                "        abstract = False",
                f"        app_label = {label!r}",
            ]
            # Donor Metas must stay standalone: subclassing the source Meta
            # would make this explicit merge duplicate inherited constraints.
            constraint_aliases = [
                alias
                for extension, alias in plan.extension_aliases
                if extension._meta.constraints
            ]
            if constraint_aliases:
                inherited_constraints = ", ".join(
                    f"*{alias}._meta.constraints" for alias in constraint_aliases
                )
                meta_lines.append(
                    f"        constraints = [*getattr({meta_name}, 'constraints', []), "
                    f"{inherited_constraints}]"
                )
            meta_lines.extend(self._rebac_meta_source(plan.model_class))
            body_lines = [
                line for name in plan.override_removed_fields for line in (f"    {name} = None", "")
            ]
            body_lines.extend(self._catalogue_marker_source(plan.model_class))
            body_lines.extend(
                self._rebac_grantable_marker_source(
                    plan.model_class,
                    masks_runtime_parent=plan.runtime_parent_alias is not None,
                )
            )
            body_lines.extend(
                line for attribute in plan.attributes for line in (*self._model_attribute_source(attribute), "")
            )
            decorator_lines = [
                self._model_decorator_source(
                    plan.model_class,
                    plan.extension_bases,
                    decorator,
                )
                for decorator in plan.decorators
            ]
            lines.extend(
                [
                    f"{meta_name} = getattr({plan.source_alias}, 'Meta', object)",
                    "",
                    *decorator_lines,
                    f"class {plan.model_class.__name__}({', '.join(base_names)}):",
                    f'    """Concrete {plan.model_class.__name__} model."""',
                    "",
                    *body_lines,
                    *self._after_resource_load_source(plan.after_resource_load_aliases),
                    f"    class Meta({meta_name}):",
                    *meta_lines,
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    def _catalogue_marker_source(self, model_class: type[AngeeModel]) -> tuple[str, ...]:
        """Return concrete class-body source for a non-inherited catalogue marker."""

        if not model_class.is_catalogue_model():
            return ()
        return (
            "    catalogue = True",
            f"    catalogue_tier = {json.dumps(model_class.get_catalogue_tier())}",
            "",
        )

    def _rebac_grantable_marker_source(
        self,
        model_class: type[AngeeModel],
        *,
        masks_runtime_parent: bool,
    ) -> tuple[str, ...]:
        """Carry a declared record-share marker onto the concrete runtime model."""

        declared = model_class.__dict__.get("rebac_grantable")
        if declared is None and not masks_runtime_parent:
            return ()
        grantable = model_class.get_rebac_grantable() if declared is not None else {}
        return (f"    rebac_grantable = {json.dumps(grantable, sort_keys=True)}", "")

    def _after_resource_load_aliases(
        self,
        model_class: type[AngeeModel],
        *,
        source_alias: str,
        runtime_parent_alias: str | None,
        extension_aliases: tuple[tuple[type[models.Model], str], ...],
        child_overrides_parent: bool = False,
    ) -> tuple[str, ...]:
        """Return composed-base aliases whose ``after_resource_load`` must aggregate.

        The resource loader resolves the hook once per composed model
        (``angee.resources.managers``) — so ``getattr(model, "after_resource_load")``
        picks only the first contributor its MRO reaches and shadows the rest.
        When two or more composed contributors run *distinct* implementations the
        composer emits an aggregating classmethod (``_after_resource_load_source``)
        that runs every one once; this returns the aliases it calls, in the same
        order as the emitted base tuple (extension donors first, then parent/source
        per the flip), so the aggregator walks the exact order the single-dispatch
        MRO would — first donor first.

        A materialized child's runtime parent is one contributor: its concrete
        emitted class already runs its *whole* composed set through its own
        ``after_resource_load`` (the parent's own aggregator, or the single func
        its MRO resolves — donors and the parent's parent included, recursively).
        So the child dedups its own donors and source against that whole set
        (``_resource_load_hook_funcs``): a function shared with any parent
        contributor runs once, via the parent, and the parent alias is emitted
        whenever the parent contributes anything. Modelling the parent by its
        *abstract source* alone would miss the parent-side donors and silently
        drop or double-run their hooks.

        Returns an empty tuple when at most one contributor survives dedup: one
        implementation resolves natively through the concrete MRO (the parent's
        aggregator, a lone donor, or the source) and the runtime stays
        byte-identical. ``child_overrides_parent`` mirrors the base-tuple flip
        (source before parent).
        """

        parent = self._runtime_parent_source(model_class) if runtime_parent_alias is not None else None
        parent_funcs = self._resource_load_hook_funcs(parent) if parent is not None else set()
        seen: set[Any] = set(parent_funcs)
        donor_aliases: list[str] = []
        for base, alias in extension_aliases:
            func = getattr(getattr(base, _RESOURCE_LOAD_HOOK, None), "__func__", None)
            if func is None or func in seen:
                continue
            seen.add(func)
            donor_aliases.append(alias)
        source_func = getattr(getattr(model_class, _RESOURCE_LOAD_HOOK, None), "__func__", None)
        source = source_alias if (source_func is not None and source_func not in seen) else None
        parent_alias = runtime_parent_alias if parent_funcs else None
        tail = (source, parent_alias) if child_overrides_parent else (parent_alias, source)
        aliases = [*donor_aliases, *(alias for alias in tail if alias is not None)]
        return tuple(aliases) if len(aliases) > 1 else ()

    def extension_bases(self, model_class: type[AngeeModel]) -> tuple[type[models.Model], ...]:
        """Return the abstract bases same-row extensions contribute to ``model_class``."""

        return tuple(
            base
            for extension in self.extensions.get(model_class._meta.label_lower, ())
            for base in extension.get_extension_bases()
        )

    def _runtime_parent_source(self, model_class: type[AngeeModel]) -> type[AngeeModel] | None:
        """Return the abstract parent source a materialized child extends, or ``None``."""

        target = model_class.get_extension_target()
        if target is None or not model_class.is_runtime_model():
            return None
        return self.source_models_by_composition_label[target]

    def _resource_load_contributor_bases(self, model_class: type[AngeeModel]) -> tuple[type[models.Model], ...]:
        """Return the composed bases a model's ``after_resource_load`` runs, in emitted-MRO order.

        Flattens a materialized child's runtime parent into the parent's own full
        contributor set (its extension donors, its parent recursively, then its
        source), so a child sees everything the concrete parent already runs — the
        parent's composed aggregator, not just its abstract source.
        """

        donors = self.extension_bases(model_class)
        parent = self._runtime_parent_source(model_class)
        parent_bases = self._resource_load_contributor_bases(parent) if parent is not None else ()
        if parent is not None and model_class.overrides_runtime_parent():
            return (*donors, model_class, *parent_bases)
        return (*donors, *parent_bases, model_class)

    def _resource_load_hook_funcs(self, model_class: type[AngeeModel]) -> set[Any]:
        """Return the distinct ``after_resource_load`` funcs a model's emitted class runs."""

        funcs: set[Any] = set()
        for base in self._resource_load_contributor_bases(model_class):
            func = getattr(getattr(base, _RESOURCE_LOAD_HOOK, None), "__func__", None)
            if func is not None:
                funcs.add(func)
        return funcs

    def _after_resource_load_source(self, aliases: tuple[str, ...]) -> list[str]:
        """Return the aggregating ``after_resource_load`` classmethod body lines.

        The emitted method forwards to each contributor's own implementation
        through ``__func__`` (unbinding the donor's classmethod so it runs against
        the composed ``cls``). The loader brackets the call in the load
        transaction, so a donor that raises fails the load loudly and rolls back —
        no skip-and-continue, and a rerun is idempotent.
        """

        if not aliases:
            return []
        lines = [
            "    @classmethod",
            f"    def {_RESOURCE_LOAD_HOOK}(cls, *args: object, **kwargs: object) -> None:",
            '        """Run each composed after_resource_load contributor once, in dependency order."""',
        ]
        lines.extend(f"        {alias}.{_RESOURCE_LOAD_HOOK}.__func__(cls, *args, **kwargs)" for alias in aliases)
        lines.append("")
        return lines

    def _model_decorators(
        self,
        model_class: type[models.Model],
        extension_bases: tuple[type[models.Model], ...],
    ) -> tuple[ModelDecorator, ...]:
        """Return model decorators contributed by composed abstract bases."""

        decorators: list[ModelDecorator] = []
        decorators_by_path: dict[str, ModelDecorator] = {}
        seen_owners: set[type] = set()
        for base in (*extension_bases, model_class):
            for owner in base.__mro__:
                if owner in seen_owners:
                    continue
                seen_owners.add(owner)
                declared = owner.__dict__.get("angee_model_decorators", ())
                for decorator in declared:
                    if not isinstance(decorator, ModelDecorator):
                        raise ImproperlyConfigured(
                            f"{owner.__module__}.{owner.__name__}.angee_model_decorators "
                            "must contain ModelDecorator instances"
                        )
                    if decorator.enabled_by_model_attr:
                        _found, enabled = self._composed_model_attr(
                            model_class,
                            extension_bases,
                            decorator.enabled_by_model_attr,
                        )
                        if not enabled:
                            continue
                    previous = decorators_by_path.get(decorator.import_path)
                    if previous is None:
                        decorators_by_path[decorator.import_path] = decorator
                        decorators.append(decorator)
                    elif previous != decorator:
                        raise ImproperlyConfigured(
                            f"{model_class._meta.label} composes conflicting decorators for {decorator.import_path!r}"
                        )
        return tuple(decorators)

    def _model_attributes(
        self,
        label: str,
        model_class: type[models.Model],
        extension_bases: tuple[type[models.Model], ...],
    ) -> tuple[ModelClassAttribute, ...]:
        """Return class-body attributes contributed by composed abstract bases."""

        attributes: list[ModelClassAttribute] = []
        attributes_by_name: dict[str, ModelClassAttribute] = {}
        seen_owners: set[type] = set()
        for base in (*extension_bases, model_class):
            for owner in base.__mro__:
                if owner in seen_owners:
                    continue
                seen_owners.add(owner)
                if "angee_model_attributes" not in owner.__dict__:
                    continue
                declared = cast(Any, owner).angee_model_attributes(
                    app_label=label,
                    model_class=model_class,
                    extension_bases=extension_bases,
                )
                for attribute in declared:
                    if not isinstance(attribute, ModelClassAttribute):
                        raise ImproperlyConfigured(
                            f"{owner.__module__}.{owner.__name__}.angee_model_attributes "
                            "must return ModelClassAttribute instances"
                        )
                    previous = attributes_by_name.get(attribute.name)
                    if previous is None:
                        attributes_by_name[attribute.name] = attribute
                        attributes.append(attribute)
                    elif previous != attribute:
                        raise ImproperlyConfigured(
                            f"{model_class._meta.label} composes conflicting class attributes for {attribute.name!r}"
                        )
        return tuple(attributes)

    def _model_decorator_imports(
        self,
        decorators: tuple[ModelDecorator, ...],
    ) -> list[str]:
        """Return import lines for model decorators."""

        return [f"import {self._model_decorator_module(decorator)}" for decorator in decorators]

    def _model_attribute_imports(
        self,
        attributes: tuple[ModelClassAttribute, ...],
    ) -> list[str]:
        """Return import lines for model class attributes."""

        return [f"import {self._model_attribute_module(attribute)}" for attribute in attributes]

    def _model_decorator_source(
        self,
        model_class: type[models.Model],
        extension_bases: tuple[type[models.Model], ...],
        decorator: ModelDecorator,
    ) -> str:
        """Return one emitted class decorator line."""

        parts = [repr(arg) for arg in decorator.args]
        parts.extend(f"{name}={value!r}" for name, value in decorator.kwargs)
        for name, attr in decorator.kwargs_from_model:
            found, value = self._composed_model_attr(
                model_class,
                extension_bases,
                attr,
            )
            if not found:
                raise ImproperlyConfigured(
                    f"{model_class._meta.label} decorator {decorator.import_path!r} requires model attribute {attr!r}"
                )
            parts.append(f"{name}={value!r}")
        return f"@{decorator.import_path}({', '.join(parts)})"

    def _model_attribute_source(
        self,
        attribute: ModelClassAttribute,
    ) -> list[str]:
        """Return one emitted class attribute declaration."""

        parts = [repr(arg) for arg in attribute.args]
        parts.extend(f"{name}={value!r}" for name, value in attribute.kwargs)
        return [f"    {attribute.name} = {attribute.import_path}({', '.join(parts)})"]

    def _composed_model_attr(
        self,
        model_class: type[models.Model],
        extension_bases: tuple[type[models.Model], ...],
        attr: str,
    ) -> tuple[bool, object]:
        """Return an attribute using the generated concrete model base order."""

        for base in (*extension_bases, model_class):
            if hasattr(base, attr):
                return True, getattr(base, attr)
        return False, None

    def _model_decorator_module(self, decorator: ModelDecorator) -> str:
        """Return the module imported for one decorator path."""

        module, _separator, name = decorator.import_path.rpartition(".")
        if not module or not name:
            raise ImproperlyConfigured(f"Model decorator import path must be dotted: {decorator.import_path!r}")
        return module

    def _model_attribute_module(self, attribute: ModelClassAttribute) -> str:
        """Return the module imported for one class attribute path."""

        module, _separator, name = attribute.import_path.rpartition(".")
        if not module or not name:
            raise ImproperlyConfigured(f"Model class attribute import path must be dotted: {attribute.import_path!r}")
        return module

    def _check_child_overrides(self) -> None:
        """Enforce the materialized-child override opt-in's build-time guards (F-e).

        A child that declares ``child_overrides_parent`` has its base tuple flipped
        so its own methods win the MRO (``render``). The flip is only safe
        when it changes nothing else, so this asserts, against a child-first probe
        built from the abstract sources (so the composer fails before emitting an
        unsafe runtime):

        - the opt-in is on a genuine materialized child (``runtime`` + ``extends``);
        - the reorder does not silently swap the child's default manager — it must
          inherit the parent's concrete default manager or declare its own
          explicitly (a child may declare a manager that differs from its parent,
          so a global flip is unsafe and only a per-child opt-in is offered);
        - the child's guarded-transition metadata still validates on the flipped
          MRO (``StateTransitions``/``@transition`` class-build checks hold).
        """

        for source_models in self.sources_by_label.values():
            for model_class in source_models:
                if not model_class.overrides_runtime_parent():
                    continue
                target = model_class.get_extension_target()
                if target is None or not model_class.is_runtime_model():
                    raise ImproperlyConfigured(
                        f"{model_class.__module__}.{model_class.__name__} sets "
                        "child_overrides_parent but is not a materialized child "
                        "(it needs runtime = True and extends = '<app>.<Model>')."
                    )
                parent = self.source_models_by_composition_label[target]
                probe = self._child_override_probe(model_class, parent)
                self._check_child_override_manager(model_class, parent, probe)
                revalidate_transition_metadata(probe)

    def _child_override_probe(
        self,
        child_class: type[AngeeModel],
        parent_class: type[AngeeModel],
    ) -> type[models.Model]:
        """Return an abstract probe modeling the child-first emitted base order.

        The probe reorders the abstract sources exactly as ``render`` will
        order the concrete bases under the flip — extension donors first, then the
        source, then the parent (``[donors] → source → parent``) — so Django's own
        manager resolution and the transition checks read the exact emitted MRO. It
        is abstract, so it never registers in the app registry and is safe to
        rebuild.
        """

        return type(
            f"_{child_class.__name__}ChildOverrideProbe",
            (*self.extension_bases(child_class), child_class, parent_class),
            {
                "__module__": f"{self.runtime_module}._child_override_probe",
                "Meta": type("Meta", (), {"abstract": True, "app_label": parent_class._meta.app_label}),
            },
        )

    def child_override_removed_fields(self, child_class: type[AngeeModel]) -> tuple[str, ...]:
        """Return the parent-shared abstract fields a flipped child must drop (F-e).

        A child that flips to child-first re-contributes the fields it flattened
        from shared abstract ancestors (``created_at``/``updated_at``) as local,
        duplicating the concrete parent's columns (``RuntimeModelRenderPlan``). The
        emitted class shadows each with ``None`` to inherit the parent's column
        instead. Only fields the child inherited from an abstract base *and* the
        parent also owns are dropped — the child's own new fields stay.

        Shadowing is only sound when the child's copy and the parent's column are
        the *same* field: a same-name field the child deliberately redefined would
        vanish behind the ``None`` shadow, silently dropping the override. Each
        dropped field is proven identical by ``deconstruct()`` first, so a genuine
        divergence fails the build loudly rather than disappearing.
        """

        target = cast(str, child_class.get_extension_target())
        parent_class = self.source_models_by_composition_label[target]
        local = {field.name for field in (*child_class._meta.local_fields, *child_class._meta.local_many_to_many)}
        inherited = local - set(self.declared_fields(child_class))
        # Forward fields only (``fields``/``many_to_many``) — ``get_fields`` builds the
        # reverse-relation tree, which needs a ready app registry that render (run
        # during app population) does not have.
        parent_fields = {field.name for field in (*parent_class._meta.fields, *parent_class._meta.many_to_many)}
        removed = tuple(sorted(inherited & parent_fields))
        for name in removed:
            child_field = child_class._meta.get_field(name)
            parent_field = parent_class._meta.get_field(name)
            if child_field.deconstruct()[1:] != parent_field.deconstruct()[1:]:
                raise ImproperlyConfigured(
                    f"{child_class._meta.label} sets child_overrides_parent and redefines "
                    f"inherited field {name!r}, but the parent {parent_class._meta.label} owns a "
                    "different column of the same name; the child-first flip would drop the "
                    "override behind a None shadow. Rename the child field or align its definition "
                    "with the parent's."
                )
        return removed

    def _check_child_override_manager(
        self,
        child_class: type[AngeeModel],
        parent_class: type[AngeeModel],
        probe: type[models.Model],
    ) -> None:
        """Assert the child-first flip does not silently swap the default manager."""

        parent_default = type(parent_class._meta.default_manager)
        child_default = type(probe._meta.default_manager)
        if child_default is parent_default:
            return  # inherits the parent's concrete default manager
        if child_class._meta.local_managers:
            return  # declares its own default manager explicitly
        raise ImproperlyConfigured(
            f"{child_class._meta.label} sets child_overrides_parent but the child-first "
            f"base order resolves its default manager to {child_default.__name__} instead "
            f"of the parent's {parent_default.__name__}; declare the manager explicitly on "
            "the child or let it inherit the parent's."
        )

    def _class_import(
        self,
        model_class: type[models.Model],
        alias: str,
    ) -> list[str]:
        """Return the ``from <module> import <name>`` line that references ``model_class``."""

        self._check_role_anchor_binding(model_class)
        return [f"from {model_class.__module__} import {model_class.__name__} as {alias}"]

    def _check_role_anchor_binding(self, model_class: type[models.Model]) -> None:
        """Verify a ``role_anchor`` resolves back to the class its import will name.

        A ``role_anchor()`` binds its ``__module__`` from ``sys._getframe`` (the
        adopting module's) — the one-line ergonomic default. A wrapper indirecting
        that call would capture the wrapper's module instead, so the emitted
        ``from <module> import <name>`` would resolve to a different symbol or none.
        Failing here — at emission, naming the mis-captured anchor — beats leaving a
        broken generated import to blow up on the next runtime load. Scoped to the
        anchor factory's own output (``__angee_role_anchor__``); ordinary source
        models emit from real module-level classes and need no probe (and the
        composer renders synthetic test modules that are not importable).
        """

        if not getattr(model_class, "__angee_role_anchor__", False):
            return
        # The adopter module is already imported (the composer scanned it to reach
        # this anchor), so read it from ``sys.modules`` rather than re-importing —
        # which also tolerates a synthetic, unregistered module used only to render.
        module = sys.modules.get(model_class.__module__)
        if module is None:
            return
        if getattr(module, model_class.__name__, None) is not model_class:
            raise ImproperlyConfigured(
                f"role_anchor {model_class.__name__!r} does not bind in {model_class.__module__!r}; "
                "sys._getframe captured the wrong module — pass module=__name__ explicitly when "
                "creating the anchor through a wrapper instead of at module level."
            )

    def _runtime_parent_alias(
        self,
        model_class: type[AngeeModel],
    ) -> str | None:
        """Return the concrete runtime parent alias for a materialized child."""

        target = model_class.get_extension_target()
        if target is None or not model_class.is_runtime_model():
            return None
        return self.source_models_by_composition_label[target].__name__

    def _runtime_parent_import(
        self,
        label: str,
        model_class: type[AngeeModel],
        alias: str,
    ) -> str | None:
        """Return the import line for a materialized child's concrete parent."""

        target = model_class.get_extension_target()
        if target is None:
            raise ImproperlyConfigured(f"{model_class.__module__}.{model_class.__name__} has no runtime parent target")
        parent = self.source_models_by_composition_label[target]
        if parent._meta.app_label == label:
            return None
        return f"from {self.runtime_module}.{parent._meta.app_label}.models import {parent.__name__} as {alias}"

    def _ordered_source_models(
        self,
        label: str,
        source_models: tuple[type[AngeeModel], ...],
    ) -> tuple[type[AngeeModel], ...]:
        """Return source models with same-app runtime parents emitted first."""

        remaining = sorted(source_models, key=lambda cls: cls._meta.object_name)
        ordered: list[type[AngeeModel]] = []
        ordered_set: set[type[AngeeModel]] = set()
        while remaining:
            progressed = False
            for model_class in tuple(remaining):
                target = model_class.get_extension_target()
                parent = self.source_models_by_composition_label.get(target) if target else None
                if parent is not None and parent._meta.app_label == label and parent not in ordered_set:
                    continue
                ordered.append(model_class)
                ordered_set.add(model_class)
                remaining.remove(model_class)
                progressed = True
            if not progressed:
                blocked = ", ".join(model.__name__ for model in remaining)
                raise ImproperlyConfigured(f"Runtime app {label!r} has cyclic materialized child models: {blocked}")
        return tuple(ordered)

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

    def declared_fields(
        self,
        model_class: type[models.Model],
    ) -> tuple[str, ...]:
        """Return fields directly declared by one abstract composition base."""

        meta = model_class._meta
        local = {field.name for field in (*meta.local_fields, *meta.local_many_to_many)}
        inherited: set[str] = set()
        for base in model_class.__mro__[1:]:
            base_meta = getattr(base, "_meta", None)
            if not issubclass(base, models.Model) or base_meta is None or not base_meta.abstract:
                continue
            inherited.update(
                field.name
                for field in (
                    *base_meta.local_fields,
                    *base_meta.local_many_to_many,
                )
            )
        return tuple(sorted(local - inherited))

