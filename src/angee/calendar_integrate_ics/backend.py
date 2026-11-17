"""Read-only ICS feed subscriptions: one calendar per feed URL, polled as full snapshots.

The feed is the source's one remote calendar: the base backend's ``calendars``
stream publishes one record for it, keyed ``feed``, and its events sync through
the ``events`` stream partitioned by that key. The feed address is a secret (a
private feed's token rides in it): it is the source credential's material, read
only here, and the source's ``config`` keeps a redacted label (:func:`feed_label`).

Every poll fetches the feed through integrate's SSRF-pinned bounded download
(public addresses only; 16 MiB, five redirects and a minute in total), with the
stream cursor's ETag and Last-Modified as a conditional GET: a 304 means no
work. ``webcal://`` is fetched as ``https://``. python-icalendar parses the
iCalendar; the calendar's base backend owns the event projection and every
write. A changed feed is paged in key order, and the driver applies only the
events whose projection changed. A key the feed stopped serving is found by
integrate's absence sweep, once per poll interval: unavailable first, then
tombstoned. One sync reads the feed once for its pages, identity reads and
sweep. The external key is the UID (its digest when too long), plus the
``RECURRENCE-ID`` instant for an instance; a feed carries at most 10,000 events.

**Zones are resolved per parse.** icalendar's parser caches each feed-defined
``VTIMEZONE`` in a process-wide registry, so a resolved value can carry another
feed's definition of the same TZID. This module never reads one: it takes each
moment's wall clock and ``TZID`` and resolves the TZID itself (:class:`_Zones`).
An IANA name, a Windows name or a vendor-prefixed IANA name maps to the IANA zone
(through icalendar's Windows table); any other TZID resolves from this feed's
own ``VTIMEZONE``.

Mapped properties:

- ``UID``, ``RECURRENCE-ID``, ``SUMMARY``, ``DESCRIPTION``, ``LOCATION``;
- ``DTSTART``, ``DTEND`` or ``DURATION`` (``VALUE=DATE`` is all-day); the event
  keeps its ``DTSTART`` zone as its own ``timezone`` when that maps to an IANA name
  (a UTC time keeps ``UTC``); a date or a floating time keeps the feed's
  ``X-WR-TIMEZONE``, else its calendar's zone;
- the first ``RRULE``; each ``EXDATE`` becomes a cancelled instance (``RDATE`` is
  not mapped);
- ``STATUS``, ``TRANSP`` (``TRANSPARENT`` is free), ``CLASS`` (``PUBLIC`` is
  public, ``PRIVATE`` and ``CONFIDENTIAL`` private, none the calendar's default);
  an instance that states no ``TRANSP`` or ``CLASS`` takes its master's;
- ``ORGANIZER`` and ``ATTENDEE`` ``mailto:`` addresses with ``CN``, ``ROLE`` and
  ``PARTSTAT``;
- the conference URL: ``CONFERENCE`` (RFC 7986), ``X-GOOGLE-CONFERENCE``,
  ``X-MICROSOFT-ONLINEMEETINGEXTERNALLINK``, ``X-MICROSOFT-SKYPETEAMSMEETINGURL``,
  then ``URL``, the first web address.

NUL characters are stripped from the whole body before parsing, so no text value
or parameter carries one.
"""

from __future__ import annotations

import hashlib
import ipaddress
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from urllib.parse import urlsplit

import icalendar
from django.apps import apps
from django.core.exceptions import ValidationError
from django.utils import timezone
from icalendar.timezone.windows_to_olson import WINDOWS_TO_OLSON

from angee.calendar.backends import (
    CalendarBackend,
    ParsedAttendee,
    ParsedCalendar,
    ParsedEvent,
    instance_key,
    series_key,
)
from angee.calendar.choices import AttendeeResponse, AttendeeRole, EventStatus, EventTransparency, EventVisibility
from angee.integrate.errors import IntegrationError
from angee.integrate.http import DownloadResult, OutboundBudget
from angee.integrate.net import AddressNotAllowedError, AddressResolutionError, is_unsafe_address, parse_http_url
from angee.integrate.states import StreamPhase
from angee.integrate.streams import StreamPage
from angee.scheduling.zones import get_zone_info, get_zone_names

FEED_KEY = "feed"
"""The key of an ICS source's one remote calendar: its feed (the calendars record and events partition)."""

_FEED_BUDGET = OutboundBudget(requests=6, redirects=5, bytes=16 * 1024 * 1024, deadline_seconds=60.0)
"""One feed fetch's bounds: a 16 MiB decoded body, five redirects, and a minute in total."""

_REPORTED_STATUSES = frozenset({304, *range(400, 600)})
"""The statuses a fetch reports itself: a conditional GET's 304, and a refusal named to the operator."""

_EVENTS_PER_FEED = 10_000
"""The most VEVENTs a feed may carry; a larger feed fails its poll with an operator message."""

_CONFERENCE_PROPERTIES = (
    "CONFERENCE",
    "X-GOOGLE-CONFERENCE",
    "X-MICROSOFT-ONLINEMEETINGEXTERNALLINK",
    "X-MICROSOFT-SKYPETEAMSMEETINGURL",
    "URL",
)
_STATUSES = {"CONFIRMED": EventStatus.CONFIRMED, "TENTATIVE": EventStatus.TENTATIVE, "CANCELLED": EventStatus.CANCELLED}
_VISIBILITIES = {
    "PUBLIC": EventVisibility.PUBLIC,
    "PRIVATE": EventVisibility.PRIVATE,
    "CONFIDENTIAL": EventVisibility.PRIVATE,
}
_ROLES = {"OPT-PARTICIPANT": AttendeeRole.OPTIONAL, "NON-PARTICIPANT": AttendeeRole.OPTIONAL}
_RESPONSES = {
    "ACCEPTED": AttendeeResponse.ACCEPTED,
    "DECLINED": AttendeeResponse.DECLINED,
    "DELEGATED": AttendeeResponse.DECLINED,
    "TENTATIVE": AttendeeResponse.TENTATIVE,
}


class IcsError(IntegrationError):
    """A backend-composed, operator-safe ICS feed refusal."""


def feed_url(url: str) -> str:
    """Return the HTTP(S) address of a feed, reading ``webcal://`` and ``webcals://`` as ``https://``.

    Only the address's shape is checked here, with no network: an http, https or
    webcal URL whose host is not a private address literal. The pinned transport
    judges a named host when the feed is fetched.
    """

    text = (url or "").strip()
    scheme, _, rest = text.partition("://")
    if scheme.lower() in {"webcal", "webcals"}:
        text = f"https://{rest}"
    try:
        host = parse_http_url(text).hostname or ""
    except ValidationError as error:
        raise IcsError("Enter an http, https or webcal calendar feed address.") from error
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        return text
    if is_unsafe_address(literal):
        raise IcsError("The calendar feed address is not allowed.")
    return text


def feed_label(url: str) -> str:
    """Return what may be shown of a feed address: its host, and its file name when that names a calendar.

    The path and query can carry the feed's secret, so nothing else is kept.
    """

    parts = urlsplit(url)
    name = parts.path.rstrip("/").rsplit("/", 1)[-1]
    shown = name.lower().endswith((".ics", ".ical", ".ifb")) and len(name) <= 40
    return f"{parts.hostname}/…/{name}" if shown else f"{parts.hostname}/…"


class IcsCalendarBackend(CalendarBackend):
    """Polls one public ICS feed into its source's read-only calendar.

    The feed is the source's one remote calendar, keyed :data:`FEED_KEY`; its address
    is the source credential's secret. The calendar takes no local edits: a feed
    never takes a write (``writable`` stays false).
    """

    key = "ics"
    label = "ICS feed"
    icon = "calendar"
    defaults = {"vendor": "ics", "poll_interval": 3600}

    _snapshot: dict[str, dict[str, Any]] | None = None

    def discover(self) -> list[ParsedCalendar]:
        """Return the feed, the source's one remote calendar, named as subscribed."""

        return [ParsedCalendar(key=FEED_KEY, name=self.bridge.display_name)]

    def extract_events(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Page the feed's events in key order; the feed's validators commit with the last page.

        A delta poll asks conditionally on the committed validators, and a 304 is no
        work; a baseline (a new or resynced stream) fetches unconditionally. While
        pages remain the cursor carries only the last key, and a later page reads the
        same snapshot (:meth:`_events_now`). A key the feed stopped serving is
        integrate's absence sweep's to find (:meth:`event_keys`).
        """

        cursor = dict(stream.cursor or {})
        if "after" in cursor:
            payloads = self._events_now(stream)
            return self.page(stream, payloads, page_bound, after=cursor["after"], cursor=cursor["next"])
        result = self._fetch(cursor if stream.phase != StreamPhase.BASELINE else {})
        if result.status_code == 304:
            return StreamPage((), cursor)
        validators = {"etag": result.headers.get("etag", ""), "last_modified": result.headers.get("last-modified", "")}
        self._snapshot = self._observe_feed(_read(result.content), zone=self.get_calendar(stream).timezone)
        return self.page(stream, self._snapshot, page_bound, after=None, cursor=validators)

    def read_events(self, stream: Any, keys: Sequence[str]) -> dict[str, dict[str, Any] | None]:
        """Read the requested keys from the feed's snapshot; a key it no longer carries is ``None``."""

        payloads = self._events_now(stream)
        return {key: payloads.get(key) for key in keys}

    def event_keys(self, stream: Any) -> list[str]:
        """Return every key the feed's snapshot carries."""

        return list(self._events_now(stream))

    def _events_now(self, stream: Any) -> dict[str, dict[str, Any]]:
        """Return the feed's events as this adapter last read them, fetching the feed once when it has not.

        One adapter serves one sync of the feed's partition, so its pages, identity
        reads and absence sweep share one snapshot. A later sync's adapter reads the
        feed afresh.
        """

        if self._snapshot is None:
            self._snapshot = self._observe_feed(_read(self._fetch({}).content), zone=self.get_calendar(stream).timezone)
        return self._snapshot

    # --- transport ---

    def _url(self) -> str:
        """Return the feed address, the source credential's secret."""

        credential = self.bridge.fresh_credential()
        address = credential.secret_value() if credential is not None else ""
        if not address:
            raise IcsError("This subscription has no feed address.")
        return feed_url(address)

    def _fetch(self, validators: Mapping[str, Any]) -> DownloadResult:
        """GET the feed from a public address within :data:`_FEED_BUDGET`, conditionally on ``validators``.

        A 304 to a conditional request returns as is; a 429 or 5xx is a transient
        refusal that keeps the server's ``Retry-After``.
        """

        headers = {"Accept": "text/calendar, */*;q=0.1"}
        if validators.get("etag"):
            headers["If-None-Match"] = str(validators["etag"])
        if validators.get("last_modified"):
            headers["If-Modified-Since"] = str(validators["last_modified"])
        try:
            result = self.http.download_bounded(
                self._url(), budget=_FEED_BUDGET, headers=headers, allow_private=False, statuses=_REPORTED_STATUSES
            )
        except AddressNotAllowedError as error:
            raise IcsError("The calendar feed address is not allowed.") from error
        except AddressResolutionError as error:
            raise IcsError("The calendar feed host could not be found.") from error
        if result is None:
            raise IcsError("The calendar feed could not be read within its size, time and redirect limits.")
        status = result.status_code
        if status == 304 and len(headers) > 1:
            return result
        if status == 429 or status >= 500:
            raise IcsError(
                f"The calendar feed returned HTTP {status}.", transient=True, retry_after=_retry_after(result.headers)
            )
        if not 200 <= status < 300:
            raise IcsError(f"The calendar feed returned HTTP {status}.")
        return result

    # --- snapshot ---

    def _observe_feed(self, feed: Any, *, zone: str) -> dict[str, dict[str, Any]]:
        """Return each event's retained payload by key; an unmappable event is a quarantine payload under its own key.

        A ``RECURRENCE-ID`` instance overrides its series when the feed carries the
        series' master, and replaces a cancelled ``EXDATE`` instance at the same
        moment; an instance whose master the feed lacks is a single event. Of two
        copies of one key, the higher ``SEQUENCE`` wins. ``zone`` is the calendar's.
        """

        components = list(feed.walk("VEVENT"))
        if len(components) > _EVENTS_PER_FEED:
            raise IcsError(f"The calendar feed has more than {_EVENTS_PER_FEED} events.")
        zones = _Zones(feed)
        feed_zone = zones.resolve(_text(feed, "X-WR-TIMEZONE"))[0] if _text(feed, "X-WR-TIMEZONE") else ""
        local = feed_zone or zone
        masters = {_text(component, "UID"): component for component in components if "RECURRENCE-ID" not in component}
        exceptions: dict[str, dict[str, Any]] = {}
        events: dict[str, tuple[int, dict[str, Any]]] = {}
        for component in components:
            uid = _text(component, "UID")
            if not uid:
                digest = hashlib.sha256(component.to_ical()).hexdigest()
                events[f"missing-uid:{digest}"] = (0, {"error": "missing_uid"})
                continue
            sequence = _sequence(component)
            try:
                observed = _events(
                    component,
                    uid=uid,
                    zones=zones,
                    zone=local,
                    feed_zone=feed_zone,
                    calendar_zone=zone,
                    master=masters.get(uid),
                )
            except Exception:  # noqa: BLE001 — the driver quarantines semantic input per record.
                events.setdefault(_own_key(component, uid, zones, local), (sequence, {"error": "invalid_event"}))
                continue
            (key, event), *cancelled = observed
            if key not in events or events[key][0] <= sequence:
                events[key] = (sequence, self.observe(event))
            for instance, exception in cancelled:
                exceptions[instance] = self.observe(exception)
        return {**exceptions, **{key: payload for key, (_sequence_, payload) in events.items()}}


class _Zones:
    """One feed's TZIDs, resolved for this parse only and never through a process-wide registry.

    An IANA name, a Windows name (icalendar's table) or an RFC 5545 vendor-prefixed
    IANA name (``/vendor/Europe/Berlin``) resolves to its IANA zone. Any other TZID
    resolves from this feed's own ``VTIMEZONE`` (``Timezone.to_tz`` without lookup),
    and has no IANA name.
    """

    def __init__(self, feed: Any) -> None:
        self._components = {
            str(component.get("TZID", "")).strip("/"): component for component in feed.walk("VTIMEZONE")
        }
        self._resolved: dict[str, tuple[str, tzinfo | None]] = {}

    def resolve(self, tzid: str) -> tuple[str, tzinfo | None]:
        """Return a TZID's IANA name (blank for a feed-defined zone) and its zone (``None`` when unknown)."""

        if tzid not in self._resolved:
            name = _iana_name(tzid)
            zone: tzinfo | None = get_zone_info(name) if name else None
            component = self._components.get(tzid.strip().strip("/"))
            if zone is None and component is not None:
                try:
                    zone = component.to_tz(lookup_tzid=False)
                except Exception:  # noqa: BLE001 — an unusable definition leaves the zone unknown.
                    zone = None
            self._resolved[tzid] = (name, zone)
        return self._resolved[tzid]


def _iana_name(tzid: str) -> str:
    """Return the IANA zone a TZID names, through icalendar's Windows table or a vendor prefix; else blank."""

    cleaned = tzid.strip().strip("/")
    names = get_zone_names()
    parts = cleaned.split("/")
    suffixes = ["/".join(parts[start:]) for start in range(1, len(parts))]
    candidates = [cleaned, WINDOWS_TO_OLSON.get(cleaned, ""), *suffixes]
    return next((candidate for candidate in candidates if candidate in names), "")


def _read(content: bytes) -> Any:
    """Parse one iCalendar body without NUL characters, refusing anything that is not a ``VCALENDAR``."""

    text = content.decode("utf-8-sig", errors="replace").replace("\x00", "")
    try:
        feed = icalendar.Calendar.from_ical(text)
    except Exception as error:  # noqa: BLE001 — any parser refusal is the same operator message.
        raise IcsError("The calendar feed is not valid iCalendar.") from error
    if feed.name != "VCALENDAR":
        raise IcsError("The calendar feed is not valid iCalendar.")
    return feed


def _events(
    component: Any, *, uid: str, zones: _Zones, zone: str, feed_zone: str, calendar_zone: str, master: Any
) -> list[tuple[str, ParsedEvent]]:
    """Map one VEVENT to its keyed event, then a master's cancelled ``EXDATE`` instances.

    ``master`` is the series' master VEVENT when the feed carries it. An instance
    that states no ``TRANSP`` or ``CLASS`` takes its master's. An ``EXDATE`` that
    is no instance of the rule is dropped, and a cancelled instance ends where the
    event owner's arithmetic puts it (``CalendarEvent.occurrence_end``: an all-day
    instance spans local days), both expanded in the zone the stored series uses.
    ``calendar_zone`` is the calendar's.
    """

    start_prop = component["DTSTART"]
    all_day = not isinstance(start_prop.dt, datetime)
    start = _instant(start_prop.dt, _param(start_prop, "TZID"), zones, zone)
    end = _end(component, start_prop.dt, start, all_day=all_day, zones=zones, zone=zone)
    rid = component.get("RECURRENCE-ID")
    original_start = _instant(rid.dt, _param(rid, "TZID"), zones, zone) if rid is not None else None
    override = original_start is not None and master is not None and master is not component
    series_master = master if override else None
    event = ParsedEvent(
        title=_text(component, "SUMMARY"),
        description=_text(component, "DESCRIPTION"),
        location=_text(component, "LOCATION"),
        start=start,
        end=end,
        all_day=all_day,
        recurrence="" if rid is not None else _rule(component),
        timezone=_event_zone(start_prop, zones, feed_zone),
        series=series_key(uid) if override else "",
        original_start=original_start if override else None,
        status=_STATUSES.get(_text(component, "STATUS").upper(), EventStatus.CONFIRMED),
        transparency=(
            EventTransparency.FREE
            if _stated(component, series_master, "TRANSP").upper() == "TRANSPARENT"
            else EventTransparency.BUSY
        ),
        visibility=_VISIBILITIES.get(_stated(component, series_master, "CLASS").upper(), EventVisibility.DEFAULT),
        conference_url=next(
            (
                url
                for name in _CONFERENCE_PROPERTIES
                for url in _texts(component, name)
                if url.lower().startswith(("https://", "http://"))
            ),
            "",
        ),
        attendees=_attendees(component),
    )
    observed = [(instance_key(uid, original_start) if original_start is not None else series_key(uid), event)]
    if rid is None and event.recurrence:
        # The unsaved row only lends its owner's rule arithmetic; nothing writes it.
        series: Any = apps.get_model("calendar", "CalendarEvent")(
            start=start, end=end, all_day=all_day, recurrence=event.recurrence
        )
        expansion = event.timezone or calendar_zone
        for exdate in _values(component, "EXDATE"):
            for value in exdate.dts:
                moment = _instant(value.dt, _param(exdate, "TZID"), zones, zone)
                if not series.is_instance(moment, zone=expansion):
                    continue
                observed.append(
                    (
                        instance_key(uid, moment),
                        replace(
                            event,
                            start=moment,
                            end=series.occurrence_end(moment, zone=expansion),
                            recurrence="",
                            series=series_key(uid),
                            original_start=moment,
                            status=EventStatus.CANCELLED,
                            attendees=(),
                        ),
                    )
                )
    return observed


def _own_key(component: Any, uid: str, zones: _Zones, zone: str) -> str:
    """Return the key an unmappable VEVENT is quarantined under: its own instance key, never its master's."""

    rid = component.get("RECURRENCE-ID")
    if rid is None:
        return series_key(uid)
    try:
        return instance_key(uid, _instant(rid.dt, _param(rid, "TZID"), zones, zone))
    except Exception:  # noqa: BLE001 — an unreadable instance keeps a key of its own content.
        return f"{series_key(uid)}#invalid:{hashlib.sha256(component.to_ical()).hexdigest()[:16]}"


def _instant(value: date | datetime, tzid: str, zones: _Zones, zone: str) -> datetime:
    """Return one iCalendar moment as aware UTC, from its wall clock and ``TZID``.

    A date is local midnight in ``zone``; a ``TZID`` resolves through :class:`_Zones`
    (an unknown one reads as ``zone``); a UTC time keeps its instant; a floating time
    is read in ``zone``. A zone icalendar attached itself is never used.
    """

    if not isinstance(value, datetime):
        return timezone.make_aware(datetime.combine(value, time.min), get_zone_info(zone)).astimezone(UTC)
    wall = value.replace(tzinfo=None)
    if tzid:
        resolved = zones.resolve(tzid)[1]
        return timezone.make_aware(wall, resolved or get_zone_info(zone)).astimezone(UTC)
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return timezone.make_aware(wall, get_zone_info(zone)).astimezone(UTC)


def _event_zone(prop: Any, zones: _Zones, feed_zone: str) -> str:
    """Return the zone an event keeps: its DTSTART's IANA zone, ``UTC`` for a UTC time, else the feed's (or blank)."""

    tzid = _param(prop, "TZID")
    if tzid:
        return zones.resolve(tzid)[0]
    value = prop.dt
    if isinstance(value, datetime) and value.tzinfo is not None:
        return "UTC"
    return feed_zone


def _end(
    component: Any, start_value: date | datetime, start: datetime, *, all_day: bool, zones: _Zones, zone: str
) -> datetime:
    """Return the event's end from ``DTEND``, else ``DURATION``, else one day (all-day) or its start."""

    if "DTEND" in component:
        prop = component["DTEND"]
        return _instant(prop.dt, _param(prop, "TZID"), zones, zone)
    if "DURATION" in component:
        duration = component["DURATION"].dt
        if all_day:
            return _instant(start_value + timedelta(days=duration.days), "", zones, zone)
        return start + duration
    if all_day:
        return _instant(start_value + timedelta(days=1), "", zones, zone)
    return start


def _attendees(component: Any) -> tuple[ParsedAttendee, ...]:
    """Return the ``mailto:`` attendees, the organizer first as the ``organizer`` attendee."""

    listed: dict[str, ParsedAttendee] = {}
    for prop in _values(component, "ATTENDEE"):
        address = _mailto(prop)
        if address:
            listed.setdefault(
                address.lower(),
                ParsedAttendee(
                    address=address,
                    role=_ROLES.get(_param(prop, "ROLE").upper(), AttendeeRole.REQUIRED),
                    response=_RESPONSES.get(_param(prop, "PARTSTAT").upper(), AttendeeResponse.NEEDS_ACTION),
                    display_name=_param(prop, "CN"),
                ),
            )
    organizer = component.get("ORGANIZER")
    address = _mailto(organizer) if organizer is not None else ""
    if not address:
        return tuple(listed.values())
    listed_as = listed.pop(address.lower(), None)
    return (
        ParsedAttendee(
            address=address,
            role=AttendeeRole.ORGANIZER,
            response=listed_as.response if listed_as is not None else AttendeeResponse.ACCEPTED,
            display_name=_param(organizer, "CN") or (listed_as.display_name if listed_as is not None else ""),
        ),
        *listed.values(),
    )


def _values(component: Any, name: str) -> list[Any]:
    """Return every occurrence of a property, which icalendar gives as one value or a list."""

    value = component.get(name)
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _text(component: Any, name: str) -> str:
    """Return a single-valued property's text, or ``""``."""

    values = _values(component, name)
    return str(values[0]).strip() if values else ""


def _texts(component: Any, name: str) -> list[str]:
    """Return the text of every occurrence of a property."""

    return [str(value).strip() for value in _values(component, name)]


def _stated(component: Any, master: Any, name: str) -> str:
    """Return a property's text, else its series master's when ``master`` is given, else ``""``."""

    return _text(component, name) or (_text(master, name) if master is not None else "")


def _rule(component: Any) -> str:
    """Return the first ``RRULE`` as its RFC 5545 text, or ``""``."""

    rules = _values(component, "RRULE")
    return rules[0].to_ical().decode().strip() if rules else ""


def _param(prop: Any, name: str) -> str:
    """Return a property parameter's text, or ``""``."""

    value = prop.params.get(name, "")
    return str(value[0] if isinstance(value, list) and value else value).strip()


def _mailto(value: Any) -> str:
    """Return the address of a ``mailto:`` calendar user, or ``""`` for another kind of address."""

    text = str(value or "").strip()
    if text.lower().startswith("mailto:"):
        return text[len("mailto:") :].strip()
    return text if "@" in text and ":" not in text else ""


def _sequence(component: Any) -> int:
    """Return the event's ``SEQUENCE``, or 0."""

    text = _text(component, "SEQUENCE")
    return int(text) if text.isdecimal() else 0


def _retry_after(headers: Mapping[str, str]) -> timedelta | None:
    """Return a bounded ``Retry-After`` horizon in seconds, when the feed gives one."""

    hint = headers.get("retry-after", "")
    return timedelta(seconds=int(hint)) if hint.isdecimal() and 0 < int(hint) <= 86400 else None
