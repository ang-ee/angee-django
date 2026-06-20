"""Credential kind registry for connection secrets.

This registry is the seam for future credential kinds such as ``vault_ref``:
add a new ``CredentialKindHandler`` subclass that owns that material shape and
call ``register_handler()``. The ``Credential`` model stores only the common
columns and delegates kind-specific behavior here.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from typing import Any, ClassVar

from django.db import models
from django.utils import timezone

from angee.integrate.oauth.client import OAuthClientProtocol


class CredentialKind(models.TextChoices):
    """Supported credential material kinds."""

    OAUTH = "oauth", "OAuth"
    STATIC_TOKEN = "static_token", "Static Token"
    SSH_KEY = "ssh_key", "SSH Key"
    BASIC_AUTH = "basic_auth", "Basic Auth"


class CredentialKindHandler:
    """Base behavior contract for one credential material kind."""

    kind: ClassVar[str]
    material_field: ClassVar[str]
    """The single secret key this kind stores — also the `CredentialInput` field
    that carries it (e.g. ``api_key``). This handler owns the kind↔secret mapping;
    callers ask the handler rather than switching on ``kind`` themselves."""
    material_fields: ClassVar[tuple[str, ...]] = ()
    """The credential-input fields this kind reads into its material when it stores
    more than one (e.g. ``("username", "password")``); empty means just
    ``material_field``. The handler owns its input shape, so the create resolver
    never switches on ``kind``."""

    def input_material_fields(self) -> tuple[str, ...]:
        """Return the credential-input field names this kind reads into its material."""

        return self.material_fields or (self.material_field,)

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

    def secret_value(self, credential: Any) -> str:
        """Return the credential's primary secret value."""

        material = self.reveal(credential)
        return str(material.get(self.material_field) or "")

    def can_refresh(self, credential: Any) -> bool:
        """Return whether this credential can renew its own secret without the user."""

        del credential
        return False

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

        return {"Authorization": f"Bearer {self.secret_value(credential)}"}

    def upsert_fields(self, material: dict[str, Any]) -> dict[str, Any]:
        """Return persisted credential metadata derived from an OAuth token response."""

        fields: dict[str, Any] = {}
        now = timezone.now()
        if material.get("access_token"):
            # A token's declared lifetime sets the refresh deadline. A response that omits
            # ``expires_in`` carries no known expiry, so clear ``expires_at`` rather than
            # leave a stale past timestamp that would force a refresh on every use.
            fields["expires_at"] = self._expires_at(material, now)
            fields["last_refresh_at"] = now
            fields["last_refresh_status"] = "ok"
        scope = material.get("scope")
        if isinstance(scope, str):
            fields["granted_scopes"] = scope.split()
        return fields

    @staticmethod
    def _expires_at(material: dict[str, Any], now: datetime) -> datetime | None:
        """Return the access-token expiry from ``expires_in`` seconds, or ``None``."""

        try:
            return now + timedelta(seconds=int(material["expires_in"]))
        except KeyError, TypeError, ValueError:
            return None

    def can_refresh(self, credential: Any) -> bool:
        """Return whether a refresh-capable provider and a stored refresh token exist."""

        oauth_client = getattr(credential, "oauth_client", None)
        if oauth_client is None or not getattr(oauth_client, "supports_refresh", False):
            return False
        return bool(self.reveal(credential).get("refresh_token"))

    def refresh(self, credential: Any) -> None:
        """Exchange the stored refresh token for fresh material, persisted in place.

        Renews this OAuth credential through the provider's refresh grant and writes the
        new tokens back under its ``(user, provider)`` identity — the same upsert the
        login flow uses, so ``expires_at`` and the refresh metadata are recomputed once.
        The provider may rotate the refresh token; a returned one replaces the stored one
        (otherwise the existing one is kept). ``credential`` is reloaded from the persisted
        row so a caller reading :meth:`secret_value` next sees the fresh token. Does not
        lock or re-check freshness — :meth:`integrate.Credential.ensure_fresh` serializes
        concurrent refreshes; raises (``OAuthFlowError``/``ValueError``) when the grant is
        rejected or no refresh token is stored.
        """

        material = self.reveal(credential)
        refresh_value = str(material.get("refresh_token") or "")
        oauth_client = getattr(credential, "oauth_client", None)
        if oauth_client is None or not refresh_value:
            raise ValueError("OAuth credential has no refresh token to renew from.")
        tokens = OAuthClientProtocol(oauth_client).refresh_token(refresh_token=refresh_value)
        # The response fully describes the new token; only carry the refresh token forward
        # when the provider didn't rotate it (so a one-time token isn't dropped) — never the
        # old token's ``expires_in``/``scope``, which describe the token being replaced.
        renewed_material = dict(tokens)
        renewed_material.setdefault("refresh_token", refresh_value)
        type(credential).objects.upsert_for_user(
            credential.user,
            oauth_client,
            self.kind,
            renewed_material,
            external_account=credential.external_account,
        )
        credential.refresh_from_db()


class StaticTokenCredentialHandler(CredentialKindHandler):
    """Handler for static API key material."""

    kind = "static_token"
    material_field = "api_key"

    def validate(self, material: dict[str, Any]) -> None:
        """Allow an empty API key — unlike OAuth, a static token is optional.

        A static-token credential may stand in for an integration that needs no key
        yet: a placeholder whose value is filled in later (e.g. before real inference
        is connected), or a vendor that authenticates by other means. ``secret_value``
        returns ``""`` for an empty key, and consumers gate on a usable secret before
        relying on it (e.g. agent provisioning refuses an empty inference credential).
        """

        del material

    def auth_headers(self, credential: Any) -> dict[str, str]:
        """Return static-token bearer authorization headers."""

        return {"Authorization": f"Bearer {self.secret_value(credential)}"}

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


class BasicAuthCredentialHandler(CredentialKindHandler):
    """Handler for HTTP Basic-auth material — a username and a password.

    Both halves live together in the encrypted ``material`` JSON; the password is
    the primary secret (``material_field``) and the username rides alongside.
    Used by a connection that authenticates its REST inventory with Basic auth,
    such as a CardDAV directory or an IMAP host.
    """

    kind = "basic_auth"
    material_field = "password"
    material_fields = ("username", "password")

    def validate(self, material: dict[str, Any]) -> None:
        """Require both a username and a password before storing the credential."""

        if not material.get("username") or not material.get("password"):
            raise ValueError("basic_auth credential material requires a username and password.")

    def auth_headers(self, credential: Any) -> dict[str, str]:
        """Return an HTTP Basic ``Authorization`` header from the username and password."""

        material = self.reveal(credential)
        raw = f"{material.get('username', '')}:{material.get('password', '')}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}"}

    def refresh(self, credential: Any) -> None:
        """Basic-auth credentials do not expire through a refresh flow."""


register_handler(OAuthCredentialHandler())
register_handler(StaticTokenCredentialHandler())
register_handler(SshKeyCredentialHandler())
register_handler(BasicAuthCredentialHandler())
