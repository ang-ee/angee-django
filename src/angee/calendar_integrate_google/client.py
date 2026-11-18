"""Google Calendar API v3, called with one source credential through the one Google REST transport.

The one owner of the Calendar wire protocol for this addon: the backends list an
account's calendars and page a calendar's events through it. Requests go through
``angee.integrate_google.api.GoogleApi`` (integrate's SSRF-pinned HTTP owner, the
credential's authorization for the backend's scopes, one renew of a rejected
token, Google's error envelope). No Google client library is involved.

A sync token Google no longer honours (``410 Gone``) is :class:`SyncTokenExpired`,
which the backend turns into a new baseline; a page token it refuses (``400``,
reason ``invalid``) is :class:`PageTokenInvalid`, which lists again from the sync
token the page continued. An event or instance Google no longer
has reads as ``None``. Every other refusal is a :class:`GoogleCalendarError`, as
the transport names it (``GoogleApi.refusal``).
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from typing import Any
from urllib.parse import quote, urlencode

from angee.integrate_google.api import GoogleApi, GoogleApiError, error_reason

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
MAX_PAGE_SIZE = 2500
"""Google's largest ``maxResults`` for ``events.list``."""

_CALENDAR_FIELDS = "nextPageToken,items(id,summary,summaryOverride,backgroundColor,timeZone)"
_CALENDAR_LIST = {"fields": _CALENDAR_FIELDS, "showHidden": "true", "minAccessRole": "reader"}
"""Every calendar the account reads, hidden ones included; deleted entries are not listed (no ``showDeleted``)."""


class GoogleCalendarError(GoogleApiError):
    """A Google Calendar refusal whose message is safe to show an operator."""


class SyncTokenExpired(Exception):
    """Google no longer honours a sync token (``410 Gone``): the calendar must be listed again in full."""


class PageTokenInvalid(Exception):
    """Google refused a continued listing's page token (``400``, reason ``invalid``): list from its start again."""


class GoogleCalendarClient:
    """One source's Google calendars, called with its credential for ``scopes``.

    Which credential a source may use is its backend's admission
    (``CalendarBackend.admit_credential``); the client only calls with it.
    """

    def __init__(self, credential: Any, scopes: Collection[str]) -> None:
        """Bind the credential every request authenticates with, through the Google transport."""

        self.api = GoogleApi(
            credential, service="Google Calendar", grant="Calendar", scopes=scopes, error=GoogleCalendarError
        )

    def calendar_list(self) -> Iterator[Mapping[str, Any]]:
        """Yield each entry of the account's calendar list, page by page."""

        token = ""
        while True:
            page = self._get("users/me/calendarList", {**_CALENDAR_LIST, **_page(token)})
            yield from page.get("items") or ()
            token = str(page.get("nextPageToken") or "")
            if not token:
                return

    def events_page(
        self, calendar_id: str, *, sync_token: str = "", page_token: str = "", max_results: int = MAX_PAGE_SIZE
    ) -> dict[str, Any]:
        """Return one page of ``events.list``: series masters, single events and exceptions, removals included.

        Without ``sync_token`` it is a full listing; with one, only what changed since
        it. The last page carries ``nextSyncToken``, every other ``nextPageToken``.
        Raises :class:`SyncTokenExpired` when Google answers ``410 Gone``.
        """

        params = {
            "showDeleted": "true",
            "maxResults": str(max(1, min(max_results, MAX_PAGE_SIZE))),
            **({"syncToken": sync_token} if sync_token else {}),
            **_page(page_token),
        }
        response = self.api.send("GET", _url(f"calendars/{_segment(calendar_id)}/events", params))
        if response.status_code == 410:
            raise SyncTokenExpired()
        if response.status_code == 400 and page_token and error_reason(response.content) == "invalid":
            raise PageTokenInvalid()
        self._raise_for_status(response)
        return self.api.json(response)

    def event(self, calendar_id: str, event_id: str) -> dict[str, Any] | None:
        """Return one event, or ``None`` once Google no longer has it."""

        return self._get_or_none(f"calendars/{_segment(calendar_id)}/events/{_segment(event_id)}", {})

    def instance(self, calendar_id: str, event_id: str, original_start: str) -> dict[str, Any] | None:
        """Return the instance of the series ``event_id`` originally at ``original_start``, cancelled ones included."""

        page = self._get_or_none(
            f"calendars/{_segment(calendar_id)}/events/{_segment(event_id)}/instances",
            {"originalStart": original_start, "showDeleted": "true", "maxResults": "1"},
        )
        items = (page or {}).get("items") or ()
        return dict(items[0]) if items else None

    def _get(self, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        """GET one Calendar JSON resource."""

        response = self.api.send("GET", _url(path, params))
        self._raise_for_status(response)
        return self.api.json(response)

    def _get_or_none(self, path: str, params: Mapping[str, str]) -> dict[str, Any] | None:
        """GET a resource that may be gone: ``404`` and ``410`` read as ``None``."""

        response = self.api.send("GET", _url(path, params))
        if response.status_code in (404, 410):
            return None
        self._raise_for_status(response)
        return self.api.json(response)

    def _raise_for_status(self, response: Any) -> None:
        """Raise a Calendar refusal: the account-wide ones, else a calendar Google no longer serves, else generic."""

        if response.is_success:
            return
        if response.status_code == 404 and self.api.refusal(response) is None:
            raise GoogleCalendarError("Google Calendar no longer serves this calendar to the account.")
        self.api.raise_for_status(response)


def _url(path: str, params: Mapping[str, str]) -> str:
    """Return the API URL of ``path`` with its query."""

    return f"{CALENDAR_API}/{path}" + (f"?{urlencode(params)}" if params else "")


def _segment(value: str) -> str:
    """Quote one path segment: calendar ids carry ``@`` and ``#``."""

    return quote(value, safe="")


def _page(token: str) -> dict[str, str]:
    """Return the ``pageToken`` parameter of a continued listing, or none."""

    return {"pageToken": token} if token else {}
