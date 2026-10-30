"""Optional native mypy projections for the configured provenance storage model.

Register after the Django/composition plugin in mypy's standard plugin list.
Django owns the populated registry; this plugin adds no app setup, composition
context, model registry, or runtime lifecycle.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import partial
from inspect import getattr_static

from django.apps import apps
from django.conf import settings
from django.db import models
from mypy.build import PRI_MYPY
from mypy.nodes import MypyFile, TypeInfo
from mypy.plugin import AttributeContext, FunctionContext, Plugin, ReportConfigContext
from mypy.typeops import make_simplified_union
from mypy.types import CallableType, Instance, NoneType, TypeType, get_proper_type
from mypy.types import Type as MypyType
from mypy_django_plugin.lib import helpers
from mypy_django_plugin.transformers.managers import create_manager_info_from_queryset
from simple_history.manager import HistoryDescriptor

from angee.provenance.context import changeset_model, changeset_model_label


class ProvenancePlugin(Plugin):
    """Resolve configured context helpers to their native concrete model type."""

    def _history_hook(self, fullname: str) -> Callable[[AttributeContext], MypyType] | None:
        owner, _, name = fullname.rpartition(".")
        symbol = self.lookup_fully_qualified(owner)
        if symbol is not None and isinstance(symbol.node, TypeInfo) and helpers.is_model_type(symbol.node):
            return partial(self.history_manager, name=name)
        return None

    def get_class_attribute_hook(self, fullname: str) -> Callable[[AttributeContext], MypyType] | None:
        return self._history_hook(fullname)

    def get_attribute_hook(self, fullname: str) -> Callable[[AttributeContext], MypyType] | None:
        return self._history_hook(fullname)

    def history_manager(self, ctx: AttributeContext, *, name: str) -> MypyType:
        """Bind native HistoryDescriptor managers to their already projected row type."""
        receiver = get_proper_type(ctx.type)
        if isinstance(receiver, TypeType):
            receiver = get_proper_type(receiver.item)
        elif isinstance(receiver, CallableType) and receiver.is_type_obj():
            receiver = get_proper_type(receiver.ret_type)
        if not isinstance(receiver, Instance):
            return ctx.default_attr_type
        module = sys.modules.get(receiver.type.module_name)
        source = vars(module).get(receiver.type.name) if module is not None else None
        if source is models.Model or not isinstance(source, type) or not issubclass(source, models.Model):
            return ctx.default_attr_type
        try:
            # The runtime source owns its native Django identity. Use that
            # identity to read the selected registry row; never rebuild a model.
            model = apps.get_model(source._meta.app_label, source._meta.model_name)
        except LookupError:
            return ctx.default_attr_type
        descriptor = getattr_static(model, name, None)
        if not isinstance(descriptor, HistoryDescriptor):
            return ctx.default_attr_type
        history_name = helpers.get_class_fullname(descriptor.model)
        manager_name = helpers.get_class_fullname(descriptor.manager_class)
        history = self.lookup_fully_qualified(history_name)
        manager = self.lookup_fully_qualified(manager_name)
        if history is None or not isinstance(history.node, TypeInfo):
            ctx.api.fail(f"Unavailable native history model {history_name}", ctx.context)
            return ctx.default_attr_type
        if manager is None or not isinstance(manager.node, TypeInfo):
            ctx.api.fail(f"Unavailable native history manager {manager_name}", ctx.context)
            return ctx.default_attr_type
        queryset_name = helpers.get_class_fullname(descriptor.queryset_class)
        queryset = self.lookup_fully_qualified(queryset_name)
        if queryset is None or not isinstance(queryset.node, TypeInfo):
            ctx.api.fail(f"Unavailable native history queryset {queryset_name}", ctx.context)
            return ctx.default_attr_type
        api = helpers.get_typechecker_api(ctx)
        try:
            selected = create_manager_info_from_queryset(
                api.modules[manager.node.module_name],
                manager.node,
                queryset.node,
                line=ctx.context.line,
            )
        except helpers.IncompleteDefnException:
            ctx.api.fail(f"Unavailable native history manager {manager_name}", ctx.context)
            return ctx.default_attr_type
        arguments = [Instance(history.node, [])] if len(selected.type_vars) == 1 else []
        return Instance(selected, arguments)

    @staticmethod
    def configured_model() -> type[models.Model] | None:
        """Read storage through the runtime owner after native Django setup."""

        if not settings.configured or not apps.apps_ready or not changeset_model_label():
            return None
        try:
            return changeset_model()
        except LookupError:
            # Like django-stubs' optional auth model, unavailable storage keeps
            # the helpers' declared protocol instead of forcing an app install.
            return None

    def report_config_data(self, ctx: ReportConfigContext) -> dict[str, str | None]:
        """Invalidate cached projections when the configured native model changes."""

        del ctx
        model = self.configured_model()
        return {"storage_model": f"{model.__module__}.{model.__qualname__}" if model is not None else None}

    def get_additional_deps(self, file: MypyFile) -> list[tuple[int, str, int]]:
        # Like django-stubs' get_user_model, the helper's module depends on the
        # configured model module, including generated concrete declarations.
        if file.fullname != "angee.provenance.context" or (model := self.configured_model()) is None:
            return []
        return [(PRI_MYPY, model.__module__, -1)]

    def get_function_hook(self, fullname: str) -> Callable[[FunctionContext], MypyType] | None:
        if fullname in {
            "angee.provenance.context.changeset_model",
            "angee.provenance.context.current_changeset",
            "angee.provenance.context.require_changeset",
        }:
            return partial(self.configured_changeset, fullname=fullname)
        return None

    def configured_changeset(self, ctx: FunctionContext, *, fullname: str) -> MypyType:
        model = self.configured_model()
        if model is None:
            return ctx.default_return_type
        symbol = self.lookup_fully_qualified(f"{model.__module__}.{model.__qualname__}")
        if symbol is None or not isinstance(symbol.node, TypeInfo):
            return ctx.default_return_type
        instance = Instance(symbol.node, [])
        if fullname == "angee.provenance.context.changeset_model":
            return TypeType(instance)
        if fullname == "angee.provenance.context.current_changeset":
            return make_simplified_union([instance, NoneType()])
        return instance


def plugin(version: str) -> type[ProvenancePlugin]:
    """Return the optional extension through mypy's standard entrypoint."""

    return ProvenancePlugin
