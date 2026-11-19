"""HubSpot records into the parties directory: the sync cursor and the neutral contact.

A HubSpot pass is a baseline or a delta. The stream owns which
(``SyncStream.phase``, a ``StreamPhase``) and passes it in to open a pass;
:class:`HubSpotCursor` carries only the position inside a pass and the
modified-time watermark, and its two pure methods decide the next request:

- a baseline walks the list endpoint by record id until it is exhausted, having
  recorded its start time first;
- a delta searches for records modified strictly after the time it has read in
  full, oldest change first, starting from the watermark minus :data:`OVERLAP`
  because HubSpot's search index lags its writes. No request pages by offset:
  each asks for a query's first page, and a block of records sharing one
  modified time that fills a page is drained by record id. A record edited
  during the pass moves to a later time, where a later page finds it, without
  shifting an unread one, and no query meets HubSpot's result ceiling;
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
from angee.parties.backends import ParsedAddress, ParsedContact
from angee.parties_integrate_hubspot.client import EPOCH, HubSpotPage, parse_options, parse_string

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
    modified time an open delta has read in full, ``None`` in a baseline; the delta
    reads what was modified strictly after it. ``block`` is the modified time of
    the block a delta is draining by record id, ``None`` otherwise. ``after`` is
    the position inside the open query: the record id a baseline walk continues
    from, or the last record id a drain read. ``watermark`` is the highest modified
    time read so far, never past the start of the pass that read it. The driver
    persists the cursor with the records it covers.
    """

    model_config = ConfigDict(frozen=True)

    after: str = ""
    since: datetime | None = None
    block: datetime | None = None
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
        delta searches strictly after the watermark minus :data:`OVERLAP` (after
        the epoch before there is a watermark).
        """

        delta = phase == StreamPhase.DELTA
        if self.started is not None and (self.since is not None) == delta:
            return self
        since = None
        if delta:
            since = max(self.watermark - OVERLAP, EPOCH) if self.watermark is not None else EPOCH
        return HubSpotCursor(since=since, started=now, watermark=self.watermark)

    def advance(self, page: HubSpotPage, *, object_type: str) -> HubSpotCursor:
        """Return the cursor after ``page``, the answer to this cursor's request; closed once the pass is done.

        A delta search page that more results follow has read in full every time
        before its latest one, so the search goes on strictly after the latest of
        those; the records at the page's latest time are read again, and the
        driver takes a record delivered again unchanged as no change. A page of
        one time may end inside a block of that time. HubSpot does not state how
        it orders records of one time, so the block is drained from its lowest
        record id, each drain page above the last id read, and only the drain's
        last page moves the search past the block. A record whose modified time
        cannot be read moves nothing, so a search page with none repeats its request.
        """

        if self.started is None:
            raise ValueError("Begin a HubSpot pass before advancing it.")
        moments = sorted({moment for record in page.results if (moment := record.modified_at(object_type)) is not None})
        watermark = self.watermark
        if moments:
            reached = min(moments[-1], self.started)
            watermark = reached if watermark is None else max(watermark, reached)
        more = bool(page.next_after)
        if self.block is not None and more:
            return self.model_copy(update={"after": page.results[-1].id, "watermark": watermark})
        if self.block is not None:
            return self.model_copy(update={"since": self.block, "block": None, "after": "", "watermark": watermark})
        if not more:
            return HubSpotCursor(watermark=watermark)
        if self.since is None:
            return self.model_copy(update={"after": page.next_after, "watermark": watermark})
        if len(moments) > 1:
            return self.model_copy(update={"since": moments[-2], "watermark": watermark})
        return self.model_copy(update={"block": moments[0] if moments else None, "watermark": watermark})


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
