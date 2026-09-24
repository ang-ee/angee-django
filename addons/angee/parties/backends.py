"""Directory replica mapping; integrate owns the loop and parties owns the fields.

Transport backends discover collections and extract neutral contacts. Both sides
compare the same deterministic projection; all inbound writes compose the parties
ingest owner. The manual backend declares no streams.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from typing import Any

from django.apps import apps
from django.db.models import CharField, OuterRef, Q, Subquery
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce

from angee.base.db import get_write_alias, refresh_deferred
from angee.base.serialization import canonical_json_sha256
from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import BridgeImpl
from angee.integrate.records import DiscrepancyKind, StreamDirection, StreamKind
from angee.integrate.streams import ApplyResult, LocalChange, RecordChange, SemanticError, StreamDefinition, StreamPage


@dataclass(frozen=True)
class ParsedPhoto:
    """A contact photo parsed from a source — inline bytes or a remote URI.

    The pure parse step decodes inline (base64 / data-URI) photos to ``data`` and
    records a ``uri`` for remote ones; the backend's transport step resolves any
    ``uri`` to ``data`` before preparation stores the bytes and replaces them
    with ``content_hash``. Only that immutable content address enters revisions.
    """

    data: bytes | None = None
    uri: str = ""
    mime: str = ""
    content_hash: str = ""


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


def contact_projection(contact: ParsedContact) -> dict[str, Any]:
    """Return the same JSON comparison shape for a remote card and a local person.

    Collection ordering is immaterial. Photos compare their content addresses.
    Source and local bases remain separate because domain fields (for example
    countries) may normalize the observed value.
    """

    result = {name: getattr(contact, name) for name in CONTACT_FIELDS}
    for name in ("birthday", "anniversary"):
        value = result[name]
        result[name] = value.isoformat() if value is not None else None
    for name in ("emails", "phones"):
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
    """One replica stream per address book, mapped through Party.ingest_contact."""

    category = "directory"
    label = "Directory"
    icon = "address-book"

    def probe(self, *, using: str | None = None) -> None:
        """Validate the source connection before a directory persists (no-op by default).

        A source backend overrides this to fail fast on a bad URL or rejected
        credentials, so the connect mutation never saves a directory that can never
        sync. It must raise on a bad connection and return ``None`` on success.
        """

        return None

    def discover(self, *, using: str | None = None) -> list[ParsedAddressbook]:
        """Return every address-book collection the source exposes."""

        raise NotImplementedError("DirectoryBackend subclasses must implement discover().")

    def streams(self, *, using: str | None = None) -> Iterable[StreamDefinition]:
        """Discover folders and seed per-collection policy on their first epoch."""

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        refresh_deferred(self.bridge, using=using, fields=("config", "owner_id"))
        folders = apps.get_model("parties", "Folder").objects.db_manager(using)
        policies = self.bridge.config.get("streams", {}).get("contacts", {})
        for book in sorted(self.discover(using=using), key=lambda item: item.href):
            folders.update_or_create(
                directory_id=self.bridge.pk,
                source_href=book.href,
                defaults={"name": book.name, "created_by_id": self.bridge.owner_id},
            )
            policy = {
                "local_delete": "conflict",
                "remote_delete": "retain",
                **policies.get(book.href, {}),
            }
            if policy["local_delete"] not in {"conflict", "propagate"} or policy["remote_delete"] not in {
                "retain",
                "propagate",
            }:
                raise ValueError("Unknown Directory deletion policy.")
            yield StreamDefinition(
                "contacts",
                partition=book.href,
                kind=StreamKind.RECORD_REPLICA,
                direction=StreamDirection.BIDIRECTIONAL,
                reconcile_interval=timedelta(days=1),
                config=policy,
            )

    def extract(self, stream: Any, page_bound: int, *, using: str | None = None) -> StreamPage:
        """Extract one protocol page; subclasses own tokens and resource versions."""

        raise NotImplementedError("Directory backends must implement extract().")

    def _folder(self, stream: Any, *, using: str) -> Any:
        return (
            apps.get_model("parties", "Folder")
            .objects.db_manager(using)
            .get(
                directory_id=self.bridge.pk,
                source_href=stream.partition,
            )
        )

    def _source_payload(self, link: Any, *, using: str) -> dict[str, Any]:
        revision = (
            apps.get_model("integrate", "RecordRevision")
            .objects.db_manager(using)
            .filter(
                link=link,
                applied_at__isnull=False,
            )
            .order_by("-number")
            .first()
        )
        return dict(revision.source_payload) if revision is not None else {}

    def _prepare_contact(self, parsed: ParsedContact, *, using: str) -> ParsedContact:
        """Store fetched media before the driver's page transaction begins."""

        refresh_deferred(self.bridge, using=using, fields=("owner_id",))
        return apps.get_model("parties", "Party").objects.db_manager(using).prepare_contact(
            parsed, created_by_id=self.bridge.owner_id, using=using
        )

    def _links(self, stream: Any, *, using: str) -> tuple[Any, ...]:
        """Read locators in one query, including locally pushed revision evidence."""

        latest = (
            apps.get_model("integrate", "RecordRevision")
            .objects.db_manager(using)
            .filter(link_id=OuterRef("pk"), applied_at__isnull=False)
            .order_by("-number")
            .annotate(href=KeyTextTransform("href", "source_payload"))
            .values("href")[:1]
        )
        return tuple(
            apps.get_model("integrate", "RecordLink")
            .objects.db_manager(using)
            .filter(stream=stream)
            .annotate(
                source_href=Coalesce(
                    KeyTextTransform("href", "metadata"), Subquery(latest), output_field=CharField()
                )
            )
            .order_by("pk")
        )

    def _local_states(
        self, stream: Any, keys: Iterable[str], *, links: Iterable[Any], using: str
    ) -> dict[str, tuple[Any, Any, str]]:
        keys = set(keys)
        if not keys:
            return {}
        linked = {link.external_key: link for link in links if link.external_key in keys}
        people = tuple(
            apps.get_model("parties", "Person")
            .objects.db_manager(using)
            .filter(
                Q(pk__in=[link.target_id for link in linked.values() if link.target_id]) | Q(source_uid__in=keys),
                folder=self._folder(stream, using=using),
            )
        )
        by_id = {str(person.pk): person for person in people}
        by_uid = {person.source_uid: person for person in people}
        parsed = apps.get_model("parties", "Party").objects.db_manager(using).project_contacts(people, using=using)
        projections = {pk: contact_projection(contact) for pk, contact in parsed.items()}
        states = {}
        for key in keys:
            link = linked.get(key)
            person = by_id.get(str(link.target_id)) if link is not None else None
            if person is None:
                person = by_uid.get(key)
            projection = projections.get(person.pk) if person is not None else None
            states[key] = (person, projection, canonical_json_sha256(projection) if projection is not None else "")
        return states

    def _local_state(self, stream: Any, external_key: str, *, using: str) -> tuple[Any, Any, str]:
        links = apps.get_model("integrate", "RecordLink").objects.db_manager(using).filter(
            stream=stream, external_key=external_key
        )
        return self._local_states(stream, (external_key,), links=links, using=using)[external_key]

    def _record_changes(
        self,
        stream: Any,
        contacts: Mapping[str, ParsedContact | RecordChange | None],
        *,
        requested_keys: Mapping[str, str] | None = None,
        using: str,
    ) -> list[RecordChange]:
        """Bind fetched contacts, preserving identities assigned before parsing.

        Identity reads supply href-to-key bindings, including unlinked hrefs and
        their tombstones. A parsed UID remains metadata on those identities.
        """

        if not contacts:
            return []
        links = self._links(stream, using=using)
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
                record = replace(parsed, external_key=key, metadata=metadata)
            elif parsed is None:
                record = RecordChange(key, {"href": href}, "", tombstone=True, metadata=metadata)
            else:
                observed = contact_projection(parsed)
                record = RecordChange(
                    key,
                    {"href": href, "raw_vcard": parsed.raw_vcard, "contact": observed},
                    canonical_json_sha256(observed),
                    remote_version=parsed.etag,
                    metadata=metadata,
                )
            # A live UID observation supersedes its old locator's tombstone.
            records[key] = record
        states = self._local_states(stream, records, links=links, using=using)
        return [
            replace(
                record,
                target=states[record.external_key][0],
                projection=states[record.external_key][1],
                local_hash=states[record.external_key][2],
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

    def apply(self, stream: Any, page: StreamPage, *, using: str | None = None) -> Iterable[ApplyResult]:
        """Revalidate under domain locks, then use the single contact ingest verb.

        The fixed contact projection has mapping version 1 and no dependency
        digest; ApplyResult's defaults are the evidence actually applied.
        """

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        refresh_deferred(self.bridge, using=using, fields=("owner_id",))
        parties = apps.get_model("parties", "Party").objects.db_manager(using)
        for record in page.records:
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
            parties.lock_contact(record.target, parsed=parsed, using=using)
            target, projection, local_hash = self._local_state(stream, record.external_key, using=using)
            if local_hash != record.local_hash:
                raise SemanticError("local_changed_during_pull", kind=DiscrepancyKind.CONFLICT)
            if record.tombstone:
                if target is not None and stream.config.get("remote_delete", "retain") == "propagate":
                    parties.filter(pk=target.pk).delete()
                    target, projection, local_hash = None, None, ""
            else:
                target = parties.ingest_contact(
                    parsed,
                    folder=self._folder(stream, using=using),
                    target=target,
                    created_by_id=self.bridge.owner_id,
                    using=using,
                )
                projection = contact_projection(parties.project_contact(target, using=using))
                local_hash = canonical_json_sha256(projection)
            yield ApplyResult(
                record.external_key,
                target=target,
                local_hash=local_hash,
                mapped_payload=projection,
            )

    def local_changes(self, stream: Any, *, using: str | None = None) -> Iterable[LocalChange]:
        """Compare every linked local projection to its base, including deletions."""

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        links = tuple(
            apps.get_model("integrate", "RecordLink").objects.db_manager(using).filter(stream=stream).order_by("pk")
        )
        states = self._local_states(stream, (link.external_key for link in links), links=links, using=using)
        for link in links:
            target, projection, local_hash = states[link.external_key]
            if local_hash != link.local_base_hash:
                yield LocalChange(link.external_key, projection, local_hash, target=target)
        people = tuple(
            apps.get_model("parties", "Person")
            .objects.db_manager(using)
            .filter(
                folder=self._folder(stream, using=using),
            )
            .exclude(pk__in=[link.target_id for link in links if link.target_id])
            .exclude(
                source_uid__in=[link.external_key for link in links],
            )
            .order_by("pk")
        )
        parties = apps.get_model("parties", "Party").objects.db_manager(using)
        parsed = parties.project_contacts(people, using=using)
        for person in people:
            projection = contact_projection(parsed[person.pk])
            yield LocalChange(
                person.source_uid or f"angee-{person.pk}",
                projection,
                canonical_json_sha256(projection),
                target=person,
            )

    def close(self) -> None:
        """The shared HTTP client closes each request's transport itself."""


class ManualDirectoryBackend(DirectoryBackend):
    """The null-object default: a directory with no source backend syncs nothing.

    Keeps ``ANGEE_DIRECTORY_BACKEND_CLASSES`` non-empty when no source addon is
    installed (``ImplClassField`` requires a non-empty registry), so the GraphQL
    enum is never empty and a new directory always has a selectable backend.
    """

    key = "manual"
    label = "Manual"

    def discover(self, *, using: str | None = None) -> list[ParsedAddressbook]:
        """Return no address books — a manual directory is populated by hand."""

        return []
