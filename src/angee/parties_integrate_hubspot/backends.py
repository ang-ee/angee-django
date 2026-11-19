"""HubSpot contacts into the parties directory: the backend, its sync cursor and the neutral contact.

:class:`HubSpotDirectoryBackend` reads one HubSpot account's contacts one way,
into one address book, through the directory base: it turns HubSpot records into
the base's payloads, and the base builds and applies the records.

A HubSpot pass is a baseline or a delta. The stream owns which
(``SyncStream.phase``, a ``StreamPhase``) and passes it in to open a pass;
:class:`HubSpotCursor` carries only the position inside a pass and the
modified-time watermark. It sends the request its position names
(:meth:`HubSpotCursor.fetch`) and decides the next one from the answer
(:meth:`HubSpotCursor.advance`):

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

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, ClassVar

from django.utils import timezone
from pydantic import BaseModel, ConfigDict, ValidationError

from angee.integrate.credentials import CredentialKind
from angee.integrate.discovery import ConnectionDiscovery
from angee.integrate.states import StreamPhase
from angee.integrate.streams import CursorInvalid, StreamPage
from angee.parties.backends import CONTACTS, DirectoryBackend, ParsedAddress, ParsedAddressbook, ParsedContact
from angee.parties_integrate_hubspot.client import (
    EPOCH,
    LIST_PAGE_LIMIT,
    MISSING_SCOPES,
    SEARCH_PAGE_LIMIT,
    SERVICE_KEY_SHAPE,
    HubSpotClient,
    HubSpotError,
    HubSpotObject,
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

_OBJECT_TYPE = "contacts"
_MERGED_PROPERTY = "hs_merged_object_ids"
_READ_PROPERTIES = (*CONTACT_PROPERTIES, _MERGED_PROPERTY)
_BOOK = ParsedAddressbook(key="contacts", name="HubSpot contacts")


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

    def fetch(
        self, client: HubSpotClient, object_type: str, *, limit: int, properties: Sequence[str]
    ) -> HubSpotPage:
        """Send the request this open pass's position names, for at most ``limit`` records within HubSpot's limits.

        A drain searches the block's modified time above the last record id it
        read, a delta searches strictly after the time it has read in full, and a
        baseline lists from the record id it continues at.
        """

        if self.block is not None:
            return client.search_modified_at(
                object_type,
                moment=self.block,
                after=self.after,
                limit=min(limit, SEARCH_PAGE_LIMIT),
                properties=properties,
            )
        if self.since is not None:
            return client.search_modified(
                object_type, since=self.since, limit=min(limit, SEARCH_PAGE_LIMIT), properties=properties
            )
        return client.list_page(object_type, after=self.after, limit=min(limit, LIST_PAGE_LIMIT), properties=properties)

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
        cannot be read moves nothing; a search page that more results follow and
        whose records state no readable time is refused, since no request could
        move past it.
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
        if not moments:
            raise HubSpotError("HubSpot sent a page of changed records without a readable modified time.")
        if len(moments) > 1:
            return self.model_copy(update={"since": moments[-2], "watermark": watermark})
        return self.model_copy(update={"block": moments[0], "watermark": watermark})


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


class HubSpotDirectoryBackend(DirectoryBackend):
    """One HubSpot account's contacts, read one way into one address book of a directory.

    The directory reads with a HubSpot service key that has the contacts read
    scope, held in a static token, and connects through no OAuth client
    (:meth:`credential_blocker`). Each page, identity read and key walk reloads the
    credential, and every request sends its own authorization headers; the backend
    keeps no key. Connection discovery learns the account's hub id and the host of
    its web app, which the source links name, and holds the directory to that
    account (:meth:`apply_discovery`). A contact's external key is its HubSpot
    record id. Contacts HubSpot archived or deleted are found by integrate's
    absence sweep (:meth:`contact_keys`).
    """

    key = "hubspot"
    label = "HubSpot"
    defaults = {"vendor": "hubspot"}
    oauth_client: ClassVar[str] = ""
    requires_connection_discovery: ClassVar[bool] = True
    sync_parallelism: ClassVar[int | None] = 1

    def credential_blocker(self, credential: Any) -> str | None:
        """Refuse anything but a static token that holds a key: the service key this backend sends as its bearer."""

        if credential.kind == CredentialKind.STATIC_TOKEN and credential.secret_value().strip():
            return None
        return "A HubSpot directory reads with a HubSpot service key: attach a static token that holds one."

    def test_connection(self) -> str:
        """Ask HubSpot for one contact: one request proves the key and its contacts read scope, refused apart.

        A saved directory reloads its credential (``Integration.fresh_credential``),
        so a key re-entered in place is the one tested; a connect flow tests an
        unsaved directory with the credential it holds.
        """

        bridge = self.bridge
        credential = bridge.fresh_credential() if bridge.pk is not None else bridge.credential
        self._client(credential).list_page(_OBJECT_TYPE, after="", limit=1, properties=())
        return "Connected to HubSpot: the service key reads this account's contacts."

    def discover_connection(self, credential: Any) -> ConnectionDiscovery:
        """Learn, outside transactions, the account ``credential`` reads: its hub id and, when stated, its web host.

        The account call states both. When HubSpot refuses it for a scope the key
        lacks, the key's introspection states the hub id instead and the host stays
        unknown; the scopes introspection lists are never read.
        """

        client = self._client(credential)
        try:
            account = client.account()
        except HubSpotError as error:
            if error.category != MISSING_SCOPES:
                raise
            return ConnectionDiscovery(data={"hub_id": client.token_info(credential.secret_value()).hub_id})
        facts: dict[str, Any] = {"hub_id": account.portal_id}
        if account.ui_domain is not None:
            facts["ui_domain"] = account.ui_domain
        return ConnectionDiscovery(data=facts)

    def apply_discovery(self, discovery: ConnectionDiscovery) -> None:
        """Keep the account's hub id and web host on the directory; refuse a key of another account.

        The first discovery stores the hub id, and every later one must find the
        same account: a directory's cursor, links and source links all belong to
        the account it first read, so a key of another one would mix two accounts'
        contacts in one address book.
        """

        held, found = (self.bridge.subscription_state or {}).get("hub_id"), discovery.data["hub_id"]
        if held is not None and held != found:
            raise HubSpotError(
                f"This service key belongs to HubSpot account {found}, but this directory reads account {held}. "
                f"Attach a key of account {held}."
            )
        self.bridge.merge_subscription_state(**discovery.data)

    def discover(self) -> list[ParsedAddressbook]:
        """Return the account's one address book, which holds every contact."""

        return [_BOOK]

    def extract_contacts(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Read the next contacts of the stream's pass, at most ``page_bound`` records, as one page.

        The stream's phase opens a baseline or a delta (:class:`HubSpotCursor`); a
        stored cursor that cannot be read starts a new baseline (``CursorInvalid``).
        Requests follow one another until the page is full or the pass is done.
        Once one has answered, none starts after ``deadline``, and a transient
        refusal ends the page with what was read, its cursor where the refused
        request would have gone on; the next page meets the refusal. A merge's
        survivor is one record (``DirectoryBackend.record_changes``).
        """

        client = self._client(self.bridge.fresh_credential())
        try:
            cursor = HubSpotCursor.model_validate(stream.cursor)
        except ValidationError:
            raise CursorInvalid() from None
        cursor = cursor.begin(stream.phase, now=timezone.now())
        payloads: dict[str, Mapping[str, Any] | None] = {}
        room, answered = page_bound, False
        while cursor.is_open and room > 0:
            if answered and deadline is not None and monotonic() >= deadline:
                break
            try:
                page = cursor.fetch(client, _OBJECT_TYPE, limit=room, properties=_READ_PROPERTIES)
            except HubSpotError as error:
                if answered and error.transient:
                    break
                raise
            answered = True
            payloads.update((record.id, self._payload(record)) for record in page.results)
            cursor = cursor.advance(page, object_type=_OBJECT_TYPE)
            room -= len(page.results)
        records = self.record_changes(stream, payloads)
        return StreamPage(records, cursor.model_dump(mode="json"), exhausted=not cursor.is_open)

    def read_contacts(self, stream: Any, keys: Sequence[str]) -> Mapping[str, Mapping[str, Any] | None]:
        """Read ``keys`` in HubSpot's batch read; an id it has no record under, or answers under another, is gone.

        Any other error HubSpot states for a record fails the read
        (``HubSpotClient.batch_read``).
        """

        del stream
        answer = self._client(self.bridge.fresh_credential()).batch_read(_OBJECT_TYPE, keys, _READ_PROPERTIES)
        found = {record.id: record for record in answer.found}
        return {key: None if key in answer.missing else self._payload(found[key]) for key in keys}

    def contact_keys(self, stream: Any, *, after: str | None) -> Iterable[str]:
        """Walk the account's live contact ids past ``after``; an archived or deleted contact is not among them."""

        del stream
        return self._client(self.bridge.fresh_credential()).record_ids(_OBJECT_TYPE, after=after)

    def source_url(self, link: Any) -> str | None:
        """Return a contact's page in the account's HubSpot web app, once discovery has learned the account."""

        state = self.bridge.subscription_state or {}
        if link.stream.key != CONTACTS or not state.get("hub_id") or not state.get("ui_domain"):
            return None
        # ``0-1`` is HubSpot's object type id for contacts.
        return f"https://{state['ui_domain']}/contacts/{state['hub_id']}/record/0-1/{link.external_key}"

    def source_label(self, link: Any) -> str:
        """Name a contact as HubSpot last stated it, else by its record id."""

        contact = (link.source_payload or {}).get("contact") or {}
        return str(contact.get("display_name") or "") or super().source_label(link)

    def _client(self, credential: Any) -> HubSpotClient:
        """Return a client that sends ``credential``'s own authorization headers.

        The client also learns whether the key has a service key's usual shape, so
        a rejection of one that does not says to check it (:data:`SERVICE_KEY_SHAPE`).
        """

        if credential is None:
            raise HubSpotError("Attach a HubSpot service key to this directory.")
        usual = SERVICE_KEY_SHAPE.fullmatch(credential.secret_value()) is not None
        return HubSpotClient(credential.auth_headers(), usual_key=usual)

    def _payload(self, record: HubSpotObject) -> dict[str, Any]:
        """Return one contact's payload; a merge's survivor names the records it replaced, in HubSpot's order."""

        replaced = parse_options(record.properties.get(_MERGED_PROPERTY))
        return self.observe(contact_from_properties(record.properties), replaces=replaced)
