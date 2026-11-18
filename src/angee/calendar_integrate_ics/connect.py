"""Subscribe a signed-in user to one ICS feed through the calendar and integrate owners."""

from __future__ import annotations

import hashlib
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import system_context

from angee.calendar_integrate_ics.backend import IcsError, feed_label, feed_url
from angee.integrate.models import IntegrationLifecycle


def subscribe_calendar(user: Any, *, name: str, url: str) -> Any:
    """Subscribe ``user`` to a feed: admit it, keep its address as a credential, and queue the first sync.

    Nothing is fetched in the request. The address's shape is checked
    (:func:`feed_url`; ``webcal://`` reads as ``https://``) and a refused address is
    the ``url`` input's validation error, so the subscriber sees its own reason
    under the field they typed it in. The calendar owner admits one more source for the user
    (:meth:`CalendarSource.admit_subscription`: the create gate, a quota and a
    rate), and the first sync reports a feed it cannot read. The address is a
    secret, a private feed's token rides in it: it is a static-token credential's
    material, and the source's ``config`` keeps only its label. The first sync
    mirrors the feed's calendar on the user's person, then its events.
    """

    source_model = apps.get_model("calendar", "CalendarSource")
    credential_model = apps.get_model("integrate", "Credential")
    vendor_model = apps.get_model("integrate", "Vendor")
    try:
        feed = feed_url(url)
    except IcsError as error:
        raise ValidationError({"url": [error.public_message]}) from error
    label = feed_label(feed)
    source_model.admit_subscription(user)
    with system_context(reason="calendar_integrate_ics.subscribe"), transaction.atomic():
        # The addon's master resource seeds the vendor (resources/master/010_integrate.vendor.yaml).
        vendor = vendor_model.objects.seeded("ics")
        credential = credential_model.objects.create_local_credential(
            user,
            kind="static_token",
            # One credential per user and address; its name reveals nothing of the address.
            name=f"Calendar feed {hashlib.sha256(feed.encode()).hexdigest()[:12]}",
            material={"api_key": feed},
        )
        source = source_model.objects.create(
            vendor=vendor,
            credential=credential,
            owner=user,
            backend_class="ics",
            display_name=name.strip() or label,
            config={"feed": label},
            lifecycle=IntegrationLifecycle.CONNECTED,
            created_by_id=user.pk,
        )
        # A bridge enters the poll loop once its first sync records a result.
        source.request_sync()
    return source
