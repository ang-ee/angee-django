"""Native managers selecting Angee captured querysets."""

from __future__ import annotations

from django.db import models

from angee.iam.managers import ProtectedManager, ProtectedUnscopedManager
from angee.iam.models.merge import ProtectedMergeableUnscopedManager
from angee.provenance.contrib.angee.querysets import (
    AngeeHistoryMergeableUnscopedQuerySet,
    AngeeHistoryQuerySet,
    AngeeHistoryUnscopedQuerySet,
)

_AngeeHistoryManagerBase = ProtectedManager.from_queryset(AngeeHistoryQuerySet)


_AngeeHistoryUnscopedManagerBase = ProtectedUnscopedManager.from_queryset(AngeeHistoryUnscopedQuerySet)


class AngeeHistoryManager[T: models.Model](_AngeeHistoryManagerBase[T]):
    """Explicit captured manager for Angee record models."""


class AngeeHistoryUnscopedManager[T: models.Model](_AngeeHistoryUnscopedManagerBase[T]):
    """Explicit captured base manager for Angee record models."""


_AngeeHistoryMergeableUnscopedManagerBase = ProtectedMergeableUnscopedManager.from_queryset(
    AngeeHistoryMergeableUnscopedQuerySet,
)


class AngeeHistoryMergeableUnscopedManager[T: models.Model](_AngeeHistoryMergeableUnscopedManagerBase[T]):
    """Captured base manager explicitly selecting the merge identity contract."""
