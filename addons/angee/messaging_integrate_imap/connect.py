"""IMAP channel connection service.

The optional IMAP addon owns the concrete bridge facts: the ``imap`` backend key,
the IMAP connection config, and the Basic-auth credential needed to sync. Base
``messaging`` owns only the neutral ``Channel`` model and list/detail surface.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db import transaction
from rebac import system_context

from angee.integrate.credentials import CredentialKind

Channel = apps.get_model("messaging", "Channel")
Credential = apps.get_model("integrate", "Credential")

_IMAP_VENDOR_SLUG = "imap"
_CREDENTIAL_NAME_MAX_LENGTH = 255


class ImapConnectError(ValueError):
    """A safe, actionable IMAP connection input failure."""


def connect_imap_channel(
    user: Any,
    *,
    name: str,
    host: str,
    username: str,
    password: str,
    security: str = "ssl",
    port: int | None = None,
    mailboxes: list[str] | None = None,
    own_addresses: list[str] | None = None,
) -> Any:
    """Create a connected IMAP channel and a channel-scoped Basic-auth credential."""

    clean_host = str(host).strip()
    if not clean_host:
        raise ImapConnectError("An IMAP host is required.")
    display_name = str(name).strip() or clean_host
    config = _connection_config(
        host=clean_host,
        security=security,
        port=port,
        mailboxes=mailboxes,
        own_addresses=own_addresses,
    )

    with system_context(reason="messaging_integrate_imap.connect"), transaction.atomic():
        channel = Channel.objects.create_disconnected(
            user,
            name=display_name,
            backend_class=_IMAP_VENDOR_SLUG,
            config=config,
        )
        credential = Credential.objects.create_local_credential(
            user,
            kind=CredentialKind.BASIC_AUTH,
            name=_credential_name(display_name, channel.sqid),
            material={"username": username, "password": password},
        )
        channel.connect(credential=credential, account=getattr(credential, "external_account", None))
    return channel


def update_imap_channel_credential(channel: Any, *, username: str, password: str) -> None:
    """Re-enter the Basic-auth login of an existing IMAP channel in place.

    The operator's "the mailbox password changed" path. The channel keeps its
    row, config, cursor, and credential identity; only the encrypted material
    is replaced, validated by the credential's own kind handler. A channel
    whose credential was detached (a disconnect that wiped it) has nothing to
    update — connect it again instead.
    """

    credential = channel.credential
    if credential is None:
        raise ImapConnectError("This channel has no credential; connect it again.")
    if credential.kind != CredentialKind.BASIC_AUTH:
        raise ImapConnectError("Only a username/password IMAP login can be re-entered here.")
    try:
        credential.replace_material({"username": str(username).strip(), "password": str(password)})
    except ValueError as error:
        raise ImapConnectError(str(error)) from error


def _connection_config(
    *,
    host: str,
    security: str,
    port: int | None,
    mailboxes: list[str] | None,
    own_addresses: list[str] | None,
) -> dict[str, Any]:
    """Return the backend config persisted on the channel."""

    clean_security = str(security or "ssl").strip().lower() or "ssl"
    config: dict[str, Any] = {"host": host, "security": clean_security}
    if port is not None:
        config["port"] = port
    if cleaned_mailboxes := _clean_string_list(mailboxes):
        config["mailboxes"] = cleaned_mailboxes
    if cleaned_own_addresses := _clean_string_list(own_addresses):
        config["own_addresses"] = cleaned_own_addresses
    return config


def _clean_string_list(values: list[str] | None) -> list[str]:
    """Return non-empty strings from optional GraphQL list input."""

    return [cleaned for value in values or [] if (cleaned := str(value).strip())]


def _credential_name(display_name: str, channel_id: str) -> str:
    """Return a provider-less credential key scoped to one channel."""

    suffix = f" ({channel_id})"
    prefix = f"IMAP - {display_name}"
    return f"{prefix[: _CREDENTIAL_NAME_MAX_LENGTH - len(suffix)]}{suffix}"
