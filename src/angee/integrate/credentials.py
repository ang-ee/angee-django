"""Closed credential kinds and their secret-material behavior."""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Collection, Mapping
from datetime import datetime, timedelta
from typing import Any, ClassVar

import httpx2
from authlib.integrations.httpx_client import AssertionClient, OAuthError
from authlib.oauth2.rfc7523 import JWTBearerGrant
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from joserfc.errors import JoseError
from joserfc.jwk import RSAKey

from angee.integrate.errors import IntegrationError, RequestNotSentError
from angee.integrate.http import HTTP_TIMEOUT_SECONDS, HttpClient
from angee.integrate.net import parse_http_url
from angee.integrate.oauth.client import OUTBOUND_HEADERS
from angee.integrate.oauth.errors import TOKEN_EXCHANGE_FAILED, OAuthFlowError

logger = logging.getLogger(__name__)


class CredentialKind(models.TextChoices):
    """Supported credential material kinds."""

    OAUTH = "oauth", "OAuth"
    STATIC_TOKEN = "static_token", "Static Token"
    SSH_KEY = "ssh_key", "SSH Key"
    BASIC_AUTH = "basic_auth", "Basic Auth"
    APP_KEYS = "app_keys", "App Keys"
    JWT_BEARER = "jwt_bearer", "JWT Bearer"

    @property
    def handler(self) -> CredentialKindHandler:
        """Return the implementation owned by this supported material kind."""

        return _CREDENTIAL_HANDLERS[self]


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
    mints_per_scope: ClassVar[bool] = False
    """Whether this kind mints one access token per scope set through
    :meth:`mint_access_token` (``Credential.scoped_access_token``)."""

    def input_material_fields(self) -> tuple[str, ...]:
        """Return the credential-input field names this kind reads into its material."""

        return self.material_fields or (self.material_field,)

    def validate(self, material: dict[str, Any]) -> None:
        """Require the kind's secret material before it is stored."""

        missing = {
            field: "This field is required."
            for field in self.input_material_fields()
            if not material.get(field)
        }
        if missing:
            raise ValidationError(missing)

    def clean(self, material: Mapping[str, Any]) -> dict[str, Any]:
        """Return the material this kind stores for its input ``material``, or raise ``ValidationError``.

        Creation and re-entry both store what this returns. By default the input is
        stored as given once :meth:`validate` accepts it; a kind whose input is a
        document reads it into its stored fields here.
        """

        cleaned = dict(material)
        self.validate(cleaned)
        return cleaned

    def upsert_fields(self, material: dict[str, Any]) -> dict[str, Any]:
        """Return common credential fields derived from ``material``, set on creation and re-entry."""

        return {}

    def auth_headers(self, credential: Any, *, scopes: Collection[str] = ()) -> dict[str, str]:
        """Return HTTP auth headers for ``credential``.

        A kind whose token was fixed when it was granted ignores ``scopes``. A kind
        that mints per scope set (:attr:`mints_per_scope`) requires them: it returns
        the cached token for that set, and on a miss mints one, which needs
        autocommit because the grant is network I/O; every refusal is raised.
        """

        raise NotImplementedError

    def probe(self, credential: Any) -> None:
        """Prove ``credential`` can authenticate without calling its vendor, or raise.

        The connection test behind ``Integration.probe_credential``. By default the
        kind produces its authorization headers; a kind that needs a network grant
        for them proves its material locally instead.
        """

        self.auth_headers(credential)

    def mint_access_token(self, credential: Any, scope: str) -> dict[str, Any]:
        """Exchange ``credential``'s material for an access token on ``scope``.

        Returns ``{"access_token": str, "expires_in": int | None, "scope": str}``
        for ``Credential.scoped_access_token`` to cache; ``scope`` is the granted
        scope. Only kinds that mint per scope set implement it.
        """

        raise NotImplementedError(f"{self.kind} credentials do not mint access tokens per scope set.")

    def records_failure(self, error: Exception) -> bool:
        """Return whether a mint refusal condemns the stored material, so it must be re-entered.

        ``Credential.scoped_access_token`` records ``last_refresh_status="failed"``
        only for these. Only kinds that mint per scope set implement it.
        """

        raise NotImplementedError(f"{self.kind} credentials do not mint access tokens per scope set.")

    def secret_value(self, credential: Any) -> str:
        """Return the credential's primary secret value."""

        return str(credential.reveal().get(self.material_field) or "")


class OAuthCredentialHandler(CredentialKindHandler):
    """Handler for OAuth bearer-token material."""

    kind = "oauth"
    material_field = "access_token"

    def auth_headers(self, credential: Any, *, scopes: Collection[str] = ()) -> dict[str, str]:
        """Return OAuth bearer authorization headers; the grant already fixed the token's scopes."""

        del scopes
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

    def auth_headers(self, credential: Any, *, scopes: Collection[str] = ()) -> dict[str, str]:
        """Return static-token bearer authorization headers; a static token has no scopes to choose."""

        del scopes
        return {"Authorization": f"Bearer {self.secret_value(credential)}"}


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

    def auth_headers(self, credential: Any, *, scopes: Collection[str] = ()) -> dict[str, str]:
        """Return no HTTP headers: an SSH key authenticates git transport, not REST."""

        del credential, scopes
        return {}


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

    def auth_headers(self, credential: Any, *, scopes: Collection[str] = ()) -> dict[str, str]:
        """Return an HTTP Basic ``Authorization`` header from the username and password."""

        del scopes
        material = credential.reveal()
        raw = f"{material.get('username', '')}:{material.get('password', '')}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}"}


class AppKeysCredentialHandler(CredentialKindHandler):
    """Handler for an external application's public id and private secret."""

    kind = "app_keys"
    material_field = "app_secret"
    material_fields = ("app_id", "app_secret")

    def auth_headers(self, credential: Any, *, scopes: Collection[str] = ()) -> dict[str, str]:
        """Return no HTTP headers because application keys parameterize a client library."""

        del credential, scopes
        return {}


class JwtBearerCredentialHandler(CredentialKindHandler):
    """Handler for a provider-issued signing key exchanged through the JWT bearer grant (RFC 7523 §2.1).

    A deployment pastes the provider's key file once (the ``key_file`` input). The
    stored material is the signing identity: ``issuer``, the PEM ``private_key``,
    ``token_endpoint``, optionally ``key_id``, ``subject`` (the delegated principal)
    and ``audience`` (defaults to the token endpoint), plus ``scope_placement``.
    Two key-file vocabularies are recognised. A Google service-account key file
    (``"type": "service_account"``) names the issuer, key id and token endpoint
    ``client_email``, ``private_key_id`` and ``token_uri``, and Google reads the
    requested scope from the assertion's ``scope`` claim. Any other key file uses
    the neutral names, and its scope rides the token request's ``scope``
    parameter (RFC 7521 §4.1).

    The signing key is never the generic secret: :meth:`secret_value` is empty, so
    no consumer can send it as a bearer token or reveal it. Each access token is
    minted for the scope set its consumer asks for, signed RS256 by Authlib's
    ``AssertionClient`` and posted through the SSRF-pinned integrate transport,
    which dials public addresses only; ``Credential.scoped_access_token`` caches
    and renews it.
    """

    kind = "jwt_bearer"
    material_field = "key_file"
    mints_per_scope = True
    signing_fields: ClassVar[tuple[str, ...]] = (
        "issuer",
        "private_key",
        "key_id",
        "token_endpoint",
        "subject",
        "audience",
    )
    """The stored signing identity, in the neutral names."""
    required_fields: ClassVar[tuple[str, ...]] = ("issuer", "private_key", "token_endpoint")
    google_key_file_names: ClassVar[Mapping[str, str]] = {
        "issuer": "client_email",
        "key_id": "private_key_id",
        "token_endpoint": "token_uri",
    }
    """A Google service-account key file's names for the signing fields it renames."""
    recorded_refusals: ClassVar[frozenset[str]] = frozenset({"invalid_grant", "invalid_client", "unauthorized_client"})
    """The RFC 6749 §5.2 errors that refuse the key or identity rather than one request."""
    algorithm: ClassVar[str] = "RS256"

    def clean(self, material: Mapping[str, Any]) -> dict[str, Any]:
        """Read the pasted key file into the signing material, refusing a key that cannot mint.

        The key file must be a JSON object whose private key parses as an RSA private
        key and whose token endpoint is an ``https`` URL with a host (RFC 6749 §3.2
        requires TLS at the token endpoint); the address policy is the pinned
        transport's, applied when the grant is sent. Every refusal is reported on
        ``key_file``, named in the key file's own vocabulary, and never echoes its
        values. Only the signing fields are stored; the rest of a key file is dropped.
        """

        self.validate(dict(material))
        try:
            document = json.loads(str(material["key_file"]))
        except ValueError:
            document = None
        if not isinstance(document, dict):
            raise ValidationError({"key_file": "Paste the provider's JSON key file."})
        google = document.get("type") == "service_account"
        names = self.google_key_file_names if google else {}
        signing = {field: str(document.get(names.get(field, field)) or "").strip() for field in self.signing_fields}
        if missing := [names.get(field, field) for field in self.required_fields if not signing[field]]:
            raise ValidationError({"key_file": f"The key file has no {', '.join(missing)}."})
        try:
            signing_key = RSAKey.import_key(signing["private_key"])
        except (JoseError, TypeError, ValueError) as error:
            raise ValidationError({"key_file": "The key file's private_key is not a PEM RSA private key."}) from error
        if not signing_key.is_private:
            raise ValidationError({"key_file": "The key file's private_key is a public key."})
        endpoint = names.get("token_endpoint", "token_endpoint")
        try:
            https = parse_http_url(signing["token_endpoint"]).scheme == "https"
        except ValidationError:
            https = False
        if not https:
            raise ValidationError({"key_file": f"The key file's {endpoint} must be an https URL with a host."})
        return {**signing, "scope_placement": "claim" if google else "parameter"}

    def upsert_fields(self, material: dict[str, Any]) -> dict[str, Any]:
        """Clear the mint outcome: a re-entered key file has not been refused yet."""

        del material
        return {"last_refresh_status": "", "last_refresh_at": None}

    def secret_value(self, credential: Any) -> str:
        """Return no generic secret: the signing key authenticates only through the grant."""

        del credential
        return ""

    def auth_headers(self, credential: Any, *, scopes: Collection[str] = ()) -> dict[str, str]:
        """Return a bearer header carrying a live access token minted for ``scopes``."""

        return {"Authorization": f"Bearer {credential.scoped_access_token(scopes)}"}

    def probe(self, credential: Any) -> None:
        """Sign one assertion locally, proving the stored key loads and signs, without a request."""

        material = credential.reveal()
        try:
            JWTBearerGrant.sign(
                RSAKey.import_key(material["private_key"]),
                issuer=material["issuer"],
                audience=material["audience"] or material["token_endpoint"],
                subject=material["subject"] or None,
                header=self._header(material),
            )
        except (JoseError, TypeError, ValueError) as error:
            raise IntegrationError("The signing key cannot sign an assertion.") from error

    def mint_access_token(self, credential: Any, scope: str) -> dict[str, Any]:
        """Sign a fresh assertion for ``scope`` and exchange it at the token endpoint.

        Authlib signs ``iss``, ``aud``, ``iat``, ``exp`` (one hour) and ``jti``, plus
        ``sub`` when stored, with ``kid`` in the header when the key file names one;
        the scope rides the claim or the request parameter as ``scope_placement``
        says. The grant is posted through ``HttpClient.transport_factory`` with
        public addresses only. A refusal raises
        ``OAuthFlowError(TOKEN_EXCHANGE_FAILED, status)`` with the response's real
        HTTP status, so a 429, a server error, ``temporarily_unavailable`` or a
        request never sent is transient; the provider's error code rides ``body``
        for :meth:`records_failure`. A key that no longer loads or signs is a
        permanent refusal. Logs carry the credential id and status only.
        """

        material = credential.reveal()
        claim = material["scope_placement"] == "claim"
        statuses: list[int] = []
        client: AssertionClient | None = None
        try:
            client = AssertionClient(
                token_endpoint=material["token_endpoint"],
                issuer=material["issuer"],
                subject=material["subject"] or None,
                audience=material["audience"] or None,
                scope=None if claim else scope,
                claims={"scope": scope} if claim else None,
                key=RSAKey.import_key(material["private_key"]),
                header=self._header(material),
                headers=dict(OUTBOUND_HEADERS),
                timeout=HTTP_TIMEOUT_SECONDS,
                transport=HttpClient.transport_factory(allow_private=False),
                event_hooks={"response": [lambda response: statuses.append(response.status_code)]},
            )
            token = client.refresh_token()
        except JoseError as error:
            self._log_failure(credential, "signing_key")
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400) from error
        except OAuthError as error:
            # Authlib raises OAuthError for any JSON error body; the hook kept its status.
            status = 503 if error.error == "temporarily_unavailable" else (statuses[-1] if statuses else 400)
            self._log_failure(credential, status)
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, status, body={"error": error.error}) from error
        except httpx2.HTTPStatusError as error:
            self._log_failure(credential, error.response.status_code)
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, error.response.status_code) from error
        except RequestNotSentError as error:
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 503) from error
        except (httpx2.HTTPError, ValueError) as error:
            self._log_failure(credential, f"transport_error:{type(error).__name__}")
            status = 503 if isinstance(error, httpx2.TransportError) else 400
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, status) from error
        finally:
            if client is not None:
                client.close()
        access_token = str(token.get("access_token") or "")
        if not access_token:
            raise OAuthFlowError(TOKEN_EXCHANGE_FAILED, 400)
        try:
            expires_in: int | None = int(token["expires_in"])
        except KeyError, TypeError, ValueError:
            expires_in = None
        # RFC 6749 §5.1: a response may omit ``scope`` when it grants what was asked.
        return {"access_token": access_token, "expires_in": expires_in, "scope": str(token.get("scope") or scope)}

    def records_failure(self, error: Exception) -> bool:
        """Return whether a mint refusal condemns the key or identity, so the key file must be re-entered.

        A refused grant, client or client authorization does (:attr:`recorded_refusals`),
        and so does a key that no longer loads or signs. A refused scope, any other
        request-specific refusal and every transient failure do not.
        """

        if not isinstance(error, OAuthFlowError) or error.transient:
            return False
        provider_error = error.body.get("error") if isinstance(error.body, Mapping) else None
        return isinstance(error.__cause__, JoseError) or provider_error in self.recorded_refusals

    def _header(self, material: Mapping[str, Any]) -> dict[str, str]:
        """Return the assertion's JOSE header, naming the key when the key file does."""

        return {"alg": self.algorithm, **({"kid": material["key_id"]} if material["key_id"] else {})}

    @staticmethod
    def _log_failure(credential: Any, status: object) -> None:
        """Log one refused grant by credential id and status, never with provider-controlled values."""

        logger.warning("JWT bearer token request failed for credential %s: status=%s", credential.pk, status)


_CREDENTIAL_HANDLERS = {
    CredentialKind.OAUTH: OAuthCredentialHandler(),
    CredentialKind.STATIC_TOKEN: StaticTokenCredentialHandler(),
    CredentialKind.SSH_KEY: SshKeyCredentialHandler(),
    CredentialKind.BASIC_AUTH: BasicAuthCredentialHandler(),
    CredentialKind.APP_KEYS: AppKeysCredentialHandler(),
    CredentialKind.JWT_BEARER: JwtBearerCredentialHandler(),
}
