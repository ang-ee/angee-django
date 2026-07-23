"""Matrix channel creation and credential attachment."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlunsplit

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import system_context

from angee.integrate.credentials import CredentialKind
from angee.integrate.net import is_unsafe_address, parse_http_url, resolved_addresses
from angee.messaging.connect import resume_channel_pairing
from angee.messaging_integrate_matrix.backend import MatrixChannelBackend

Channel = apps.get_model("messaging", "Channel")
Credential = apps.get_model("integrate", "Credential")

_MATRIX_SLUG = MatrixChannelBackend.key


def create_matrix_channel(user: Any, homeserver: str, username: str, password: str) -> Any:
    """Create a Matrix credential and channel atomically, then start recovery pairing."""

    base_url = matrix_homeserver_url(homeserver)
    clean_username = str(username).strip()
    if not clean_username or not password:
        raise ValueError("A Matrix username and password are required.")
    with system_context(reason="messaging_integrate_matrix.create"), transaction.atomic():
        credential = Credential.objects.create_local_credential(
            user,
            kind=CredentialKind.BASIC_AUTH,
            name=f"Matrix - {clean_username}",
            material={"username": clean_username, "password": password},
        )
        channel = Channel.objects.create_disconnected(
            user,
            name=clean_username,
            backend_class=_MATRIX_SLUG,
            subscription_state={"homeserver": base_url},
        )
        channel.connect(credential=credential)
    resume_channel_pairing(channel)
    return channel


def matrix_login(credential: Any) -> tuple[str, str]:
    """Return the Matrix ``(username, password)`` owned by one BASIC_AUTH row."""

    if credential.kind != CredentialKind.BASIC_AUTH:
        raise ValueError("A Matrix channel requires a basic-auth credential.")
    material = credential.reveal()
    username = str(material.get("username") or "").strip()
    password = str(material.get("password") or "")
    if not username or not password:
        raise ValueError("The Matrix credential requires a username and password.")
    return username, password


def matrix_homeserver_url(value: object) -> str:
    """Validate a Matrix homeserver URL and return its normalized base.

    Matrix homeservers are routinely self-hosted on private networks, so this
    admin-only verb applies integrate's operator-configured-connection SSRF
    policy (``allow_private=True``): RFC-1918 / loopback hosts are permitted, but
    the escapes with no legitimate target either way — cloud metadata,
    link-local, multicast — are still rejected.
    """

    try:
        parsed = parse_http_url(str(value).strip())
    except ValidationError as error:
        raise ValueError("A valid Matrix homeserver URL is required.") from error
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("The Matrix homeserver URL cannot contain credentials, a query, or a fragment.")
    path = parsed.path.rstrip("/")
    normalized = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    try:
        addresses = resolved_addresses(cast(str, parsed.hostname), parsed.port)
    except ValidationError as error:
        raise ValueError("The Matrix homeserver host could not be resolved.") from error
    if any(is_unsafe_address(address, allow_private=True) for address in addresses):
        raise ValueError("The Matrix homeserver must not resolve to a metadata, link-local, or multicast address.")
    return normalized
