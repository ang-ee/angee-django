"""Connect one of a signed-in user's Google accounts as a calendar source, through the calendar and integrate owners."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError

from angee.calendar_integrate_google.backend import GoogleCalendarBackend


def connect_google_calendar(user: Any, credential: Any) -> Any:
    """Connect ``credential``, one of ``user``'s Google accounts, as a calendar source and queue its first sync.

    ``credential`` must be one of the user's own connected accounts
    (``CredentialManager.connected_for``; the mutation looks it up there, so an
    unknown id and another user's fail alike). The calendar owner connects
    it (``CalendarSourceManager.subscribe``: the quota and rate, then integrate's
    attachment), and the backend admits only an account the source reads with
    (``Integration.reads_with``: its OAuth client, with Calendar access) that no
    other source syncs (``GoogleCalendarBackend.admit_credential``). The user's
    accounts it reads with are the ``calendar_source_backend`` query's. The first
    sync mirrors each calendar of the account on the user's person, then its events.
    """

    if not apps.get_model("integrate", "Credential").objects.connected_for(user).filter(pk=credential.pk).exists():
        raise ValidationError({"credential": "Choose one of your connected Google accounts."})
    account = credential.external_account
    name = (account.email or account.display_name) if account is not None else ""
    return apps.get_model("calendar", "CalendarSource").objects.subscribe(
        user, credential, backend_class=GoogleCalendarBackend.key, display_name=name or GoogleCalendarBackend.label
    )
