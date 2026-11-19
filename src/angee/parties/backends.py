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


def contact_points_projection(value: ParsedContact | ParsedOrganization) -> dict[str, Any]:
    """Return the comparison shape of a contact's or an organization's emails, phones and addresses.

    Emails and phones compare as sets (:data:`SET_FIELDS`) and addresses as a
    list in canonical order, so collection ordering is immaterial.
    """

    result: dict[str, Any] = {name: [list(item) for item in sorted(set(getattr(value, name)))] for name in SET_FIELDS}
    result["addresses"] = sorted((asdict(address) for address in value.addresses), key=canonical_json_sha256)
    return result


def contact_projection(contact: ParsedContact) -> dict[str, Any]:
    """Return the same JSON comparison shape for a remote card and a local person.

    Collection ordering is immaterial (:func:`contact_points_projection`). Photos
    compare their content addresses. A fetched contact arrives in its stored form
    (``prepare_contact``), so a country a source spells out compares equal to the
    stored code.
    """

    result = {name: getattr(contact, name) for name in CONTACT_FIELDS}
    for name in ("birthday", "anniversary"):
        value = result[name]
        result[name] = value.isoformat() if value is not None else None
    result.update(contact_points_projection(contact))
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

    Its contact points and addresses compare exactly as a contact's do
    (:func:`contact_points_projection`). A fetched organization arrives in its
    stored form, as a contact does (``prepare_contact``).
    """

    return {
        "display_name": organization.display_name,
        "legal_name": organization.legal_name,
        "domain": organization.domain,
        **contact_points_projection(organization),
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
        return self._contact_records(stream, {key: payloads.get(key) for key in keys})

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

    def observe(self, contact: ParsedContact, *, replaces: Iterable[str] = ()) -> dict[str, Any]:
        """Return the retained source payload of one contact, in the form parties stores.

        The contact takes its stored form first (photo stored, addresses as
        stored: ``PartyManager.prepare_contact``), so an unchanged source compares
        unchanged. ``replaces`` names the keys of the records a merge at the source
        folded into this one (:meth:`record_changes`); the payload keeps them in
        key order. Call it while extracting, outside any transaction.
        """

        payload: dict[str, Any] = {"contact": contact_projection(self._prepare_contact(contact))}
        if replaced := sorted(set(replaces)):
            payload["replaces"] = replaced
        return payload

    def record_changes(self, stream: Any, payloads: Mapping[str, Mapping[str, Any] | None]) -> list[RecordChange]:
        """Turn one page's payloads, keyed by external key, into records bound to their linked people.

        ``None`` is a record the source removed (a tombstone). A payload carrying
        ``error`` is one the backend could not map; the driver quarantines it under
        its key. Any other payload is one :meth:`observe` made: its ``contact``
        projection is the remote side, and the source hash covers the whole payload.

        A payload's ``replaces`` names the records a merge at the source folded into
        it. Each one with a link follows its survivor in the page as a tombstone: the
        survivor lands first, on the person a replaced link names
        (:meth:`_contact_target`), and the replaced link's statements then move to the
        survivor's link (``RecordLink.withdraw_statements``). Identity reads keep only
        the keys they were asked for (:meth:`read_keys`).
        """

        ordered: dict[str, Mapping[str, Any] | None] = {}
        for key, payload in payloads.items():
            ordered[key] = payload
            for replaced in (payload or {}).get("replaces", ()):
                if replaced not in payloads:
                    ordered.setdefault(replaced, None)
        followers = [key for key in ordered if key not in payloads]
        linked = set(stream.links.filter(external_key__in=followers).values_list("external_key", flat=True))
        return self._contact_records(
            stream, {key: payload for key, payload in ordered.items() if key in payloads or key in linked}
        )

    def _contact_records(self, stream: Any, payloads: Mapping[str, Mapping[str, Any] | None]) -> list[RecordChange]:
        """Build one record per payload, each bound to its linked person and the local side of its comparison."""

        links = stream.links.filter(external_key__in=list(payloads)).with_sync_evidence()
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
        """Return each key's linked person and the local side of its comparison, read once for the batch.

        A two-way stream compares the person's projection. A pull-only stream
        compares what this source last applied (the link's applied base), as the
        address-book stream does: a person's own edit is a statement that outranks
        the source, never a local change, and an unchanged re-read applies nothing.
        ``links`` carry their sync evidence on a pull-only stream. A key without a
        link has neither; a trashed person is no target.
        """

        keys = set(keys)
        if not keys:
            return {}
        linked = [link for link in links if link.external_key in keys]
        targets = self._contact_targets(linked)
        if stream.direction == StreamDirection.PULL:
            applied = {link.external_key: link.applied_base.mapped_payload for link in linked if link.applied_base_id}
            return {key: (targets.get(key), applied.get(key)) for key in keys}
        people = [person for person in targets.values() if person is not None]
        parsed = apps.get_model("parties", "Party").objects.project_contacts(people)
        projections = {pk: contact_projection(contact) for pk, contact in parsed.items()}
        states = {}
        for key in keys:
            person = targets.get(key)
            states[key] = person, projections.get(person.pk) if person is not None else None
        return states

    def _contact_targets(self, links: Sequence[Any]) -> dict[str, Any | None]:
        """Return each link's untrashed person by external key, read once for the batch; ``None`` while unbound."""

        parties = apps.get_model("parties", "Party").objects
        prefetch_related_objects(
            list(links),
            GenericPrefetch("target", [
                parties.model._base_manager.select_related("person"),
            ]),
        )
        targets = {link.external_key: parties.contact_person(link) for link in links}
        people = (
            apps.get_model("parties", "Person")
            .objects.untrashed()
            .filter(pk__in=[person.pk for person in targets.values() if person is not None])
        )
        by_id = {str(person.pk): person for person in people}
        return {key: by_id.get(str(person.pk)) if person is not None else None for key, person in targets.items()}

    def _contact_target(self, stream: Any, record: RecordChange) -> Any | None:
        """Return the person a contact record lands on: the first one this resolution order finds.

        Its own link's person; then, for a merge at the source, the person a link
        it replaces names (``replaces`` in its payload, in that order). Matching
        an existing person through its handles would be a later step of this
        order; it is not one yet.
        """

        keys = [record.external_key, *record.source_payload.get("replaces", ())]
        targets = self._contact_targets(tuple(stream.links.filter(external_key__in=keys)))
        return next((targets[key] for key in keys if targets.get(key) is not None), None)

    def apply_record(self, stream: Any, record: RecordChange) -> ApplyResult:
        """Revalidate under domain locks, then use the single folder or contact ingest verb.

        An address book states its folder's name, and its tombstone withdraws that
        statement while the folder stays. A contact lands through
        ``PartyManager.ingest_contact`` from its payload's ``contact`` projection,
        on the person :meth:`_contact_target` resolves under the contact locks, and
        its tombstone withdraws the link's statements while the party stays. A
        two-way stream's applied base is the person's projection; a pull-only
        stream's is the source's own (none for a tombstone), the side its
        comparison reads (:meth:`_local_states`). The fixed contact projection has
        no dependency digest. The driver stamps the observation's mapping version
        independently of the apply result.
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
        parties.lock_contact(self._contact_target(stream, record), parsed=parsed)
        target = self._contact_target(stream, record)
        if parsed is None:
            link.withdraw_statements()
        else:
            target = parties.ingest_contact(
                parsed, stream=stream, target=target, created_by_id=self.bridge.owner_id, link=link
            )
        if stream.direction == StreamDirection.PULL:
            return ApplyResult(target=target, mapped_payload=None if parsed is None else record.mapped_payload)
        return ApplyResult(
            target=target,
            mapped_payload=contact_projection(parties.project_contact(target)) if target is not None else None,
        )

    def on_absent(self, stream: Any, links: Sequence[Any]) -> dict[Any, ApplyResult] | None:
        """A confirmed removal withdraws the statements its link made, as a read tombstone does.

        An address book and a pull-only stream's contact report each withdrawal as a
        read tombstone's evidence (no projection, the folder or party kept), so a
        record that returns applies and states its facts again. An unavailable
        record keeps them. A two-way stream's contacts keep the sweep's existing
        absence behaviour: the link is tombstoned and its statements stay.
        """

        contacts = stream.key == CONTACTS
        if contacts and stream.direction != StreamDirection.PULL:
            return None
        gone = [link for link in links if link.status == LinkStatus.TOMBSTONE]
        people = self._contact_targets(gone) if contacts else {}
        parties = apps.get_model("parties", "Party").objects
        withdrawn: dict[Any, ApplyResult] = {}
        for link in gone:
            if contacts:
                parties.lock_contact(people.get(link.external_key))
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
