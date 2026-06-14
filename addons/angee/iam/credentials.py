"""Credential kind registry for IAM connection secrets.

This registry is the seam for future credential kinds such as ``vault_ref``:
add a new ``CredentialKindHandler`` subclass that owns that material shape and
call ``register_handler()``. The ``Credential`` model stores only the common
columns and delegates kind-specific behavior here.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, ClassVar

from django.db import models
from django.utils import timezone


class CredentialKind(models.TextChoices):
    """Supported credential material kinds."""

    OAUTH = "oauth", "OAuth"
    STATIC_TOKEN = "static_token", "Static Token"
    SSH_KEY = "ssh_key", "SSH Key"


class CredentialKindHandler:
    """Base behavior contract for one credential material kind."""

    kind: ClassVar[str]
    material_field: ClassVar[str]
    """The single secret key this kind stores — also the `CredentialInput` field
    that carries it (e.g. ``api_key``). This handler owns the kind↔secret mapping;
    callers ask the handler rather than switching on ``kind`` themselves."""

    def validate(self, material: dict[str, Any]) -> None:
        """Require the kind's secret material before it is stored."""

        if not material.get(self.material_field):
            raise ValueError(
                f"{self.kind} credential material requires {self.material_field}.",
            )

    def upsert_fields(self, material: dict[str, Any]) -> dict[str, Any]:
        """Return common credential fields derived from ``material``."""

        return {}

    def auth_headers(self, credential: Any) -> dict[str, str]:
        """Return HTTP auth headers for ``credential``."""

        raise NotImplementedError

    def refresh(self, credential: Any) -> None:
        """Refresh ``credential`` in place when the kind supports it."""

        raise NotImplementedError

    def reveal(self, credential: Any) -> dict[str, Any]:
        """Return the decrypted JSON material for ``credential``."""

        return json.loads(credential.material or "{}")


_handlers: dict[str, CredentialKindHandler] = {}


def register_handler(handler: CredentialKindHandler) -> None:
    """Register one credential kind handler."""

    kind = getattr(handler, "kind", "")
    if not kind:
        raise ValueError("Credential kind handlers must define a non-empty kind.")
    _handlers[str(kind)] = handler


def handler_for(kind: str | CredentialKind) -> CredentialKindHandler:
    """Return the handler for ``kind`` or raise a clear configuration error."""

    kind_value = kind.value if isinstance(kind, CredentialKind) else str(kind)
    try:
        return _handlers[kind_value]
    except KeyError as exc:
        raise ValueError(f"No credential handler registered for kind {kind!r}.") from exc


class OAuthCredentialHandler(CredentialKindHandler):
    """Handler for OAuth bearer-token material."""

    kind = "oauth"
    material_field = "access_token"

    def auth_headers(self, credential: Any) -> dict[str, str]:
        """Return OAuth bearer authorization headers."""

        material = self.reveal(credential)
        return {"Authorization": f"Bearer {material['access_token']}"}

    def upsert_fields(self, material: dict[str, Any]) -> dict[str, Any]:
        """Return persisted credential metadata derived from an OAuth token response."""

        fields: dict[str, Any] = {}
        now = timezone.now()
        if "expires_in" in material:
            try:
                fields["expires_at"] = now + timedelta(seconds=int(material["expires_in"]))
            except (TypeError, ValueError):
                pass
        scope = material.get("scope")
        if isinstance(scope, str):
            fields["granted_scopes"] = scope.split()
        if material.get("access_token"):
            fields["last_refresh_at"] = now
            fields["last_refresh_status"] = "ok"
        return fields

    def refresh(self, credential: Any) -> None:
        """Refresh OAuth tokens."""

        raise NotImplementedError("OAuth token refresh wired in S3")


class StaticTokenCredentialHandler(CredentialKindHandler):
    """Handler for static API key material."""

    kind = "static_token"
    material_field = "api_key"

    def auth_headers(self, credential: Any) -> dict[str, str]:
        """Return static-token bearer authorization headers."""

        material = self.reveal(credential)
        return {"Authorization": f"Bearer {material['api_key']}"}

    def refresh(self, credential: Any) -> None:
        """Static tokens do not expire through a refresh flow."""


class SshKeyCredentialHandler(CredentialKindHandler):
    """Handler for SSH private-key material used to clone over git+ssh.

    The key is stored encrypted at rest and revealed only for the operator, which
    owns git transport — Django never opens a git connection, so this kind has no
    HTTP ``auth_headers``. A connection that authenticates its REST inventory over
    HTTP needs a token credential instead; an ssh-key credential serves the
    operator's clone only.
    """

    kind = "ssh_key"
    material_field = "private_key"

    def auth_headers(self, credential: Any) -> dict[str, str]:
        """Return no HTTP headers: an SSH key authenticates git transport, not REST."""

        del credential
        return {}

    def refresh(self, credential: Any) -> None:
        """SSH keys do not expire through a refresh flow."""


register_handler(OAuthCredentialHandler())
register_handler(StaticTokenCredentialHandler())
register_handler(SshKeyCredentialHandler())
