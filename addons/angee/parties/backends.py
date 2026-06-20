"""Directory backend contract — sync a contacts source into parties.

A :class:`~angee.parties.models.Directory` (an ``integrate.Integration`` child +
``Bridge``) selects one ``DirectoryBackend`` by registry key. The backend does the
per-source *transport* + *parse* (return neutral :class:`ParsedContact` rows); the
*map* onto parties (``Handle``/``Party``/``Address``) is owned by the parties
managers, so every directory source shares one write path. The
``parties_integrate_carddav`` addon contributes the ``carddav`` backend; the
``manual`` null-object keeps the registry non-empty when no source is installed.
"""

from __future__ import annotations

from dataclasses import dataclass

from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import BridgeImpl


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

    Emails/phones are ``(value, label)`` pairs; the map turns each into a
    ``Handle``. ``uid`` is the source's stable id (a vCard ``UID``) used to
    re-resolve the same party on re-sync, and ``raw_vcard`` is kept for lossless
    round-trip.
    """

    uid: str = ""
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
    emails: tuple[tuple[str, str], ...] = ()
    phones: tuple[tuple[str, str], ...] = ()
    addresses: tuple[ParsedAddress, ...] = ()
    raw_vcard: str = ""


class DirectoryBackend(BridgeImpl, HttpClientMixin):
    """Abstract backend that fetches and parses a contacts source.

    ``self.bridge`` is the ``Directory`` row — its ``config`` carries the source
    URL and ``self.bridge.credential`` authenticates — and ``self.http`` is the
    shared SSRF-pinned client (a self-hosted source passes ``allow_private=True``).
    """

    category = "directory"
    label = "Directory"
    icon = "address-book"

    def fetch_contacts(self) -> list[ParsedContact]:
        """Return every contact from the source as neutral dataclasses."""

        raise NotImplementedError("DirectoryBackend subclasses must implement fetch_contacts().")


class ManualDirectoryBackend(DirectoryBackend):
    """The null-object default: a directory with no source backend syncs nothing.

    Keeps ``ANGEE_DIRECTORY_BACKEND_CLASSES`` non-empty when no source addon is
    installed (``ImplClassField`` requires a non-empty registry), so the GraphQL
    enum is never empty and a draft directory always has a selectable backend.
    """

    key = "manual"
    label = "Manual"

    def fetch_contacts(self) -> list[ParsedContact]:
        """Return no contacts — a manual directory is populated by hand."""

        return []
