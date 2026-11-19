"""HubSpot contacts into the parties directory, and other HubSpot objects for consumers' mappings.

:class:`HubSpotDirectoryBackend` reads one HubSpot account's contacts one way,
into one address book, through the directory base: it turns HubSpot records into
the base's payloads, and the base builds and applies the records. Beside them it
reads one more stream per consumer's :class:`HubSpotObjectMapping`, whose records
(:class:`HubSpotObjectRecord`) the mapping lands itself.

A HubSpot pass, of contacts or of another type, is a baseline or a delta. The
stream owns which (``SyncStream.phase``, a ``StreamPhase``) and passes it in to
open a pass; :class:`HubSpotCursor` carries only the position inside a pass, the
modified-time watermark and whether the stream has caught up with it. It sends
the request its position names (:meth:`HubSpotCursor.fetch`) and decides the
next one from the answer (:meth:`HubSpotCursor.advance`). Every time it keeps is
HubSpot's: a pass starts when its first answer says it was sent (the ``Date``
HubSpot states), never by the local clock.

- a baseline walks the list endpoint by record id until it is exhausted;
- a delta searches for records modified strictly after the time it has read in
  full, oldest change first. No request pages by offset: each asks for a
  query's first page, and a block of records sharing one modified time that
  fills a page is drained by record id. A record edited during the pass moves
  to a later time, where a later page finds it, without shifting an unread one,
  and no query meets HubSpot's result ceiling;
- the watermark advances to the highest modified time a pass has seen, never
  past that pass's own start, so every change made once a pass has started is
  newer than the watermark it leaves;
- HubSpot's search index shows a change up to :data:`OVERLAP` late, so a delta
  reaches back from the watermark by the overlap until the stream has caught up:
  a pass that started at least the overlap after the watermark has completed. Every
  change at or before the watermark was searchable when that pass ran, so it was
  read, and later deltas search strictly after the watermark until a change moves
  it. A quiet account then costs one search per poll that reads nothing.

:func:`contact_from_properties` maps HubSpot's standard contact properties onto
the parties neutral contact.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, ClassVar

from django.apps import apps
from pydantic import BaseModel, ConfigDict, ValidationError

from angee.base.implementations import ImplBase, resolve_all_impl_classes, resolve_impl_class
from angee.base.serialization import canonical_json_sha256
from angee.integrate.credentials import CredentialKind
from angee.integrate.discovery import ConnectionDiscovery
from angee.integrate.states import (
    LinkStatus,
    MappingContribution,
    StreamDefinition,
    StreamKind,
    StreamPhase,
    SyncFailureKind,
)
from angee.integrate.streams import ApplyResult, CursorInvalid, RecordChange, SemanticError, StreamPage
from angee.parties.backends import (
    CONTACTS,
    MAPPING_REFUSED,
    DirectoryBackend,
    ParsedAddress,
    ParsedAddressbook,
    ParsedContact,
)
from angee.parties_integrate_hubspot.client import (
    EPOCH,
    LIST_PAGE_LIMIT,
    MERGED_PROPERTY,
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

logger = logging.getLogger(__name__)

OVERLAP = timedelta(minutes=5)
"""The longest HubSpot's search index takes to show a change, and so how far a delta reaches back until caught up.

It also absorbs the time HubSpot takes to answer the first request of a pass,
whose ``Date`` the pass starts at: at most the request timeout of 30 seconds.
"""

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

OBJECTS = "objects"
"""The stream of one :class:`HubSpotObjectMapping`'s records; its partition is the mapping's key."""

_CONTACT_TYPE = "contacts"
_READ_PROPERTIES = (*CONTACT_PROPERTIES, MERGED_PROPERTY)
_BOOK = ParsedAddressbook(key="contacts", name="HubSpot contacts")


class HubSpotCursor(BaseModel):
    """One HubSpot stream's position inside its open pass, its watermark, and whether it has caught up with that.

    ``started`` is the open pass's start on HubSpot's clock: the time its first
    answer was sent (``HubSpotPage.sent_at``); ``None`` between passes and until
    a new pass's first answer arrives. ``since`` is the modified time an open delta
    has read in full, ``None`` in a baseline; the delta reads what was modified
    strictly after it. ``block`` is the modified time of the block a delta is
    draining by record id, ``None`` otherwise. ``after`` is the position inside
    the open query: the record id a baseline walk continues from, or the last
    record id a drain read. ``watermark`` is the highest modified time read so far,
    never past the start of the pass that read it. ``caught_up`` states that every
    change at or before the watermark has been read: the last pass to complete
    started at least :data:`OVERLAP` after it. The driver persists the cursor with
    the records it covers.
    """

    model_config = ConfigDict(frozen=True)

    after: str = ""
    since: datetime | None = None
    block: datetime | None = None
    started: datetime | None = None
    watermark: datetime | None = None
    caught_up: bool = False

    @property
    def is_open(self) -> bool:
        """Whether a pass is in progress; once it is not, the stream page is exhausted."""

        return self.started is not None

    def begin(self, phase: str) -> HubSpotCursor:
        """Return the cursor with a pass of the stream's ``phase`` open, or opening.

        An open pass of that phase continues. Otherwise a new pass opens, and starts
        at its first answer (:meth:`advance`): a baseline walks from the first record
        id; a delta searches strictly after the watermark once it has caught up, and
        until then after the watermark minus :data:`OVERLAP` (after the epoch before
        there is a watermark).
        """

        delta = phase == StreamPhase.DELTA
        if self.started is not None and (self.since is not None) == delta:
            return self
        since = None
        if delta:
            since = EPOCH if self.watermark is None else self.watermark
            if self.watermark is not None and not self.caught_up:
                since = max(self.watermark - OVERLAP, EPOCH)
        return HubSpotCursor(since=since, watermark=self.watermark, caught_up=self.caught_up)

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

        A new pass starts when its first answer was sent (``HubSpotPage.sent_at``);
        an answer that states no readable time cannot start one and is refused. A
        delta search page that more results follow has read in full every time
        before its latest one, so the search goes on strictly after the latest of
        those; the records at the page's latest time are read again, and the
        driver takes a record delivered again unchanged as no change. A page of
        one time may end inside a block of that time. HubSpot does not state how
        it orders records of one time, so the block is drained from its lowest
        record id, each drain page above the last id read, and only the drain's
        last page moves the search past the block. A record whose modified time
        cannot be read moves nothing; a search page that more results follow and
        whose records state no readable time is refused, since no request could
        move past it. A completed pass leaves the stream caught up when it started
        at least :data:`OVERLAP` after the watermark.
        """

        started = self.started if self.started is not None else page.sent_at
        if started is None:
            raise HubSpotError("HubSpot sent an answer without a readable Date header.")
        moments = sorted({moment for record in page.results if (moment := record.modified_at(object_type)) is not None})
        watermark = self.watermark
        if moments:
            reached = min(moments[-1], started)
            watermark = reached if watermark is None else max(watermark, reached)
        more = bool(page.next_after)
        read = self.model_copy(update={"started": started, "watermark": watermark})
        if self.block is not None and more:
            return read.model_copy(update={"after": page.results[-1].id})
        if self.block is not None:
            return read.model_copy(update={"since": self.block, "block": None, "after": ""})
        if not more:
            caught_up = watermark is not None and watermark <= started - OVERLAP
            return HubSpotCursor(watermark=watermark, caught_up=caught_up)
        if self.since is None:
            return read.model_copy(update={"after": page.next_after})
        if not moments:
            raise HubSpotError("HubSpot sent a page of changed records without a readable modified time.")
        if len(moments) > 1:
            return read.model_copy(update={"since": moments[-2]})
        return read.model_copy(update={"block": moments[0]})


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


class ContactNotLanded(SemanticError):
    """A mapping's refusal of a record whose contact this directory has not landed yet: the record waits.

    It is the stream driver's missing-dependency refusal
    (``SyncFailureKind.MISSING_DEPENDENCY``), naming the contact: the record is
    kept under its id, its link failed, and the stream's rescan reads it again on
    its backoff, so it lands once the contact has. Object streams begin only once
    the directory has read its contacts in full (``HubSpotDirectoryBackend.streams``),
    so a record waits only for a contact HubSpot gained since, or one it does not hold.
    """

    def __init__(self, contact_id: str) -> None:
        super().__init__(
            "contact_not_landed", details={"contact": contact_id}, kind=SyncFailureKind.MISSING_DEPENDENCY
        )


class PropertiesNotRead(SemanticError):
    """A mapping needed the properties HubSpot sent for a record this pass did not read: the record is read again.

    The stream driver's missing-dependency refusal (``SyncFailureKind.MISSING_DEPENDENCY``):
    the record waits under its id, and the stream's rescan reads it again
    (``read_keys``) and applies it with what HubSpot sends then. Reading
    :attr:`HubSpotObjectRecord.properties` without them raises it, so a mapping
    never writes from its projection what it needed from the source.
    """

    def __init__(self) -> None:
        super().__init__("properties_not_read", kind=SyncFailureKind.MISSING_DEPENDENCY)


@dataclass(frozen=True)
class HubSpotObjectRecord:
    """One HubSpot record of another object type as its mapping receives it, neutral of what it means.

    ``id`` is HubSpot's record id, its link's external key. ``contacts`` names the
    contacts HubSpot associates with it, in id order, when the mapping asks for
    them (:attr:`HubSpotObjectMapping.contacts`). ``values`` is what
    :meth:`HubSpotObjectMapping.project` kept, which its link holds and compares
    (handed to ``project`` itself, the properties as sent). :attr:`properties` is
    what HubSpot sent for the mapping's properties, held in memory for the pass
    that read the record and never stored, repeated or logged.
    """

    id: str
    values: Mapping[str, Any]
    contacts: tuple[str, ...]
    directory: Any = field(repr=False, compare=False)
    """The HubSpot directory the record was read for."""
    sent: Mapping[str, str | None] | None = field(default=None, repr=False, compare=False)
    """What HubSpot sent, when this pass read the record; read it through :attr:`properties`."""

    @property
    def has_properties(self) -> bool:
        """Whether this pass read the record from HubSpot, so :attr:`properties` holds what it sent."""

        return self.sent is not None

    @property
    def properties(self) -> Mapping[str, str | None]:
        """HubSpot's values of the mapping's properties as it sent them; :class:`PropertiesNotRead` without them.

        Each is a nullable string (read them with the ``parse_*`` functions of
        ``angee.parties_integrate_hubspot.client``); a property the account does not
        define is absent. Every apply the stream driver makes follows its own read
        of the record, so they are there; a record applied from what its link holds
        alone has only :attr:`values`, and reading this raises the refusal that has
        the driver read it again.
        """

        if self.sent is None:
            raise PropertiesNotRead()
        return self.sent

    def person(self, contact_id: str) -> Any | None:
        """Return the person this directory's contact ``contact_id`` landed on, ``None`` until it has.

        A contact lands when the directory's contacts stream binds its link to a
        person (``PartyManager.contact_people``: a trashed person is nobody's);
        one not read yet, refused or gone has landed nowhere. A mapping that needs
        the person raises :class:`ContactNotLanded` to wait for it. The person is
        read as committed, not locked; the verb that writes on it takes its locks.
        """

        links = apps.get_model("integrate", "RecordLink").objects.filter(
            stream__integration_id=self.directory.pk, stream__key=CONTACTS, external_key=contact_id
        )
        link = links.observing().first()
        return None if link is None else apps.get_model("parties", "Party").objects.contact_people([link])[link.pk]


class HubSpotObjectMapping(ImplBase):
    """Read the records of another HubSpot object type for a consumer addon, which decides what they mean.

    The consumer registers its mapping under the mapping's ``key`` from its
    ``autoconfig.py``: ``"ANGEE_HUBSPOT_OBJECT_MAPPING_CLASSES.<key>": "<dotted.path>"``,
    checked at startup (``check_object_mappings``). Every HubSpot directory then
    reads one more stream, :data:`OBJECTS` partitioned by the key, pull-only, of
    ``object_type``'s records: HubSpot's plural of any type its list, search and
    batch-read endpoints serve (``deals``, ``notes``), never ``contacts``, which a
    directory reads itself and a ``DirectoryMapping`` maps. Every read asks HubSpot
    for ``properties`` and nothing more besides the bookkeeping the pass needs, and,
    when ``contacts`` is set, for each record's associated contact ids. The stream
    keeps the contacts stream's guarantees: a baseline walk, a delta on the type's
    own modified property that reaches back by the overlap on HubSpot's clock,
    equal-time drains, a daily absence sweep where only HubSpot's word makes a
    record gone, and per-record refusals. ``version`` is the stream's mapping:
    changing it replays every record once.

    While extracting, outside any transaction, :meth:`project` turns the record as
    HubSpot sent it into the values the mapping keeps: only they and the contact
    ids are kept on the link, in its history without limit, and compared, so a
    change the projection hides applies nothing; it drops what must not be kept
    (a long text replaced by its digest, say). :meth:`apply_record` lands one
    record inside the page transaction, database work only, and runs again
    whenever those values, its contacts or the mapping change. It receives the
    values and, from the read that applies it, what HubSpot sent
    (:attr:`HubSpotObjectRecord.properties`), so it can write the full text whose
    digest alone is kept. Whatever it states, it states through ``link`` as the
    integration (``link.asserting_source()``), so :meth:`withdraw_record` can
    withdraw it. A record whose contact has not landed
    raises :class:`ContactNotLanded` and waits. A ``ValueError`` from either step
    refuses that record alone (``MAPPING_REFUSED``, naming the mapping, never the
    value), the rest of its page lands, and the rescan retries it; any other error
    is a bug and fails the page.
    """

    registry_setting = "ANGEE_HUBSPOT_OBJECT_MAPPING_CLASSES"

    object_type: ClassVar[str] = ""
    """HubSpot's plural name of the object type this mapping reads (``deals``)."""
    properties: ClassVar[tuple[str, ...]] = ()
    """The properties of ``object_type`` to request, by HubSpot's internal names."""
    contacts: ClassVar[bool] = False
    """Whether each record names the contacts HubSpot associates with it."""
    version: ClassVar[int] = 1
    """This mapping's version; change it when :meth:`project` or :meth:`apply_record` changes what it does."""

    def project(self, record: HubSpotObjectRecord) -> dict[str, Any]:
        """Return the JSON values to keep and compare of ``record``, whose ``properties`` are as HubSpot sent them.

        The default keeps the requested properties as sent. Drop here whatever must
        not be kept: nothing else of the properties survives on the link, and
        ``apply_record`` still receives them, from the read that applies it.
        """

        return dict(record.properties)

    def apply_record(self, record: HubSpotObjectRecord, *, link: Any) -> Any:
        """Land ``record`` through its ``link``; return the row its link then holds, or ``None`` to bind none.

        A returned row is the record's own, a row of the consumer's, which the
        link holds (and keeps from deletion) as long as the link lasts; ``None``
        leaves the link's binding as it is. Never return the person a contact
        landed on: the contact's link holds it, and a gone record's statements
        would move to that link (:meth:`withdraw_record`).
        """

        raise NotImplementedError("HubSpotObjectMapping subclasses must implement apply_record().")

    def withdraw_record(self, link: Any) -> None:
        """The record is gone at the source: withdraw what it stated through ``link``.

        Called once HubSpot says so, on a read of the record or after the absence
        sweeps. The default withdraws what the link asserts
        (``RecordLink.withdraw_statements``, which moves it instead to another live
        link of the integration holding the same row). Survivorship keeps a value
        nobody states any more, unsupported; the link keeps holding its row.
        """

        link.withdraw_statements()


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

    A consumer's ``DirectoryMapping`` of this backend names HubSpot contact
    properties by their internal names (``DirectoryBackend.mapped_fields``). Every
    read of contacts (a list page, a search, a drain, a batch read) asks HubSpot
    for them beside the backend's own, and for nothing more, and hands HubSpot's
    values of them to the base with each contact (``DirectoryBackend.observe``).
    HubSpot leaves a property the account does not define out of its answer, so a
    mapping receives it as not sent and the sync goes on.

    A consumer's :class:`HubSpotObjectMapping` adds a stream of its own
    (:meth:`streams`): this backend routes the :data:`OBJECTS` key through its
    extract, identity reads, key walk, apply and absence hooks, and every other key
    to the base, so a directory without an object mapping reads only contacts.
    """

    key = "hubspot"
    label = "HubSpot"
    defaults = {"vendor": "hubspot"}
    oauth_client: ClassVar[str] = ""
    requires_connection_discovery: ClassVar[bool] = True
    sync_parallelism: ClassVar[int | None] = 1

    def __init__(self, integration: Any) -> None:
        super().__init__(integration)
        self._sent: dict[str, tuple[str, Mapping[str, str | None]]] = {}
        """What HubSpot sent for each object record of the last read, by id, beside the hash of its payload.

        Filled by the read (:meth:`_object_payloads`), handed to the mapping when that
        read's own record is applied, and dropped when its page finishes
        (:meth:`finish_page`) or the next read begins; never given to the driver.
        """

    def credential_blocker(self, credential: Any) -> str | None:
        """Refuse anything but a static token holding a key, and one that needs reconnecting.

        The key is judged as it is sent: a static token's bearer is its key as
        stored, so only an empty one, with nothing to send, is refused here, and
        HubSpot judges any other.
        """

        if credential.kind != CredentialKind.STATIC_TOKEN or not credential.secret_value():
            return "A HubSpot directory reads with a HubSpot service key: attach a static token that holds one."
        if credential.is_reconnect_required:
            return "This HubSpot service key must be reconnected: enter the key again."
        return None

    def test_connection(self) -> str:
        """Ask HubSpot for one contact: one request proves the key and its contacts read scope, refused apart.

        A saved directory reloads its credential (``Integration.fresh_credential``),
        so a key re-entered in place is the one tested; a connect flow tests an
        unsaved directory with the credential it holds. The base's check comes
        first, on that credential: one that needs reconnecting is refused without
        a request (``Integration.probe_credential``).
        """

        bridge = self.bridge
        credential = bridge.fresh_credential() if bridge.pk is not None else bridge.credential
        super().test_connection()
        self._client(credential).list_page(_CONTACT_TYPE, after="", limit=1, properties=())
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

    def streams(self, *, deadline: float | None = None) -> Iterator[StreamDefinition]:
        """Declare the directory's address book and contacts, then one :data:`OBJECTS` stream per object mapping.

        Each object stream is pull-only, partitioned by its mapping's key, swept
        daily, and its mapping is that mapping's key and version, so a changed
        version replays its records once (``StreamDefinition.mapping_contributions``).
        Object streams are declared only once the directory has read its contacts
        in full (a generation of its contacts stream completed its baseline), from
        local state as the contacts stream waits for its live address book: their
        records name contacts, so on a fresh account the first sync lands the
        contacts and the next reads the objects, none of which waits for a contact
        HubSpot already held. Without an object mapping the directory declares only
        what the base does.
        """

        yield from super().streams(deadline=deadline)
        streams = apps.get_model("integrate", "SyncStream").unscoped_objects
        if not streams.filter(integration_id=self.bridge.pk, key=CONTACTS, phase=StreamPhase.DELTA).exists():
            return
        for mapping in resolve_all_impl_classes(HubSpotObjectMapping):
            yield StreamDefinition(
                OBJECTS,
                partition=mapping.key,
                kind=StreamKind.RECORD_REPLICA,
                reconcile_interval=timedelta(days=1),
                mapping_contributions=(MappingContribution(mapping.key, mapping.version, sequence=0),),
            )

    def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Read the next page of an object stream's pass (:meth:`_read_pass`); the base reads every other stream.

        When the mapping asks for contacts, one association read follows the
        page's records, within HubSpot's batch limit.
        """

        if stream.key != OBJECTS:
            return super().extract(stream, page_bound, deadline=deadline)
        mapping = self._object_mapping(stream)
        client = self._client(self.bridge.fresh_credential())
        records, cursor = self._read_pass(
            client,
            stream,
            page_bound,
            object_type=mapping.object_type,
            properties=mapping.properties,
            deadline=deadline,
        )
        changes = self._object_changes(stream, self._object_payloads(client, mapping, records))
        return StreamPage(changes, cursor.model_dump(mode="json"), exhausted=not cursor.is_open)

    def read_keys(self, stream: Any, keys: Sequence[str]) -> Iterable[RecordChange]:
        """Read an object stream's ``keys`` in HubSpot's batch read, gone only on HubSpot's word; the base reads others.

        ``HubSpotClient.batch_read`` decides which ids are gone; the rest are read
        with their associated contacts when the mapping asks for them.
        """

        if stream.key != OBJECTS:
            return super().read_keys(stream, keys)
        mapping = self._object_mapping(stream)
        client = self._client(self.bridge.fresh_credential())
        answer = client.batch_read(mapping.object_type, keys, mapping.properties)
        found = {record.id: record for record in answer.found}
        live = {key: found[key] for key in keys if key not in answer.missing}
        payloads = self._object_payloads(client, mapping, live)
        return self._object_changes(stream, {key: payloads.get(key) for key in keys})

    def enumerate_keys(self, stream: Any, *, after: str | None = None) -> Iterable[str]:
        """Walk an object stream's live record ids past ``after`` (:meth:`_live_ids`); the base walks contacts."""

        if stream.key != OBJECTS:
            return super().enumerate_keys(stream, after=after)
        return self._live_ids(stream, self._object_mapping(stream).object_type, after=after)

    def apply_record(self, stream: Any, record: RecordChange) -> ApplyResult:
        """Land an object stream's record through its mapping; the base applies every other stream's.

        A record gone at the source reaches :meth:`HubSpotObjectMapping.withdraw_record`.
        Any other is handed to :meth:`HubSpotObjectMapping.apply_record` as a
        :class:`HubSpotObjectRecord` carrying the values its payload holds and,
        when this backend's last read produced exactly this payload, what HubSpot
        sent; its link holds the row the mapping returns. The mapping's
        ``ValueError`` refuses the record alone (``MAPPING_REFUSED``, naming the
        mapping).
        """

        if stream.key != OBJECTS:
            return super().apply_record(stream, record)
        mapping = self._object_mapping(stream)
        link = stream.links.get(external_key=record.external_key)
        if record.tombstone:
            mapping.withdraw_record(link)
            return ApplyResult(mapped_payload=None)
        payload = record.mapped_payload
        read = self._sent.get(record.external_key)
        neutral = HubSpotObjectRecord(
            record.external_key,
            payload["values"],
            tuple(payload["contacts"]),
            directory=self.bridge,
            sent=read[1] if read is not None and read[0] == record.source_hash else None,
        )
        try:
            target = mapping.apply_record(neutral, link=link)
        except ValueError as error:
            raise SemanticError(MAPPING_REFUSED, details={"mapping": mapping.key}) from error
        if target is None:
            # An explicit ``None`` target would withdraw the link: a record on no row of its own binds nothing.
            return ApplyResult(mapped_payload=payload)
        return ApplyResult(target=target, mapped_payload=payload)

    def finish_page(self, stream: Any, page: StreamPage, outcomes: Sequence[ApplyResult]) -> None:
        """Drop what HubSpot sent for the page's records, once the page is applied: nothing outlives its pass."""

        self._sent = {}
        super().finish_page(stream, page, outcomes)

    def on_absent(self, stream: Any, links: Sequence[Any]) -> dict[Any, ApplyResult] | None:
        """Hand each object record the absence sweeps found gone to its mapping's gone hook; the base handles others.

        An unavailable record keeps what it stated until the absence threshold
        tombstones its link. Each withdrawal is the link's evidence, so a record
        that returns applies again.
        """

        if stream.key != OBJECTS:
            return super().on_absent(stream, links)
        mapping = self._object_mapping(stream)
        gone = [link for link in links if link.status == LinkStatus.TOMBSTONE]
        for link in gone:
            mapping.withdraw_record(link)
        return {link.pk: ApplyResult(mapped_payload=None) for link in gone}

    def extract_contacts(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Read the next contacts of the stream's pass, at most ``page_bound`` records, as one page.

        :meth:`_read_pass` reads them; a merge's survivor is one record
        (``DirectoryBackend.record_changes``).
        """

        client = self._client(self.bridge.fresh_credential())
        mapped = self.mapped_fields()
        records, cursor = self._read_pass(
            client,
            stream,
            page_bound,
            object_type=_CONTACT_TYPE,
            properties=_read_properties(mapped),
            deadline=deadline,
        )
        changes = self.record_changes(stream, {key: self._payload(record, mapped) for key, record in records.items()})
        return StreamPage(changes, cursor.model_dump(mode="json"), exhausted=not cursor.is_open)

    def read_contacts(self, stream: Any, keys: Sequence[str]) -> Mapping[str, Mapping[str, Any] | None]:
        """Read ``keys`` in HubSpot's batch read; an id is gone only on HubSpot's word (``HubSpotClient.batch_read``).

        HubSpot names it not found, or a record it answers names it among those its
        merge replaced. Any other id HubSpot leaves unanswered, and any other error
        it states, fails the read as transient, so the driver reads it again later.
        """

        del stream
        mapped = self.mapped_fields()
        client = self._client(self.bridge.fresh_credential())
        answer = client.batch_read(_CONTACT_TYPE, keys, _read_properties(mapped))
        found = {record.id: record for record in answer.found}
        return {key: None if key in answer.missing else self._payload(found[key], mapped) for key in keys}

    def contact_keys(self, stream: Any, *, after: str | None) -> Iterator[str]:
        """Walk the account's live contact ids past ``after`` (:meth:`_live_ids`)."""

        return self._live_ids(stream, _CONTACT_TYPE, after=after)

    def _read_pass(
        self,
        client: HubSpotClient,
        stream: Any,
        page_bound: int,
        *,
        object_type: str,
        properties: Sequence[str],
        deadline: float | None,
    ) -> tuple[dict[str, HubSpotObject], HubSpotCursor]:
        """Read the next records of ``object_type`` in the stream's pass, at most ``page_bound``, by id; and the cursor.

        The stream's phase opens a baseline or a delta (:class:`HubSpotCursor`); a
        stored cursor that cannot be read starts a new baseline (``CursorInvalid``).
        Requests follow one another until the page is full or the pass is done.
        Once one has answered, none starts after ``deadline``, and a transient
        refusal ends the page with what was read, its cursor where the refused
        request would have gone on; the next page meets the refusal. A record read
        twice is kept as last read. A record HubSpot states unreadably is kept by
        its id, to be that record's refusal; one without a usable id cannot be a
        record, and is logged and skipped.
        """

        try:
            cursor = HubSpotCursor.model_validate(stream.cursor)
        except ValidationError:
            raise CursorInvalid() from None
        cursor = cursor.begin(stream.phase)
        records: dict[str, HubSpotObject] = {}
        room, answered, unkeyed = page_bound, False, 0
        while room > 0 and (cursor.is_open or not answered):
            if answered and deadline is not None and monotonic() >= deadline:
                break
            try:
                page = cursor.fetch(client, object_type, limit=room, properties=properties)
            except HubSpotError as error:
                if answered and error.transient:
                    break
                raise
            answered, unkeyed = True, unkeyed + page.unkeyed
            records.update((record.id, record) for record in page.results)
            cursor = cursor.advance(page, object_type=object_type)
            room -= len(page.results)
        if unkeyed:
            noun = "contact" if object_type == _CONTACT_TYPE else f"{object_type} record"
            logger.warning(
                "HubSpot directory %s skipped %d %s%s without a usable record id.",
                self.bridge.pk,
                unkeyed,
                noun,
                "" if unkeyed == 1 else "s",
            )
        return records, cursor

    def _live_ids(self, stream: Any, object_type: str, *, after: str | None) -> Iterator[str]:
        """Walk the account's live ``object_type`` ids past ``after``; an archived or deleted record is not among them.

        A walk from the start that lists no record while the stream holds live
        links is believed only when HubSpot's search counts none either; otherwise
        it is a transient refusal and the sweep tries again. A walk cut short
        part-way cannot be told from a complete one: the records it misses wait
        for the absence threshold, two sweeps.
        """

        client = self._client(self.bridge.fresh_credential())
        keys = client.record_ids(object_type, after=after)
        first = next(keys, None)
        if first is None and after is None and stream.links.observing().exists():
            if client.search_modified(object_type, since=EPOCH, limit=1, properties=()).total != 0:
                raise HubSpotError(
                    f"HubSpot listed no {object_type}, but its search still counts some; retrying later.",
                    transient=True,
                )
        if first is not None:
            yield first
            yield from keys

    def _object_mapping(self, stream: Any) -> HubSpotObjectMapping:
        """Return the registered mapping an object stream reads for: the one its partition names."""

        return resolve_impl_class(HubSpotObjectMapping, stream.partition)()

    def _object_payloads(
        self, client: HubSpotClient, mapping: HubSpotObjectMapping, records: Mapping[str, HubSpotObject]
    ) -> dict[str, dict[str, Any]]:
        """Return each record's payload: what the mapping projected of it and its contacts in id order.

        The associated contacts are read in one association read when the mapping
        asks for them. The mapping projects the record as HubSpot sent it
        (:meth:`HubSpotObjectMapping.project`), and only the projection enters the
        payload; what HubSpot sent is held in memory for this read's own apply
        (``_sent``), in place of the last read's. A record HubSpot stated
        unreadably, or whose value the projection cannot read (``ValueError``), is
        an error payload the driver quarantines under its id; the projection's
        refusal names the mapping, never the value.
        """

        readable = [key for key, record in records.items() if not record.unreadable]
        associated: dict[str, tuple[str, ...]] = {}
        if mapping.contacts and readable:
            associated = client.associations(mapping.object_type, _CONTACT_TYPE, readable)
        payloads: dict[str, dict[str, Any]] = {}
        self._sent = {}
        for key, record in records.items():
            if record.unreadable:
                payloads[key] = {"error": "unreadable_record"}
                continue
            stated = record.properties
            sent = {name: stated[name] for name in mapping.properties if name in stated}
            contacts = sorted(associated.get(key, ()), key=int)
            try:
                values = mapping.project(HubSpotObjectRecord(key, sent, tuple(contacts), self.bridge, sent=sent))
            except ValueError:
                payloads[key] = {"error": MAPPING_REFUSED, "mapping": mapping.key}
                continue
            payloads[key] = {"values": values, "contacts": contacts}
            self._sent[key] = (canonical_json_sha256(payloads[key]), sent)
        return payloads

    def _object_changes(self, stream: Any, payloads: Mapping[str, Mapping[str, Any] | None]) -> list[RecordChange]:
        """Turn an object stream's payloads, keyed by record id, into one record each; ``None`` is a tombstone.

        The local side of each comparison is what this source last applied (the
        link's applied base), as on every pull-only stream, so an unchanged record
        applies nothing. A payload carrying ``error`` is that record's refusal.
        """

        links = stream.links.filter(external_key__in=list(payloads)).with_sync_evidence()
        applied = {link.external_key: link.applied_base.mapped_payload for link in links if link.applied_base_id}
        changes = []
        for key, payload in payloads.items():
            base = applied.get(key)
            if payload is None:
                changes.append(RecordChange(key, {}, "", tombstone=True, projection=base))
            elif "error" in payload:
                details = {name: value for name, value in payload.items() if name != "error"}
                failure = SemanticError(payload["error"], details=details)
                changes.append(RecordChange(key, dict(payload), canonical_json_sha256(payload), failure=failure))
            else:
                digest = canonical_json_sha256(payload)
                changes.append(RecordChange(key, dict(payload), digest, mapped_payload=dict(payload), projection=base))
        return changes

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
        """Return a client that sends ``credential``'s own authorization headers through this backend's ``http``.

        The client also learns whether the key has a service key's usual shape, so
        a rejection of one that does not says to check it (:data:`SERVICE_KEY_SHAPE`).
        """

        if credential is None:
            raise HubSpotError("Attach a HubSpot service key to this directory.")
        usual = SERVICE_KEY_SHAPE.fullmatch(credential.secret_value()) is not None
        return HubSpotClient(self.http, credential.auth_headers(), usual_key=usual)

    def _payload(self, record: HubSpotObject, mapped: Sequence[str]) -> dict[str, Any]:
        """Return one contact's payload; a merge's survivor names the records it replaced, in HubSpot's order.

        HubSpot's values of the ``mapped`` properties go to the base as the row its
        mappings project, as HubSpot sent them: one the account does not define is
        absent, an unset one is ``None``. A record HubSpot stated unreadably is the
        base's error payload, which the driver quarantines under its id.
        """

        if record.unreadable:
            return {"error": "unreadable_record"}
        properties = record.properties
        row = {name: properties[name] for name in mapped if name in properties}
        return self.observe(contact_from_properties(properties), replaces=record.merged_ids(), row=row)


def _read_properties(mapped: Sequence[str]) -> tuple[str, ...]:
    """Return what a contacts read asks HubSpot for: the backend's own properties, then the ``mapped`` ones."""

    return tuple(dict.fromkeys((*_READ_PROPERTIES, *mapped)))
