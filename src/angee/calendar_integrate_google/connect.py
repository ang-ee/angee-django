"""Connect one of a signed-in user's Google accounts as a calendar source, through the calendar and integrate owners."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import system_context

from angee.calendar_integrate_google.backend import GoogleCalendarBackend
from angee.integrate.models import IntegrationLifecycle


def connect_google_calendar(user: Any, credential: Any) -> Any:
    """Connect ``credential``, one of ``user``'s Google accounts, as a calendar source and queue its first sync.

    Nothing is fetched in the request. The credential must be one of the user's own
    connected accounts (``CredentialManager.connected_for``); the calendar owner
    admits one more source for the user (``CalendarSource.admit_subscription``: the
    create gate, a quota and a rate), and the attachment admits only an account of
    the ``google`` client that granted Calendar access
    (``GoogleCalendarBackend.admit_credential``). One account is one source: an
    account the user already syncs is refused, and each other account connects as
    a source of its own. The first sync mirrors each calendar of the account on the
    user's person, then its events.
    """

    source_model = apps.get_model("calendar", "CalendarSource")
    credential_model = apps.get_model("integrate", "Credential")
    if not credential_model.objects.connected_for(user).filter(pk=credential.pk).exists():
        raise ValidationError({"credential": "Choose one of your connected Google accounts."})
    source_model.admit_subscription(user)
    with system_context(reason="calendar_integrate_google.connect"), transaction.atomic():
        synced = source_model.system_queryset().filter(
            owner=user, backend_class=GoogleCalendarBackend.key, account_id=credential.external_account_id
        )
        if synced.exclude(lifecycle=IntegrationLifecycle.DISCONNECTED).exists():
            raise ValidationError({"credential": "This Google account's calendars are already connected."})
        account = credential.external_account
        name = (account.email or account.display_name) if account is not None else ""
        source = source_model(
            vendor=apps.get_model("integrate", "Vendor").objects.seeded("google"),
            owner=user,
            backend_class=GoogleCalendarBackend.key,
            display_name=name or GoogleCalendarBackend.label,
            lifecycle=IntegrationLifecycle.CONNECTED,
            created_by_id=user.pk,
        )
        source.attach_connection(credential)
        source.save()
        # A bridge enters the poll loop once its first sync records a result.
        source.request_sync()
    return source
