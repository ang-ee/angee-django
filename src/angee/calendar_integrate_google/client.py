"""Google Calendar API v3, read with one source credential over integrate's HTTP owner.

The one owner of the Calendar wire protocol for this addon: the backend lists an
account's calendars and pages a calendar's events through it. Requests go through
``angee.integrate.http.HttpClient`` (SSRF-pinned, bounded) with the credential's
authorization for the backend's scopes (``Credential.auth_headers(scopes=...)``),
freshened by ``Credential.ensure_fresh`` before each request. No Google client
library is involved.

Refusals keep their reach. A sync token Google no longer honours (``410 Gone``)
is :class:`SyncTokenExpired`, which the backend turns into a new baseline. An
event or instance Google no longer has reads as ``None``. Everything else is a
:class:`GoogleCalendarError`: a rate limit or an outage is transient (with
Google's ``Retry-After`` when sent); a token Google no longer accepts is renewed
once through ``Credential.refresh_now`` and otherwise asks for a reconnect, as
does a token without Calendar access.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterator, Mapping
from typing import Any
from urllib.parse import quote, urlencode

import httpx2

from angee.integrate.errors import IntegrationError
from angee.integrate.http import HttpClient, ResponseTooLargeError, retry_after

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
MAX_PAGE_SIZE = 2500
"""Google's largest ``maxResults`` for ``events.list``."""

_JSON_MAX_BYTES = 8 * 1024 * 1024
_CALENDAR_FIELDS = "nextPageToken,items(id,summary,summaryOverride,backgroundColor,timeZone,deleted)"
_RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"})
_SCOPE_REFUSALS = frozenset({"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"})


class GoogleCalendarError(IntegrationError):
    """An account-wide Google Calendar refusal whose message is safe to show an operator."""


class SyncTokenExpired(Exception):
    """Google no longer honours a sync token (``410 Gone``): the calendar must be listed again in full."""


class GoogleCalendarClient:
    """One source's Google calendars, read with its credential for ``scopes``.

    Which credential a source may read with is the backend's admission
    (``GoogleCalendarBackend.admit_credential``); the client only reads with it.
    """

    def __init__(self, credential: Any, scopes: Collection[str]) -> None:
        """Bind the credential every request authenticates with, and the scopes it is asked for."""

        self.credential = credential
        self.scopes = tuple(scopes)
        self.http = HttpClient()
        self._renewed = False

    def calendar_list(self) -> Iterator[Mapping[str, Any]]:
        """Yield each entry of the account's calendar list, page by page."""

        token = ""
        while True:
            page = self._get("users/me/calendarList", {"fields": _CALENDAR_FIELDS, **_page(token)})
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
        return self._get(f"calendars/{_segment(calendar_id)}/events", params, gone=SyncTokenExpired)

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

    def _get_or_none(self, path: str, params: Mapping[str, str]) -> dict[str, Any] | None:
        """GET a resource that may be gone: ``404`` and ``410`` read as ``None``."""

        try:
            return self._get(path, params, missing=_Missing)
        except _Missing:
            return None

    def _get(
        self,
        path: str,
        params: Mapping[str, str],
        *,
        gone: type[Exception] | None = None,
        missing: type[Exception] | None = None,
    ) -> dict[str, Any]:
        """GET one Calendar JSON resource, renewing a rejected token once.

        ``gone`` is raised for ``410``, ``missing`` for ``404`` and ``410``.
        """

        url = f"{CALENDAR_API}/{path}" + (f"?{urlencode(params)}" if params else "")
        response = self._request(url)
        if response.status_code == 401 and self._renew():
            response = self._request(url)
        status = response.status_code
        if gone is not None and status == 410:
            raise gone()
        if missing is not None and status in (404, 410):
            raise missing()
        _raise_for_status(response)
        try:
            data = response.json()
        except ValueError as error:
            raise GoogleCalendarError("Google Calendar returned an unreadable response.", transient=True) from error
        if not isinstance(data, dict):
            raise GoogleCalendarError("Google Calendar returned an unreadable response.", transient=True)
        return data

    def _request(self, url: str) -> httpx2.Response:
        """Send one JSON GET; a transport failure or an oversized answer is a transient refusal."""

        try:
            return self.http.request("GET", url, headers=self._headers(), max_bytes=_JSON_MAX_BYTES)
        except httpx2.TransportError as error:
            raise GoogleCalendarError("Google Calendar could not be reached.", transient=True) from error
        except ResponseTooLargeError as error:
            raise GoogleCalendarError("Google Calendar returned an oversized response.", transient=True) from error

    def _headers(self) -> dict[str, str]:
        """Return a fresh authorization for the next request."""

        self.credential.ensure_fresh()
        return {**self.credential.auth_headers(scopes=self.scopes), "Accept": "application/json"}

    def _renew(self) -> bool:
        """Renew a token Google rejected, once, through the credential owner; whether a retry may help."""

        if self._renewed or not self.credential.refreshable:
            return False
        self._renewed = True
        try:
            self.credential.refresh_now()
        except ValueError:
            return False
        except IntegrationError as error:
            if error.transient:
                raise
            return False
        return True


class _Missing(Exception):
    """A resource Google no longer has (``404`` or ``410``)."""


def _raise_for_status(response: httpx2.Response) -> None:
    """Map a Calendar refusal onto an operator-safe :class:`GoogleCalendarError`."""

    status = response.status_code
    if response.is_success:
        return
    reason = _error_reason(response.content)
    if status == 429 or (status == 403 and reason in _RATE_LIMIT_REASONS):
        raise GoogleCalendarError(
            "Google Calendar is rate limiting this account; the sync tries again later.",
            transient=True,
            retry_after=retry_after(response),
        )
    if status == 401:
        raise GoogleCalendarError("Google Calendar no longer accepts this account; reconnect the Google account.")
    if status == 403 and reason in _SCOPE_REFUSALS:
        raise GoogleCalendarError(
            "This Google account has not granted Calendar access; reconnect it with Calendar access."
        )
    if status == 404:
        raise GoogleCalendarError("Google Calendar no longer serves this calendar to the account.")
    if status >= 500:
        raise GoogleCalendarError("Google Calendar is temporarily unavailable.", transient=True)
    raise GoogleCalendarError(f"Google Calendar refused the request (HTTP {status}).")


def _error_reason(body: bytes) -> str:
    """Return Google's error reason (``rateLimitExceeded``, ...) from a refusal body, or ``""``."""

    try:
        error = json.loads(body).get("error") or {}
        errors = error.get("errors") or ()
        return str(errors[0].get("reason") or "") if errors else str(error.get("status") or "")
    except (ValueError, AttributeError, IndexError, TypeError):
        return ""


def _segment(value: str) -> str:
    """Quote one path segment: calendar ids carry ``@`` and ``#``."""

    return quote(value, safe="")


def _page(token: str) -> dict[str, str]:
    """Return the ``pageToken`` parameter of a continued listing, or none."""

    return {"pageToken": token} if token else {}
