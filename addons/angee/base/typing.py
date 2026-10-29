"""Foundation model and field typing over Django's native composition plugin."""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import partial

import django_yamlconf
from django.db import models
from django_compose.conf import setting_name
from django_compose.typing import CompositionContext, CompositionPlugin
from mypy.join import join_types
from mypy.nodes import (
    ARG_NAMED_OPT,
    GDEF,
    Argument,
    Import,
    ImportFrom,
    MemberExpr,
    MypyFile,
    NameExpr,
    PlaceholderNode,
    SymbolTableNode,
    TypeInfo,
    Var,
)
from mypy.plugin import AttributeContext, ClassDefContext, DynamicClassDefContext
from mypy.plugins.common import add_method_to_class
from mypy.typeops import make_simplified_union
from mypy.types import AnyType, CallableType, Instance, NoneType, TypedDictType, TypeType, get_proper_type
from mypy.types import Type as MypyType
from mypy_django_plugin.lib import fullnames, helpers

from angee.base.fields.implementation import ImplClassField


class BaseCompositionContext(CompositionContext):
    """Project checked parent contracts through native REBAC manager injection."""

    def is_fallback_manager(self, manager: models.Manager[models.Model]) -> bool:
        # Native model imports require Django's completed setup, supplied by the
        # context before manager projection begins.
        from rebac.mixins import INJECTED_BASE_MANAGER

        return super().is_fallback_manager(manager) or manager.name == INJECTED_BASE_MANAGER


class BasePlugin(CompositionPlugin):
    """Project model-toolkit contracts for Angee hosts."""

    context_class = BaseCompositionContext
    default_settings_module: str | None = "angee.base.autoconfig"

    def get_additional_deps(self, file: MypyFile) -> list[tuple[int, str, int]]:
        dependencies = super().get_additional_deps(file)
        if any(
            isinstance(item, ImportFrom) and item.id == "django.conf"
            or isinstance(item, Import) and any(name == "django.conf" for name, _alias in item.ids)
            for item in file.imports
        ):
            # Consumers require direct edges: adding/removing an autoconfig key
            # does not alter django.conf's own static module interface.
            dependencies.extend(
                self._new_dependency(module.__name__)
                for app in self.django_context.apps_registry.get_app_configs()
                if (module := sys.modules.get(f"{app.name}.autoconfig")) is not None
            )
            if self.default_settings_module is not None and self.django_context.composed:
                dependencies.append(self._new_dependency(self.default_settings_module))
        return dependencies

    def get_attribute_hook(self, fullname: str) -> Callable[[AttributeContext], MypyType] | None:
        native = super().get_attribute_hook(fullname)
        if fullname.rpartition(".")[0] == fullnames.DUMMY_SETTINGS_BASE_CLASS and native is not None:
            return partial(self._autoconfig_setting, native=native)
        return native

    @staticmethod
    def _whole_setting_types(declaration: TypedDictType, name: str) -> list[MypyType]:
        # A dotted-key declaration describes one nested member, not the whole
        # setting's type. Native configure owns normalization of the root name.
        return [
            value for key, value in declaration.items.items()
            if "." not in key and setting_name(key) == name
        ]

    def _autoconfig_setting(
        self, ctx: AttributeContext, *, native: Callable[[AttributeContext], MypyType],
    ) -> MypyType:
        """Project declared types from native yamlconf provenance, not runtime values."""
        if not isinstance(ctx.context, MemberExpr):
            return native(ctx)
        name = ctx.context.name
        modules = helpers.get_typechecker_api(ctx).modules
        project = modules.get(self.django_context.django_settings_module)
        if project is not None and name in project.names:
            return native(ctx)
        info = django_yamlconf.get_attr_info(name, self.django_context.settings)
        sources = tuple(dict.fromkeys(
            source
            for source in (info.get("source"), *(source for _value, source in info.get("history", ())))
            if isinstance(source, str)
        )) if info else ()
        if not sources:
            defaults = (
                modules.get(self.default_settings_module)
                if self.default_settings_module is not None and self.django_context.composed else None
            )
            symbol = defaults.names.get(name) if defaults is not None else None
            if symbol is not None and symbol.type is not None and not isinstance(
                get_proper_type(symbol.type), AnyType,
            ):
                return symbol.type
            if defaults is None:
                return native(ctx)
            sources = (defaults.fullname,)
        candidates: list[MypyType] = []
        for source in sources:
            # Native configure records the declaration's module name already.
            # Environment/internal records and YAML file paths have no module.
            module = modules.get(source)
            if module is None:
                continue
            symbol = module.names.get("SETTINGS")
            declaration = get_proper_type(symbol.type) if symbol is not None else None
            declared: list[MypyType] = []
            if isinstance(declaration, TypedDictType):
                declared = self._whole_setting_types(declaration, name)
                candidates.extend(declared)
            if not declared:
                function = module.names.get("settings")
                signature = get_proper_type(function.type) if function is not None else None
                if isinstance(signature, CallableType):
                    result = get_proper_type(signature.ret_type)
                    if isinstance(result, TypedDictType):
                        candidates.extend(self._whole_setting_types(result, name))
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
        return partial(self._implementation_accessors, native=native) if native is not None else None

    def _implementation_accessors(
        self, ctx: ClassDefContext, *, native: Callable[[ClassDefContext], None],
    ) -> None:
        """Project only methods that ImplClassField contributes during Django model construction."""
        native(ctx)
        model = self.django_context.get_model_class_by_fullname(ctx.cls.fullname)
        if model is None:
            return
        for field in self.django_context.get_model_fields(model):
            if not isinstance(field, ImplClassField) or field.base_class is None:
                continue
            fullname = helpers.get_class_fullname(field.base_class)
            symbol = self.lookup_fully_qualified(fullname)
            if symbol is None or not isinstance(symbol.node, TypeInfo) or not symbol.node.mro:
                if ctx.api.final_iteration:
                    ctx.api.fail(f"Unavailable implementation base {fullname}", ctx.cls)
                else:
                    ctx.api.defer()
                continue
            default_type = make_simplified_union([ctx.api.named_type("builtins.str"), NoneType()])
            add_method_to_class(
                ctx.api, ctx.cls, name=f"get_{field.name}_impl",
                args=[Argument(Var("default", default_type), default_type, NameExpr("None"), ARG_NAMED_OPT)],
                return_type=TypeType.make_normalized(Instance(symbol.node, [])),
            )

    def get_class_attribute_hook(self, fullname: str) -> Callable[[AttributeContext], MypyType] | None:
        class_name, _, method_name = fullname.rpartition(".")
        if method_name == "system_queryset":
            info = self._get_typeinfo_or_none(class_name)
            symbol = info.get(method_name) if info is not None else None
            if symbol is not None and symbol.node is not None and symbol.node.fullname == (
                "angee.base.models.persistence.PersistenceMixin.system_queryset"
            ):
                return self._system_queryset
        return super().get_class_attribute_hook(fullname)

    def _system_queryset(self, ctx: AttributeContext) -> MypyType:
        # This owner clones the configured default queryset without changing its
        # class. Native member analysis retains that class and model binding.
        signature = get_proper_type(ctx.default_attr_type)
        if not isinstance(signature, CallableType):
            return ctx.default_attr_type
        api = helpers.get_typechecker_api(ctx)
        manager = helpers.analyze_member_access(
            "_default_manager", ctx.type, ctx.context, is_lvalue=False,
            is_super=False, is_operator=False, original_type=ctx.type, chk=api,
        )
        method = get_proper_type(helpers.analyze_member_access(
            "get_queryset", manager, ctx.context, is_lvalue=False,
            is_super=False, is_operator=False, original_type=manager, chk=api,
        ))
        if isinstance(method, CallableType):
            result = get_proper_type(method.ret_type)
            if isinstance(result, Instance) and result.type.has_base(fullnames.QUERYSET_CLASS_FULLNAME):
                return signature.copy_modified(ret_type=method.ret_type)
        return ctx.default_attr_type

    def get_dynamic_class_hook(self, fullname: str) -> Callable[[DynamicClassDefContext], None] | None:
        if fullname == "angee.base.models.roles.role_anchor":
            return self._role_anchor
        return super().get_dynamic_class_hook(fullname)

    def _role_anchor(self, ctx: DynamicClassDefContext) -> None:
        api = helpers.get_semanal_api(ctx)
        existing = api.lookup_current_scope(ctx.name)
        if existing is not None and isinstance(existing.node, TypeInfo):
            return
        symbol = self.lookup_fully_qualified("angee.base.models.base.AngeeModel")
        if symbol is None or not isinstance(symbol.node, TypeInfo) or not symbol.node.mro:
            if not ctx.api.final_iteration:
                if existing is None or not isinstance(existing.node, PlaceholderNode):
                    placeholder = PlaceholderNode(
                        api.qualified_name(ctx.name), ctx.call, ctx.call.line, becomes_typeinfo=True,
                    )
                    ctx.api.add_symbol_table_node(ctx.name, SymbolTableNode(GDEF, placeholder))
                ctx.api.defer()
            else:
                ctx.api.fail("role_anchor requires the AngeeModel typing contract", ctx.call)
            return
        info = ctx.api.basic_new_typeinfo(ctx.name, Instance(symbol.node, []), ctx.call.line)
        info.metaclass_type = symbol.node.metaclass_type
        ctx.api.add_symbol_table_node(ctx.name, SymbolTableNode(GDEF, info))



def plugin(version: str) -> type[BasePlugin]:
    """Return the host extension through mypy's native plugin entrypoint."""

    return BasePlugin
