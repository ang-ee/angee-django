"""Read-only ICS feed subscriptions: one calendar per feed URL, polled as full snapshots.

The feed is the source's one remote calendar: the base backend's ``calendars``
stream publishes one record for it, keyed ``feed``, and its events sync through
the ``events`` stream partitioned by that key. The feed address is a secret (a
private feed's token rides in it): it is the source credential's material, read
only here, and the source's ``config`` keeps only its host (:func:`feed_label`).

Every poll fetches the feed through integrate's SSRF-pinned bounded download
(public addresses only; 16 MiB, five redirects and a minute in total), with the
stream cursor's ETag and Last-Modified as a conditional GET: a 304 means no
work. ``webcal://`` is fetched as ``https://``. python-icalendar parses the
iCalendar; the calendar's base backend owns the event projection and every
write. A changed feed is paged in key order, and the driver applies only the
events whose projection changed. A key the feed stopped serving is found by
integrate's absence sweep, once per poll interval: unavailable first, then
tombstoned. An adapter keeps the snapshot it fetched, with its validators, for
its pages, identity reads and sweep (:meth:`IcsCalendarBackend._events_now`).
The external key is the UID (its digest when too long), plus the
``RECURRENCE-ID`` instant for an instance. A feed produces at most 10,000
events, cancelled instances included, and defines at most 100 time zones; both
are counted before it is parsed (:attr:`IcsCalendarBackend.events_per_feed`,
:attr:`IcsCalendarBackend.timezones_per_feed`). A rule is validated by the
event's own field before any ``EXDATE`` is judged (``cancelled_instances``); a
master names at most 10,000 ``EXDATE``s (``excluded_dates``), each judged on its
own window.

**Zones are resolved per parse.** icalendar's parser caches each feed-defined
``VTIMEZONE`` in a process-wide registry, so a resolved value can carry another
feed's definition of the same TZID. This module never reads one: it takes each
moment's wall clock and ``TZID`` and resolves the TZID itself (:class:`_Zones`).
An IANA name, a Windows name or a ``/``-prefixed vendor IANA name maps to the
IANA zone (through icalendar's Windows table); any other TZID falls back to the
feed's ``X-WR-TIMEZONE``, then to this feed's own ``VTIMEZONE``. Parsing still
fills icalendar's process-wide cache, which only grows: a sync worker recycled
after a number of tasks (Celery's ``worker_max_tasks_per_child``) bounds it.

Mapped properties:

- ``UID``, ``RECURRENCE-ID``, ``SUMMARY``, ``DESCRIPTION``, ``LOCATION``;
- ``DTSTART``, ``DTEND`` or ``DURATION`` (``VALUE=DATE`` is all-day); the event
  keeps its ``DTSTART`` zone as its own ``timezone`` when that maps to an IANA name
  (a UTC time keeps ``UTC``); a custom zone, a date or a floating time keeps the
  feed's ``X-WR-TIMEZONE``, else its calendar's zone;
- the first ``RRULE`` (``series_rule``); each ``EXDATE`` that is an instance becomes
  a cancelled one (``RDATE`` is not mapped);
- ``STATUS``, ``TRANSP`` (``TRANSPARENT`` is free), ``CLASS`` (``PUBLIC`` is
  public, ``PRIVATE`` and ``CONFIDENTIAL`` private, none the calendar's default);
  an instance that states no ``TRANSP`` or ``CLASS`` takes its master's;
- ``ORGANIZER`` and ``ATTENDEE`` ``mailto:`` addresses with ``CN``, ``ROLE`` and
  ``PARTSTAT``, the organizer first (``organizer_first``);
- the conference URL: ``CONFERENCE`` (RFC 7986), ``X-GOOGLE-CONFERENCE``,
  ``X-MICROSOFT-ONLINEMEETINGEXTERNALLINK``, ``X-MICROSOFT-SKYPETEAMSMEETINGURL``,
  then ``URL``, the first web address.

NUL characters are stripped from the whole body before parsing, so no text value
or parameter carries one.
"""

from __future__ import annotations

import hashlib
import ipaddress
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from itertools import islice, takewhile
from time import monotonic
from typing import Any, ClassVar
from urllib.parse import urlsplit

import icalendar
from dateutil.rrule import rrulestr
from django.core.exceptions import ValidationError
from django.utils import timezone
from icalendar.parser import Contentlines
from icalendar.timezone.windows_to_olson import WINDOWS_TO_OLSON

from angee.calendar.backends import (
    CalendarBackend,
    ParsedAttendee,
    ParsedCalendar,
    ParsedEvent,
    cancelled_instances,
    excluded_dates,
    instance_key,
    organizer_first,
    series_key,
    series_rule,
)
from angee.calendar.choices import AttendeeResponse, AttendeeRole, EventStatus, EventTransparency, EventVisibility
from angee.integrate.credentials import CredentialKind
from angee.integrate.errors import IntegrationError
from angee.integrate.http import DownloadResult, HttpRefusal, OutboundBudget
from angee.integrate.net import AddressNotAllowedError, AddressResolutionError, is_unsafe_address, parse_http_url
from angee.integrate.states import StreamPhase
from angee.integrate.streams import StreamPage
from angee.integrate.sync import sync_deadline
from angee.scheduling.recurrence import Recurrence
from angee.scheduling.zones import get_zone_info, get_zone_names

FEED_KEY = "feed"
"""The key of an ICS source's one remote calendar: its feed (the calendars record and events partition)."""

_FEED_BUDGET = OutboundBudget(requests=6, redirects=5, bytes=16 * 1024 * 1024, deadline_seconds=60.0)
"""One feed fetch's bounds: a 16 MiB decoded body, five redirects, and a minute in total."""

_REPORTED_STATUSES = frozenset({304, *range(400, 600)})
"""The statuses a fetch reports itself: a conditional GET's 304, and a refusal named to the operator."""

_TRANSITIONS_PER_ZONE = 5_000
"""The most transitions one feed-defined VTIMEZONE may unroll (``Timezone.to_tz``), every observance counted."""

_ZONE_RULE_END = datetime(2038, 12, 31, tzinfo=UTC)
"""Where icalendar ends a zone rule that states no ``UNTIL`` or ``COUNT`` (its zoneinfo provider's default)."""

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
    """Return what may be shown of a feed address: its host alone.

    Any path segment, file name or query can carry the feed's secret.
    """

    return urlsplit(url).hostname or ""


class IcsCalendarBackend(CalendarBackend):
    """Polls one public ICS feed into its source's read-only calendar.

    The feed is the source's one remote calendar, keyed :data:`FEED_KEY`; its address
    is the source credential's secret. The calendar takes no local edits: a feed is
    only pulled (``direction`` stays ``PULL``).
    """

    key = "ics"
    label = "ICS feed"
    icon = "calendar"
    defaults = {"vendor": "ics", "poll_interval": 3600}

    events_per_feed: ClassVar[int] = 10_000
    """The most events a feed may produce, cancelled instances included; a larger feed fails its poll."""

    timezones_per_feed: ClassVar[int] = 100
    """The most VTIMEZONEs a feed may define; a larger feed fails its poll before it is parsed."""

    parse_seconds: ClassVar[float] = 60.0
    """How long one parse may run outside a bridge sync, which holds no deadline (a direct identity read)."""

    _snapshot: _Snapshot | None = None

    def credential_blocker(self, credential: Any) -> str | None:
        """Refuse anything but a static token: the feed address a subscription keeps as its secret.

        The backend reads the feed address from that token alone, so any other kind
        (an OAuth grant, a key pair) would send another credential's material to a
        feed server.
        """

        if credential.kind == CredentialKind.STATIC_TOKEN:
            return None
        return "An ICS feed subscription keeps its feed address as a static token."

    def discover(self) -> list[ParsedCalendar]:
        """Return the feed, the source's one remote calendar, named as subscribed."""

        return [ParsedCalendar(key=FEED_KEY, name=self.bridge.display_name)]

    def extract_events(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Page the feed's events in key order; the feed's validators commit with the last page.

        The parse reads the sync's deadline where every other parse does
        (:meth:`_read_snapshot`), so ``deadline``, the same instant, is not read twice.

        Without a snapshot in hand, a delta poll asks conditionally on the committed
        validators and a 304 is no work; a baseline (a new or resynced stream)
        fetches unconditionally. A snapshot this adapter already holds (an identity
        read fetched it) pages without another fetch, and is no work when its
        validators are the ones the cursor committed. While pages remain the cursor
        carries only the last key. A key the feed stopped serving is integrate's
        absence sweep's to find (:meth:`event_keys`).
        """

        del deadline
        cursor = dict(stream.cursor or {})
        if "after" in cursor:
            snapshot = self._events_now(stream)
            return self.page(stream, snapshot.payloads, page_bound, after=cursor["after"], cursor=cursor["next"])
        baseline = stream.phase == StreamPhase.BASELINE
        held = self._snapshot
        if held is None:
            result = self._fetch({} if baseline else cursor)
            if result.status_code == 304:
                return StreamPage((), cursor)
            held = self._snapshot = self._read_snapshot(stream, result)
        elif not baseline and any(held.validators.values()) and held.validators == _validators(cursor):
            return StreamPage((), cursor)
        return self.page(stream, held.payloads, page_bound, after=None, cursor=held.validators)

    def read_events(self, stream: Any, keys: Sequence[str]) -> dict[str, dict[str, Any] | None]:
        """Read the requested keys from the feed's snapshot; a key it no longer carries is ``None``."""

        payloads = self._events_now(stream).payloads
        return {key: payloads.get(key) for key in keys}

    def event_keys(self, stream: Any) -> list[str]:
        """Return every key the feed's snapshot carries."""

        return list(self._events_now(stream).payloads)

    def _events_now(self, stream: Any) -> _Snapshot:
        """Return the snapshot this adapter holds, fetching the feed unconditionally when it holds none.

        One adapter serves one sync of the feed's partition. While it holds a
        snapshot, its identity reads, later pages and absence sweep read that one; a
        sync whose extraction got a 304 fetches the feed once more only when its
        sweep or an identity read is due. A later sync's adapter starts without one.
        """

        if self._snapshot is None:
            self._snapshot = self._read_snapshot(stream, self._fetch({}))
        return self._snapshot

    def _read_snapshot(self, stream: Any, result: DownloadResult) -> _Snapshot:
        """Parse a fetched feed into its snapshot, kept with the validators it was served with.

        Every parse runs against a deadline: the bridge sync's (integrate's
        ``sync_deadline()``), whichever call parses (an extraction, an identity read or
        the sweep), else :attr:`parse_seconds` from now outside a sync. A parse that
        reaches it stops, and the next sync reads the feed again.
        """

        deadline = sync_deadline()
        if deadline is None:
            deadline = monotonic() + self.parse_seconds
        feed = _read(result.content, events=self.events_per_feed, timezones=self.timezones_per_feed)
        payloads = self._observe_feed(feed, zone=self.get_calendar(stream).timezone, deadline=deadline)
        return _Snapshot(
            {"etag": result.headers.get("etag", ""), "last_modified": result.headers.get("last-modified", "")},
            # A master the feed serves unmappable keeps its linked instances, set aside with it.
            self.quarantine_series_instances(stream, payloads),
        )

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

        A 304 to a conditional request returns as is; any other status but a
        success is the feed's refusal, classified by integrate's ``HttpRefusal``.
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
        if (result.status_code == 304 and len(headers) > 1) or 200 <= result.status_code < 300:
            return result
        raise HttpRefusal.of(result).as_error(IcsError, "The calendar feed")

    # --- snapshot ---

    def _observe_feed(self, feed: Any, *, zone: str, deadline: float | None = None) -> dict[str, dict[str, Any]]:
        """Return each event's retained payload by key; an unmappable event is a quarantine payload under its own key.

        A ``RECURRENCE-ID`` instance overrides its series when the feed carries the
        series' master, and replaces a cancelled ``EXDATE`` instance at the same
        moment; an instance whose master the feed lacks is a single event. Of two
        copies of one key, the higher ``SEQUENCE`` wins. Every produced record,
        cancelled instances included, counts against :attr:`events_per_feed`.
        ``zone`` is the calendar's; a parse that reaches the sync's ``deadline`` stops
        as a transient refusal, never one event's quarantine.
        """

        components = list(feed.walk("VEVENT"))
        zones = _Zones(feed, deadline=deadline)
        feed_zone = zones.feed_zone
        local = feed_zone or zone
        masters = {_text(component, "UID"): component for component in components if "RECURRENCE-ID" not in component}
        exceptions: dict[str, dict[str, Any]] = {}
        events: dict[str, tuple[int, dict[str, Any]]] = {}
        for component in components:
            _check_deadline(deadline)
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
                    deadline=deadline,
                )
            except IcsError:
                raise
            except Exception:  # noqa: BLE001 — the driver quarantines semantic input per record.
                events.setdefault(_own_key(component, uid, zones, local), (sequence, {"error": "invalid_event"}))
                continue
            (key, event), *cancelled = observed
            if key not in events or events[key][0] <= sequence:
                events[key] = (sequence, self.observe(event))
            for instance, exception in cancelled:
                exceptions[instance] = self.observe(exception)
            if len(events) + len(exceptions) > self.events_per_feed:
                raise IcsError(f"The calendar feed has more than {self.events_per_feed} events.")
        # A deadline an unmappable event's own key swallowed (_own_key) still stops the parse.
        _check_deadline(deadline)
        return {**exceptions, **{key: payload for key, (_sequence_, payload) in events.items()}}


@dataclass(frozen=True)
class _Snapshot:
    """One fetched feed: the validators it was served with, and each event's retained payload by key."""

    validators: dict[str, str]
    payloads: dict[str, dict[str, Any]]


def _validators(cursor: Mapping[str, Any]) -> dict[str, str]:
    """Return the feed validators a stream cursor committed."""

    return {"etag": str(cursor.get("etag", "")), "last_modified": str(cursor.get("last_modified", ""))}


class _Zones:
    """One feed's TZIDs, resolved for this parse only and never through a process-wide registry.

    An IANA name, a Windows name (icalendar's table) or an RFC 5545 vendor-prefixed
    IANA name (``/vendor/Europe/Berlin``) resolves to its IANA zone. Any other
    TZID falls back to the feed's ``X-WR-TIMEZONE`` (``feed_zone``), and without
    one to this feed's own ``VTIMEZONE`` (``Timezone.to_tz`` without lookup), which
    has no IANA name. A definition built in unbounded memory (a transition rule
    finer than yearly, a rule that never matches, or more than
    :data:`_TRANSITIONS_PER_ZONE` transitions) is never built: its moments read in
    the calendar's zone. Counting runs against the parse's ``deadline``.
    """

    def __init__(self, feed: Any, *, deadline: float | None = None) -> None:
        self._components = {str(component.get("TZID", "")).strip(): component for component in feed.walk("VTIMEZONE")}
        self.feed_zone = _iana_name(_text(feed, "X-WR-TIMEZONE"))
        """The feed's ``X-WR-TIMEZONE`` as an IANA name, or blank."""
        self._deadline = deadline
        self._resolved: dict[str, tuple[str, tzinfo | None]] = {}

    def resolve(self, tzid: str) -> tuple[str, tzinfo | None]:
        """Return a TZID's IANA name (blank for a feed-defined zone) and its zone (``None`` when unknown)."""

        if tzid not in self._resolved:
            name = _iana_name(tzid) or self.feed_zone
            zone: tzinfo | None = get_zone_info(name) if name else None
            component = self._components.get(tzid.strip())
            if zone is None and component is not None and _bounded_definition(component, self._deadline):
                _check_deadline(self._deadline)
                try:
                    zone = component.to_tz(lookup_tzid=False)
                except Exception:  # noqa: BLE001 — an unusable definition leaves the zone unknown.
                    zone = None
            self._resolved[tzid] = (name, zone)
        return self._resolved[tzid]


def _check_deadline(deadline: float | None) -> None:
    """Stop a parse that reached the sync's deadline, as a transient refusal the next sync retries."""

    if deadline is not None and monotonic() > deadline:
        raise IcsError("The calendar feed took too long to read; the next sync reads it again.", transient=True)


def _bounded_definition(component: Any, deadline: float | None = None) -> bool:
    """Whether a feed-defined VTIMEZONE builds in bounded memory: yearly rules, and few transitions.

    ``Timezone.to_tz`` unrolls every observance's rule and ``RDATE`` into
    transitions, so a secondly rule would exhaust memory before it returned; this
    counts them first (:func:`_transitions`). A definition that passes holds at
    most :data:`_TRANSITIONS_PER_ZONE` transitions, which bounds ``to_tz``'s own
    walk of the same rules: the second walk is bounded by the first's count.
    """

    return _transitions(component, _TRANSITIONS_PER_ZONE, deadline) <= _TRANSITIONS_PER_ZONE


def _transitions(component: Any, cap: int, deadline: float | None = None) -> int:
    """Count the transitions ``Timezone.to_tz`` would unroll for ``component``, stopping just past ``cap``.

    Every observance counts at least once, nested ones included (``walk()``, the
    VTIMEZONE itself excluded), so a flood of observances reaches the cap: its
    ``DTSTART``, each ``RDATE``, and each instance of each ``RRULE`` up to
    icalendar's own end (the rule's ``UNTIL`` or ``COUNT``, else
    :data:`_ZONE_RULE_END`). A yearly rule from year 1 counts as the 2,038 it is.
    A rule finer than yearly, one with no occurrence within about 30 years of its
    observance's ``DTSTART`` (the recurrence owner's ``never_occurs``), or one that
    does not parse counts past the cap at once. Each rule is counted only up to
    what the cap has left, so many ``RRULE`` lines on one observance stop at the cap
    too, and each first checks the parse's ``deadline``.
    """

    total = 0
    for observance in component.walk():
        if observance is component:
            continue
        _check_deadline(deadline)
        unrolled = 0
        for rule in _values(observance, "RRULE"):
            _check_deadline(deadline)
            unrolled += _rule_transitions(observance, rule, cap - total - unrolled + 1)
            if total + unrolled > cap:
                break
        total += max(1, unrolled) + sum(len(rdate.dts) for rdate in _values(observance, "RDATE"))
        if total > cap:
            break
    return total


def _rule_transitions(observance: Any, rule: Any, limit: int) -> int:
    """Count one observance rule's transitions, at most ``limit``; ``limit`` for a rule refused outright."""

    if [str(freq).upper() for freq in rule.get("FREQ", [])] != ["YEARLY"]:
        return limit
    try:
        text = rule.to_ical().decode()
        start = observance["DTSTART"].dt
        anchor = (datetime.combine(start, time.min) if not isinstance(start, datetime) else start).replace(tzinfo=UTC)
        # Probed from the observance's own start, so an ordinal BYDAY or BYSETPOS that never matches is refused too.
        if Recurrence(text).never_occurs(anchor, zone="UTC"):
            return limit
        compiled = rrulestr(text, dtstart=anchor)
        ends = {"UNTIL", "COUNT"} & {str(key).upper() for key in rule}
        moments = compiled if ends else takewhile(lambda moment: moment <= _ZONE_RULE_END, compiled)
        return sum(1 for _moment in islice(moments, limit))
    except Exception:  # noqa: BLE001 — a rule icalendar could not unroll either counts as refused.
        return limit


def _iana_name(tzid: str) -> str:
    """Return the IANA zone a TZID names, directly, through icalendar's Windows table, or behind a vendor prefix.

    Only a TZID that starts with ``/`` (RFC 5545's globally unique form) is read
    for a vendor prefix; any other unknown TZID names no IANA zone.
    """

    text = tzid.strip()
    names = get_zone_names()
    if text in names:
        return text
    if (windows := WINDOWS_TO_OLSON.get(text, "")) in names:
        return windows
    if text.startswith("/"):
        parts = text.strip("/").split("/")
        return next((name for start in range(len(parts)) if (name := "/".join(parts[start:])) in names), "")
    return ""


def _components(text: str) -> Counter[str]:
    """Count the components a body opens, by name, on icalendar's own line grammar.

    ``Contentlines`` unfolds and splits the body as the parser will, so a folded,
    blank-line-separated or parameterised ``BEGIN`` counts as the parser sees it.
    """

    counts: Counter[str] = Counter()
    for line in Contentlines.from_ical(text):
        if line[:5].upper() != "BEGIN":
            continue
        try:
            name, _params, value = line.parts()
        except ValueError:
            continue
        if name.upper() == "BEGIN":
            counts[str(value).strip().upper()] += 1
    return counts


def _read(content: bytes, *, events: int, timezones: int) -> Any:
    """Parse one iCalendar body without NUL characters, refusing anything that is not a ``VCALENDAR``.

    Its components are counted before it is parsed: a feed past ``events`` events
    or ``timezones`` time zones (the backend's caps) fails its poll unparsed.
    """

    text = content.decode("utf-8-sig", errors="replace").replace("\x00", "")
    counts = _components(text)
    if counts["VEVENT"] > events:
        raise IcsError(f"The calendar feed has more than {events} events.")
    if counts["VTIMEZONE"] > timezones:
        raise IcsError(f"The calendar feed defines more than {timezones} time zones.")
    try:
        # Bytes: icalendar reads a one-line str as a file path on this server.
        feed = icalendar.Calendar.from_ical(text.encode())
    except Exception as error:  # noqa: BLE001 — any parser refusal is the same operator message.
        raise IcsError("The calendar feed is not valid iCalendar.") from error
    if feed.name != "VCALENDAR":
        raise IcsError("The calendar feed is not valid iCalendar.")
    return feed


def _events(
    component: Any,
    *,
    uid: str,
    zones: _Zones,
    zone: str,
    feed_zone: str,
    calendar_zone: str,
    master: Any,
    deadline: float | None = None,
) -> list[tuple[str, ParsedEvent]]:
    """Map one VEVENT to its keyed event, then a master's cancelled ``EXDATE`` instances.

    ``master`` is the series' master VEVENT when the feed carries it. An instance
    that states no ``TRANSP`` or ``CLASS`` takes its master's. A master's rule and
    its ``EXDATE``s go through :func:`~angee.calendar.backends.excluded_dates`, which
    refuses a master naming more than 10,000 of them before any is mapped, and
    :func:`~angee.calendar.backends.cancelled_instances`, which validates the rule (an
    invalid one makes the master an invalid event) and keeps each ``EXDATE`` that is
    an instance, judged on its own window after the parse's ``deadline`` is checked.
    ``calendar_zone`` is the calendar's.
    """

    start_prop = component["DTSTART"]
    all_day = not isinstance(start_prop.dt, datetime)
    start = _instant(start_prop.dt, _param(start_prop, "TZID"), zones, zone)
    end = _end(component, start_prop.dt, start, all_day=all_day, zones=zones, zone=zone)
    rid = component.get("RECURRENCE-ID")
    recurrence = "" if rid is not None else series_rule(component)
    event_zone = _event_zone(start_prop, zones, feed_zone)
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
        recurrence=recurrence,
        timezone=event_zone,
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
    excluded = [_instant(value, tzid, zones, zone) for value, tzid in excluded_dates(component)] if recurrence else []
    observed.extend(
        cancelled_instances(
            event, uid, excluded, calendar_zone=calendar_zone, check_deadline=lambda: _check_deadline(deadline)
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
    except Exception:  # noqa: BLE001 — an unreadable instance keeps a bounded key of its own content.
        return "sha256:" + hashlib.sha256(uid.encode() + b"#invalid\n" + component.to_ical()).hexdigest()


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
    """Return the zone an event keeps: its DTSTART's IANA zone, ``UTC`` for a UTC time, else the feed's (or blank).

    A custom ``TZID`` keeps the feed's ``X-WR-TIMEZONE`` (:class:`_Zones`), else none.
    """

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
    """Return the ``mailto:`` attendees as listed, the organizer first (``organizer_first``)."""

    listed = [
        ParsedAttendee(
            address=address,
            role=_ROLES.get(_param(prop, "ROLE").upper(), AttendeeRole.REQUIRED),
            response=_RESPONSES.get(_param(prop, "PARTSTAT").upper(), AttendeeResponse.NEEDS_ACTION),
            display_name=_param(prop, "CN"),
        )
        for prop in _values(component, "ATTENDEE")
        if (address := _mailto(prop))
    ]
    organizer = component.get("ORGANIZER")
    if organizer is None:
        return organizer_first(listed, "")
    return organizer_first(listed, _mailto(organizer), name=_param(organizer, "CN"))


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
