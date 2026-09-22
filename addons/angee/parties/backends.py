"""Directory replica mapping; integrate owns the loop and parties owns the fields.

Transport backends discover collections and extract neutral contacts. Both sides
compare the same deterministic projection; all inbound writes compose the parties
ingest owner. The manual backend declares no streams.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any

from django.apps import apps
from django.db.models import Q

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
    ``uri`` to ``data`` before the map ingests it through the storage File owner.
    """

    data: bytes | None = None
    uri: str = ""
    mime: str = ""


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

    Collection ordering is immaterial. Photos compare their bytes, not mutable
    URLs or storage ids. Source and local bases remain separate because domain
    fields (for example countries) may normalize the observed value.
    """

    result = {name: getattr(contact, name) for name in CONTACT_FIELDS}
    for name in ("birthday", "anniversary"):
        value = result[name]
        result[name] = value.isoformat() if value is not None else None
    for name in ("emails", "phones"):
        result[name] = [list(item) for item in sorted(set(result[name]))]
    result["addresses"] = sorted((asdict(address) for address in contact.addresses), key=canonical_json_sha256)
    result["photo"] = (
        {"data": base64.b64encode(contact.photo.data).decode("ascii"), "mime": contact.photo.mime}
        if contact.photo is not None and contact.photo.data
        else None
    )
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
    values["photo"] = (
        ParsedPhoto(data=base64.b64decode(photo["data"], validate=True), mime=photo["mime"]) if photo else None
    )
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
                "field_ownership": {
                    "bidirectional": list(CONTACT_FIELDS),
                    "enforcement": "compare_to_base",
                },
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

    def _local_state(self, stream: Any, external_key: str, *, using: str) -> tuple[Any, Any, str]:
        links = apps.get_model("integrate", "RecordLink").objects.db_manager(using)
        link = links.filter(stream=stream, external_key=external_key).first()
        people = apps.get_model("parties", "Person").objects.db_manager(using)
        rows = people.filter(folder=self._folder(stream, using=using))
        person = rows.filter(pk=link.target_id).first() if link is not None and link.target_id else None
        if person is None:
            person = rows.filter(source_uid=external_key).first()
        if person is None:
            return None, None, ""
        parsed = apps.get_model("parties", "Party").objects.db_manager(using).project_contact(person, using=using)
        projection = contact_projection(parsed)
        return person, projection, canonical_json_sha256(projection)

    def _record_change(self, stream: Any, parsed: ParsedContact, *, using: str) -> RecordChange:
        prior = self._link_for_href(stream, parsed.href, using=using)
        key = prior.external_key if prior is not None else parsed.uid
        target, projection, local_hash = self._local_state(stream, key, using=using)
        observed = contact_projection(parsed)
        return RecordChange(
            key,
            {"href": parsed.href, "raw_vcard": parsed.raw_vcard, "contact": observed},
            canonical_json_sha256(observed),
            local_hash=local_hash,
            remote_version=parsed.etag,
            projection=projection,
            target=target,
            metadata={"href": parsed.href},
        )

    def _link_for_href(self, stream: Any, href: str, *, using: str) -> Any:
        """Resolve the observed locator, including quarantined cards without a UID."""

        links = (
            apps.get_model("integrate", "RecordLink")
            .objects.db_manager(using)
            .filter(
                Q(metadata__href=href) | Q(revisions__source_payload__href=href),
                stream=stream,
            )
            .distinct()
        )
        for link in links.order_by("pk"):
            observed_href = link.metadata.get("href")
            if observed_href == href or (
                observed_href is None and self._source_payload(link, using=using).get("href") == href
            ):
                return link
        return None

    def _removed_change(self, stream: Any, href: str, *, using: str) -> RecordChange | None:
        link = self._link_for_href(stream, href, using=using)
        if link is None:
            return None
        target, projection, local_hash = self._local_state(stream, link.external_key, using=using)
        return RecordChange(
            link.external_key,
            {"href": href},
            "",
            local_hash=local_hash,
            projection=projection,
            target=target,
            tombstone=True,
            metadata={"href": href},
        )

    def apply(self, stream: Any, page: StreamPage, *, using: str | None = None) -> Iterable[ApplyResult]:
        """Revalidate under domain locks, then use the single contact ingest verb."""

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
        for link in links:
            target, projection, local_hash = self._local_state(stream, link.external_key, using=using)
            if local_hash != link.local_base_hash:
                yield LocalChange(link.external_key, projection, local_hash, target=target)
        people = (
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
        for person in people:
            projection = contact_projection(parties.project_contact(person, using=using))
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
