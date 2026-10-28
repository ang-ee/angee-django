"""Optional native mypy projections for the configured provenance storage model.

Register after the Django/composition plugin in mypy's standard plugin list.
Django owns the populated registry; this plugin adds no app setup, composition
context, model registry, or runtime lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from django.apps import apps
from django.conf import settings
from django.db import models
from mypy.build import PRI_MYPY
from mypy.nodes import MypyFile, TypeInfo
from mypy.plugin import FunctionContext, Plugin, ReportConfigContext
from mypy.typeops import make_simplified_union
from mypy.types import Instance, NoneType, TypeType
from mypy.types import Type as MypyType

from angee.provenance.context import changeset_model, changeset_model_label


class ProvenancePlugin(Plugin):
    """Resolve configured context helpers to their native concrete model type."""

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
