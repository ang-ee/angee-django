"""Angee model-toolkit typing over the reusable composition plugin."""

from __future__ import annotations

from collections.abc import Callable

from mypy.nodes import GDEF, PlaceholderNode, SymbolTableNode, TypeInfo
from mypy.plugin import AttributeContext, DynamicClassDefContext
from mypy.types import CallableType, Instance, get_proper_type
from mypy.types import Type as MypyType
from mypy_django_plugin.lib import fullnames, helpers

from angee.compose.typing import CompositionPlugin


class AngeePlugin(CompositionPlugin):
    """Project model-toolkit contracts for Angee hosts."""

    default_settings_module = "angee.defaults"

    def get_class_attribute_hook(self, fullname: str) -> Callable[[AttributeContext], MypyType] | None:
        class_name, _, method_name = fullname.rpartition(".")
        if method_name == "system_queryset":
            info = self._get_typeinfo_or_none(class_name)
            symbol = info.get(method_name) if info is not None else None
            if symbol is not None and symbol.node is not None and symbol.node.fullname == (
                "angee.models.query.SystemQuerysetMixin.system_queryset"
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
        if fullname == "angee.models.base.role_anchor":
            return self._role_anchor
        return super().get_dynamic_class_hook(fullname)

    def _role_anchor(self, ctx: DynamicClassDefContext) -> None:
        api = helpers.get_semanal_api(ctx)
        existing = api.lookup_current_scope(ctx.name)
        if existing is not None and isinstance(existing.node, TypeInfo):
            return
        symbol = self.lookup_fully_qualified("angee.models.base.AngeeModel")
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



def plugin(version: str) -> type[AngeePlugin]:
    """Return the host extension through mypy's native plugin entrypoint."""

    return AngeePlugin
