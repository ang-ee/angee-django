"""CardDAV directory backend: sync a CardDAV address book into parties.

Transport + parse for the ``carddav`` directory backend: an ``addressbook-query``
REPORT fetches every vCard from the configured address-book collection over the
shared SSRF-pinned client (``allow_private=True`` — self-hosted CardDAV servers are
common), authenticated by the directory's Basic-auth credential, and ``vobject``
parses each card into a neutral :class:`~angee.parties.backends.ParsedContact`. The
map onto parties rows is owned by the parties managers, not here.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from typing import Any

import vobject

from angee.parties.backends import DirectoryBackend, ParsedAddress, ParsedContact

_NS = {"D": "DAV:", "C": "urn:ietf:params:xml:ns:carddav"}

# Pull every card's address-data in one REPORT against the address-book collection.
_ADDRESSBOOK_QUERY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<C:addressbook-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">'
    "<D:prop><D:getetag/><C:address-data/></D:prop>"
    "<C:filter/>"
    "</C:addressbook-query>"
)


class CardDavError(Exception):
    """Raised when the CardDAV server returns a non-success response."""


class CardDavDirectoryBackend(DirectoryBackend):
    """Fetches vCards from a CardDAV address-book collection.

    ``config["carddav_url"]`` is the address-book collection URL; the directory's
    Basic-auth credential authenticates. Discovery (``.well-known/carddav`` →
    principal → home-set) is a later refinement — for now the operator supplies
    the collection URL directly.
    """

    key = "carddav"
    label = "CardDAV"
    icon = "address-book"
    defaults = {"vendor": "carddav"}

    def fetch_contacts(self) -> list[ParsedContact]:
        """Run an addressbook-query REPORT and parse every returned vCard."""

        url = str(self.bridge.config.get("carddav_url") or "").strip()
        credential = self.bridge.credential
        if not url or credential is None:
            return []
        headers = {
            "Depth": "1",
            "Content-Type": "application/xml; charset=utf-8",
            **credential.auth_headers(),
        }
        response = self.http.request(
            "REPORT",
            url,
            headers=headers,
            body=_ADDRESSBOOK_QUERY.encode("utf-8"),
            allow_private=True,
        )
        if not response.ok:
            raise CardDavError(f"CardDAV REPORT {url} returned HTTP {response.status}.")
        return list(self._parse_multistatus(response.body))

    def _parse_multistatus(self, body: bytes) -> Any:
        """Yield one :class:`ParsedContact` per ``address-data`` in a multistatus body."""

        root = ElementTree.fromstring(body)
        for response in root.findall("D:response", _NS):
            data = response.find(".//C:address-data", _NS)
            text = (data.text or "") if data is not None else ""
            if not text.strip():
                continue
            try:
                card = vobject.readOne(text)
            except Exception:  # noqa: BLE001 — one malformed card must not abort the whole sync.
                continue
            yield _parse_vcard(card, text)


def _parse_vcard(card: Any, raw: str) -> ParsedContact:
    """Map one parsed vCard into a neutral :class:`ParsedContact`."""

    name = getattr(card, "n", None)
    name_value = name.value if name is not None else None
    org = getattr(card, "org", None)
    org_values = org.value if org is not None else []
    return ParsedContact(
        uid=_text(card, "uid"),
        display_name=_text(card, "fn"),
        name_prefix=str(getattr(name_value, "prefix", "") or ""),
        given_name=str(getattr(name_value, "given", "") or ""),
        additional_name=str(getattr(name_value, "additional", "") or ""),
        family_name=str(getattr(name_value, "family", "") or ""),
        name_suffix=str(getattr(name_value, "suffix", "") or ""),
        nickname=_text(card, "nickname"),
        notes=_text(card, "note"),
        organization=str(org_values[0]) if org_values else "",
        title=_text(card, "title"),
        emails=tuple(_labelled(item) for item in card.contents.get("email", [])),
        phones=tuple(_labelled(item) for item in card.contents.get("tel", [])),
        addresses=tuple(_address(item) for item in card.contents.get("adr", [])),
        raw_vcard=raw,
    )


def _text(card: Any, prop: str) -> str:
    """Return a single-valued vCard property's text, or ``""``."""

    component = getattr(card, prop, None)
    return str(component.value) if component is not None and component.value else ""


def _labelled(component: Any) -> tuple[str, str]:
    """Return ``(value, label)`` for an EMAIL/TEL component using its first TYPE."""

    types = component.params.get("TYPE", []) if hasattr(component, "params") else []
    label = str(types[0]).lower() if types else ""
    return str(component.value or ""), label


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
