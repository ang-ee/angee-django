"""Google Calendar API v3, called with one source credential through the one Google REST transport.

The one owner of the Calendar wire protocol for this addon: the backends list an
account's calendars and page a calendar's events through it, and a publishing
backend finds, inserts and conditionally patches the events it writes. Requests go through
``angee.integrate_google.api.GoogleApi`` (integrate's SSRF-pinned HTTP owner, the
credential's authorization for the backend's scopes, one renew of a rejected
token, Google's error envelope). No Google client library is involved.

A sync token Google no longer honours (``410 Gone``) is :class:`SyncTokenExpired`,
which the backend turns into a new baseline; a page token it refuses (``400``,
reason ``invalid``) is :class:`PageTokenInvalid`, which lists again from the sync
token the page continued. An event or instance Google no longer
has reads as ``None``: a ``410``, or a ``404`` once the calendar itself is known to
be served (:meth:`GoogleCalendarClient.require_calendar`, read once per client); a
calendar Google no longer serves is the whole calendar's refusal. A write names its
precondition: an insert whose id is taken is :class:`EventIdTaken` (``409``), and a
patch or delete whose ``If-Match`` ETag is stale is :class:`PreconditionFailed`
(``412``); writes send no invitations (``sendUpdates=none``). Google's refusal of one
event (``400``, or a ``403`` whose reason names the event) is :class:`EventRefused`.
Every other refusal is a :class:`GoogleCalendarError`, as the transport names it
(``GoogleApi.account_error``): the account's or the calendar's, never one event's.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

from angee.integrate.http import HttpRefusal
from angee.integrate_google.api import GoogleApi, GoogleApiError, error_reason
from angee.scheduling.zones import get_zone_info

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
MAX_PAGE_SIZE = 2500
"""Google's largest ``maxResults`` for ``events.list``."""

_CALENDAR_FIELDS = "nextPageToken,items(id,summary,summaryOverride,backgroundColor,timeZone)"
_CALENDAR_LIST = {"fields": _CALENDAR_FIELDS, "showHidden": "true", "minAccessRole": "reader"}
"""Every calendar the account reads, hidden ones included; deleted entries are not listed (no ``showDeleted``)."""

_QUIET = {"sendUpdates": "none"}
"""A write tells no attendee: a published event is the calendar's, not an invitation."""

_PROBE = {"maxResults": "1", "fields": "kind"}
"""What :meth:`GoogleCalendarClient.require_calendar` reads of a calendar: whether it lists, nothing of its events."""

_STAMPED_PAGE = 10
"""How many stamped events one lookup reads: a stamp names one event, so more is already a conflict."""

_EVENT_REASONS = {
    "forbiddenForNonOrganizer": "Only this event's organizer may make this change in Google Calendar.",
    "forbiddenForServiceAccounts": "Google Calendar does not let this service account invite the event's attendees.",
    "cannotChangeOrganizer": "Google Calendar does not allow changing this event's organizer.",
    "cannotChangeOrganizerOfInstance": "Google Calendar does not allow changing the organizer of one occurrence.",
}
"""The ``403`` reasons that refuse one event (its organizer, its attendees), not the calendar or the account,
each in this addon's words; any other event refusal names only its status."""


class GoogleCalendarError(GoogleApiError):
    """A Google Calendar refusal whose message is safe to show an operator."""


class SyncTokenExpired(Exception):
    """Google no longer honours a sync token (``410 Gone``): the calendar must be listed again in full."""


class PageTokenInvalid(Exception):
    """Google refused a continued listing's page token (``400``, reason ``invalid``): list from its start again."""


class EventIdTaken(Exception):
    """Google refused an insert (``409``): an event, a deleted one included, already has the chosen id."""


class PreconditionFailed(Exception):
    """Google refused a conditional write (``412``): the event changed since the ETag the write named."""


class EventRefused(GoogleCalendarError):
    """Google refused a request about one event (``400``, or a ``403`` naming the event): that event's own refusal.

    A caller that does not set the event aside meets it as the generic refusal it extends.
    """

    def __init__(self, status: int, reason: str) -> None:
        """Word Google's ``reason`` in this addon's phrase (:data:`_EVENT_REASONS`) and keep the HTTP status."""

        super().__init__(_EVENT_REASONS.get(reason, f"Google Calendar refused this event (HTTP {status})."))
        self.status = status


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
        self._served: set[str] = set()

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
        self._raise_for_status(response, page_token=page_token)
        return self.api.json(response)

    def require_calendar(self, calendar_id: str) -> None:
        """Refuse, as the calendar's failure, a calendar Google no longer serves; each is read once per client.

        The probe lists one event (``events.list``), a read every scope this client
        reads or writes events under allows; ``calendars.get`` needs a calendar
        scope, which a publishing key (``calendar.events``) never holds.
        """

        if calendar_id not in self._served:
            self._get(f"calendars/{_segment(calendar_id)}/events", _PROBE)
            self._served.add(calendar_id)

    def event(self, calendar_id: str, event_id: str) -> dict[str, Any] | None:
        """Return one event, or ``None`` once Google no longer has it."""

        return self._event_or_none(calendar_id, f"events/{_segment(event_id)}", {})

    def instance(
        self, calendar_id: str, event_id: str, original: datetime, *, all_day_zone: str | None = None
    ) -> dict[str, Any] | None:
        """Return the instance of the series ``event_id`` originally at ``original``, cancelled ones included.

        Google names an instance by its ``originalStart``: an all-day series' by its
        local date in ``all_day_zone`` (the zone the series expands in; blank is the
        project's), any other's (``all_day_zone`` ``None``) by its UTC instant.
        """

        original_start = (
            original.astimezone(get_zone_info(all_day_zone)).date().isoformat()
            if all_day_zone is not None
            else original.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        page = self._event_or_none(
            calendar_id,
            f"events/{_segment(event_id)}/instances",
            {"originalStart": original_start, "showDeleted": "true", "maxResults": "1"},
        )
        items = (page or {}).get("items") or ()
        return dict(items[0]) if items else None

    def stamped(self, calendar_id: str, name: str, value: str) -> list[dict[str, Any]]:
        """Return the events whose private extended property ``name`` is ``value``, removed ones included."""

        params = {"privateExtendedProperty": f"{name}={value}", "showDeleted": "true", "maxResults": str(_STAMPED_PAGE)}
        response = self.api.send("GET", _url(f"calendars/{_segment(calendar_id)}/events", params))
        self._raise_for_status(response, event=True)
        return [dict(item) for item in self.api.json(response).get("items") or ()]

    def insert(self, calendar_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Insert one event under the id ``body`` names and return it; raise :class:`EventIdTaken` when it is taken."""

        response = self.api.send("POST", _url(f"calendars/{_segment(calendar_id)}/events", _QUIET), body=dict(body))
        if response.status_code == 409:
            raise EventIdTaken()
        self._raise_for_status(response, event=True)
        return self.api.json(response)

    def patch(self, calendar_id: str, event_id: str, body: Mapping[str, Any], *, etag: str) -> dict[str, Any] | None:
        """Change the fields ``body`` names while the event's ETag is ``etag``; ``None`` once Google no longer has it.

        Raises :class:`PreconditionFailed` when the event changed since that ETag.
        """

        path = f"calendars/{_segment(calendar_id)}/events/{_segment(event_id)}"
        response = self.api.send("PATCH", _url(path, _QUIET), body=dict(body), headers={"If-Match": etag})
        if response.status_code == 412:
            raise PreconditionFailed()
        if self._gone(calendar_id, response):
            return None
        self._raise_for_status(response, event=True)
        return self.api.json(response)

    def delete(self, calendar_id: str, event_id: str, *, etag: str) -> None:
        """Delete one event while its ETag is ``etag``; one Google no longer has is already deleted.

        Raises :class:`PreconditionFailed` when the event changed since that ETag.
        """

        path = f"calendars/{_segment(calendar_id)}/events/{_segment(event_id)}"
        response = self.api.send("DELETE", _url(path, _QUIET), headers={"If-Match": etag})
        if response.status_code == 412:
            raise PreconditionFailed()
        if not self._gone(calendar_id, response):
            self._raise_for_status(response, event=True)

    def _get(self, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        """GET one Calendar JSON resource."""

        response = self.api.send("GET", _url(path, params))
        self._raise_for_status(response)
        return self.api.json(response)

    def _event_or_none(self, calendar_id: str, path: str, params: Mapping[str, str]) -> dict[str, Any] | None:
        """GET a resource of one event of ``calendar_id`` that may be gone (:meth:`_gone`): ``None`` then."""

        response = self.api.send("GET", _url(f"calendars/{_segment(calendar_id)}/{path}", params))
        if self._gone(calendar_id, response):
            return None
        self._raise_for_status(response, event=True)
        return self.api.json(response)

    def _gone(self, calendar_id: str, response: Any) -> bool:
        """Whether an answer about one event says Google no longer has it: ``410``, or ``404`` on a served calendar.

        A ``404`` never reaches the whole account (``GoogleApi.account_error``); it names the event only once the
        calendar is known to be served (:meth:`require_calendar`), otherwise the calendar's refusal is raised.
        """

        if response.status_code == 404:
            self.require_calendar(calendar_id)
            return True
        return response.status_code == 410

    def _raise_for_status(self, response: Any, *, event: bool = False, page_token: str = "") -> None:
        """Raise a Calendar refusal, which the Google transport builds once (``GoogleApi.raise_for_status``).

        After the account-wide refusals: a continued listing's page token Google refused (``400``, reason
        ``invalid``) is :class:`PageTokenInvalid`; a request about one ``event`` refused for that event (``400``,
        or a ``403`` whose reason names it) is :class:`EventRefused`; a ``404`` is a calendar Google no longer
        serves; anything else is the generic refusal.
        """

        status = response.status_code

        def own(refusal: HttpRefusal) -> Exception | None:
            reason = error_reason(refusal.body)
            if page_token and status == 400 and reason == "invalid":
                return PageTokenInvalid()
            if event and (status == 400 or (status == 403 and reason in _EVENT_REASONS)):
                return EventRefused(status, reason)
            if status == 404:
                return GoogleCalendarError("Google Calendar no longer serves this calendar to the account.")
            return None

        self.api.raise_for_status(response, own=own)


def _url(path: str, params: Mapping[str, str]) -> str:
    """Return the API URL of ``path`` with its query."""

    return f"{CALENDAR_API}/{path}" + (f"?{urlencode(params)}" if params else "")


def _segment(value: str) -> str:
    """Quote one path segment: calendar ids carry ``@`` and ``#``."""

    return quote(value, safe="")


def _page(token: str) -> dict[str, str]:
    """Return the ``pageToken`` parameter of a continued listing, or none."""

    return {"pageToken": token} if token else {}
