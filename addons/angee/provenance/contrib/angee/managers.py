"""Native managers selecting Angee captured querysets."""

from __future__ import annotations

from django.db import models

from angee.models.manager import AngeeManager, AngeeUnscopedManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet, AngeeHistoryUnscopedQuerySet

_AngeeHistoryManagerBase = AngeeManager.from_queryset(AngeeHistoryQuerySet)


_AngeeHistoryUnscopedManagerBase = AngeeUnscopedManager.from_queryset(AngeeHistoryUnscopedQuerySet)


class AngeeHistoryManager[T: models.Model](_AngeeHistoryManagerBase[T]):
    """Explicit captured manager for Angee record models."""


class AngeeHistoryUnscopedManager[T: models.Model](_AngeeHistoryUnscopedManagerBase[T]):
    """Explicit captured base manager for Angee record models."""
