"""Read-only ICS feed subscriptions: one calendar per feed URL, polled as full snapshots.

Every poll fetches the feed through integrate's SSRF-pinned client, public
addresses only, with the stream cursor's ETag and Last-Modified as a conditional
GET: a 304 means no work. ``webcal://`` is fetched as ``https://``. vobject parses
the iCalendar; the calendar's base backend owns the event projection and every
write. Each fetched feed is a full snapshot: events whose payload differs from
their link's last observation are applied, and keys the feed no longer carries
are tombstoned, so an unchanged feed applies nothing. The external key is the
UID, plus the ``RECURRENCE-ID`` instant for an instance. Mapped properties:

- ``UID``, ``RECURRENCE-ID``, ``SUMMARY``, ``DESCRIPTION``, ``LOCATION``;
- ``DTSTART``, ``DTEND`` or ``DURATION`` (``VALUE=DATE`` is all-day); a ``TZID``
  that names an IANA zone resolves through the scheduling zones owner, another
  through the feed's ``VTIMEZONE``, and a floating time or a date in the calendar's
  zone;
- the first ``RRULE``; each ``EXDATE`` becomes a cancelled instance (``RDATE`` is
  not mapped);
- ``STATUS``, ``TRANSP`` (``TRANSPARENT`` is free), ``CLASS`` (``PUBLIC`` is
  public, ``PRIVATE`` and ``CONFIDENTIAL`` private, none the calendar's default);
- ``ORGANIZER`` and ``ATTENDEE`` ``mailto:`` addresses with ``CN``, ``ROLE`` and
  ``PARTSTAT``;
- the conference URL: ``CONFERENCE`` (RFC 7986), ``X-GOOGLE-CONFERENCE``,
  ``X-MICROSOFT-ONLINEMEETINGEXTERNALLINK``, ``X-MICROSOFT-SKYPETEAMSMEETINGURL``,
  then ``URL``, the first web address.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, ClassVar
from urllib.parse import urlsplit

import vobject
from django.core.exceptions import ValidationError
from django.utils import timezone

from angee.base.serialization import canonical_json_sha256
from angee.calendar.backends import (
    ORGANIZER,
    CalendarBackend,
    ParsedAttendee,
    ParsedCalendar,
    ParsedEvent,
    instance_key,
)
from angee.calendar.models import AttendeeResponse, AttendeeRole, EventStatus, EventTransparency, EventVisibility
from angee.integrate.errors import IntegrationError
from angee.integrate.http import ResponseTooLargeError
from angee.integrate.net import AddressNotAllowedError, AddressResolutionError, parse_http_url
from angee.integrate.states import LinkStatus
from angee.integrate.streams import StreamPage
from angee.scheduling.zones import get_zone_info, get_zone_names

_FEED_CAP = 16 * 1024 * 1024
"""The largest decoded feed body read; a larger feed fails its poll with an operator message."""

_CONFERENCE_PROPERTIES = (
    "conference",
    "x-google-conference",
    "x-microsoft-onlinemeetingexternallink",
    "x-microsoft-skypeteamsmeetingurl",
    "url",
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
    """Return the HTTP(S) address of a feed, reading ``webcal://`` and ``webcals://`` as ``https://``."""

    text = (url or "").strip()
    scheme, _, rest = text.partition("://")
    if scheme.lower() in {"webcal", "webcals"}:
        text = f"https://{rest}"
    try:
        parse_http_url(text)
    except ValidationError as error:
        raise IcsError("Enter an http, https or webcal calendar feed address.") from error
    return text


class IcsCalendarBackend(CalendarBackend):
    """Polls one public ICS feed into its source's read-only calendar.

    ``config["url"]`` is the feed address. The calendar takes no local edits: a feed
    never takes a write (``writable`` stays false).
    """

    key = "ics"
    label = "ICS feed"
    icon = "calendar"
    defaults = {"vendor": "ics", "poll_interval": 3600}
    feed_cap: ClassVar[int] = _FEED_CAP

    def probe(self) -> ParsedCalendar:
        """Fetch and parse the feed once, returning its name and zone, so a dead feed is never subscribed."""

        feed = _read(self._fetch({}).content)
        return ParsedCalendar(name=_text(feed, "x-wr-calname"), timezone=_feed_zone(feed))

    def discover(self) -> list[ParsedCalendar]:
        """Return the feed's one calendar; its facts were read when the source subscribed."""

        return [ParsedCalendar(name=self.bridge.display_name)]

    def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Fetch a changed feed once and page its differences; the validators commit with the last page.

        A fresh cursor (a baseline) applies every event; a later poll applies the
        events whose payload changed and tombstones the keys the feed dropped.
        """

        cursor = dict(stream.cursor or {})
        if "pending" in cursor:
            pending, validators = cursor["pending"], cursor["validators"]
        else:
            response = self._fetch(cursor)
            if response.status_code == 304:
                return StreamPage((), cursor)
            validators = {
                "etag": response.headers.get("etag", ""),
                "last_modified": response.headers.get("last-modified", ""),
            }
            payloads = self._observe_feed(_read(response.content), zone=self.calendar().timezone)
            pending = self._pending(stream, payloads, baseline="etag" not in cursor)
        bound = max(1, page_bound)
        batch, remaining = pending[:bound], pending[bound:]
        records = self.record_changes(stream, {key: payload for key, payload in batch})
        next_cursor = {"validators": validators, "pending": remaining} if remaining else validators
        return StreamPage(records, next_cursor, exhausted=not remaining)

    # --- transport ---

    def _fetch(self, validators: Mapping[str, Any]) -> Any:
        """GET the feed from a public address, conditionally on ``validators``; a 304 returns as is."""

        headers = {"Accept": "text/calendar, */*;q=0.1"}
        if validators.get("etag"):
            headers["If-None-Match"] = str(validators["etag"])
        if validators.get("last_modified"):
            headers["If-Modified-Since"] = str(validators["last_modified"])
        try:
            response = self.http.request(
                "GET",
                feed_url(str((self.bridge.config or {}).get("url", ""))),
                headers=headers,
                allow_private=False,
                follow_redirects=True,
                max_bytes=self.feed_cap,
            )
        except ResponseTooLargeError as error:
            raise IcsError("The calendar feed exceeds the size limit.") from error
        except AddressNotAllowedError as error:
            raise IcsError("The calendar feed address is not allowed.") from error
        except AddressResolutionError as error:
            raise IcsError("The calendar feed host could not be found.") from error
        if response.status_code == 304 and headers.keys() - {"Accept"}:
            return response
        if response.status_code == 429 or response.status_code >= 500:
            raise IcsError(
                f"The calendar feed returned HTTP {response.status_code}.",
                transient=True,
                retry_after=_retry_after(response),
            )
        if not response.is_success:
            raise IcsError(f"The calendar feed returned HTTP {response.status_code}.")
        return response

    # --- snapshot ---

    def _observe_feed(self, feed: Any, *, zone: str) -> dict[str, dict[str, Any]]:
        """Return each event's retained payload by key; an unmappable event is a quarantine payload.

        A ``RECURRENCE-ID`` instance overrides its series when the feed carries the
        series' master, and replaces a cancelled ``EXDATE`` instance at the same
        moment; an instance whose master the feed lacks is a single event. Of two
        copies of one key, the higher ``SEQUENCE`` wins.
        """

        components = list(feed.contents.get("vevent", []))
        masters = {_text(component, "uid") for component in components if "recurrence-id" not in component.contents}
        exceptions: dict[str, dict[str, Any]] = {}
        events: dict[str, tuple[int, dict[str, Any]]] = {}
        for component in components:
            uid = _text(component, "uid")
            if not uid:
                digest = hashlib.sha256(component.serialize().encode()).hexdigest()
                events[f"missing-uid:{digest}"] = (0, {"error": "missing_uid"})
                continue
            sequence = _sequence(component)
            try:
                observed = _events(component, uid=uid, zone=zone, has_master=uid in masters)
            except Exception:  # noqa: BLE001 — the driver quarantines semantic input per record.
                events.setdefault(uid, (sequence, {"error": "invalid_event"}))
                continue
            (key, event), *cancelled = observed
            if key not in events or events[key][0] <= sequence:
                events[key] = (sequence, self.observe(event))
            for instance, exception in cancelled:
                exceptions[instance] = self.observe(exception)
        return {**exceptions, **{key: payload for key, (_sequence_, payload) in events.items()}}

    def _pending(
        self, stream: Any, payloads: Mapping[str, dict[str, Any]], *, baseline: bool
    ) -> list[list[Any]]:
        """Return ``[key, payload]`` for each change against the stream's links, ``[key, None]`` for a removal.

        Keys sort so a series' master precedes its instances.
        """

        links = {
            key: (source_hash, status)
            for key, source_hash, status in stream.links.values_list("external_key", "source_hash", "status")
        }
        pending: list[list[Any]] = []
        for key in sorted(payloads):
            known = links.get(key)
            if (
                baseline
                or known is None
                or known[1] == LinkStatus.TOMBSTONE
                or known[0] != canonical_json_sha256(payloads[key])
            ):
                pending.append([key, payloads[key]])
        pending.extend(
            [key, None] for key, (_hash, status) in sorted(links.items())
            if key not in payloads and status != LinkStatus.TOMBSTONE
        )
        return pending


def _read(content: bytes) -> Any:
    """Parse one iCalendar body, refusing anything that is not a ``VCALENDAR``."""

    try:
        feed = vobject.readOne(content.decode("utf-8-sig", errors="replace"), ignoreUnreadable=True)
    except Exception as error:  # noqa: BLE001 — any parser refusal is the same operator message.
        raise IcsError("The calendar feed is not valid iCalendar.") from error
    if getattr(feed, "name", "") != "VCALENDAR":
        raise IcsError("The calendar feed is not valid iCalendar.")
    return feed


def _events(component: Any, *, uid: str, zone: str, has_master: bool) -> list[tuple[str, ParsedEvent]]:
    """Map one VEVENT to its keyed event, then a master's cancelled ``EXDATE`` instances."""

    start_line = component.contents["dtstart"][0]
    all_day = not isinstance(start_line.value, datetime)
    start = _instant(start_line.value, _tzid(start_line), zone)
    end = _end(component, start_line.value, start, all_day=all_day, zone=zone)
    rid = component.contents.get("recurrence-id", [None])[0]
    original_start = _instant(rid.value, _tzid(rid), zone) if rid is not None else None
    override = original_start is not None and has_master
    event = ParsedEvent(
        title=_text(component, "summary"),
        description=_text(component, "description"),
        location=_text(component, "location"),
        start=start,
        end=end,
        all_day=all_day,
        recurrence="" if rid is not None else _text(component, "rrule").removeprefix("RRULE:"),
        series=uid if override else "",
        original_start=original_start if override else None,
        status=_STATUSES.get(_text(component, "status").upper(), EventStatus.CONFIRMED),
        transparency=(
            EventTransparency.FREE if _text(component, "transp").upper() == "TRANSPARENT" else EventTransparency.BUSY
        ),
        visibility=_VISIBILITIES.get(_text(component, "class").upper(), EventVisibility.DEFAULT),
        conference_url=next(
            (
                value
                for name in _CONFERENCE_PROPERTIES
                if (value := _text(component, name)).lower().startswith(("https://", "http://"))
            ),
            "",
        ),
        attendees=_attendees(component),
    )
    observed = [(instance_key(uid, original_start) if original_start is not None else uid, event)]
    if rid is None and event.recurrence:
        for line in component.contents.get("exdate", []):
            for value in line.value if isinstance(line.value, list) else [line.value]:
                moment = _instant(value, _tzid(line), zone)
                observed.append(
                    (
                        instance_key(uid, moment),
                        replace(
                            event,
                            start=moment,
                            end=moment + (end - start),
                            recurrence="",
                            series=uid,
                            original_start=moment,
                            status=EventStatus.CANCELLED,
                            attendees=(),
                        ),
                    )
                )
    return observed


def _instant(value: date | datetime, tzid: str, zone: str) -> datetime:
    """Return one iCalendar moment as aware UTC.

    A date is local midnight in ``zone``; an IANA ``TZID`` resolves through the
    scheduling zones owner; another zone through the feed's ``VTIMEZONE`` (vobject
    attached it); a floating time is read in ``zone``.
    """

    if not isinstance(value, datetime):
        return timezone.make_aware(datetime.combine(value, time.min), get_zone_info(zone)).astimezone(UTC)
    if tzid in get_zone_names():
        return timezone.make_aware(value.replace(tzinfo=None), get_zone_info(tzid)).astimezone(UTC)
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return timezone.make_aware(value, get_zone_info(zone)).astimezone(UTC)


def _end(component: Any, start_value: date | datetime, start: datetime, *, all_day: bool, zone: str) -> datetime:
    """Return the event's end from ``DTEND``, else ``DURATION``, else one day (all-day) or its start."""

    if "dtend" in component.contents:
        line = component.contents["dtend"][0]
        return _instant(line.value, _tzid(line), zone)
    if "duration" in component.contents:
        duration = component.contents["duration"][0].value
        if all_day:
            return _instant(start_value + timedelta(days=duration.days), "", zone)
        return start + duration
    if all_day:
        return _instant(start_value + timedelta(days=1), "", zone)
    return start


def _attendees(component: Any) -> tuple[ParsedAttendee, ...]:
    """Return the ``mailto:`` attendees, the organizer first as the ``organizer`` attendee."""

    listed: dict[str, ParsedAttendee] = {}
    for line in component.contents.get("attendee", []):
        address = _mailto(line.value)
        if address:
            listed.setdefault(
                address.lower(),
                ParsedAttendee(
                    address=address,
                    role=_ROLES.get(_param(line, "ROLE").upper(), AttendeeRole.REQUIRED),
                    response=_RESPONSES.get(_param(line, "PARTSTAT").upper(), AttendeeResponse.NEEDS_ACTION),
                    display_name=_param(line, "CN"),
                ),
            )
    organizer = component.contents.get("organizer", [None])[0]
    address = _mailto(organizer.value) if organizer is not None else ""
    if not address:
        return tuple(listed.values())
    listed_as = listed.pop(address.lower(), None)
    return (
        ParsedAttendee(
            address=address,
            role=ORGANIZER,
            response=listed_as.response if listed_as is not None else AttendeeResponse.ACCEPTED,
            display_name=_param(organizer, "CN") or (listed_as.display_name if listed_as is not None else ""),
        ),
        *listed.values(),
    )


def _feed_zone(feed: Any) -> str:
    """Return the feed's IANA zone: ``X-WR-TIMEZONE``, a recurring master's ``TZID``, or a ``VTIMEZONE``'s."""

    candidates = [_text(feed, "x-wr-timezone")]
    candidates += [
        _tzid(component.contents["dtstart"][0])
        for component in feed.contents.get("vevent", [])
        if "rrule" in component.contents and "dtstart" in component.contents
    ]
    candidates += [_text(component, "tzid") for component in feed.contents.get("vtimezone", [])]
    names = get_zone_names()
    return next((name for name in candidates if name in names), "")


def _text(component: Any, name: str) -> str:
    """Return a single-valued property's text, or ``""``."""

    lines = component.contents.get(name)
    return str(lines[0].value or "").strip() if lines else ""


def _param(line: Any, name: str) -> str:
    """Return a property parameter's first value, or ``""``."""

    values = line.params.get(name) or [""]
    return str(values[0]).strip()


def _tzid(line: Any) -> str:
    """Return the ``TZID`` a moment was written in (vobject keeps it once it resolves the zone)."""

    return _param(line, "X-VOBJ-ORIGINAL-TZID") or _param(line, "TZID")


def _mailto(value: Any) -> str:
    """Return the address of a ``mailto:`` calendar user, or ``""`` for another kind of address."""

    text = str(value or "").strip()
    if text.lower().startswith("mailto:"):
        return text[len("mailto:") :].strip()
    return text if "@" in text and ":" not in text else ""


def _sequence(component: Any) -> int:
    """Return the event's ``SEQUENCE``, or 0."""

    text = _text(component, "sequence")
    return int(text) if text.isdecimal() else 0


def _retry_after(response: Any) -> timedelta | None:
    """Return a bounded ``Retry-After`` horizon in seconds, when the feed gives one."""

    hint = response.headers.get("retry-after", "")
    return timedelta(seconds=int(hint)) if hint.isdecimal() and 0 < int(hint) <= 86400 else None


def feed_host(url: str) -> str:
    """Return the host a feed is served from, the fallback name of an unnamed subscription."""

    return urlsplit(url).hostname or ""
