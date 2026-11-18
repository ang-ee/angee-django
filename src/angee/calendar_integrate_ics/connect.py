"""Subscribe a signed-in user to one ICS feed through the calendar and integrate owners."""

from __future__ import annotations

import hashlib
from typing import Any

from django.apps import apps
from django.db import transaction
from rebac import system_context

from angee.calendar_integrate_ics.backend import feed_label, feed_url


def subscribe_calendar(user: Any, *, name: str, url: str) -> Any:
    """Subscribe ``user`` to a feed: admit it, keep its address as a credential, and queue the first sync.

    Nothing is fetched in the request. The address's shape is checked
    (:func:`feed_url`), the calendar owner connects one more source for the user
    (``CalendarSourceManager.subscribe``: the create gate, a quota and a rate,
    then integrate's attachment), and the first sync reports a feed it cannot read. The address is a
    secret, a private feed's token rides in it: it is a static-token credential's
    material, and the source's ``config`` keeps only its label. The first sync
    mirrors the feed's calendar on the user's person, then its events.
    """

    credential_model = apps.get_model("integrate", "Credential")
    vendor_model = apps.get_model("integrate", "Vendor")
    feed = feed_url(url)
    label = feed_label(feed)
    with system_context(reason="calendar_integrate_ics.subscribe"), transaction.atomic():
        # The backend's defaults name this vendor; it has no seed row of its own.
        vendor_model.objects.get_or_create(slug="ics", defaults={"display_name": "ICS feed"})
        credential = credential_model.objects.create_local_credential(
            user,
            kind="static_token",
            # One credential per user and address; its name reveals nothing of the address.
            name=f"Calendar feed {hashlib.sha256(feed.encode()).hexdigest()[:12]}",
            material={"api_key": feed},
        )
        return apps.get_model("calendar", "CalendarSource").objects.subscribe(
            user, credential, backend_class="ics", display_name=name.strip() or label, config={"feed": label}
        )
