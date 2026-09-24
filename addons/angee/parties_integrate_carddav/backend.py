"""CardDAV record replicas: RFC 6578 deltas and conditional vCard writes.

RFC 6764/6352 discovery creates one contacts stream per address book. Baselines
list and multiget; later pages use DAV:sync-collection and opaque sync tokens.
vobject owns vCard parsing/serialization, parties owns the synchronized projection
and ingest policy, and integrate owns bases, conflict classification and cursors.
Every HTTP request uses the shared SSRF-pinned client and the Integration owner's
Basic-auth credential. Writes use resource ETags with If-Match, never timestamps.

Auth is Basic only: HTTP Digest is not yet supported. A Digest server would need a
``DigestAuthCredentialHandler`` on the credential registry seam (the challenge /
response round-trip), which is deferred until a Digest-only source needs it.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from xml.sax.saxutils import escape

import vobject
from defusedxml import ElementTree
from django.apps import apps

from angee.base.db import get_write_alias, refresh_deferred, related_on
from angee.base.serialization import canonical_json_sha256
from angee.integrate.records import LinkStatus
from angee.integrate.streams import CursorInvalid, RecordChange, RemoteRejected, StreamPage, WriteBackResult
from angee.parties.backends import (
    DirectoryBackend,
    ParsedAddress,
    ParsedAddressbook,
    ParsedContact,
    ParsedPhoto,
    contact_from_projection,
    contact_projection,
)

_NS = {
    "d": "DAV:",
    "card": "urn:ietf:params:xml:ns:carddav",
}
_MULTIGET_CHUNK = 100
_PHOTO_CAP = 5 * 1024 * 1024
_REDIRECT_STATUSES = (301, 302, 307, 308)
# vCard's reserved year for a birthday/anniversary whose year is omitted (``--MMDD``).
_NO_YEAR_SENTINEL = 1604

_PRINCIPAL_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
)
_HOME_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
    "<d:prop><card:addressbook-home-set/></d:prop></d:propfind>"
)
_BOOKS_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:">'
    "<d:prop><d:resourcetype/><d:displayname/></d:prop></d:propfind>"
)
_LISTING_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:getetag/><d:getcontenttype/><d:resourcetype/></d:prop></d:propfind>"
)
_TOKEN_BODY = (
    '<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"><d:prop><d:sync-token/></d:prop></d:propfind>'
)


class CardDavError(Exception):
    """Raised when the CardDAV server returns an unexpected response."""


class CardDavDirectoryBackend(DirectoryBackend):
    """Discovers a CardDAV account's address books and fetches each one's vCards.

    ``config["server_url"]`` is the account/server URL; discovery finds the
    collections, so the operator never pastes an exact collection URL. The
    directory's Basic-auth credential authenticates.
    """

    key = "carddav"
    label = "CardDAV"
    icon = "address-book"
    defaults = {"vendor": "carddav"}

    # --- discovery (DirectoryBackend contract) ---

    def probe(self, *, using: str | None = None) -> None:
        """Run discovery once to validate the URL + credentials before persisting.

        Raises :class:`CardDavError` if the server URL is missing, unreachable, or
        rejects the credentials — so the connect mutation fails fast instead of
        saving a directory whose first sync would silently error. An empty (but
        reachable, authenticated) account is allowed.
        """

        using = get_write_alias(type(self.bridge), instance=self.bridge, using=using)
        if not self._base_url(using=using):
            raise CardDavError("A CardDAV server URL is required.")
        try:
            self.discover(using=using)
        except CardDavError:
            raise
        except Exception as error:  # noqa: BLE001 — surface any transport/SSRF failure as a connect error.
            raise CardDavError("Could not connect to the CardDAV server.") from error

    def discover(self, *, using: str | None = None) -> list[ParsedAddressbook]:
        """Resolve the account's principal → home-set → address-book collections."""

        using = get_write_alias(type(self.bridge), instance=self.bridge, using=using)
        base = self._base_url(using=using)
        if not base:
            return []
        principal = ""
        for candidate in (base, _well_known(base)):
            principal = self._first_href(candidate, _PRINCIPAL_BODY, ".//d:current-user-principal/d:href", using=using)
            if principal:
                break
        principal = principal or base
        home = self._first_href(principal, _HOME_BODY, ".//card:addressbook-home-set/d:href", using=using) or principal
        return self._enumerate(home, using=using)

    def extract(self, stream: Any, page_bound: int, *, using: str | None = None) -> StreamPage:
        """Fetch a bounded page; its cursor commits only alongside applied records.

        Baselines capture the token before listing, so concurrent edits replay
        on the next delta. Pending hrefs keep a large report durable across pages.
        """

        using = get_write_alias(type(self.bridge), instance=self.bridge, using=using)
        cursor = dict(stream.cursor or {})
        token = str(cursor.get("sync_token", ""))
        if "pending" in cursor:
            pending = cursor["pending"]
            next_token = cursor["next_token"]
            more = cursor.get("more", False)
        elif token:
            pending, next_token, more = self._sync_changes(stream.partition, token, using=using)
        else:
            next_token = self._collection_token(stream.partition, using=using)
            hrefs = self._list_vcard_hrefs(stream.partition, using=using)
            pending = [{"href": href, "removed": False} for href in hrefs]
            present = set(hrefs)
            for link in self._links(stream, using=using):
                href = link.source_href
                if link.status != LinkStatus.TOMBSTONE and href and href not in present:
                    pending.append({"href": href, "removed": True})
            more = False
        bound = max(1, min(page_bound, _MULTIGET_CHUNK))
        batch, remaining = pending[:bound], pending[bound:]
        contacts = self._multiget(
            stream.partition, [item["href"] for item in batch if not item["removed"]], using=using
        )
        for item in batch:
            if item["removed"]:
                contacts[item["href"]] = None
        changes = self._record_changes(stream, contacts, using=using)
        if remaining:
            next_cursor = {
                "sync_token": token,
                "next_token": next_token,
                "pending": remaining,
                "more": more,
            }
        else:
            next_cursor = {"sync_token": next_token}
        return StreamPage(changes, next_cursor, exhausted=not remaining and not more)

    def read_keys(self, stream: Any, keys: Sequence[str], *, using: str | None = None) -> list[RecordChange]:
        """Read bound identities or new inventory hrefs through the same mapper."""

        using = get_write_alias(type(self.bridge), instance=self.bridge, using=using)
        if not keys:
            return []
        bindings = {link.external_key: link.source_href for link in self._links(stream, using=using)}
        requested = {self._href_for_key(key, bindings=bindings): key for key in keys}
        hrefs = sorted(requested)
        if len(requested) != len(keys) or any(not urlsplit(href).netloc for href in hrefs):
            raise CardDavError("A requested CardDAV identity has no resource href.")
        contacts = {}
        for start in range(0, len(hrefs), _MULTIGET_CHUNK):
            contacts.update(self._multiget(stream.partition, hrefs[start : start + _MULTIGET_CHUNK], using=using))
        return self._record_changes(stream, contacts, requested_keys=requested, using=using)

    def enumerate_keys(self, stream: Any, *, after: str | None = None, using: str | None = None) -> list[str]:
        """Seek exclusively by href over every depth-one inventory member.

        Known hrefs keep their keys. New hrefs are identities before their UIDs
        can be parsed; the mapper retains that key and stores the UID as metadata.
        The committed link resolves continuation even if its href has vanished.
        """

        using = get_write_alias(type(self.bridge), instance=self.bridge, using=using)
        links = self._links(stream, using=using)
        occupied = {link.external_key: link.source_href for link in links}
        by_href = {link.source_href: link.external_key for link in links if link.source_href}
        after_href = self._href_for_key(after, bindings=occupied) if after is not None else None
        return [
            self._claim_key(by_href.get(href, href), href, occupied=occupied)
            for href in self._list_vcard_hrefs(stream.partition, using=using)
            if after_href is None or href > after_href
        ]

    def write_back(
        self, link: Any, projection: Any, *, expected_version: str, using: str | None = None
    ) -> WriteBackResult:
        """Preserve unmapped vCard properties and fence every write by its ETag."""

        using = get_write_alias(type(self.bridge), instance=self.bridge, using=using)
        refresh_deferred(link, using=using)
        stream = related_on(link, "stream", using=using)
        if stream is None:
            raise CardDavError("A CardDAV record link must belong to a stream.")
        if link.status == LinkStatus.TOMBSTONE:
            raise RemoteRejected("remote_deleted")
        source = self._source_payload(link, using=using)
        # Unchanged revalidation can relocate a card without a new revision.
        href = link.metadata.get("href") or source.get("href")
        if not href:
            href = urljoin(stream.partition.rstrip("/") + "/", quote(link.external_key, safe="") + ".vcf")
        headers = {"If-Match": expected_version} if expected_version else {"If-None-Match": "*"}
        if projection is None:
            if stream.config.get("local_delete", "conflict") != "propagate":
                raise RemoteRejected("local_deleted")
            if not expected_version:
                raise RemoteRejected("remote_version_missing")
            self._request("DELETE", href, "", headers=headers, using=using)
            return WriteBackResult("", "", source_payload={"href": href}, tombstone=True)
        if source and not expected_version:
            raise RemoteRejected("remote_version_missing")
        photo_data = None
        photo = contact_from_projection(projection).photo
        if photo is not None:
            file = (
                apps.get_model("parties", "Party")
                .objects.db_manager(using)
                .resolve_contact_photo(photo, using=using)
            )
            with file.open_stream() as content:
                photo_data = content.read()
        raw = _render_vcard(projection, source=source, uid=link.external_key, photo_data=photo_data)
        response = self._request(
            "PUT",
            href,
            raw,
            headers={**headers, "Content-Type": "text/vcard; charset=utf-8"},
            using=using,
        )
        etag = response.headers.get("etag", "")
        if not etag or etag.startswith("W/"):
            response = self._request("GET", href, "", using=using)
            raw = response.content.decode("utf-8")
            etag = response.headers.get("etag", "")
        if not etag or etag.startswith("W/"):
            raise CardDavError("The CardDAV server did not return a strong resource ETag.")
        # XML normalizes line endings during multiget; retain the same evidence
        # on push so our next pull does not append a formatting-only revision.
        raw = raw.replace("\r\n", "\n").strip()
        parsed = self._prepare_contact(
            self._resolve_photo(
                _parse_vcard(vobject.readOne(raw), etag=etag, href=href, raw=raw),
                collection=stream.partition,
                using=using,
            ),
            using=using,
        )
        observed = contact_projection(parsed)
        return WriteBackResult(
            etag,
            canonical_json_sha256(observed),
            {"href": href, "raw_vcard": parsed.raw_vcard, "contact": observed},
        )

    def _resolve_photo(self, contact: ParsedContact, *, collection: str, using: str) -> ParsedContact:
        """Bound PHOTO downloads to public addresses on the collection's origin."""

        photo = contact.photo
        if photo is None or photo.data is not None or not photo.uri:
            return contact
        if _origin(photo.uri) != _origin(collection):
            raise CardDavError("The vCard photo must share the address book's origin.")
        try:
            data = self.http.download_capped(
                photo.uri,
                cap=_PHOTO_CAP,
                allow_private=False,
                follow_redirects=False,
            )
        except Exception as error:  # noqa: BLE001 — do not promote a partial comparison base.
            raise CardDavError("The vCard photo could not be retrieved.") from error
        if not data:
            raise CardDavError("The vCard photo could not be retrieved.")
        return replace(contact, photo=ParsedPhoto(data=data, mime=photo.mime or "application/octet-stream"))

    # --- transport ---

    def _request(
        self,
        method: str,
        url: str,
        body: str,
        *,
        using: str,
        depth: str = "0",
        headers: dict[str, str] | None = None,
        cursor_request: bool = False,
        _hops: int = 0,
    ) -> Any:
        """Send DAV through the pinned client and translate conditional failures."""

        request_headers = {
            "Depth": depth,
            "Content-Type": "application/xml; charset=utf-8",
            **self._auth(using=using),
            **(headers or {}),
        }
        response = self.http.request(
            method, url, headers=request_headers, body=body.encode("utf-8"), allow_private=True
        )
        if response.status_code in _REDIRECT_STATUSES and _hops < 3:
            location = response.headers.get("location", "")
            if location:
                destination = urljoin(url, location)
                if _origin(destination) != _origin(url):
                    raise CardDavError("CardDAV redirects must retain the request origin.")
                return self._request(
                    method,
                    destination,
                    body,
                    depth=depth,
                    headers=headers,
                    cursor_request=cursor_request,
                    _hops=_hops + 1,
                    using=using,
                )
        if response.status_code == 412:
            raise RemoteRejected()
        if cursor_request:
            if response.status_code == 404:
                raise CursorInvalid()
            if response.status_code == 403:
                try:
                    invalid = _xml(response.content).find(".//d:valid-sync-token", _NS) is not None
                except ElementTree.ParseError:
                    invalid = False
                if invalid:
                    raise CursorInvalid()
        if not response.is_success:
            raise CardDavError(f"CardDAV {method} returned HTTP {response.status_code}.")
        return response

    def _auth(self, *, using: str) -> dict[str, str]:
        """Return the directory credential's auth headers (empty if unconfigured)."""

        # Connect intentionally passes an unsaved credential before any FK exists.
        credential = (
            self.bridge.credential if self.bridge._state.adding else related_on(self.bridge, "credential", using=using)
        )
        return credential.auth_headers() if credential is not None else {}

    def _base_url(self, *, using: str) -> str:
        """Return the configured server URL."""

        refresh_deferred(self.bridge, using=using, fields=("config",))
        return str((self.bridge.config or {}).get("server_url") or "").strip()

    # --- discovery helpers ---

    def _first_href(self, url: str, body: str, xpath: str, *, using: str) -> str:
        """PROPFIND ``url`` and return the first ``xpath`` href, absolutised, or ``""``."""

        try:
            response = self._request("PROPFIND", url, body, depth="0", using=using)
        except CardDavError:
            return ""
        node = _xml(response.content).find(xpath, _NS)
        return urljoin(url, node.text.strip()) if node is not None and node.text else ""

    def _enumerate(self, home: str, *, using: str) -> list[ParsedAddressbook]:
        """Return the address-book collections under ``home`` (Depth:1)."""

        response = self._request("PROPFIND", home, _BOOKS_BODY, depth="1", using=using)
        books: list[ParsedAddressbook] = []
        for resp in _xml(response.content).findall("d:response", _NS):
            if resp.find(".//d:resourcetype/card:addressbook", _NS) is None:
                continue
            href = _href(resp)
            if not href:
                continue
            books.append(
                ParsedAddressbook(
                    href=urljoin(home, href),
                    name=_text(resp, ".//d:displayname") or "Contacts",
                )
            )
        return sorted(books, key=lambda book: book.href)

    def _collection_token(self, collection: str, *, using: str) -> str:
        response = self._request("PROPFIND", collection, _TOKEN_BODY, cursor_request=True, using=using)
        token = _text(_xml(response.content), ".//d:sync-token")
        if not token:
            raise CardDavError("The CardDAV address book does not expose a DAV:sync-token.")
        return token

    def _list_vcard_hrefs(self, collection: str, *, using: str) -> list[str]:
        """Return the hrefs of the vCard resources in ``collection`` (Depth:1)."""

        response = self._request("PROPFIND", collection, _LISTING_BODY, depth="1", cursor_request=True, using=using)
        hrefs: set[str] = set()
        for resp in _xml(response.content).findall("d:response", _NS):
            href = urljoin(collection, _href(resp))
            if href.rstrip("/") == collection.rstrip("/") or resp.find(".//d:collection", _NS) is not None:
                continue
            if _status(resp) not in (0, 200):
                raise CardDavError("The CardDAV server could not enumerate an address-book member.")
            if _href(resp) and ("vcard" in _text(resp, ".//d:getcontenttype").lower() or _text(resp, ".//d:getetag")):
                hrefs.add(href)
        return sorted(hrefs)

    def _sync_changes(self, collection: str, token: str, *, using: str) -> tuple[list[dict[str, Any]], str, bool]:
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:sync-collection xmlns:d="DAV:">'
            f"<d:sync-token>{escape(token)}</d:sync-token><d:sync-level>1</d:sync-level>"
            "<d:prop><d:getetag/></d:prop></d:sync-collection>"
        )
        root = _xml(self._request("REPORT", collection, body, cursor_request=True, using=using).content)
        next_token = _text(root, "d:sync-token")
        if not next_token:
            raise CardDavError("The CardDAV sync report omitted its DAV:sync-token.")
        pending = []
        seen_hrefs = set()
        more = False
        for resp in root.findall("d:response", _NS):
            href = urljoin(collection, _href(resp))
            status = _status(resp)
            if href.rstrip("/") == collection.rstrip("/"):
                if status == 507:
                    more = True
                if status == 404:
                    raise CursorInvalid()
                if status not in (0, 200, 507):
                    raise CardDavError("The CardDAV sync report contains an unavailable collection.")
                continue
            if not _href(resp) or href in seen_hrefs or status not in (0, 200, 404):
                raise CardDavError("The CardDAV sync report contains an unavailable member.")
            seen_hrefs.add(href)
            pending.append({"href": href, "removed": status == 404})
        pending.sort(key=lambda item: (item["removed"], item["href"]))
        return pending, next_token, more

    def _multiget(
        self,
        collection: str,
        hrefs: list[str],
        *,
        using: str,
    ) -> dict[str, ParsedContact | RecordChange | None]:
        """Read each requested href or its explicit tombstone; never skip bad cards."""

        if not hrefs:
            return {}
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<card:addressbook-multiget xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
            "<d:prop><d:getetag/><card:address-data/></d:prop>"
            + "".join(f"<d:href>{escape(href)}</d:href>" for href in hrefs)
            + "</card:addressbook-multiget>"
        )
        response = self._request("REPORT", collection, body, depth="1", cursor_request=True, using=using)
        contacts: dict[str, ParsedContact | RecordChange | None] = {}
        for resp in _xml(response.content).findall("d:response", _NS):
            href = urljoin(collection, _href(resp))
            if href not in hrefs or href in contacts:
                raise CardDavError("The CardDAV multiget returned an unexpected resource.")
            if _status(resp) == 404:
                contacts[href] = None
                continue
            data = _text(resp, ".//card:address-data")
            etag = _text(resp, ".//d:getetag")
            if not data or not etag or _status(resp) not in (0, 200):
                raise CardDavError("The CardDAV multiget omitted a vCard or its ETag.")
            try:
                card = vobject.readOne(data)
                if card.name != "VCARD":
                    raise ValueError("The address object is not a vCard.")
                contact = _parse_vcard(card, etag=etag, href=href, raw=data)
            except Exception:  # noqa: BLE001 — the driver quarantines semantic input per record.
                payload = {"href": href, "source_digest": canonical_json_sha256(data), "error": "invalid_vcard"}
                contacts[href] = RecordChange(
                    href,
                    payload,
                    canonical_json_sha256(payload),
                    remote_version=etag,
                    metadata={"href": href},
                )
                continue
            contacts[href] = self._prepare_contact(
                self._resolve_photo(contact, collection=collection, using=using), using=using
            )
        if set(contacts) != set(hrefs):
            raise CardDavError("The CardDAV multiget omitted a requested resource.")
        return contacts


def _well_known(base: str) -> str:
    """Return ``{origin}/.well-known/carddav`` for a base URL."""

    parts = urlsplit(base)
    return urlunsplit((parts.scheme, parts.netloc, "/.well-known/carddav", "", ""))


def _origin(url: str) -> tuple[str, str | None, int | None]:
    """Compare URL origins including the effective port, never user information."""

    parts = urlsplit(url)
    port = parts.port if parts.port is not None else {"http": 80, "https": 443}.get(parts.scheme)
    return parts.scheme, parts.hostname, port


def _render_vcard(
    projection: dict[str, Any], *, source: dict[str, Any], uid: str, photo_data: bytes | None = None
) -> str:
    """Patch only changed parties projection fields, retaining UID and extensions."""

    raw = source.get("raw_vcard", "")
    card = vobject.readOne(raw) if raw else vobject.vCard()
    previous = source.get("contact", {})
    changed = {field for field in projection if projection[field] != previous.get(field)}
    contact = contact_from_projection(projection)
    if not _prop(card, "uid"):
        card.add("uid").value = uid

    def set_value(prop: str, value: Any) -> None:
        card.contents.pop(prop, None)
        if value is not None and value != "":
            card.add(prop).value = value

    for field, prop in (
        ("display_name", "fn"),
        ("nickname", "nickname"),
        ("notes", "note"),
        ("title", "title"),
        ("role", "role"),
    ):
        if field in changed:
            set_value(prop, getattr(contact, field))
    if not card.contents.get("fn"):
        card.add("fn").value = contact.display_name
    if not raw or changed.intersection({"name_prefix", "given_name", "additional_name", "family_name", "name_suffix"}):
        set_value(
            "n",
            vobject.vcard.Name(
                family=contact.family_name,
                given=contact.given_name,
                additional=contact.additional_name,
                prefix=contact.name_prefix,
                suffix=contact.name_suffix,
            ),
        )
    if "organization" in changed:
        units = list(getattr(getattr(card, "org", None), "value", []) or [""])
        units[0] = contact.organization
        set_value("org", units if any(units) else None)
    for field, prop in (("birthday", "bday"), ("anniversary", "anniversary")):
        if field in changed:
            value = getattr(contact, field)
            set_value(prop, value.isoformat() if value else None)
    for field, prop in (("emails", "email"), ("phones", "tel")):
        if field not in changed:
            continue
        card.contents.pop(prop, None)
        for value, label, preferred in getattr(contact, field):
            item = card.add(prop)
            item.value = value
            types = [label] if label else []
            if preferred:
                if _prop(card, "version") == "4.0":
                    item.params["PREF"] = ["1"]
                else:
                    types.append("PREF")
            if types:
                item.params["TYPE"] = types
    if "addresses" in changed:
        card.contents.pop("adr", None)
        for address in contact.addresses:
            item = card.add("adr")
            item.value = vobject.vcard.Address(
                box=address.po_box,
                extended=address.extended,
                street=address.street,
                city=address.city,
                region=address.region,
                code=address.postal_code,
                country=address.country,
            )
            if address.label:
                item.params["TYPE"] = [address.label]
    # The retained wire template excludes PHOTO; hydrate it only for the PUT.
    card.contents.pop("photo", None)
    if contact.photo is not None and photo_data is not None:
        photo = card.add("photo")
        photo.value = photo_data
        photo.params["ENCODING"] = ["b"]
        if contact.photo.mime:
            photo.params["TYPE"] = [contact.photo.mime.removeprefix("image/").upper()]
    return card.serialize()


def _xml(body: bytes) -> Any:
    """Parse a DAV multistatus body."""

    return ElementTree.fromstring(body, forbid_dtd=True, forbid_entities=True, forbid_external=True)


def _href(response_el: Any) -> str:
    """Return the ``d:href`` text of one multistatus response element, or ``""``."""

    node = response_el.find("d:href", _NS)
    return node.text.strip() if node is not None and node.text else ""


def _text(element: Any, xpath: str) -> str:
    """Return the stripped text at ``xpath`` under ``element``, or ``""``."""

    node = element.find(xpath, _NS)
    return (node.text or "").strip() if node is not None else ""


def _status(response_el: Any) -> int:
    """Return the resource status, or the first property status for live members."""

    value = _text(response_el, "d:status") or _text(response_el, "d:propstat/d:status")
    parts = value.split()
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0


def _parse_vcard(card: Any, *, etag: str, href: str, raw: str) -> ParsedContact:
    """Map one parsed vCard into a neutral :class:`ParsedContact`.

    ``uid`` falls back to the resource ``href`` when the server omits ``UID``.
    PHOTO bytes travel separately and never enter the retained wire template.
    """

    name = getattr(getattr(card, "n", None), "value", None)
    org_values = getattr(getattr(card, "org", None), "value", None) or []
    photo = _parse_photo(getattr(card, "photo", None))
    if card.contents.pop("photo", None):
        raw = card.serialize().replace("\r\n", "\n").strip()
    return ParsedContact(
        uid=_prop(card, "uid") or href,
        etag=etag,
        href=href,
        display_name=_prop(card, "fn"),
        name_prefix=str(getattr(name, "prefix", "") or ""),
        given_name=str(getattr(name, "given", "") or ""),
        additional_name=str(getattr(name, "additional", "") or ""),
        family_name=str(getattr(name, "family", "") or ""),
        name_suffix=str(getattr(name, "suffix", "") or ""),
        nickname=_prop(card, "nickname"),
        notes=_prop(card, "note"),
        # vCard ORG is structured "Org;Unit;…" — the first part is the org, the
        # second its department.
        organization=str(org_values[0]) if org_values else "",
        department=str(org_values[1]) if len(org_values) > 1 else "",
        title=_prop(card, "title"),
        role=_prop(card, "role"),
        birthday=_parse_date(_prop(card, "bday")),
        anniversary=_parse_date(_prop(card, "anniversary")),
        emails=tuple(_labelled(item) for item in card.contents.get("email", [])),
        phones=tuple(_labelled(item) for item in card.contents.get("tel", [])),
        addresses=tuple(_address(item) for item in card.contents.get("adr", [])),
        photo=photo,
        raw_vcard=raw,
    )


def _prop(card: Any, prop: str) -> str:
    """Return a single-valued vCard property's text, or ``""``."""

    component = getattr(card, prop, None)
    return str(component.value) if component is not None and component.value else ""


def _labelled(component: Any) -> tuple[str, str, bool]:
    """Return ``(value, label, is_preferred)`` for an EMAIL/TEL component.

    ``label`` is the first non-``PREF`` ``TYPE`` (home/work/cell/…); ``is_preferred``
    is set by ``TYPE=PREF`` (vCard 3.0) or a ``PREF`` parameter (vCard 4.0).
    """

    params = component.params if hasattr(component, "params") else {}
    types = [str(item).lower() for item in params.get("TYPE", [])]
    label = next((item for item in types if item != "pref"), "")
    is_preferred = "pref" in types or bool(params.get("PREF"))
    return str(component.value or ""), label, is_preferred


def _parse_date(value: str) -> date | None:
    """Parse a vCard ``BDAY``/``ANNIVERSARY`` into a date across the common formats.

    Accepts ``YYYY-MM-DD`` and ``YYYYMMDD``; a year-omitted ``--MMDD``/``--MM-DD``
    keeps the month/day under the vCard ``1604`` no-year sentinel. An unparseable or
    empty value yields ``None`` rather than raising — one odd card never aborts a sync.
    """

    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Year-omitted vCard forms (--MMDD / --MM-DD): build month/day directly rather
    # than via strptime, whose default-year handling is deprecated for day-of-month.
    if text.startswith("--"):
        digits = text[2:].replace("-", "")
        if len(digits) == 4 and digits.isdigit():
            try:
                return date(_NO_YEAR_SENTINEL, int(digits[:2]), int(digits[2:]))
            except ValueError:
                return None
    return None


def _parse_photo(component: Any) -> ParsedPhoto | None:
    """Parse a vCard ``PHOTO`` into inline bytes or a remote URI (pure — no fetch).

    Handles vobject's already-decoded bytes, ``ENCODING=b``/base64 inline data, a
    ``data:`` URI, and a plain ``http(s)`` URI (resolved later by the transport).
    """

    if component is None:
        return None
    params = component.params if hasattr(component, "params") else {}
    mime = _photo_mime(params)
    value = component.value
    if isinstance(value, bytes):
        return ParsedPhoto(data=value, mime=mime)
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("data:"):
        return _parse_data_uri(text) or ParsedPhoto(mime=mime)
    encodings = [str(item).lower() for item in params.get("ENCODING", [])]
    if "b" in encodings or "base64" in encodings:
        decoded = _b64decode(text)
        return ParsedPhoto(data=decoded, mime=mime) if decoded is not None else None
    if text.startswith(("http://", "https://")):
        return ParsedPhoto(uri=text, mime=mime)
    return None


def _parse_data_uri(text: str) -> ParsedPhoto | None:
    """Parse a ``data:[<mime>][;base64],<payload>`` URI into a ParsedPhoto."""

    try:
        header, _, payload = text[len("data:") :].partition(",")
    except ValueError:
        return None
    if not payload:
        return None
    mime = header.split(";")[0].strip()
    if ";base64" in header:
        decoded = _b64decode(payload)
        return ParsedPhoto(data=decoded, mime=mime) if decoded is not None else None
    return None


def _b64decode(text: str) -> bytes | None:
    """Decode base64 text (whitespace tolerated), or ``None`` if malformed."""

    try:
        return base64.b64decode("".join(text.split()), validate=True)
    except (binascii.Error, ValueError):
        return None


def _photo_mime(params: dict[str, Any]) -> str:
    """Return a MIME type for a PHOTO from its ``TYPE`` param (e.g. JPEG → image/jpeg)."""

    types = [str(item).lower() for item in params.get("TYPE", [])]
    subtype = next((item for item in types if item not in ("", "uri")), "")
    return f"image/{subtype}" if subtype else ""


def _address(component: Any) -> ParsedAddress:
    """Return a :class:`ParsedAddress` from an ADR component."""

    value = component.value
    types = component.params.get("TYPE", []) if hasattr(component, "params") else []
    return ParsedAddress(
        label=str(types[0]).lower() if types else "",
        po_box=str(getattr(value, "box", "") or ""),
        extended=str(getattr(value, "extended", "") or ""),
        street=str(getattr(value, "street", "") or ""),
        city=str(getattr(value, "city", "") or ""),
        region=str(getattr(value, "region", "") or ""),
        postal_code=str(getattr(value, "code", "") or ""),
        country=str(getattr(value, "country", "") or ""),
    )
