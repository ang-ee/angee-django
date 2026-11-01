"""IAM tracked persistence composed with the neutral append-only invariant."""

from django.db import models

from angee.base.models.append_only import AppendOnlyBaseQuerySet, AppendOnlyModel, AppendOnlyQuerySet
from angee.iam.querysets import ProtectedQuerySet, ProtectedUnscopedQuerySet


class ProtectedAppendOnlyQuerySet[_M: models.Model](AppendOnlyQuerySet[_M], ProtectedQuerySet[_M]):
    """Close writes while preserving native actor authorization."""

    def insert(self, obj: _M) -> _M:
        """Validate one append before its ordinary authorized insertion."""

        self.validate_insert()
        return super().insert(obj)


class ProtectedAppendOnlyBaseQuerySet(AppendOnlyBaseQuerySet, ProtectedUnscopedQuerySet):
    """Preserve closed writes and tracked unfiltered base-manager persistence."""


_ProtectedAppendOnlyBaseManager = models.Manager.from_queryset(ProtectedAppendOnlyBaseQuerySet)


class ProtectedAppendOnlyModel(AppendOnlyModel, concrete=False):
    """Source declaration retaining native REBAC base-manager requirements."""

    _angee_base = _ProtectedAppendOnlyBaseManager()

    class Meta:
        abstract = True
        base_manager_name = "_angee_base"


class IAMAppendOnlyMixin(ProtectedAppendOnlyModel, extends="base.AppendOnlyModel"):
    """Contribute tracked append-only querysets at the source's named slots."""

    compose_querysets = {"objects": ProtectedAppendOnlyQuerySet, "_angee_base": ProtectedAppendOnlyBaseQuerySet}

    class Meta:
        abstract = True
