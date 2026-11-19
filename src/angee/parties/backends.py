"""Directory replica mapping; integrate owns the loop and parties owns the fields.

A source backend discovers its address books and reads each one's contacts as
neutral payloads keyed by external key. The ``addressbooks`` stream mirrors each
discovered book as a folder, and each book's link is the container of its
``contacts`` stream, which syncs the way the backend declares
(:attr:`DirectoryBackend.direction`). The base turns payloads into records and
applies them; both sides compare one deterministic projection, and every inbound
write composes the parties ingest owners. The manual backend declares no streams.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, ClassVar, cast

from django.apps import apps
from django.contrib.contenttypes.prefetch import GenericPrefetch
from django.db.models import prefetch_related_objects

from angee.base.serialization import canonical_json_sha256
from angee.integrate.errors import IntegrationError
from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import BridgeImpl
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
    """An address book's stable key in its source and its display name; progress belongs to SyncStream.

    ``key`` is the book's record key in the ``addressbooks`` stream and the
    partition of its contacts stream.
    """

    key: str
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

    Its source identity is its link's external key, never a field here; a backend
    that keeps wire facts beside a contact subclasses it. Emails/phones are
    ``(value, label, is_preferred)`` triples. ``organization``/``title``/``role``
    carry the employment (the map folds them onto an ``employee``
    ``Relationship``); ``birthday``/``anniversary`` are resolved dates; ``photo``
    is the avatar.
    """

    display_name: str = ""
    name_prefix: str = ""
    given_name: str = ""
    additional_name: str = ""
    family_name: str = ""
    name_suffix: str = ""
    nickname: str = ""
    notes: str = ""
    organization: str = ""
    title: str = ""
    role: str = ""
    birthday: date | None = None
    anniversary: date | None = None
    emails: tuple[tuple[str, str, bool], ...] = ()
    phones: tuple[tuple[str, str, bool], ...] = ()
    addresses: tuple[ParsedAddress, ...] = ()
    photo: ParsedPhoto | None = None


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
"""The exact mapping both sync sides compare; a source's identity and wire facts stay outside it."""

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


@dataclass(frozen=True)
class ParsedOrganization:
    """One organization parsed from a directory source, neutral of the wire format.

    The organization counterpart of :class:`ParsedContact`, landed by
    ``PartyManager.ingest_organization``; its link owns the source identity.
    ``legal_name`` and ``domain`` are its legal identity, stated together.
    Emails/phones are ``(value, label, is_preferred)`` triples.
    """

    display_name: str = ""
    legal_name: str = ""
    domain: str = ""
    emails: tuple[tuple[str, str, bool], ...] = ()
    phones: tuple[tuple[str, str, bool], ...] = ()
    addresses: tuple[ParsedAddress, ...] = ()


def organization_projection(organization: ParsedOrganization) -> dict[str, Any]:
    """Return the JSON comparison shape of a source organization, as :func:`contact_projection` does a contact's.

    Its contact points and addresses compare exactly as a contact's do, so that
    projection orders them.
    """

    points = contact_projection(
        ParsedContact(emails=organization.emails, phones=organization.phones, addresses=organization.addresses)
    )
    return {
        "display_name": organization.display_name,
        "legal_name": organization.legal_name,
        "domain": organization.domain,
        **{name: points[name] for name in (*SET_FIELDS, "addresses")},
    }


class DirectoryBackend(BridgeImpl, HttpClientMixin):
    """An address-book stream, and one contacts replica per address book mapped through Party.ingest_contact.

    A source backend implements :meth:`discover` for its address books and, for
    one book's contacts, :meth:`extract_contacts` (a page it builds with
    :meth:`record_changes`), :meth:`read_contacts` (payloads by key) and
    :meth:`contact_keys` (the keys it serves, for integrate's absence sweep); it
    makes each payload with :meth:`observe`. This base routes the
    ``addressbooks`` stream itself, builds every record and applies it. A backend
    that maps a wire identity of its own onto the keys builds its records itself,
    binds them through :meth:`_local_states`, and reads keys through its own
    :meth:`read_keys`.
    """

    registry_setting = "ANGEE_DIRECTORY_BACKEND_CLASSES"

    category = "directory"
    label = "Directory"
    icon = "address-book"

    direction: ClassVar[StreamDirection] = StreamDirection.PULL
    """Which way this backend's contacts sync.

    ``PULL`` (the default) reads its source; ``BIDIRECTIONAL`` also writes local
    edits back through integrate's ``write_back``.
    """

    supports_identity_reads: ClassVar[bool] = True
    """Both streams re-read identities (:meth:`read_keys`), so a retried or swept record reads its source."""

    def test_connection(self) -> str:
        """Prove this backend reaches its source; return the operator message.

        ``Directory.test_connection`` forwards here, and a connect flow calls it on
        an unsaved directory before saving one that could never sync. A source
        backend overrides it with a real handshake; the default proves only the
        directory's credential (``Integration.probe_credential``). Failures raise
        ``IntegrationError`` with the operator-safe reason.
        """

        return self.bridge.probe_credential()

    def admit_credential(self, credential: Any) -> None:
        """Refuse a credential this backend cannot use, raising ``ValidationError`` on ``credential``.

        ``Directory.admit_credential`` asks on every attachment. The base admits
        any credential.
        """

        del credential

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

        return StreamDefinition(ADDRESSBOOKS, kind=StreamKind.RECORD_REPLICA, holds_containers=True)

    def contacts_stream(self, addressbook: Any) -> StreamDefinition:
        """Declare the contacts stream an address-book link holds, in :attr:`direction`, writing nothing.

        The partition is the address book's key, the link's external key.
        Integrate's absence sweep reads it daily (:meth:`contact_keys`).
        """

        return StreamDefinition(
            CONTACTS,
            partition=addressbook.external_key,
            kind=StreamKind.RECORD_REPLICA,
            direction=self.direction,
            reconcile_interval=timedelta(days=1),
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
            listed = {book.key for book in books}
            absent = tuple(
                stream.links.observing().exclude(external_key__in=listed).order_by("pk").values_list(
                    "external_key", flat=True
                )
            )
        return StreamPage(changes[:page_bound], {}, exhausted=exhausted, absent=absent)

    def read_keys(self, stream: Any, keys: Sequence[str]) -> Iterable[RecordChange]:
        """Re-read the requested identities, each once, a missing one as a tombstone (integrate's identity reads).

        Address books read from the listing; contacts through :meth:`read_contacts`.
        """

        if stream.key == ADDRESSBOOKS:
            books = self.discover()
            if not books:
                raise IntegrationError("The address-book listing came back empty; retrying later.", transient=True)
            return self._addressbook_changes(stream, books, keys=keys)
        payloads = self.read_contacts(stream, keys)
        return self.record_changes(stream, {key: payloads.get(key) for key in keys})

    def enumerate_keys(self, stream: Any, *, after: str | None = None) -> Iterable[str]:
        """Return the contact keys the source serves now, seeking past ``after`` (integrate's absence sweep).

        The backend's :meth:`contact_keys` owns the order and the seek; nothing
        here lists or sorts the inventory. The address-book stream never sweeps:
        its listing reports its own absences (:meth:`extract`).
        """

        return self.contact_keys(stream, after=after)

    def extract_contacts(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Fetch one bounded page of an address book's contacts outside transactions (:meth:`record_changes`)."""

        raise NotImplementedError("DirectoryBackend subclasses must implement extract_contacts().")

    def read_contacts(self, stream: Any, keys: Sequence[str]) -> Mapping[str, Mapping[str, Any] | None]:
        """Read the current payload of each requested contact key, ``None`` for one the source lacks.

        Each payload is one :meth:`observe` made, or ``{"error": <code>}`` for a
        record the backend cannot map.
        """

        raise NotImplementedError("DirectoryBackend subclasses must implement read_contacts().")

    def contact_keys(self, stream: Any, *, after: str | None) -> Iterable[str]:
        """Yield every contact key the address book ``stream.partition`` serves now, in a stable order after ``after``.

        Seek past ``after`` at the source rather than listing and sorting every key
        (``BridgeImpl.enumerate_keys``).
        """

        raise NotImplementedError("DirectoryBackend subclasses must implement contact_keys().")

    def observe(self, contact: ParsedContact) -> dict[str, Any]:
        """Return the retained source payload of one contact, in the form parties stores.

        The contact takes its stored form first (photo stored, addresses as
        stored: ``PartyManager.prepare_contact``), so an unchanged source compares
        unchanged. Call it while extracting, outside any transaction.
        """

        return {"contact": contact_projection(self._prepare_contact(contact))}

    def record_changes(self, stream: Any, payloads: Mapping[str, Mapping[str, Any] | None]) -> list[RecordChange]:
        """Turn payloads keyed by external key into records bound to their linked people and projections.

        ``None`` is a record the source removed (a tombstone). A payload carrying
        ``error`` is one the backend could not map; the driver quarantines it under
        its key. Any other payload is one :meth:`observe` made: its ``contact``
        projection is the remote side, and the source hash covers the whole payload.
        """

        links = stream.links.filter(external_key__in=list(payloads))
        states = self._local_states(stream, payloads, links=links)
        records = []
        for key, payload in payloads.items():
            target, projection = states[key]
            if payload is None:
                records.append(RecordChange(key, {}, "", tombstone=True, target=target, projection=projection))
            elif "error" in payload:
                records.append(
                    RecordChange(
                        key, dict(payload), canonical_json_sha256(payload), failure=SemanticError(payload["error"])
                    )
                )
            else:
                records.append(
                    RecordChange(
                        key,
                        dict(payload),
                        canonical_json_sha256(payload),
                        mapped_payload=payload["contact"],
                        target=target,
                        projection=projection,
                    )
                )
        return records

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

        rows = stream.links.with_sync_evidence(source_payload=True).order_by("pk")
        links = {link.external_key: link for link in rows}
        listed = {book.key: book for book in books}
        baseline = stream.phase == StreamPhase.BASELINE
        changes = []
        for key in sorted(listed) if keys is None else keys:
            book, link = listed.get(key), links.get(key)
            payload = {"key": key} if book is None else {"key": key, "name": book.name}
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
                RecordChange(key, payload, "", tombstone=True, target=target, projection=projection)
                if book is None
                else RecordChange(
                    key,
                    payload,
                    canonical_json_sha256(payload),
                    mapped_payload={"name": book.name},
                    target=target,
                    projection=projection,
                )
            )
        return changes

    def _prepare_contact[C: ParsedContact](self, parsed: C) -> C:
        """Store fetched media and take the stored form before the driver's page transaction begins.

        A backend's subclass of the contact comes back as itself (the manager
        rewrites it with ``dataclasses.replace``), wire facts kept.
        """

        return cast(C, apps.get_model("parties", "Party").objects.prepare_contact(parsed))

    def _local_states(
        self, stream: Any, keys: Iterable[str], *, links: Iterable[Any]
    ) -> dict[str, tuple[Any | None, dict[str, Any] | None]]:
        """Return each key's linked person and local projection, read once for the batch from ``links``.

        A key without a link, or whose person is trashed, has neither.
        """

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

    def apply_record(self, stream: Any, record: RecordChange) -> ApplyResult:
        """Revalidate under domain locks, then use the single folder or contact ingest verb.

        An address book states its folder's name, and its tombstone withdraws that
        statement while the folder stays. A contact lands through
        ``PartyManager.ingest_contact`` from its payload's ``contact`` projection,
        and its tombstone withdraws the link's statements while the party stays.
        The fixed contact projection has no dependency digest. The driver stamps
        the observation's mapping version independently of the apply result.
        """

        links = apps.get_model("integrate", "RecordLink").objects
        link = links.get(stream=stream, external_key=record.external_key)
        if stream.key == ADDRESSBOOKS:
            if record.tombstone:
                link.withdraw_statements()
                return ApplyResult(mapped_payload=None)
            name = record.mapped_payload["name"]
            folder = apps.get_model("parties", "Folder").objects.ingest_addressbook(link, name=name)
            return ApplyResult(target=folder, mapped_payload={"name": name})
        parties = apps.get_model("parties", "Party").objects
        parsed = None if record.tombstone else contact_from_projection(record.source_payload["contact"])
        parties.lock_contact(record.target, parsed=parsed)
        target, _projection = self._local_state(stream, record.external_key)
        if parsed is None:
            link.withdraw_statements()
        else:
            target = parties.ingest_contact(
                parsed, stream=stream, target=target, created_by_id=self.bridge.owner_id, link=link
            )
        return ApplyResult(
            target=target,
            mapped_payload=contact_projection(parties.project_contact(target)) if target is not None else None,
        )

    def on_absent(self, stream: Any, links: Sequence[Any]) -> dict[Any, ApplyResult] | None:
        """A confirmed address-book removal withdraws the statements its link made, as a read tombstone does.

        Each withdrawal is reported as a read tombstone's evidence (no projection, the
        folder kept), so a book that returns applies and states its name again. An
        unavailable book keeps them; contacts keep the sweep's existing absence behaviour.
        """

        if stream.key != ADDRESSBOOKS:
            return None
        withdrawn: dict[Any, ApplyResult] = {}
        for link in links:
            if link.status == LinkStatus.TOMBSTONE:
                link.withdraw_statements()
                withdrawn[link.pk] = ApplyResult(mapped_payload=None)
        return withdrawn

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
