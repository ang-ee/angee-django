"""IAM typing through the foundation/native compiler plugin extension seam."""

from collections.abc import Callable

from django.db import models
from django_compose.typing import CompositionContext
from mypy.nodes import GDEF, PlaceholderNode, SymbolTableNode, TypeInfo
from mypy.plugin import AttributeContext, DynamicClassDefContext
from mypy.subtypes import is_subtype
from mypy.types import CallableType, Instance, get_proper_type
from mypy.types import Type as MypyType
from mypy_django_plugin.lib import helpers

from angee.base.typing import BasePlugin


class IAMCompositionContext(CompositionContext):
    """Retain native protected-model fallback identity in required-parent typing."""

    def is_fallback_manager(self, manager: models.Manager[models.Model]) -> bool:
        from rebac.mixins import INJECTED_BASE_MANAGER

        return super().is_fallback_manager(manager) or manager.name == INJECTED_BASE_MANAGER


class IAMPlugin(BasePlugin):
    context_class = IAMCompositionContext

    def _system_queryset(self, ctx: AttributeContext) -> MypyType:
        """Keep the protected declaration when manager typing is less specific."""

        declared = get_proper_type(ctx.default_attr_type)
        projected = get_proper_type(super()._system_queryset(ctx))
        if (
            isinstance(declared, CallableType)
            and isinstance(projected, CallableType)
            and is_subtype(projected.ret_type, declared.ret_type)
        ):
            return projected
        return ctx.default_attr_type

    def get_class_attribute_hook(self, fullname: str) -> Callable[[AttributeContext], MypyType] | None:
        class_name, _, name = fullname.rpartition(".")
        if name == "system_queryset":
            info = self._get_typeinfo_or_none(class_name)
            symbol = info.get(name) if info is not None else None
            if (
                symbol is not None
                and symbol.node is not None
                and symbol.node.fullname == ("angee.iam.models.base.ProtectedPersistenceMixin.system_queryset")
            ):
                return self._system_queryset
        return super().get_class_attribute_hook(fullname)

    def get_dynamic_class_hook(self, fullname: str) -> Callable[[DynamicClassDefContext], None] | None:
        if fullname == "angee.iam.models.roles.role_anchor":
            return self._role_anchor
        return super().get_dynamic_class_hook(fullname)

    def _role_anchor(self, ctx: DynamicClassDefContext) -> None:
        api = helpers.get_semanal_api(ctx)
        existing = api.lookup_current_scope(ctx.name)
        if existing is not None and isinstance(existing.node, TypeInfo):
            return
        symbol = self.lookup_fully_qualified("angee.iam.models.base.ProtectedModel")
        if symbol is None or not isinstance(symbol.node, TypeInfo) or not symbol.node.mro:
            if not ctx.api.final_iteration:
                if existing is None or not isinstance(existing.node, PlaceholderNode):
                    placeholder = PlaceholderNode(
                        api.qualified_name(ctx.name),
                        ctx.call,
                        ctx.call.line,
                        becomes_typeinfo=True,
                    )
                    ctx.api.add_symbol_table_node(ctx.name, SymbolTableNode(GDEF, placeholder))
                ctx.api.defer()
            else:
                ctx.api.fail("role_anchor requires the AngeeModel typing contract", ctx.call)
            return
        info = ctx.api.basic_new_typeinfo(ctx.name, Instance(symbol.node, []), ctx.call.line)
        info.metaclass_type = symbol.node.metaclass_type
        ctx.api.add_symbol_table_node(ctx.name, SymbolTableNode(GDEF, info))


def plugin(version: str) -> type[IAMPlugin]:
    return IAMPlugin
