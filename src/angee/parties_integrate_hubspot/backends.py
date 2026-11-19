"""HubSpot records into the parties directory: the sync cursor and the neutral contact.

A HubSpot pass is a baseline or a delta. The stream owns which
(``SyncStream.phase``, a ``StreamPhase``) and passes it in; :class:`HubSpotCursor`
carries only the position inside a pass and the modified-time watermark, and its
two pure methods decide the next request:

- a baseline walks the list endpoint by record id until it is exhausted, having
  recorded its start time first;
- a delta searches from the watermark minus :data:`OVERLAP`, ascending on the
  modified property, because HubSpot's search index lags its writes;
- a delta query that reaches the search ceiling restarts from the last modified
  time it saw;
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
    decode_options,
    decode_string,
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
    open delta query's lower bound; ``after`` is the position inside the open
    query: the record id a baseline walk continues from, or the result offset of
    a delta search. ``watermark`` is the highest modified time read so far, never
    past the start of the pass that read it. The driver persists the cursor with
    the records it covers.
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
        self,
        phase: str,
        page: HubSpotPage,
        *,
        modified_property: str,
        limit: int,
        ceiling: int = SEARCH_RESULT_LIMIT,
    ) -> HubSpotCursor:
        """Return the cursor after ``page``, the answer to this cursor's request; closed once the pass is done.

        ``limit`` is the page size the request asked for and ``ceiling`` the most
        results one search pages through. A delta query whose following page would
        pass the ceiling restarts from the last modified time it saw. When that time
        is no later than the query's own lower bound, every remaining result shares
        one modified time and only a list walk can read them: :class:`CursorInvalid`
        asks the driver for a new baseline, seeded with the watermark.
        """

        if self.started is None:
            raise ValueError("Begin a HubSpot pass before advancing it.")
        seen = [moment for record in page.results if (moment := record.modified_at(modified_property)) is not None]
        watermark = self.watermark
        if seen:
            reached = min(max(seen), self.started)
            watermark = reached if watermark is None else max(watermark, reached)
        following = page.next_after
        if not following:
            return HubSpotCursor(watermark=watermark)
        if phase == StreamPhase.DELTA and int(following) + limit > ceiling:
            restart = max(seen, default=None)
            if restart is None or self.since is None or restart <= self.since:
                raise CursorInvalid(cursor=HubSpotCursor(watermark=watermark).model_dump(mode="json"))
            return self.model_copy(update={"since": restart, "after": "", "watermark": watermark})
        return self.model_copy(update={"after": following, "watermark": watermark})


def contact_from_properties(properties: Mapping[str, str | None]) -> ParsedContact:
    """Map one HubSpot contact's standard properties onto the parties neutral contact.

    The display name is the given and family names joined, else the primary
    email. ``email`` is the preferred email and each ``hs_additional_emails`` entry
    a further one. ``phone`` is unlabelled and ``mobilephone`` carries ``cell``,
    the vCard label the CardDAV directory uses; neither is preferred, because
    HubSpot names no primary phone. The postal fields form one address when any is
    present. The record's identity and version belong to its link, so none of the
    contact's transport fields are set.
    """

    value = {name: decode_string(properties.get(name)) for name in CONTACT_PROPERTIES}
    given, family, email = value["firstname"], value["lastname"], value["email"]
    emails = [(email, "", True)] if email else []
    emails += [
        (extra, "", False)
        for extra in decode_options(properties.get("hs_additional_emails"))
        if extra.casefold() != email.casefold()
    ]
    phones = [
        (number, label, False) for number, label in ((value["phone"], ""), (value["mobilephone"], "cell")) if number
    ]
    address = ParsedAddress(
        street=value["address"],
        city=value["city"],
        region=value["state"],
        postal_code=value["zip"],
        country=value["country"],
    )
    return ParsedContact(
        display_name=" ".join(part for part in (given, family) if part) or email,
        given_name=given,
        family_name=family,
        title=value["jobtitle"],
        organization=value["company"],
        emails=tuple(emails),
        phones=tuple(phones),
        addresses=(address,) if address != ParsedAddress() else (),
    )
