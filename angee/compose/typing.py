"""Mypy adapters for declarations whose concrete owner is the composer.

Field, manager and settings checking stays with django-stubs. This adapter
describes source composition and installed settings declarations.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Literal

from django.db import models
from mypy.join import join_types
from mypy.maptype import map_instance_to_supertype
from mypy.mro import calculate_mro
from mypy.nodes import (
    MDEF,
    Decorator,
    FuncDef,
    Import,
    ImportFrom,
    MemberExpr,
    MypyFile,
    OverloadedFuncDef,
    PlaceholderNode,
    SymbolTableNode,
    TypeInfo,
    Var,
)
from mypy.options import Options
from mypy.plugin import AttributeContext, ClassDefContext, DynamicClassDefContext
from mypy.semanal_shared import PRIORITY_FALLBACKS, has_placeholder
from mypy.server.trigger import make_trigger
from mypy.typeops import expand_type, expand_type_by_instance
from mypy.types import AnyType, CallableType, Instance, Overloaded, TypedDictType, TypeType, get_proper_type
from mypy.types import Type as MypyType
from mypy.typevars import fill_typevars
from mypy_django_plugin.django.context import DjangoContext
from mypy_django_plugin.lib import fullnames, helpers
from mypy_django_plugin.main import NewSemanalDjangoPlugin
from mypy_django_plugin.transformers.managers import create_new_manager_class_from_from_queryset_method

from angee.compose.yamlconf import setting_name, setting_sources

if TYPE_CHECKING:
    from mypy.checker import TypeChecker
    from mypy.semanal import SemanticAnalyzer


class CompositionContext(DjangoContext):
    """Resolve static declaration identities from the composer's native models."""

    def __init__(self, django_settings_module: str) -> None:
        super().__init__(django_settings_module)
        self.required_parents: dict[type[models.Model], type[models.Model]] = {}
        self.composed: dict[str, type[models.Model]] = {}
        self.static_names: dict[type[models.Model], str] = {}
        self.extends: dict[str, str] = {}
        self.contributions: dict[str, tuple[str, tuple[str, ...]]] = {}
        self.member_origins: dict[str, dict[str, str]] = {}
        self.member_receivers: dict[str, dict[str, str]] = {}
        self.member_modules: dict[str, tuple[str, ...]] = {}
        if not self.apps_registry.is_installed("angee.compose"):
            return
        # Runtime owns declarations; serving models retain their native classes.
        from angee.compose.runtime import Runtime

        composition = Runtime.from_django().composition
        self.required_parents = composition.required_parents
        for source in composition.ordered_models:
            fullname = f"{source.__module__}.{source.__qualname__}"
            concrete = self.apps_registry.get_model(source._meta.app_label, source.__name__)
            self.composed[fullname] = concrete
            self.static_names[concrete] = fullname
            if (parent := composition.parent(source)) is not None:
                self.extends[fullname] = f"{parent.__module__}.{parent.__qualname__}"
            for donor in composition.donors(source):
                donor_name = f"{donor.__module__}.{donor.__qualname__}"
                self.composed[donor_name] = concrete
                self.extends[donor_name] = fullname
                self.contributions[donor_name] = (fullname, tuple(
                    field.name for field in composition.contributed_fields(source) if field.model is donor
                ))
            # The materialized class already owns Python's C3 winner. Ignore
            # authored/native target ancestors: their existing contracts remain.
            origins: dict[str, str] = {}
            receivers: dict[str, str] = {}
            modules: set[str] = set()
            donors = composition.donors(source)
            donor_owners = {owner for donor in donors for owner in donor.__mro__}
            for owner in concrete.__mro__:
                if owner not in donor_owners or owner in source.__mro__:
                    continue
                owner_name = f"{owner.__module__}.{owner.__qualname__}"
                modules.add(owner.__module__)
                receiver = next(donor for donor in concrete.__mro__ if donor in donors and owner in donor.__mro__)
                for name in vars(owner):
                    origins.setdefault(name, owner_name)
                    receivers.setdefault(name, f"{receiver.__module__}.{receiver.__qualname__}")
            self.member_origins[fullname] = origins
            self.member_receivers[fullname] = receivers
            self.member_modules[source.__module__] = tuple(sorted({
                *self.member_modules.get(source.__module__, ()), *modules,
            }))
        for declaration, parent in composition.required_parents.items():
            self.extends.setdefault(
                f"{declaration.__module__}.{declaration.__qualname__}",
                f"{parent.__module__}.{parent.__qualname__}",
            )

    def get_model_fullname(self, model_cls: type[models.Model]) -> str:
        return self.static_names.get(model_cls) or super().get_model_fullname(model_cls)

    def get_model_class_by_fullname(self, fullname: str) -> type[models.Model] | None:
        return self.composed.get(fullname) or super().get_model_class_by_fullname(fullname)

    def is_fallback_manager(self, manager: models.Manager[models.Model]) -> bool:
        """Identify Django's implicit manager; host adapters extend native markers."""

        return manager.auto_created

    def get_model_manager(
        self, api: TypeChecker | SemanticAnalyzer, model_cls: type[models.Model],
        name: Literal["_default_manager", "_base_manager"],
    ) -> models.Manager[models.Model] | None:
        """Inherit checked parent managers only where Django marks a fallback."""
        manager = super().get_model_manager(api, model_cls, name)
        parent = self.required_parents.get(model_cls)
        if parent is None or manager is None or not self.is_fallback_manager(manager):
            return manager
        info = self.lookup_model_typeinfo(api, model_cls)
        if info is None:
            return manager
        meta = info.names.get("Meta")
        if meta is not None and isinstance(meta.node, TypeInfo) and name[1:] + "_name" in meta.node.names:
            return manager
        parent_model = self.get_model_class_by_fullname(helpers.get_class_fullname(parent))
        if parent_model is None:
            api.fail(f"Unavailable required parent {helpers.get_class_fullname(parent)}", info.defn)
            return manager
        selected = super().get_model_manager(api, parent_model, name)
        if selected is None:
            return manager
        override = model_cls._meta.managers_map.get(selected.name)
        return override if override is not None and not self.is_fallback_manager(override) else selected


class CompositionPlugin(NewSemanalDjangoPlugin):
    """Extend the native Django plugin with build-time composition declarations."""

    django_context: CompositionContext
    context_class: type[CompositionContext] = CompositionContext
    default_settings_module: str | None = None

    def __init__(self, options: Options) -> None:
        super().__init__(options)
        self.django_context = self.context_class(self.plugin_config.django_settings_module)

    def get_additional_deps(self, file: MypyFile) -> list[tuple[int, str, int]]:
        dependencies = super().get_additional_deps(file)
        dependencies.extend(
            self._new_dependency(module)
            for module in self.django_context.member_modules.get(file.fullname, ())
            if module != file.fullname
        )
        if any(
            isinstance(item, ImportFrom) and item.id == "django.conf"
            or isinstance(item, Import) and any(name == "django.conf" for name, _alias in item.ids)
            for item in file.imports
        ):
            # Consumers need direct edges: django.conf's interface itself does
            # not change when one autoconfig TypedDict changes.
            dependencies.extend(
                self._new_dependency(f"{app.name}.autoconfig")
                for app in self.django_context.apps_registry.get_app_configs()
                if f"{app.name}.autoconfig" in sys.modules
            )
            if self.default_settings_module is not None and self.django_context.composed:
                dependencies.append(self._new_dependency(self.default_settings_module))
        return dependencies

    def get_attribute_hook(self, fullname: str) -> Callable[[AttributeContext], MypyType] | None:
        native = super().get_attribute_hook(fullname)
        if fullname.rpartition(".")[0] == fullnames.DUMMY_SETTINGS_BASE_CLASS and native is not None:
            return partial(self._autoconfig_setting, native=native)
        return native

    def _autoconfig_setting(
        self, ctx: AttributeContext, *, native: Callable[[AttributeContext], MypyType],
    ) -> MypyType:
        if not isinstance(ctx.context, MemberExpr):
            return native(ctx)
        name = ctx.context.name
        modules = helpers.get_typechecker_api(ctx).modules
        project = modules.get(self.django_context.django_settings_module)
        if project is not None and name in project.names:
            return native(ctx)
        sources = setting_sources(self.django_context.settings, name)
        if not sources:
            defaults = (modules.get(self.default_settings_module)
                        if self.default_settings_module is not None and self.django_context.composed else None)
            symbol = defaults.names.get(name) if defaults is not None else None
            if symbol is not None and symbol.type is not None and not isinstance(
                get_proper_type(symbol.type), AnyType,
            ):
                return symbol.type
            return native(ctx)
        candidates: list[MypyType] = []
        for source in sources:
            module = modules.get(f"{source}.autoconfig")
            if module is None:
                continue
            symbol = module.names.get("SETTINGS")
            declaration = get_proper_type(symbol.type) if symbol is not None else None
            declared: list[MypyType] = []
            if isinstance(declaration, TypedDictType):
                declared = [value for key, value in declaration.items.items() if key.partition(":")[0] == name]
                candidates.extend(declared)
            if not declared:
                function = module.names.get("settings")
                signature = get_proper_type(function.type) if function is not None else None
                if isinstance(signature, CallableType):
                    result = get_proper_type(signature.ret_type)
                    if isinstance(result, TypedDictType):
                        candidates.extend(
                            value for key, value in result.items.items() if setting_name(key) == name
                        )
                    elif (
                        isinstance(result, Instance) and result.type.fullname == "builtins.dict"
                        and len(result.args) == 2 and source == sources[0]
                        and not isinstance(get_proper_type(result.args[1]), AnyType)
                    ):
                        candidates.append(result.args[1])
        if not candidates:
            return native(ctx)
        result_type = candidates[0]
        for candidate in candidates[1:]:
            result_type = join_types(result_type, candidate)
        return result_type

    def get_base_class_hook(self, fullname: str) -> Callable[[ClassDefContext], None] | None:
        native = super().get_base_class_hook(fullname)
        return partial(self._composition_contract, native=native) if native is not None else None

    def _composition_contract(
        self, ctx: ClassDefContext, *, native: Callable[[ClassDefContext], None],
    ) -> None:
        target_name = self.django_context.extends.get(ctx.cls.fullname)
        if target_name is not None:
            target = self.lookup_fully_qualified(target_name)
            if target is None or not isinstance(target.node, TypeInfo) or not target.node.mro:
                if ctx.api.final_iteration:
                    ctx.api.fail(f"Unresolved composition target {target_name}", ctx.cls)
                else:
                    ctx.api.defer()
                return
            target_info = target.node
            # Semantic reanalysis restores Python bases before rebuilding MRO.
            # The class's previous MRO can still contain a now-absent target.
            if not any(base.type.has_base(target_name) for base in ctx.cls.info.bases):
                inherited = {item.fullname for item in target_info.mro}
                bases = [base for base in ctx.cls.info.bases if base.type.fullname not in inherited]
                target_instance = fill_typevars(target_info)
                assert isinstance(target_instance, Instance)
                ctx.cls.info.bases = [*bases, target_instance]
            # A target's own declared parent may have resolved in a later pass.
            ctx.cls.info.mro = []
            calculate_mro(ctx.cls.info)
            ctx.api.add_plugin_dependency(make_trigger(target_name))
        native(ctx)
        contribution = self.django_context.contributions.get(ctx.cls.fullname)
        if contribution is not None:
            target_name, fields = contribution
            target = self.lookup_fully_qualified(target_name)
            assert target is not None and isinstance(target.node, TypeInfo)
            for name in fields:
                symbol = ctx.cls.info.get(name)
                if symbol is None:
                    ctx.api.fail(f"Missing contributed field {ctx.cls.fullname}.{name}", ctx.cls)
                    continue
                target.node.names[name] = symbol
                ctx.api.add_plugin_dependency(make_trigger(f"{ctx.cls.fullname}.{name}"), target=target_name)
            ctx.api.add_plugin_dependency(make_trigger(ctx.cls.fullname), target=target_name)
            self._schedule_members(ctx, target.node)
        if ctx.cls.fullname in self.django_context.member_origins:
            self._schedule_members(ctx, ctx.cls.info)

    def _schedule_members(self, ctx: ClassDefContext, target: TypeInfo) -> None:
        for name, origin in self.django_context.member_origins.get(target.fullname, {}).items():
            ctx.api.add_plugin_dependency(make_trigger(f"{origin}.{name}"), target=target.fullname)
            ctx.api.add_plugin_dependency(make_trigger(origin), target=target.fullname)
            receiver = self.django_context.member_receivers[target.fullname][name]
            ctx.api.add_plugin_dependency(make_trigger(receiver), target=target.fullname)
        helpers.get_semanal_api(ctx).schedule_patch(
            # Function signatures and overload/property metadata are complete
            # here; copying them in the base hook captures unanalyzed types.
            PRIORITY_FALLBACKS,
            partial(self._project_members, target, ctx.api.named_type("builtins.function")),
        )

    @staticmethod
    def _clear_projected_members(info: TypeInfo) -> None:
        for name in info.metadata.pop("angee_members", {}):
            symbol = info.names.get(name)
            if symbol is not None and symbol.plugin_generated and isinstance(symbol.node, Var):
                del info.names[name]

    def _project_members(self, target: TypeInfo, function_fallback: Instance) -> None:
        self._clear_projected_members(target)
        projected = target.metadata.setdefault("angee_members", {})
        for name, origin_name in self.django_context.member_origins.get(target.fullname, {}).items():
            if target.get(name) is not None and name not in projected:
                continue
            origin = self.lookup_fully_qualified(origin_name)
            if origin is None or not isinstance(origin.node, TypeInfo):
                continue
            receiver_name = self.django_context.member_receivers[target.fullname][name]
            receiver = self.lookup_fully_qualified(receiver_name)
            if receiver is None or not isinstance(receiver.node, TypeInfo):
                continue
            receiver_type = fill_typevars(receiver.node)
            assert isinstance(receiver_type, Instance)
            origin_type = map_instance_to_supertype(receiver_type, origin.node)
            symbol = origin.node.names.get(name)
            if symbol is None or symbol.node is None:
                continue
            node = symbol.node
            declaration: Var
            if isinstance(node, Var):
                declaration = node
            elif isinstance(node, (FuncDef, Decorator, OverloadedFuncDef)):
                function = node.func if isinstance(node, Decorator) else node
                signature: MypyType | None
                if isinstance(function, OverloadedFuncDef) and function.is_property:
                    getter = function.items[0]
                    if not isinstance(getter, Decorator):
                        continue
                    declaration = getter.var
                    signature = getter.func.type
                    if len(function.items) > 1 and isinstance(setter := function.items[1], Decorator):
                        setter_type = get_proper_type(setter.func.type)
                    else:
                        setter_type = None
                else:
                    declaration = node.var if isinstance(node, Decorator) else Var(name)
                    signature = node.var.type if isinstance(node, Decorator) else node.type
                    if signature is None and isinstance(node, Decorator) and not node.decorators and (
                        node.func.is_property or node.func.is_class or node.func.is_static
                    ):
                        signature = node.func.type
                    setter_type = declaration.setter_type
                if signature is None or has_placeholder(signature):
                    continue
                if isinstance(function, (FuncDef, OverloadedFuncDef)):
                    variable = Var(name, signature)
                    variable.info = origin.node
                    variable.is_initialized_in_class = True
                    variable.is_inferred = True
                    variable.is_classmethod = function.is_class
                    variable.is_staticmethod = function.is_static
                    variable.is_property = function.is_property
                    variable.is_classvar = not function.is_property and isinstance(
                        get_proper_type(signature), (CallableType, Overloaded),
                    )
                    variable.is_final = function.is_final or declaration.is_final
                    variable.is_settable_property = declaration.is_settable_property
                    if isinstance(setter_type, CallableType):
                        variable.setter_type = setter_type
                    declaration = variable
            else:
                continue
            if declaration.type is None or has_placeholder(declaration.type):
                continue
            replacement = fill_typevars(target)
            if origin.node.self_type is not None:
                if target.self_type is None:
                    target.self_type = origin.node.self_type.copy_modified(upper_bound=replacement)
                remapping = {origin.node.self_type.id: target.self_type}
            else:
                remapping = {}
            def projected_type(typ: MypyType) -> MypyType:
                typ = get_proper_type(expand_type(expand_type_by_instance(typ, origin_type), remapping))
                if isinstance(typ, Overloaded):
                    items: list[CallableType] = []
                    for item in typ.items:
                        projected_item = projected_type(item)
                        assert isinstance(projected_item, CallableType)
                        items.append(projected_item)
                    return Overloaded(items)
                if isinstance(typ, CallableType):
                    arguments = list(typ.arg_types)
                    if arguments and not declaration.is_staticmethod:
                        first = get_proper_type(arguments[0])
                        if isinstance(first, Instance) and first.type is origin.node:
                            arguments[0] = replacement
                        elif isinstance(first, TypeType) and isinstance(first.item, Instance) and (
                            first.item.type is origin.node
                        ):
                            arguments[0] = TypeType(replacement)
                    return typ.copy_modified(
                        arg_types=arguments,
                        variables=[remapping.get(variable.id, variable) for variable in typ.variables],
                        fallback=function_fallback,
                    )
                return typ

            variable = Var(name, projected_type(declaration.type))
            variable.info = target
            variable._fullname = f"{target.fullname}.{name}"
            for flag in (
                "is_initialized_in_class", "is_staticmethod", "is_classmethod", "is_property",
                "is_settable_property", "is_classvar", "is_final", "is_abstract_var",
                "is_inferred",
            ):
                setattr(variable, flag, getattr(declaration, flag))
            if declaration.setter_type is not None:
                setter_signature = projected_type(declaration.setter_type)
                assert isinstance(setter_signature, CallableType)
                variable.setter_type = setter_signature
            target.names[name] = SymbolTableNode(MDEF, variable, plugin_generated=True)
            projected[name] = origin_name

    def get_dynamic_class_hook(self, fullname: str) -> Callable[[DynamicClassDefContext], None] | None:
        native = super().get_dynamic_class_hook(fullname)
        if native is not None:
            return native
        class_name, _, method_name = fullname.rpartition(".")
        symbol = self.lookup_fully_qualified(class_name)
        if (
            method_name == "from_queryset"
            and symbol is not None
            and isinstance(symbol.node, PlaceholderNode)
            and symbol.node.becomes_typeinfo
        ):
            # Preserve native deferral while an imported class is unresolved.
            # The native builder checks both Manager and QuerySet inheritance
            # before it creates a class; resolved unrelated methods stay native.
            return create_new_manager_class_from_from_queryset_method
        return None


def plugin(version: str) -> type[CompositionPlugin]:
    """Return the native-plugin extension through mypy's standard entrypoint."""

    return CompositionPlugin
