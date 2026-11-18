"""Calendar's system checks, registered by ``CalendarConfig.ready()``."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core import checks


def check_publish_targets(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
    """Refuse an ``ANGEE_CALENDAR_PUBLISH_TARGETS`` that is neither ``None`` nor a list or tuple of calendar ids.

    ``CalendarSource.may_publish_to`` matches whole ids, so a string, which would
    match by substring, or any other shape fails at startup instead.
    """

    del app_configs, kwargs
    targets = getattr(settings, "ANGEE_CALENDAR_PUBLISH_TARGETS", None)
    if targets is None or (isinstance(targets, list | tuple) and all(isinstance(item, str) for item in targets)):
        return []
    return [
        checks.Error(
            "ANGEE_CALENDAR_PUBLISH_TARGETS must be None or a list of remote calendar ids.",
            obj="ANGEE_CALENDAR_PUBLISH_TARGETS",
            id="calendar.E001",
        )
    ]
