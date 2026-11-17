"""Directory replica mapping; integrate owns the loop and parties owns the fields.

Transport backends discover collections and extract neutral contacts. The
``addressbooks`` stream mirrors each discovered collection as a folder, and each
address book's link is the container of its ``contacts`` stream. Both sides
compare the same deterministic projection; all inbound writes compose the parties
ingest owners. The manual backend declares no streams.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.prefetch import GenericPrefetch
from django.db.models import CharField, prefetch_related_objects
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce

from angee.base.serialization import canonical_json_sha256
from angee.integrate.errors import IntegrationError
from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import AdapterContractError, BridgeImpl
from angee.integrate.states import LinkStatus, StreamDefinition, StreamDirection, StreamKind, StreamPhase
from angee.integrate.streams import ApplyResult, LocalChange, RecordChange, SemanticError, StreamPage

ADDRESSBOOKS = "addressbooks"
"""The stream whose records are a directory's address books; each targets its folder."""
CONTACTS = "contacts"
"""One address book's contacts; its stream's container is the book's link."""


@dataclass(frozen=True)
class ParsedPhoto:
    """A contact photo parsed from a source — inline bytes or a remote URI.

    The pure parse step decodes inline (base64 / data-URI) photos to ``data`` and
    records a ``uri`` for remote ones; the backend's transport step resolves any
    ``uri`` to ``data`` before preparation stores the bytes and replaces them
    with ``content_hash``. Only that immutable content address enters link history.
    """

    data: bytes | None = None
    uri: str = ""
    mime: str = ""
    content_hash: str = ""


@dataclass(frozen=True)
class AvatarReport:
    """The avatar a handle's own network shows, as a live source reports it.

    The handle is named by the identity ingest gave it (``external_id`` first,
    else ``value``); a report never creates or renames one. ``avatar`` holds the
    image bytes, or ``None`` when the network states that it shows none, which
    clears the handle's avatar (:meth:`HandleManager.fill_avatar`).
    """

    platform: str
    value: str
    external_id: str
    avatar: bytes | None


@dataclass(frozen=True)
class ParsedAddressbook:
    """Collection identity and display name; progress belongs to SyncStream."""

    href: str
    name: str = "Contacts"


@dataclass(frozen=True)
class ParsedAddress:
    """One physical address parsed from a directory source."""

    label: str = ""
    po_box: str = ""
    extended: str = ""
    street: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""


@dataclass(frozen=True)
class ParsedContact:
    """One contact parsed from a directory source, neutral of the wire format.

    ``uid`` is the source's stable id (a vCard ``UID``); it is the per-folder
    idempotency key, so it must be stable across syncs. ``etag`` is the
    per-resource version (for change detection) and ``raw_vcard`` is kept for
    lossless round-trip. Emails/phones are ``(value, label, is_preferred)`` triples.
    ``organization``/``department``/``title``/``role`` carry the employment (the map
    folds ``organization``/``title``/``role`` onto an ``employee`` ``Relationship``);
    ``birthday``/``anniversary`` are resolved dates; ``photo`` is the avatar.
    """

    uid: str = ""
    etag: str = ""
    display_name: str = ""
    name_prefix: str = ""
    given_name: str = ""
    additional_name: str = ""
    family_name: str = ""
    name_suffix: str = ""
    nickname: str = ""
    notes: str = ""
    organization: str = ""
    department: str = ""
    title: str = ""
    role: str = ""
    birthday: date | None = None
    anniversary: date | None = None
    emails: tuple[tuple[str, str, bool], ...] = ()
    phones: tuple[tuple[str, str, bool], ...] = ()
    addresses: tuple[ParsedAddress, ...] = ()
    photo: ParsedPhoto | None = None
    raw_vcard: str = ""
    href: str = ""


CONTACT_FIELDS = (
    "display_name",
    "name_prefix",
    "given_name",
    "additional_name",
    "family_name",
    "name_suffix",
    "nickname",
    "notes",
    "organization",
    "title",
    "role",
    "birthday",
    "anniversary",
    "emails",
    "phones",
    "addresses",
    "photo",
)
"""The exact bidirectional mapping; UID, transport facts and unmapped ORG units stay outside it."""

SET_FIELDS = ("emails", "phones")
"""Collections the projection compares as sets: a repeated value is no difference.

Addresses compare as a list, so a second copy of one address is a difference.
"""


def contact_projection(contact: ParsedContact) -> dict[str, Any]:
    """Return the same JSON comparison shape for a remote card and a local person.

    Collection ordering is immaterial. Photos compare their content addresses.
    A fetched contact arrives in its stored form (``prepare_contact``), so a
    country a source spells out compares equal to the stored code.
    """

    result = {name: getattr(contact, name) for name in CONTACT_FIELDS}
    for name in ("birthday", "anniversary"):
        value = result[name]
        result[name] = value.isoformat() if value is not None else None
    for name in SET_FIELDS:
        result[name] = [list(item) for item in sorted(set(result[name]))]
    result["addresses"] = sorted((asdict(address) for address in contact.addresses), key=canonical_json_sha256)
    photo = contact.photo
    result["photo"] = None
    if photo is not None and (photo.content_hash or photo.data):
        digest = photo.content_hash or hashlib.sha256(photo.data or b"").hexdigest()
        result["photo"] = {"hash": digest, "mime": photo.mime}
    return result


def contact_from_projection(projection: Mapping[str, Any]) -> ParsedContact:
    """Decode the parties projection for the existing ingest and transport verbs."""

    values = {name: projection[name] for name in CONTACT_FIELDS}
    for name in ("birthday", "anniversary"):
        values[name] = date.fromisoformat(values[name]) if values[name] else None
    for name in ("emails", "phones"):
        values[name] = tuple(tuple(item) for item in values[name])
    values["addresses"] = tuple(ParsedAddress(**item) for item in values["addresses"])
    photo = values["photo"]
    values["photo"] = ParsedPhoto(content_hash=photo["hash"], mime=photo["mime"]) if photo else None
    return ParsedContact(**values)


class DirectoryBackend(BridgeImpl, HttpClientMixin):
    """An address-book stream, and one contacts replica per address book mapped through Party.ingest_contact.

    A source backend implements ``discover`` for the address books and
    ``extract_contacts``/``read_contacts`` for one book's contacts; this base
    routes the ``addressbooks`` stream itself.
    """

    registry_setting = "ANGEE_DIRECTORY_BACKEND_CLASSES"

    category = "directory"
    label = "Directory"
    icon = "address-book"

    def probe(self) -> None:
        """Validate the source connection before a directory persists (no-op by default).

        A source backend overrides this to fail fast on a bad URL or rejected
        credentials, so the connect mutation never saves a directory that can never
        sync. It must raise on a bad connection and return ``None`` on success.
        """

        return None

    def discover(self) -> list[ParsedAddressbook]:
        """Return every address-book collection the source exposes."""

        raise NotImplementedError("DirectoryBackend subclasses must implement discover().")

    def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
        """Declare the address-book listing and the contacts of each live address book.

        Local state only: an address book is known once the ``addressbooks`` stream
        applied it, and its folder is created on that stream's apply path.
        """

        yield self.addressbooks_stream()
        for addressbook in apps.get_model("integrate", "RecordLink").objects.containers_of(self.bridge, ADDRESSBOOKS):
            yield self.contacts_stream(addressbook)

    def addressbooks_stream(self) -> StreamDefinition:
        """Declare the pull-only listing whose records are the directory's address books."""

        return StreamDefinition(ADDRESSBOOKS, kind=StreamKind.RECORD_REPLICA)

    def contacts_stream(self, addressbook: Any) -> StreamDefinition:
        """Declare the contacts stream an address-book link holds, writing nothing.

        The partition is the address book's href, the link's external key. The
        stream's deletion policy seeds from the directory's per-collection config.
        """

        policy = {
            "local_delete": "conflict",
            "remote_delete": "retain",
            **self.bridge.config.get("streams", {}).get(CONTACTS, {}).get(addressbook.external_key, {}),
        }
        if policy["local_delete"] not in {"conflict", "propagate"} or policy["remote_delete"] not in {
            "retain",
            "propagate",
        }:
            raise ValueError("Unknown Directory deletion policy.")
        return StreamDefinition(
            CONTACTS,
            partition=addressbook.external_key,
            kind=StreamKind.RECORD_REPLICA,
            direction=StreamDirection.BIDIRECTIONAL,
            reconcile_interval=timedelta(days=1),
            config=policy,
            container=addressbook,
        )

    def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Compare the complete address-book listing with its links; contacts page through the source.

        A book the listing omits is reported absent on the listing's last page,
        so it pauses its contacts and retires them only once its absence is
        confirmed. An empty listing while books are known confirms nothing.
        """

        if stream.key != ADDRESSBOOKS:
            return self.extract_contacts(stream, page_bound, deadline=deadline)
        books = self.discover()
        changes = self._addressbook_changes(stream, books)
        exhausted = len(changes) <= page_bound
        absent = ()
        if exhausted and books:
            listed = {book.href for book in books}
            absent = tuple(
                stream.links.observing().exclude(external_key__in=listed).order_by("pk").values_list(
                    "external_key", flat=True
                )
            )
        return StreamPage(changes[:page_bound], {}, exhausted=exhausted, absent=absent)

    def read_keys(self, stream: Any, keys: Sequence[str]) -> Iterable[RecordChange]:
        """Read address books from the listing, or one book's contacts through the source."""

        if stream.key != ADDRESSBOOKS:
            return self.read_contacts(stream, keys)
        books = self.discover()
        if not books:
            raise IntegrationError("The address-book listing came back empty; retrying later.", transient=True)
        return self._addressbook_changes(stream, books, keys=keys)

    def extract_contacts(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Fetch one bounded page of an address book's contacts outside transactions."""

        raise NotImplementedError("DirectoryBackend subclasses must implement extract_contacts().")

    def read_contacts(self, stream: Any, keys: Sequence[str]) -> Iterable[RecordChange]:
        """Read each requested contact identity of one address book, including tombstones."""

        raise AdapterContractError("This directory backend does not support identity reads.")

    def _addressbook_changes(
        self, stream: Any, books: Iterable[ParsedAddressbook], *, keys: Sequence[str] | None = None
    ) -> list[RecordChange]:
        """Map the listing to address-book records, or to the requested identities.

        A baseline reads each listed book once per generation; later, only a
        new, renamed or reappearing book is a record. A requested identity the
        listing omits is a tombstone. The local side is what this source last
        applied: on a pull-only stream a person's rename is a stance of its own
        (a statement), never a local change.
        """

        links = {link.external_key: link for link in stream.links.with_sync_evidence().order_by("pk")}
        listed = {book.href: book for book in books}
        baseline = stream.phase == StreamPhase.BASELINE
        changes = []
        for href in sorted(listed) if keys is None else keys:
            book, link = listed.get(href), links.get(href)
            payload = {"href": href} if book is None else {"href": href, "name": book.name}
            if (
                keys is None
                and link is not None
                and link.status != LinkStatus.TOMBSTONE
                and not link.absence_count
                and link.source_payload == payload
                and (not baseline or link.last_verified_generation == stream.generation)
            ):
                continue
            target = link.target if link is not None else None
            projection = link.applied_base.mapped_payload if link is not None and link.applied_base else None
            changes.append(
                RecordChange(href, payload, "", tombstone=True, target=target, projection=projection)
                if book is None
                else RecordChange(
                    href,
                    payload,
                    canonical_json_sha256(payload),
                    mapped_payload={"name": book.name},
                    target=target,
                    projection=projection,
                )
            )
        return changes

    def _source_payload(self, link: Any) -> dict[str, Any]:
        return dict(link.source_payload)

    def _prepare_contact(self, parsed: ParsedContact) -> ParsedContact:
        """Store fetched media and take the stored form before the driver's page transaction begins."""

        return apps.get_model("parties", "Party").objects.prepare_contact(parsed)

    def _links(self, stream: Any) -> tuple[Any, ...]:
        """Read the latest retained locators directly from their owning links."""

        return tuple(
            apps.get_model("integrate", "RecordLink")
            .objects.filter(stream=stream)
            .annotate(
                source_href=Coalesce(
                    KeyTextTransform("href", "metadata"),
                    KeyTextTransform("href", "source_payload"),
                    output_field=CharField(),
                )
            )
            .order_by("pk")
        )

    def _local_states(
        self, stream: Any, keys: Iterable[str], *, links: Iterable[Any]
    ) -> dict[str, tuple[Any | None, dict[str, Any] | None]]:
        keys = set(keys)
        if not keys:
            return {}
        linked = {link.external_key: link for link in links if link.external_key in keys}
        parties = apps.get_model("parties", "Party").objects
        prefetch_related_objects(
            list(linked.values()),
            GenericPrefetch("target", [
                parties.model._base_manager.select_related("person"),
            ]),
        )
        targets = {key: parties.contact_person(link) for key, link in linked.items()}
        people = tuple(
            apps.get_model("parties", "Person")
            .objects.untrashed()
            .filter(pk__in=[person.pk for person in targets.values() if person is not None])
        )
        by_id = {str(person.pk): person for person in people}
        parsed = parties.project_contacts(people)
        projections = {pk: contact_projection(contact) for pk, contact in parsed.items()}
        states = {}
        for key in keys:
            target = targets.get(key)
            person = by_id.get(str(target.pk)) if target is not None else None
            projection = projections.get(person.pk) if person is not None else None
            states[key] = person, projection
        return states

    def _local_state(self, stream: Any, external_key: str) -> tuple[Any | None, dict[str, Any] | None]:
        links = apps.get_model("integrate", "RecordLink").objects.filter(stream=stream, external_key=external_key)
        return self._local_states(stream, (external_key,), links=links)[external_key]

    def _record_changes(
        self,
        stream: Any,
        contacts: Mapping[str, ParsedContact | RecordChange | None],
        *,
        requested_keys: Mapping[str, str] | None = None,
    ) -> list[RecordChange]:
        """Bind fetched contacts, preserving identities assigned before parsing.

        Identity reads supply href-to-key bindings, including unlinked hrefs and
        their tombstones. A parsed UID remains metadata on those identities.
        """

        if not contacts:
            return []
        links = self._links(stream)
        by_href = {link.source_href: link for link in links if link.source_href}
        occupied = {link.external_key: link.source_href for link in links}
        identities = {link.metadata.get("uid", link.external_key): link.source_href for link in links}
        records: dict[str, RecordChange] = {}
        for href, parsed in sorted(contacts.items()):
            prior = by_href.get(href)
            if prior is None and isinstance(parsed, ParsedContact) and requested_keys is None:
                owner = identities.get(parsed.uid)
                if owner is not None and owner in contacts and contacts[owner] is None:
                    prior = by_href.get(owner)
            if parsed is None and prior is None and requested_keys is None:
                continue
            if requested_keys is not None:
                key = requested_keys[href]
            elif prior is not None:
                key = prior.external_key
            else:
                key = parsed.uid if isinstance(parsed, ParsedContact) else href
            if parsed is None and prior is not None and occupied.get(key) != href:
                continue  # The same batch already relocated this UID.
            metadata = {**(prior.metadata if prior is not None else {}), "href": href}
            moved = False
            if isinstance(parsed, ParsedContact):
                owner = identities.get(parsed.uid)
                duplicate = owner and owner != href and not (owner in contacts and contacts[owner] is None)
                key_owner = occupied.get(key)
                moved = (
                    key_owner is not None
                    and key_owner in contacts
                    and contacts[key_owner] is None
                    and (key == parsed.uid or (prior is not None and prior.metadata.get("uid") == parsed.uid))
                )
                if duplicate or (key_owner not in (None, href) and not moved):
                    if requested_keys is None:
                        key = prior.external_key if prior is not None else href
                    parsed = RecordChange(
                        key,
                        {"href": href, "error": "duplicate_vcard_uid"},
                        canonical_json_sha256({"href": href, "error": "duplicate_vcard_uid"}),
                    )
                else:
                    metadata["uid"] = parsed.uid
                    identities[parsed.uid] = href
            if moved and isinstance(parsed, ParsedContact):
                occupied[key] = href
            key = self._claim_key(key, href, occupied=occupied)
            if isinstance(parsed, RecordChange):
                record = replace(
                    parsed, external_key=key, metadata=metadata, failure=SemanticError(parsed.source_payload["error"])
                )
            elif parsed is None:
                record = RecordChange(key, {"href": href}, "", tombstone=True, metadata=metadata)
            else:
                observed = contact_projection(parsed)
                record = RecordChange(
                    key,
                    {"href": href, "raw_vcard": parsed.raw_vcard, "contact": observed},
                    canonical_json_sha256(observed),
                    mapped_payload=observed,
                    remote_version=parsed.etag,
                    metadata=metadata,
                )
            # A live UID observation supersedes its old locator's tombstone.
            records[key] = record
        states = self._local_states(stream, records, links=links)
        return [
            replace(
                record,
                target=states[record.external_key][0],
                projection=states[record.external_key][1],
            )
            for record in records.values()
        ]

    def _claim_key(self, key: str, href: str, *, occupied: dict[str, str | None]) -> str:
        """Reserve a locator without overwriting a UID that happens to equal it."""

        while key in occupied and occupied[key] not in (None, href):
            key = f"href:{key}"
        occupied[key] = href
        return key

    def _href_for_key(self, key: str, *, bindings: Mapping[str, str | None]) -> str:
        """Resolve retained locators or decode a not-yet-observed inventory key."""

        if href := bindings.get(key):
            return href
        while key.startswith("href:"):
            key = key.removeprefix("href:")
        return key

    def apply_record(self, stream: Any, record: RecordChange) -> ApplyResult:
        """Revalidate under domain locks, then use the single folder or contact ingest verb.

        An address book states its folder's name, and its tombstone withdraws that
        statement while the folder stays. The fixed contact projection has no
        dependency digest. The driver stamps the observation's mapping version
        independently of the apply result.
        """

        if stream.key == ADDRESSBOOKS:
            links = apps.get_model("integrate", "RecordLink").objects
            link = links.get(stream=stream, external_key=record.external_key)
            if record.tombstone:
                link.withdraw_statements()
                return ApplyResult(mapped_payload=None)
            name = record.mapped_payload["name"]
            folder = apps.get_model("parties", "Folder").objects.ingest_addressbook(link, name=name)
            return ApplyResult(target=folder, mapped_payload={"name": name})
        parties = apps.get_model("parties", "Party").objects
        if record.source_payload.get("error"):
            raise SemanticError(record.source_payload["error"])
        parsed = (
            None
            if record.tombstone
            else replace(
                contact_from_projection(record.source_payload["contact"]),
                uid=record.external_key,
                etag=record.remote_version,
                raw_vcard=record.source_payload["raw_vcard"],
            )
        )
        parties.lock_contact(record.target, parsed=parsed)
        target, projection = self._local_state(stream, record.external_key)
        if record.tombstone:
            link = apps.get_model("integrate", "RecordLink").objects.get(
                stream=stream, external_key=record.external_key
            )
            link.withdraw_statements()
            projection = contact_projection(parties.project_contact(target)) if target is not None else None
        else:
            assert parsed is not None  # Only tombstones omit the parsed contact.
            target = parties.ingest_contact(
                parsed,
                stream=stream,
                target=target,
                created_by_id=self.bridge.owner_id,
                link=apps.get_model("integrate", "RecordLink").objects.get(
                    stream=stream, external_key=record.external_key
                ),
            )
            projection = contact_projection(parties.project_contact(target))
        return ApplyResult(
            target=target,
            mapped_payload=projection,
        )

    def local_changes(self, stream: Any, *, keys: frozenset[str] | None = None) -> Iterable[LocalChange]:
        """Compare selected local projections to their bases, including deletions."""

        stream_links = apps.get_model("integrate", "RecordLink").objects.filter(stream=stream)
        links_query = stream_links
        if keys is not None:
            links_query = links_query.filter(external_key__in=keys)
        links = tuple(links_query.with_sync_evidence().order_by("pk"))
        states = self._local_states(stream, (link.external_key for link in links), links=links)
        for link in links:
            target, projection = states[link.external_key]
            if link.local_changed(projection):
                yield LocalChange(link.external_key, projection, target=target)


class ManualDirectoryBackend(DirectoryBackend):
    """The null-object default: a directory with no source backend syncs nothing.

    Keeps ``ANGEE_DIRECTORY_BACKEND_CLASSES`` non-empty when no source addon is
    installed (``ImplClassField`` requires a non-empty registry), so the GraphQL
    enum is never empty and a new directory always has a selectable backend.
    """

    key = "manual"
    label = "Manual"

    def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
        """Declare nothing: a manual directory has no source to sync."""

        return ()
