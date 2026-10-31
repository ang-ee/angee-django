"""Native connections owners for integration contracts."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from rebac import (
    system_context,
)
from strawberry_django.descriptors import model_property

from angee.base.fields.encrypted import EncryptedField
from angee.base.fields.implementation import ImplClassField
from angee.base.fields.state import StateField
from angee.base.fields.text import DiagnosticTextField
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.implementations import ImplDefaultsMixin
from angee.base.models.ownership import OwnerMixin
from angee.base.serialization import canonical_json
from angee.integrate.choices import (
    AccountStatus,
    CredentialStatus,
)
from angee.integrate.credentials import CredentialKind, CredentialKindHandler
from angee.integrate.errors import (
    IntegrationError,
)
from angee.integrate.managers import (
    CredentialManager,
    ExternalAccountManager,
    OAuthClientManager,
)
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.discovery import discovery_document as oidc_metadata_document
from angee.integrate.oauth.errors import CLIENT_NOT_CONFIGURED, OAuthFlowError
from angee.integrate.oauth.providers import OAuthProviderType
from angee.jobs.locks import record_lock_key, task_lock

logger = logging.getLogger(__name__)

_OAUTH_REFRESH_MARGIN = timedelta(minutes=5)


class OAuthClient(ImplDefaultsMixin, AuditMixin, AngeeDataModel, concrete=True):
    """OAuth2 client registration for connecting an external account.

    The base of the connection substrate: enough to run the authorization-code and
    refresh flows and act against a provider's API (Gemini, Grok, Anthropic). It
    carries no identity or login policy itself — a provider that also
    authenticates a *login* gains direct fields from the ``iam_integrate_oidc``
    extension when that addon is installed.

    Self-describing: ``slug`` is its own connect-client key and ``icon``/
    ``display_name`` its own button branding. The third-party catalogue is
    ``integrate.Vendor``; that ``slug`` is a deliberately independent namespace,
    not a foreign key into this one.
    """

    sqid_prefix = "clt_"
    slug = models.SlugField()
    provider_type = ImplClassField(
        OAuthProviderType,
        default="generic_oauth2",
    )
    """Provider preset key whose defaults seed this OAuth client."""
    icon = models.CharField(max_length=128, blank=True)
    environment = models.CharField(max_length=32, default="prod")
    display_name = models.CharField(max_length=128)
    client_id = models.CharField(max_length=255, blank=True)
    client_secret = EncryptedField(blank=True)
    discovery_url = models.URLField(blank=True)
    authorize_endpoint = models.URLField(blank=True)
    token_endpoint = models.URLField(blank=True)
    revoke_endpoint = models.URLField(blank=True)
    userinfo_endpoint = models.URLField(blank=True)
    """Access-token-protected profile endpoint used to label a connected account
    (read through the claim mappings below). Plain OAuth, not OIDC: a connect-only
    provider populates its ``ExternalAccount`` from here without any ID token."""
    token_request_format = models.CharField(max_length=16, default="form", blank=True)
    is_enabled = models.BooleanField(default=True, db_index=True)
    scopes_catalogue = models.JSONField(default=list, blank=True)
    default_scopes = models.JSONField(default=list, blank=True)
    supports_refresh = models.BooleanField(default=True)
    refresh_rotates = models.BooleanField(default=False)
    supports_pkce = models.BooleanField(default=True)
    max_refresh_age_seconds = models.PositiveIntegerField(null=True, blank=True)
    authorize_params = models.JSONField(default=dict, blank=True)
    token_params = models.JSONField(default=dict, blank=True)
    # Fixed manual-paste callback for fixed public clients (e.g. Anthropic) whose
    # allow-list we cannot extend. When set, connect uses a localhost loopback redirect
    # when the console runs on localhost, else this manual page (see resolve_connect_redirect).
    manual_redirect_uri = models.URLField(blank=True)
    # The bare loopback path the same fixed public client's allow-list registers for
    # native-app localhost connect (RFC 8252 §7.3 — any port, exact path). Anthropic's
    # public client allows ``/callback`` only, not the console's own callback path, so on
    # localhost connect must redirect to ``{origin}{loopback_redirect_path}``. The browser
    # mounts a matching route at this path (see the frontend ``CONNECT_CALLBACK_LOOPBACK_PATH``).
    # Connect-flow only: ``resolve_connect_redirect`` is shared with OIDC login via
    # ``issue_flow``, so a login-capable client must leave this blank or its login redirect
    # would be rewritten to the connect loopback.
    loopback_redirect_path = models.CharField(max_length=255, blank=True)
    # Claim mapping: how to read a stable subject/email/label/avatar out of this
    # provider's profile (userinfo or, for OIDC, the verified ID token). Connect uses
    # it to label the account; OIDC login reuses it to identify the user.
    external_id_claim = models.CharField(max_length=128, default="sub", blank=True)
    email_claim = models.CharField(max_length=128, default="email", blank=True)
    display_name_claim = models.CharField(max_length=128, blank=True)
    avatar_url_claim = models.CharField(max_length=128, blank=True)

    objects = OAuthClientManager()

    class Meta:
        """Django model options for OAuth clients."""

        abstract = True
        ordering = ("slug", "environment")
        rebac_resource_type = "integrate/oauth_client"
        constraints = (
            models.UniqueConstraint(
                fields=("slug", "environment"),
                name="uniq_integrate_oauth_client_slug_environment",
            ),
        )

    def __str__(self) -> str:
        """Return the configured OAuth client display name or slug environment."""

        if self.display_name:
            return self.display_name
        return f"{self.slug or '?'} ({self.environment})"

    @property
    def is_public_client(self) -> bool:
        """The fixed manual callback declares a public, PKCE-authenticated client."""

        return bool(self.manual_redirect_uri)

    readiness_fields = ("slug", "is_enabled", "client_id", "discovery_url", "authorize_endpoint", "token_endpoint")
    """Public facts used by the single connection readiness rule."""

    @property
    def configuration_state(self) -> str:
        """Return this OAuth client's operator-facing configuration readiness."""

        if not self.is_enabled:
            return "disabled"
        if not self.client_id:
            return "needs_client"
        # A discovery URL resolves the transport endpoints at flow time
        # (``discover_endpoints``), so it satisfies the endpoint requirement.
        if not self.discovery_url and not (self.authorize_endpoint and self.token_endpoint):
            return "needs_endpoints"
        return "ready"

    def require_configured(self) -> None:
        """Refuse protocol startup through the registration's one readiness rule."""

        state = self.configuration_state
        if state != "ready":
            raise OAuthFlowError(
                CLIENT_NOT_CONFIGURED,
                400,
                f"OAuth client is not fully configured ({state}).",
            )

    def refine_grant(self, protocol: OAuthClientProtocol, tokens: dict[str, Any]) -> dict[str, Any]:
        """Let the selected provider refine the grant before credential persistence."""

        return self.provider.refine_grant(protocol, tokens)

    @property
    def provider(self) -> type[OAuthProviderType]:
        """Resolve the selected provider preset through its implementation field."""

        return type(self).provider_type.field.resolve_for(self)

    def userinfo_params(self, protocol: OAuthClientProtocol, access_token: str) -> Mapping[str, str]:
        """Ask the selected preset for profile-request parameters."""

        return self.provider.userinfo_params(protocol, access_token)

    @property
    def default_scope_values(self) -> list[str]:
        """Return the configured default OAuth scopes as strings."""

        return self._string_list(self.default_scopes)

    @property
    def scopes_catalogue_values(self) -> list[str]:
        """Return the advertised OAuth scopes as strings."""

        return self._string_list(self.scopes_catalogue)

    @property
    def authorize_param_values(self) -> dict[str, str]:
        """Return configured provider-specific authorize parameters."""

        return self._string_mapping(self.authorize_params)

    @property
    def token_param_values(self) -> dict[str, Any]:
        """Return provider token parameters while preserving JSON scalar types."""

        if not isinstance(self.token_params, Mapping):
            return {}
        return {str(key): item for key, item in self.token_params.items() if item is not None}

    def resolve_connect_redirect(self, proposed_redirect_uri: str) -> tuple[str, str]:
        """Return the ``(redirect_uri, mode)`` this client uses to connect from a browser.

        ``mode`` is ``"auto"`` (the provider redirects back to the returned redirect)
        or ``"manual"`` (the user copies the code the provider displays and pastes it
        back). A client with no ``manual_redirect_uri`` always redirects back to the
        browser-proposed redirect. A fixed public client (``manual_redirect_uri`` set)
        has an allow-list we cannot extend: on ``localhost`` it can round-trip only to
        the loopback path its allow-list registers, so the proposed redirect's path is
        replaced with ``loopback_redirect_path`` (origin preserved); off-localhost — or
        with no loopback path declared — its allow-list rejects the redirect and the
        cross-origin callback would drop the session, so it falls back to manual paste.
        """

        if not self.manual_redirect_uri:
            return proposed_redirect_uri, "auto"
        parts = urlsplit(proposed_redirect_uri)
        host = (parts.hostname or "").lower()
        if self.loopback_redirect_path and parts.scheme in {"http", "https"} and host == "localhost":
            # Rebuild the origin from validated parts only — never echo userinfo or host
            # casing from the proposed URL into the redirect we hand the provider, which
            # exact-matches it again at token exchange.
            netloc = f"{host}:{parts.port}" if parts.port else host
            loopback = urlunsplit((parts.scheme, netloc, self.loopback_redirect_path, "", ""))
            return loopback, "auto"
        return self.manual_redirect_uri, "manual"

    @property
    def token_request_format_value(self) -> str:
        """Return the configured token request body format."""

        value = str(self.token_request_format or "form").strip().lower()
        if value in {"form", "json"}:
            return value
        return "form"

    def _string_list(self, value: object) -> list[str]:
        """Return one JSON-backed column value as a string list."""

        if not isinstance(value, (list, tuple)):
            return []
        return [str(item) for item in value]

    def _string_mapping(self, value: object) -> dict[str, str]:
        """Return one JSON-backed column value as string query/form params."""

        if not isinstance(value, Mapping):
            return {}
        return {str(key): str(item) for key, item in value.items() if item is not None}

    def external_id_from_claims(self, claims: Mapping[str, Any]) -> str:
        """Return this provider account's stable external id from profile claims."""

        return self._claim_string(claims, self.external_id_claim)

    def email_from_claims(self, claims: Mapping[str, Any]) -> str:
        """Return this provider account's email from profile claims."""

        return self._claim_string(claims, self.email_claim)

    def display_name_from_claims(self, claims: Mapping[str, Any], email: str) -> str:
        """Return this provider account's display label from profile claims."""

        configured = self._claim_string(claims, self.display_name_claim)
        if configured:
            return configured
        return ExternalAccount.display_name_from_claims(claims, email)

    def avatar_url_from_claims(self, claims: Mapping[str, Any]) -> str:
        """Return this provider account's avatar URL from profile claims."""

        return self._claim_string(claims, self.avatar_url_claim)

    def _claim_string(self, claims: Mapping[str, Any], claim_name: str | None) -> str:
        """Return a flat or dotted-path claim value as a string."""

        name = str(claim_name or "").strip()
        if not name:
            return ""
        if name in claims:
            return self._string_claim_value(claims.get(name))
        value: Any = claims
        for part in name.split("."):
            if not isinstance(value, Mapping):
                return ""
            value = value.get(part)
            if value in (None, ""):
                return ""
        return self._string_claim_value(value)

    def _string_claim_value(self, value: Any) -> str:
        """Return scalar identity claim values only."""

        if value in (None, ""):
            return ""
        if isinstance(value, Mapping | list | tuple | set):
            return ""
        return str(value)

    # OIDC discovery key -> the blank endpoint field this client fills from it. The
    # OAuth base owns its own transport/userinfo endpoints; discovery fills only
    # blanks, so explicit configuration is never overwritten.
    DISCOVERY_ENDPOINT_FIELDS = {
        "authorize_endpoint": "authorization_endpoint",
        "token_endpoint": "token_endpoint",
        "revoke_endpoint": "revocation_endpoint",
        "userinfo_endpoint": "userinfo_endpoint",
    }

    def fill_endpoints_from_discovery(self, discovery: Mapping[str, Any]) -> bool:
        """Fill this client's blank endpoints from a discovery document; return whether changed.

        The client owns projecting a discovery document onto its own endpoint
        fields, so a caller (the OIDC protocol/discovery action) never reaches in to
        set them by name.
        """

        changed = False
        for field, key in self.DISCOVERY_ENDPOINT_FIELDS.items():
            if getattr(self, field, ""):
                continue
            value = discovery.get(key)
            if value:
                setattr(self, field, str(value))
                changed = True
        return changed

    def fill_extension_fields_from_discovery(self, discovery: Mapping[str, Any]) -> bool:
        """Hook for composed extensions to project discovery onto their own fields."""

        del discovery
        return False

    def discover_endpoints(self) -> Mapping[str, Any]:
        """Fetch OIDC metadata and fill blank OAuth/extension endpoints on this row."""

        discovery_url = str(getattr(self, "discovery_url", "") or "")
        if not discovery_url:
            return {}
        discovery = oidc_metadata_document(discovery_url)
        self.fill_endpoints_from_discovery(discovery)
        self.fill_extension_fields_from_discovery(discovery)
        return discovery


class ExternalAccount(OwnerMixin, AngeeDataModel, concrete=True):
    """A user's identity at a provider, owned by the user it belongs to.

    Connection identity only: which client minted it (``oauth_client``), which
    external subject (``external_id``), and the credential that authenticates as
    it. The integration that *runs over* a connection lives in
    ``integrate.Integration``, which owns implementation health.
    """

    sqid_prefix = "eac_"
    oauth_client = models.ForeignKey(
        "integrate.OAuthClient",
        on_delete=models.PROTECT,
        related_name="external_accounts",
    )
    external_id = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(blank=True)
    credential = models.ForeignKey(
        "integrate.Credential",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    status = StateField(choices_enum=AccountStatus, default=AccountStatus.ACTIVE)
    identity_claims = models.JSONField(default=dict, blank=True)
    last_error = DiagnosticTextField(blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = ExternalAccountManager()

    class Meta:
        """Django model options for external accounts."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "integrate/external_account"
        constraints = (
            models.UniqueConstraint(
                fields=("oauth_client", "external_id"),
                name="uniq_integrate_external_account_oauth_client_external_id",
            ),
        )

    def insert_owner_default(self) -> Any:
        """Leave the identity unowned unless its linking verb names its user.

        OIDC sign-in resolves an identity to its owner, so neither the creator
        nor the acting user is ever implied.
        """

        return None

    def __str__(self) -> str:
        """Return a stable provider-qualified account label."""

        client_slug = getattr(getattr(self, "oauth_client", None), "slug", "?")
        return f"{client_slug}:{self.external_id}"

    @property
    def credential_status(self) -> str:
        """Return the current OAuth credential status, if this account has one."""

        credential = getattr(self, "credential", None)
        return "" if credential is None else str(getattr(credential, "status", "") or "")

    @property
    def provider_slug(self) -> str:
        """Return the originating OAuth client's slug."""

        client = getattr(self, "oauth_client", None)
        return str(getattr(client, "slug", "") or "")

    @property
    def provider_environment(self) -> str:
        """Return the originating OAuth client's environment."""

        client = getattr(self, "oauth_client", None)
        return str(getattr(client, "environment", "") or "")

    @property
    def provider_label(self) -> str:
        """Return the originating OAuth client's display label."""

        client = getattr(self, "oauth_client", None)
        return str(getattr(client, "display_name", "") or "")

    @property
    def provider_icon(self) -> str:
        """Return the originating OAuth client's branding icon."""

        client = getattr(self, "oauth_client", None)
        return str(getattr(client, "icon", "") or "")

    @staticmethod
    def display_name_from_claims(claims: Mapping[str, Any], email: str) -> str:
        """Return the best display label from verified identity claims."""

        for key in ("name", "preferred_username", "given_name"):
            value = claims.get(key)
            if value:
                return str(value)
        return email


class Credential(AuditMixin, AngeeDataModel, concrete=True):
    """Per-user credential material for acting against a vendor OAuth client."""

    def check_disconnect(self) -> None:
        """Validate explicit disconnect; contributions raise a coded ``ValidationError``."""

    def revoke_remote(self) -> None:
        """Revoke this credential's OAuth token when its provider supports it."""

        oauth_client = self.oauth_client
        if oauth_client is None or not getattr(oauth_client, "revoke_endpoint", ""):
            return
        token = str(self.reveal().get("access_token") or "")
        if not token:
            return
        OAuthClientProtocol(oauth_client).revoke_token(token)

    sqid_prefix = "crd_"
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="credentials",
    )
    oauth_client = models.ForeignKey(
        "integrate.OAuthClient",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="oauth_credentials",
    )
    """The provider this credential authenticates to — required for ``oauth`` (its
    identity is the provider account), optional for local kinds
    (``static_token``/``ssh_key``), which may be created without one."""
    external_account = models.ForeignKey(
        "integrate.ExternalAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="credentials",
    )
    name = models.CharField(max_length=255, blank=True)
    """Human credential label; provider-backed rows are named on create, local
    rows use it as their per-user identity."""
    kind = StateField(choices_enum=CredentialKind)
    material = EncryptedField()
    status = StateField(choices_enum=CredentialStatus, default=CredentialStatus.ACTIVE)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    granted_scopes = models.JSONField(default=list, blank=True)
    last_refresh_at = models.DateTimeField(null=True, blank=True)
    last_refresh_status = models.CharField(max_length=32, blank=True)

    @property
    def refreshable(self) -> bool:
        """Derive refreshability from the current client and this grant's material."""

        return (
            self.kind == CredentialKind.OAUTH
            and self.oauth_client is not None
            and self.oauth_client.supports_refresh
            and bool(self.reveal().get("refresh_token"))
        )

    @property
    def is_reconnect_required(self) -> bool:
        """Require consent only for inactive, failed-refresh or unrenewable expired grants."""

        return (
            self.status != CredentialStatus.ACTIVE
            or self.last_refresh_status == "failed"
            or (self.expires_at is not None and self.expires_at <= timezone.now() and not self.refreshable)
        )

    objects = CredentialManager()

    class Meta:
        """Django model options for credentials."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "integrate/credential"
        constraints = (
            # OAuth identity: one credential per (user, provider). NULLs are
            # distinct in SQL unique, so provider-less kinds need their own arm.
            models.UniqueConstraint(
                fields=("user", "oauth_client"),
                condition=Q(oauth_client__isnull=False),
                name="uniq_integrate_credential_user_oauth_client",
            ),
            # Provider-less identity: one named credential per user (named local
            # credentials; provider-bearing rows use the arm above).
            models.UniqueConstraint(
                fields=("user", "name"),
                condition=Q(oauth_client__isnull=True) & ~Q(name=""),
                name="uniq_integrate_credential_user_name_local",
            ),
            # A provider is required only for ``oauth`` (its identity is the
            # provider account); local kinds may omit it.
            models.CheckConstraint(
                condition=~Q(kind=CredentialKind.OAUTH) | Q(oauth_client__isnull=False),
                name="integrate_credential_oauth_requires_provider",
            ),
            # The mirror of the unique arm above: a provider-less credential is
            # identified by a non-blank name, so the DB (not just the factory) owns
            # "every local credential is named".
            models.CheckConstraint(
                condition=Q(oauth_client__isnull=False) | ~Q(name=""),
                name="integrate_credential_local_requires_name",
            ),
        )

    @property
    def handler(self) -> CredentialKindHandler:
        """Return the behavior owned by this credential kind."""

        return CredentialKind(self.kind).handler

    def reveal(self) -> dict[str, Any]:
        """Decode this row's decrypted JSON material."""

        return json.loads(self.material or "{}")

    @staticmethod
    def encode_material(material: Mapping[str, Any]) -> str:
        """Encode credential material into its deterministic encrypted-field payload."""

        return canonical_json(dict(material))

    def update_material(self, **changes: Any) -> None:
        """Merge encrypted material changes under a row lock; ``None`` deletes.

        Credential material may be edited concurrently by operator and worker
        processes holding separately-loaded instances. Re-read the row under the
        same authoritative lock canon as :meth:`Bridge.merge_subscription_state`
        so one key's consume-once edit cannot clobber another writer's material.
        Kind handlers validate required keys at credential creation; transient
        extra keys remain permitted here.
        """

        with system_context(reason="integrate.credential.material"):
            with transaction.atomic():
                row: Credential = type(self).objects.lock_if_supported().get(pk=self.pk)
                material = dict(row.reveal())
                for key, value in changes.items():
                    if value is None:
                        material.pop(key, None)
                    else:
                        material[key] = value
                # EncryptedField's descriptor is not modeled by django-stubs.
                row.material = self.encode_material(material)
                row.save(update_fields=["material", "updated_at"])
            self.refresh_from_db(fields=["material", "updated_at"])

    def replace_material(self, material: Mapping[str, Any]) -> None:
        """Re-enter this credential's secret(s) under the kind handler's validation.

        The operator's "the password changed" path: ``material`` carries the
        kind's own input fields (the handler names them), is validated exactly
        as at creation, then lands through :meth:`update_material` under the row
        lock — the row, its kind, and its owner stay as they are, so every
        integration attached to it keeps working without re-attachment.
        """

        replacement = dict(material)
        self.handler.validate(replacement)
        self.update_material(**replacement)

    def auth_headers(self) -> dict[str, str]:
        """Return authorization headers through the kind handler."""

        return self.handler.auth_headers(self)

    def secret_value(self) -> str:
        """Return the primary secret value through the kind handler."""

        return str(self.handler.secret_value(self))

    @model_property(
        only=["name", "oauth_client_id", "external_account_id"],
    )
    def display_name(self) -> str:
        """Return a human label for lists, headers, and relation pickers."""

        if self.name:
            return str(self.name)
        client = getattr(self, "oauth_client", None)
        if client is not None:
            provider = str(getattr(client, "slug", "") or getattr(client, "display_name", "") or "credential")
            account = getattr(self, "external_account", None)
            subject = str(getattr(account, "external_id", "") or "") if account else ""
            return f"{provider}: {subject}" if subject else provider
        return "credential"

    @property
    def connected_display_name(self) -> str:
        """Return a public-safe label for the current user's connected account."""

        if self.name:
            return str(self.name)
        account = getattr(self, "external_account", None)
        if account is not None:
            subject = str(
                getattr(account, "email", "")
                or getattr(account, "display_name", "")
                or getattr(account, "external_id", "")
                or ""
            )
            if subject:
                return subject
        return "credential"

    def ensure_fresh(self) -> None:
        """Renew this credential's token in place when it is near expiry and can refresh.

        A best-effort freshening hook for a server-side consumer about to *use* the secret
        (e.g. syncing it into a provisioned agent): an OAuth credential whose access token
        is near expiry is renewed through its provider refresh grant; every other case — a
        non-expiring local token, a still-valid token, or a credential with no refresh
        grant — is a no-op. The refresh is serialized and re-checked under a session lock
        (:meth:`_refresh_locked`), so racing consumers issue at most one network refresh.
        A permanent refresh refusal records ``last_refresh_status="failed"`` and leaves
        the existing token for the consumer to try. Transient refusals propagate so its
        retry owner can reschedule without prompting for consent once the access
        token has expired. Before expiry the existing token remains usable. Unexpected errors
        also propagate.
        """

        if self.expires_at is None or self.expires_at > timezone.now() + _OAUTH_REFRESH_MARGIN:
            return
        if not self.refreshable:
            return
        try:
            self._refresh_locked()
        except (IntegrationError, ValueError) as error:
            if (
                isinstance(error, IntegrationError)
                and error.transient
                and self.expires_at is not None
                and self.expires_at <= timezone.now()
            ):
                raise
            logger.warning(
                "Credential %s refresh failed (%s); using the existing token.",
                self.pk,
                type(error).__name__,
            )

    def refresh_now(self) -> None:
        """Force a provider refresh now for an interactive caller, raising on failure.

        The explicit counterpart to :meth:`ensure_fresh`: a console refresh action
        renews the token regardless of how much life it has left and surfaces the
        outcome instead of swallowing it. Requires a refresh-capable provider and a
        stored refresh token — an expired *access* token still refreshes, since the
        grant uses the refresh token; otherwise raises ``ValueError`` telling the caller
        to reconnect. A provider rejecting the grant records ``last_refresh_status`` as
        ``"failed"`` and re-raises (``OAuthFlowError``) so the caller can report it.
        Serialized under the same session lock as :meth:`ensure_fresh`.
        """

        if not self.refreshable:
            raise ValueError("This credential cannot be refreshed; reconnect the account.")
        self._refresh_locked(force=True)

    def _refresh_locked(self, *, force: bool = False) -> None:
        """Serialize token exchange without holding a database transaction.

        Re-read after acquisition. A short row-lock fence rejects results for a
        grant reconnected, revoked or edited while the provider was responding.
        The session advisory lock excludes rotating-token exchanges in other workers.
        """

        rows = type(self).objects.db_manager(hints={"instance": self})
        if transaction.get_connection(rows.db).in_atomic_block:
            raise RuntimeError("Credential refresh requires autocommit.")
        with task_lock(record_lock_key(self._meta.label_lower, self.pk, "refresh")) as acquired:
            if not acquired:
                raise IntegrationError("Credential refresh is already running.", transient=True)
            with system_context(reason="integrate.credential.refresh"):
                current = rows.select_related("oauth_client").get(pk=self.pk)
                stale = current.expires_at is not None and current.expires_at <= timezone.now() + _OAUTH_REFRESH_MARGIN
                if current.status != CredentialStatus.ACTIVE or not (force or stale):
                    self.refresh_from_db()
                    return
                if not current.refreshable:
                    raise ValueError("This credential cannot be refreshed; reconnect the account.")
                material = current.reveal()
                refresh_value = str(material.get("refresh_token") or "")
                fence = current.refresh_fence
                try:
                    tokens = OAuthClientProtocol(current.oauth_client).refresh_token(refresh_token=refresh_value)
                except (IntegrationError, ValueError) as error:
                    if not isinstance(error, IntegrationError) or not error.transient:
                        with transaction.atomic(using=rows.db):
                            locked = rows.lock_if_supported().get(pk=self.pk)
                            if locked.refresh_fence == fence:
                                locked.last_refresh_status = "failed"
                                locked.save(update_fields=["last_refresh_status", "updated_at"])
                    self.refresh_from_db()
                    raise
                renewed = dict(tokens)
                renewed.setdefault("refresh_token", refresh_value)
                with transaction.atomic(using=rows.db):
                    locked = rows.lock_if_supported().get(pk=self.pk)
                    if locked.refresh_fence == fence:
                        rows.upsert_for_user(
                            locked.user,
                            locked.oauth_client,
                            CredentialKind.OAUTH,
                            renewed,
                            external_account=locked.external_account,
                            reconnect=False,
                        )
                self.refresh_from_db()

    @property
    def refresh_fence(self) -> tuple[Any, ...]:
        """Identify the exact grant whose exchange may update this row."""

        return (
            self.material,
            self.status,
            self.oauth_client_id,
            self.external_account_id,
            self.user_id,
            self.last_refresh_at,
        )
