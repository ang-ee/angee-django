"""Beat scheduler: django-celery-beat's database schedule, owned by code."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from django.db import close_old_connections
from django.db.utils import DatabaseError, InterfaceError
from django_celery_beat.schedulers import DatabaseScheduler as NativeDatabaseScheduler

logger = logging.getLogger(__name__)


class DatabaseScheduler(NativeDatabaseScheduler):
    """Keep beat's schedule in ``PeriodicTask`` rows while code stays its owner.

    Addons declare entries in ``CELERY_BEAT_SCHEDULE``; the native scheduler
    only adds and updates rows at startup, so an entry removed or renamed in
    code would keep firing. Setup therefore deletes every row that neither code
    nor the library's own defaults declared: rows hold run state, not a second
    place to author schedules (user-authored schedules are workflow Triggers).

    Beat runs embedded in the shared worker, where nothing restarts it, so a
    database error during the periodic full re-read keeps the last schedule
    instead of ending beat.
    """

    _declared: set[str]

    def setup_schedule(self) -> None:
        """Install declared entries, then prune rows code no longer declares."""

        self._declared = set()
        super().setup_schedule()
        stale = self.Model.objects.exclude(name__in=self._declared)
        names = sorted(stale.values_list("name", flat=True))
        if names:
            stale.delete()
            logger.info("Beat removed schedule entries no longer declared in code: %s", ", ".join(names))

    def update_from_dict(self, mapping: Mapping[str, Any]) -> None:
        """Record every entry name setup declares (code entries and library defaults)."""

        if hasattr(self, "_declared"):
            self._declared.update(mapping)
        super().update_from_dict(mapping)

    def all_as_schedule(self) -> dict[str, Any]:
        """Re-read enabled rows; on a database error keep the last known schedule."""

        try:
            return super().all_as_schedule()
        except (DatabaseError, InterfaceError):
            logger.warning("Beat could not re-read its schedule; keeping the last one.", exc_info=True)
            close_old_connections()
            return dict(getattr(self, "_schedule", None) or {})
