"""Subscribe to one ICS feed through the calendar and integrate owners."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from django.apps import apps
from django.db import transaction
from rebac import system_context

from angee.calendar_integrate_ics.backend import feed_host, feed_url


def subscribe_calendar(user: Any, *, name: str, url: str) -> Any:
    """Probe a feed before atomically retaining its source and calendar, then queue the first poll.

    A subscription needs no credential. Its calendar sits on the subscriber's
    person, named ``name`` (else the feed's own name, else its host) and expanded in
    the feed's zone. The probe runs on an unsaved source, so a feed that cannot be
    read is never subscribed.
    """

    source_model = apps.get_model("calendar", "CalendarSource")
    vendor_model = apps.get_model("integrate", "Vendor")
    feed = feed_url(url)
    values = {
        "owner": user,
        "backend_class": "ics",
        "display_name": name.strip(),
        "config": {"url": feed},
        "lifecycle": "connected",
        "created_by_id": user.pk,
    }
    # Network validation precedes the transaction; the probe row is never saved.
    remote = source_model(**values).backend.probe()
    values["display_name"] = values["display_name"] or remote.name or feed_host(feed)

    with system_context(reason="calendar_integrate_ics.subscribe"), transaction.atomic():
        vendor, _created = vendor_model.objects.get_or_create(slug="ics", defaults={"display_name": "ICS feed"})
        source = source_model.objects.create(vendor=vendor, **values)
        source.calendar_stream(replace(remote, name=values["display_name"]))
        # A bridge enters the poll loop once its first sync records a result.
        source.request_sync()
    return source
