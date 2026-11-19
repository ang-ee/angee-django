"""HubSpot records into the parties directory: the sync cursor and the neutral contact.

A HubSpot pass is a baseline or a delta. The stream owns which
(``SyncStream.phase``, a ``StreamPhase``) and passes it in to open a pass;
:class:`HubSpotCursor` carries only the position inside a pass and the
modified-time watermark, and its two pure methods decide the next request:

- a baseline walks the list endpoint by record id until it is exhausted, having
  recorded its start time first;
- a delta searches from the watermark minus :data:`OVERLAP`, oldest change first,
  because HubSpot's search index lags its writes. After each page it searches
  again from the latest modified time that page read, so a record edited during
  the pass, which moves to the end of the results, cannot shift an unread one
  past the next page. Offsets page only inside a block of records sharing one
  modified time;
- the watermark advances to the highest modified time a pass has seen, never
  past that pass's own start.

:func:`contact_from_properties` maps HubSpot's standard contact properties onto
the parties neutral contact.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from angee.integrate.states import StreamPhase
from angee.integrate.streams import CursorInvalid
from angee.parties.backends import ParsedAddress, ParsedContact
from angee.parties_integrate_hubspot.client import (
    EPOCH,
    SEARCH_RESULT_LIMIT,
    HubSpotPage,
    parse_options,
    parse_string,
)

OVERLAP = timedelta(minutes=5)
"""How far a delta search reaches back behind the watermark, for writes HubSpot's search index had not caught."""

CONTACT_PROPERTIES = (
    "firstname",
    "lastname",
    "email",
    "hs_additional_emails",
    "phone",
    "mobilephone",
    "jobtitle",
    "company",
    "address",
    "city",
    "state",
    "zip",
    "country",
)
"""The standard HubSpot contact properties :func:`contact_from_properties` reads."""


class HubSpotCursor(BaseModel):
    """One HubSpot stream's position inside its open pass, and its watermark.

    ``started`` is the open pass's start, ``None`` between passes. ``since`` is the
    open delta query's lower bound, ``None`` in a baseline. ``after`` is the
    position inside the open query: the record id a baseline walk continues from,
    or the result offset inside a delta's block of one modified time.
    ``watermark`` is the highest modified time read so far, never past the start
    of the pass that read it. The driver persists the cursor with the records it
    covers.
    """

    model_config = ConfigDict(frozen=True)

    after: str = ""
    since: datetime | None = None
    started: datetime | None = None
    watermark: datetime | None = None

    @property
    def is_open(self) -> bool:
        """Whether a pass is in progress; once it is not, the stream page is exhausted."""

        return self.started is not None

    def begin(self, phase: str, *, now: datetime) -> HubSpotCursor:
        """Return the cursor with a pass of the stream's ``phase`` open.

        An open pass of that phase continues. Otherwise a new pass opens and
        records ``now`` as its start: a baseline walks from the first record id, a
        delta searches from the watermark minus :data:`OVERLAP` (from the epoch
        before there is a watermark).
        """

        delta = phase == StreamPhase.DELTA
        if self.started is not None and (self.since is not None) == delta:
            return self
        since = None
        if delta:
            since = max(self.watermark - OVERLAP, EPOCH) if self.watermark is not None else EPOCH
        return HubSpotCursor(since=since, started=now, watermark=self.watermark)

    def advance(
        self, page: HubSpotPage, *, object_type: str, limit: int, ceiling: int = SEARCH_RESULT_LIMIT
    ) -> HubSpotCursor:
        """Return the cursor after ``page``, the answer to this cursor's request; closed once the pass is done.

        ``limit`` is the page size the request asked for and ``ceiling`` the most
        results one search pages through. A delta page that read a modified time
        later than its query's lower bound searches again from that time; the
        records at that time are read again, and the driver takes a record
        delivered again unchanged as no change. A delta page whose records all sit
        at the lower bound pages on by offset;
        when that block would pass the ceiling, only a list walk can read it, and
        :class:`CursorInvalid` asks the driver for a new baseline, seeded with the
        watermark. A record whose modified time cannot be read moves nothing.
        """

        if self.started is None:
            raise ValueError("Begin a HubSpot pass before advancing it.")
        latest = max(
            (moment for record in page.results if (moment := record.modified_at(object_type)) is not None),
            default=None,
        )
        watermark = self.watermark
        if latest is not None:
            reached = min(latest, self.started)
            watermark = reached if watermark is None else max(watermark, reached)
        following = page.next_after
        if not following:
            return HubSpotCursor(watermark=watermark)
        if self.since is not None:
            if latest is not None and latest > self.since:
                return self.model_copy(update={"since": latest, "after": "", "watermark": watermark})
            if int(following) + limit > ceiling:
                raise CursorInvalid(cursor=HubSpotCursor(watermark=watermark).model_dump(mode="json"))
        return self.model_copy(update={"after": following, "watermark": watermark})


def contact_from_properties(properties: Mapping[str, str | None]) -> ParsedContact:
    """Map one HubSpot contact's standard properties onto the parties neutral contact.

    The display name is the given and family names joined, else the primary
    email. ``email`` is the preferred email and each ``hs_additional_emails`` entry
    a further one; only an exact repeat is dropped, because ingest's identity rules
    own address normalisation. ``phone`` is unlabelled and ``mobilephone`` carries
    ``cell``, the vCard label the CardDAV directory uses; neither is preferred,
    because HubSpot names no primary phone. The postal fields form one address when
    any is present. The record's identity and version belong to its link, so none
    of the contact's transport fields are set.
    """

    def text(name: str) -> str:
        return parse_string(properties.get(name))

    given, family, email = text("firstname"), text("lastname"), text("email")
    stated = (email, *parse_options(properties.get("hs_additional_emails")))
    emails = dict.fromkeys(entry for entry in stated if entry)
    numbers = ((text("phone"), ""), (text("mobilephone"), "cell"))
    address = ParsedAddress(
        street=text("address"),
        city=text("city"),
        region=text("state"),
        postal_code=text("zip"),
        country=text("country"),
    )
    return ParsedContact(
        display_name=" ".join(part for part in (given, family) if part) or email,
        given_name=given,
        family_name=family,
        title=text("jobtitle"),
        organization=text("company"),
        emails=tuple((entry, "", entry == email) for entry in emails),
        phones=tuple((number, label, False) for number, label in numbers if number),
        addresses=(address,) if address != ParsedAddress() else (),
    )
