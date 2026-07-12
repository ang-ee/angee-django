"""Source models for Angee's integration runtime primitives.

This addon owns the integration layer end to end. The connection substrate — the
``OAuthClient`` registration, the user's ``ExternalAccount`` at a provider, and the
per-user ``Credential`` material — authenticates everything above it. On top of
that sit the third-party ``Vendor`` catalogue, the first-class ``Integration``
an integration runs over, concrete child integration kinds such as ``VcsBridge``,
addon-owned children such as ``agents.InferenceProvider``, the
host-agnostic VCS inventory (``VcsBridge`` + ``Repository``/``Source``/
``Template``), and outbound ``WebhookSubscription``.

This addon is pure OAuth: it connects *out* to external systems and never
authenticates a session. OIDC login fields and ID-token verification live one
level up in ``iam_integrate_oidc``, which extends this OAuth base and composes the
``iam`` user. Host-specific VCS backends live in their own addons
(``integrate_github``) and are named per ``VcsBridge.backend_class`` row; this
addon never imports them.
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from functools import cache
from typing import TYPE_CHECKING, Any, ClassVar, cast
from urllib.parse import urlsplit, urlunsplit

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import checks
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections, models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.text import capfirst
from rebac import (
    RelationshipTuple,
    app_settings,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model
from strawberry_django.descriptors import model_property

from angee.base.fields import EncryptedField, StateField
from angee.base.impl import ImplClassField, ImplDefaultsMixin
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeManager, AngeeModel, AngeeQuerySet
from angee.base.transitions import StateTransitions, save_state, transition
from angee.integrate import registry
from angee.integrate.credentials import CredentialKind, handler_for
from angee.integrate.events import EventKind
from angee.integrate.impl import IntegrationImpl
from angee.integrate.locks import bridge_is_locked
from angee.integrate.net import validate_public_url
from angee.integrate.oauth.discovery import discovery_document
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.oauth.providers import OAuthProviderType
from angee.integrate.sync import bridge_progress_context, bridge_sync_context
from angee.integrate.vcs.backend import VCSBackend
from angee.integrate.vcs.templates import parse_template_meta
from angee.integrate.webhooks import PinnedWebhookClient, WebhookDeliveryError
from angee.tasks.locks import LockKey, record_lock_key, task_locks_are_cross_process

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Renew an OAuth access token this far ahead of its expiry, so a consumer about to
# use it (e.g. provisioning) gets a token with life left rather than one about to lapse.
_OAUTH_REFRESH_MARGIN = timedelta(minutes=5)
_UNSET = object()


class AccountStatus(models.TextChoices):
    """Connection lifecycle for a linked external account.

    Pure connection health — does the account's credential still work. The
    integration implementation health lives on ``Integration``, not here.
    """

    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    REVOKED = "revoked", "Revoked"


class CredentialStatus(models.TextChoices):
    """Lifecycle state for per-user credential material."""

    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    REVOKED = "revoked", "Revoked"


class OAuthClientQuerySet(AngeeQuerySet[Any]):
    """REBAC-scoped reads for OAuth client registration."""

    def connectable(self) -> OAuthClientQuerySet:
        """Return enabled, client-configured OAuth clients for the connect picker."""

        return cast(
            OAuthClientQuerySet,
            self.system_context(reason="integrate.graphql.connectable").filter(is_enabled=True).exclude(client_id=""),
        )

    def enabled_for_slug(self, slug: str, *, environment: str = "prod") -> Any | None:
        """Return the preferred OAuth client for a slug when that row is enabled."""

        client = self.filter(slug=slug, environment=environment).first()
        client = client or self.filter(slug=slug).order_by("environment").first()
        return client if client is not None and client.is_enabled else None


class OAuthClientManager(AngeeManager.from_queryset(OAuthClientQuerySet)):  # type: ignore[misc]
    """Manager for settings-sourced OAuth client registration.

    OAuth only: settings entries carry the OAuth base fields. The OIDC refinement
    (issuer/JWKS/discovery + login policy) is owned by the ``iam_integrate_oidc``
    addon and seeded there, never from here.
    """

    seed_fields = frozenset(
        {
            "display_name",
            "provider_type",
            "icon",
            "client_id",
            "discovery_url",
            "authorize_endpoint",
            "token_endpoint",
            "revoke_endpoint",
            "token_request_format",
            "is_enabled",
            "scopes_catalogue",
            "default_scopes",
            "supports_refresh",
            "refresh_rotates",
            "supports_pkce",
            "max_refresh_age_seconds",
            "authorize_params",
            "token_params",
            "manual_redirect_uri",
            "loopback_redirect_path",
        }
    )
    setting_fields = seed_fields | frozenset({"slug", "environment", "client_secret"})
    required_setting_fields = frozenset({"slug", "display_name", "client_id"})

    def sync_from_settings(
        self,
        entries: Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | None = None,
    ) -> tuple[Any, ...]:
        """Create or update OAuth clients declared in ``settings.ANGEE_INTEGRATE_OAUTH_CLIENTS``.

        The host owns reading environment variables. Integrate reads only Django
        settings and keeps secrets out of resource files.
        """

        synced: list[Any] = []
        with system_context(reason="integrate.oauth_clients.seed"), transaction.atomic():
            for index, entry in enumerate(self._setting_entries(entries), start=1):
                synced.append(self._sync_setting_entry(index, entry))
        return tuple(synced)

    def _setting_entries(
        self,
        entries: Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return normalized setting entries from an explicit value or settings."""

        raw_entries = getattr(settings, "ANGEE_INTEGRATE_OAUTH_CLIENTS", ()) if entries is None else entries
        if not raw_entries:
            return ()
        if isinstance(raw_entries, Mapping):
            values = tuple(raw_entries.values())
        elif isinstance(raw_entries, Iterable) and not isinstance(raw_entries, str | bytes):
            values = tuple(raw_entries)
        else:
            raise ValueError("ANGEE_INTEGRATE_OAUTH_CLIENTS must be a sequence or mapping of OAuth client entries.")
        for index, entry in enumerate(values, start=1):
            if not isinstance(entry, Mapping):
                raise ValueError(f"ANGEE_INTEGRATE_OAUTH_CLIENTS entry {index} must be a mapping.")
        return values

    def _sync_setting_entry(self, index: int, entry: Mapping[str, Any]) -> Any:
        """Upsert one settings-authored OAuth client."""

        self._validate_setting_entry(index, entry)
        slug = str(entry["slug"])
        environment = str(entry.get("environment") or "prod")
        defaults = {field: entry[field] for field in sorted(self.seed_fields) if field in entry}
        if "client_secret" in entry:
            defaults["client_secret"] = str(entry.get("client_secret") or "")
        oauth_client, _created = self.update_or_create(
            slug=slug,
            environment=environment,
            defaults=defaults,
        )
        return oauth_client

    def _validate_setting_entry(self, index: int, entry: Mapping[str, Any]) -> None:
        """Raise a clear error for malformed OAuth client seed settings."""

        unknown = set(entry) - self.setting_fields
        if unknown:
            names = ", ".join(sorted(str(name) for name in unknown))
            raise ValueError(f"ANGEE_INTEGRATE_OAUTH_CLIENTS entry {index} has unknown field(s): {names}")
        missing = {field for field in self.required_setting_fields if not entry.get(field)}
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"ANGEE_INTEGRATE_OAUTH_CLIENTS entry {index} is missing required field(s): {names}")


class OAuthClient(SqidMixin, ImplDefaultsMixin, AuditMixin, AngeeModel):
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

    runtime = True

    sqid_prefix = "clt_"
    slug = models.SlugField()
    provider_type = ImplClassField(
        base_class=OAuthProviderType,
        registry_setting="ANGEE_OAUTH_PROVIDER_TYPES",
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
        rebac_id_attr = "sqid"
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
    def token_param_values(self) -> dict[str, str]:
        """Return configured provider-specific token-exchange parameters."""

        return self._string_mapping(self.token_params)

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
        """Fetch discovery and fill blank OAuth/extension endpoints on this row."""

        discovery_url = str(getattr(self, "discovery_url", "") or "")
        if not discovery_url:
            return {}
        discovery = discovery_document(discovery_url)
        self.fill_endpoints_from_discovery(discovery)
        self.fill_extension_fields_from_discovery(discovery)
        return discovery


class ExternalAccountQuerySet(AngeeQuerySet[Any]):
    """REBAC-scoped reads for external accounts."""

    def console_external_accounts(self) -> ExternalAccountQuerySet:
        """Return admin-visible external accounts with guarded FK joins."""

        return cast(ExternalAccountQuerySet, self.rebac_select_related("oauth_client", "credential"))


class ExternalAccountManager(AngeeManager.from_queryset(ExternalAccountQuerySet)):  # type: ignore[misc]
    """Manager for idempotent external account linking.

    Actor-less framework writes run under ``system_context``; update paths do not maintain ``updated_by``.
    """

    caller_fields = frozenset(
        {
            "email",
            "display_name",
            "avatar_url",
            "status",
            "identity_claims",
            "last_error",
            "last_error_at",
        }
    )

    def link(
        self,
        oauth_client: Any,
        external_id: str,
        *,
        owner: Any | None = None,
        **identity: Any,
    ) -> Any:
        """Create or update one ``(oauth_client, external_id)`` external account."""

        reason = "integrate.connections.link"
        update_values = _validated_manager_values(
            self.model,
            identity,
            allowed=self.caller_fields,
        )
        create_values = {
            "email": "",
            "display_name": "",
            "avatar_url": "",
            "status": AccountStatus.ACTIVE,
            "identity_claims": {},
            "last_error": "",
            "last_error_at": None,
            "last_used_at": None,
            **update_values,
        }
        with system_context(reason=reason), transaction.atomic():
            instance, created = self.update_or_create(
                oauth_client=oauth_client,
                external_id=external_id,
                defaults=update_values,
                create_defaults=create_values,
            )
            if owner is not None and (created or self.owner_for(instance) is None):
                self.grant_owner(instance, owner)
        return instance

    def grant_owner(self, account: Any, owner: Any) -> None:
        """Grant ``owner`` direct ownership of an external account."""

        write_relationships(
            [
                RelationshipTuple(
                    resource=to_object_ref(account),
                    relation="owner",
                    subject=to_subject_ref(owner),
                )
            ]
        )

    def revoke_owner(self, account: Any, owner: Any) -> None:
        """Revoke ``owner`` direct ownership of an external account."""

        delete_relationship(
            RelationshipTuple(
                resource=to_object_ref(account),
                relation="owner",
                subject=to_subject_ref(owner),
            )
        )

    def owner_for(self, account: Any) -> Any | None:
        """Return the user granted owner on ``account``, if one exists."""

        resource_ref = to_object_ref(account)
        Relationship = active_relationship_model()
        with system_context(reason="integrate.connections.owner"):
            row = (
                Relationship.objects.filter(
                    resource_type=resource_ref.resource_type,
                    resource_id=resource_ref.resource_id,
                    relation="owner",
                    subject_type=app_settings.REBAC_USER_TYPE,
                    optional_subject_relation="",
                )
                .order_by_subject()
                .first()
            )
            if row is None:
                return None
            UserModel = get_user_model()
            # REBAC exposes SubjectRef creation publicly, but not inverse model lookup.
            user_id_attr = str(getattr(UserModel._meta, "rebac_id_attr", None) or app_settings.REBAC_USER_ID_ATTR)
            try:
                return UserModel.objects.get(**{user_id_attr: row.subject_id})
            except UserModel.DoesNotExist:
                return None

class ExternalAccount(SqidMixin, AuditMixin, AngeeModel):
    """A user's identity at a provider, shared by principals through REBAC grants.

    Connection identity only: which client minted it (``oauth_client``), which
    external subject (``external_id``), and the credential that authenticates as
    it. The integration that *runs over* a connection lives in
    ``integrate.Integration``, which owns implementation health.
    """

    runtime = True

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
    last_error = models.TextField(blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = ExternalAccountManager()

    class Meta:
        """Django model options for external accounts."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "integrate/external_account"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("oauth_client", "external_id"),
                name="uniq_integrate_external_account_oauth_client_external_id",
            ),
        )

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


class CredentialQuerySet(AngeeQuerySet[Any]):
    """REBAC-scoped reads for credential health and connected accounts."""

    def connected_for(self, user: Any) -> CredentialQuerySet:
        """Return ``user``'s external-account-backed credentials.

        Each row is a credential whose ``external_account`` (and that account's
        ``credential``) is preloaded under the ambient actor's scope. Owns the
        "what counts as a connected account" predicate for the self-service
        connected-accounts surface.
        """

        return cast(
            CredentialQuerySet,
            self.filter(
                user=user,
                external_account__isnull=False,
            ).rebac_select_related("external_account", "external_account__credential"),
        )

    def console_credentials(self) -> CredentialQuerySet:
        """Return admin-visible credential health with guarded FK joins."""

        return cast(
            CredentialQuerySet,
            self.rebac_select_related(
                "oauth_client",
                "external_account",
            ),
        )


class CredentialManager(AngeeManager.from_queryset(CredentialQuerySet)):  # type: ignore[misc]
    """Manager for idempotent per-user credential writes.

    Actor-less framework writes run under ``system_context``; update paths do not maintain ``updated_by``.
    """

    caller_fields = frozenset(
        {
            "external_account",
            "status",
            "expires_at",
            "granted_scopes",
            "last_refresh_at",
            "last_refresh_status",
        }
    )
    operation_fields = frozenset({"kind", "material"})

    _REASON = "integrate.connections.credential"

    def check_disconnect(self, credential: Any) -> None:
        """Run installed credential-disconnect guards for an explicit disconnect."""

        for guard in credential_disconnect_guards():
            guard(credential)

    def live_oauth_for_user(self, user: Any, oauth_client: Any) -> Any | None:
        """Return this user's active, non-expired OAuth credential for one client."""

        with system_context(reason="integrate.connections.credential.live"):
            return (
                self.filter(
                    user=user,
                    oauth_client=oauth_client,
                    kind=CredentialKind.OAUTH,
                    status=CredentialStatus.ACTIVE,
                )
                .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
                .select_related("external_account", "oauth_client")
                .first()
            )

    def upsert_for_user(
        self,
        user: Any,
        oauth_client: Any,
        kind: str,
        material: dict[str, Any],
        /,
        *,
        external_account: Any | None = None,
        **fields: Any,
    ) -> Any:
        """Create or update one ``(user, oauth_client)`` OAuth credential (connect/login flow)."""

        handler = handler_for(kind)
        operation_values, update_values = self._assemble_values(handler, material, fields)
        if external_account is not None:
            update_values["external_account"] = external_account
        create_values = {
            "external_account": external_account,
            **self._blank_create_values(),
            **operation_values,
            **update_values,
        }
        # Give the credential a human label on create (OAuth rows carry no name of their
        # own). Create-only so an admin rename, and token refreshes, are preserved.
        if not str(create_values.get("name") or ""):
            create_values["name"] = self._oauth_credential_name(oauth_client, external_account)
        with system_context(reason=self._REASON), transaction.atomic():
            instance, _created = self.update_or_create(
                user=user,
                oauth_client=oauth_client,
                defaults={**operation_values, **update_values},
                create_defaults=create_values,
            )
        return instance

    @staticmethod
    def _oauth_credential_name(oauth_client: Any, external_account: Any | None) -> str:
        """Return a default label for an OAuth credential: the provider, plus its subject."""

        label = str(getattr(oauth_client, "display_name", "") or getattr(oauth_client, "slug", "") or "OAuth")
        subject = ""
        if external_account is not None:
            subject = str(
                getattr(external_account, "email", "")
                or getattr(external_account, "display_name", "")
                or getattr(external_account, "external_id", "")
                or ""
            )
        return f"{label} ({subject})" if subject else label

    def create_local_credential(
        self,
        user: Any,
        *,
        kind: str,
        name: str,
        material: dict[str, Any],
        **fields: Any,
    ) -> Any:
        """Create or update one provider-less (static/ssh) credential, keyed by ``(user, name)``.

        The provider-less counterpart to :meth:`upsert_for_user`: ``oauth_client`` is
        ``NULL`` (the kind/provider invariant forbids one), and ``name`` is the
        credential's identity. OAuth credentials are minted by the connect/login flow only.
        """

        handler = handler_for(kind)
        if handler.kind == CredentialKind.OAUTH:
            raise ValueError("OAuth credentials are minted by the connect/login flow, not create_local_credential().")
        if not name:
            raise ValueError("A provider-less credential requires a name.")
        operation_values, update_values = self._assemble_values(handler, material, fields)
        create_values = {
            "oauth_client": None,
            "external_account": None,
            **self._blank_create_values(),
            **operation_values,
            **update_values,
        }
        with system_context(reason=self._REASON), transaction.atomic():
            instance, _created = self.update_or_create(
                user=user,
                name=name,
                oauth_client=None,
                defaults={**operation_values, **update_values},
                create_defaults=create_values,
            )
        return instance

    def _assemble_values(
        self,
        handler: Any,
        material: dict[str, Any],
        fields: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return ``(operation_values, update_values)`` for both create paths."""

        handler.validate(material)
        if owned := self.operation_fields & fields.keys():
            names = ", ".join(sorted(owned))
            raise ValueError(f"Credential field(s) are owned by the manager: {names}")
        update_values = handler.upsert_fields(material)
        update_values.update(_validated_manager_values(self.model, fields, allowed=self.caller_fields))
        update_values["status"] = CredentialStatus.ACTIVE
        material_value = json.dumps(material, sort_keys=True, separators=(",", ":"))
        operation_values = {"kind": handler.kind, "material": material_value}
        return operation_values, update_values

    @staticmethod
    def _blank_create_values() -> dict[str, Any]:
        """Return the inactive-refresh defaults a fresh credential row starts from."""

        return {
            "status": CredentialStatus.ACTIVE,
            "expires_at": None,
            "granted_scopes": [],
            "last_refresh_at": None,
            "last_refresh_status": "",
        }


@cache
def credential_disconnect_guards() -> tuple[Any, ...]:
    """Return configured credential-disconnect guard callables."""

    return tuple(import_string(str(path)) for path in getattr(settings, "ANGEE_CREDENTIAL_DISCONNECT_GUARDS", ()))


def check_credential_disconnect_guards(
    app_configs: list[object] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Validate configured credential-disconnect guard callables."""

    del app_configs, kwargs
    errors: list[checks.CheckMessage] = []
    for path in getattr(settings, "ANGEE_CREDENTIAL_DISCONNECT_GUARDS", ()):
        try:
            guard = import_string(str(path))
        except (AttributeError, ImportError, ModuleNotFoundError) as error:
            errors.append(
                checks.Error(
                    f"ANGEE_CREDENTIAL_DISCONNECT_GUARDS entry {path!r} cannot be imported: {error}",
                    id="angee.integrate.E003",
                )
            )
            continue
        if not callable(guard):
            errors.append(
                checks.Error(
                    f"ANGEE_CREDENTIAL_DISCONNECT_GUARDS entry {path!r} is not callable.",
                    id="angee.integrate.E004",
                )
            )
    return errors


class Credential(SqidMixin, AuditMixin, AngeeModel):
    """Per-user credential material for acting against a vendor OAuth client."""

    runtime = True

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

    objects = CredentialManager()

    class Meta:
        """Django model options for credentials."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "integrate/credential"
        rebac_id_attr = "sqid"
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
    def handler(self) -> Any:
        """Return the registered handler for this credential kind."""

        return handler_for(self.kind)

    def reveal(self) -> dict[str, Any]:
        """Return decrypted material through the kind handler."""

        return self.handler.reveal(self)

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
        grant — is a no-op. The refresh is serialized and re-checked under a row lock
        (:meth:`_refresh_locked`), so racing consumers issue at most one network refresh.
        A provider rejecting the refresh is recorded (``last_refresh_status="failed"``) and
        logged, not raised, so it never blocks the consumer (the stale token then fails
        downstream as it would have anyway); unexpected errors propagate.
        """

        if self.expires_at is None or self.expires_at > timezone.now() + _OAUTH_REFRESH_MARGIN:
            return
        if not self.handler.can_refresh(self):
            return
        try:
            self._refresh_locked()
        except OAuthFlowError, ValueError:
            logger.warning("Credential %s refresh failed; using the existing token.", self.pk, exc_info=True)
            self._record_refresh_failure()

    def refresh_now(self) -> None:
        """Force a provider refresh now for an interactive caller, raising on failure.

        The explicit counterpart to :meth:`ensure_fresh`: a console refresh action
        renews the token regardless of how much life it has left and surfaces the
        outcome instead of swallowing it. Requires a refresh-capable provider and a
        stored refresh token — an expired *access* token still refreshes, since the
        grant uses the refresh token; otherwise raises ``ValueError`` telling the caller
        to reconnect. A provider rejecting the grant records ``last_refresh_status`` as
        ``"failed"`` and re-raises (``OAuthFlowError``) so the caller can report it.
        Serialized under the same row lock as :meth:`ensure_fresh`.
        """

        if not self.handler.can_refresh(self):
            raise ValueError("This credential cannot be refreshed; reconnect the account.")
        try:
            self._refresh_locked(force=True)
        except OAuthFlowError, ValueError:
            self._record_refresh_failure()
            raise

    def _refresh_locked(self, *, force: bool = False) -> None:
        """Refresh under a row lock, skipping the network when a concurrent consumer won.

        Locks the credential row, re-reads its expiry, and performs the provider refresh
        while it is *still* stale — so two consumers racing to refresh the same
        credential (concurrent provisions, or one plan's inference + MCP reads) issue at
        most one network refresh and never replay a rotated refresh token. ``force``
        renews regardless of expiry for an explicit, user-initiated refresh, still under
        the lock so it serializes against the lazy path. The in-memory instance is
        reloaded from the persisted row either way, so it adopts whichever consumer's
        tokens won.
        """

        with transaction.atomic():
            locked = type(self).objects.sudo(reason="integrate.credential.refresh").locked_get(pk=self.pk)
            stale = locked.expires_at is not None and locked.expires_at <= timezone.now() + _OAUTH_REFRESH_MARGIN
            if force or stale:
                self.handler.refresh(locked)
        self.refresh_from_db()

    def _record_refresh_failure(self) -> None:
        """Persist a failed-refresh marker so the console can prompt re-authorization."""

        self.last_refresh_status = "failed"
        with system_context(reason="integrate.credential.refresh.failed"):
            self.save(update_fields=["last_refresh_status", "updated_at"])


def _validated_manager_values(
    model: type[models.Model],
    values: dict[str, Any],
    *,
    allowed: frozenset[str],
) -> dict[str, Any]:
    """Return caller values validated against one manager-owned field set."""

    unknown = set(values) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown {model.__name__} field(s): {names}")
    result = dict(values)
    if "status" in result:
        result["status"] = model._meta.get_field("status").to_python(result["status"])
    return result


class Vendor(SqidMixin, AuditMixin, AngeeModel):
    """Admin-managed third-party catalogue (GitHub, Google, Slack, …).

    The single source of truth for "what is this third party" — branding and
    reference metadata only. New integration addons add their own row via an
    install-tier resource seed (``adopt: slug``). The connect-side ``OAuthClient``
    carries its own ``slug``; that is a deliberately independent namespace, not a
    foreign key into this catalogue.
    """

    runtime = True
    catalogue = True
    catalogue_tier = "master"

    sqid_prefix = "vnd_"
    slug = models.SlugField(unique=True)
    display_name = models.CharField(max_length=128)
    website_url = models.URLField(blank=True)
    icon = models.CharField(max_length=128, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        """Django model options for integration vendors."""

        abstract = True
        ordering = ("slug",)
        rebac_resource_type = "integrate/vendor"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the display label used by Django surfaces."""

        return self.display_name or self.slug


class IntegrationLifecycle(models.TextChoices):
    """Declared lifecycle state for one integration implementation."""

    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    DISABLED = "disabled", "Disabled"

    @classmethod
    def from_value(cls, value: object) -> IntegrationLifecycle:
        """Return the member for one string or enum integration-lifecycle value."""

        raw = str(getattr(value, "value", value)).strip()
        member = cls.__members__.get(raw)
        if member is not None:
            return cast(IntegrationLifecycle, member)
        try:
            return cast(IntegrationLifecycle, cls(raw))
        except ValueError as error:
            raise ValueError(f"Unsupported integration lifecycle: {raw}") from error


class IntegrationRuntimeStatus(models.TextChoices):
    """Observed runtime health for one integration implementation."""

    OK = "ok", "OK"
    ERROR = "error", "Error"

    @classmethod
    def from_value(cls, value: object) -> IntegrationRuntimeStatus:
        """Return the member for one string or enum runtime-status value."""

        raw = str(getattr(value, "value", value)).strip()
        member = cls.__members__.get(raw)
        if member is not None:
            return cast(IntegrationRuntimeStatus, member)
        try:
            return cast(IntegrationRuntimeStatus, cls(raw))
        except ValueError as error:
            raise ValueError(f"Unsupported integration runtime status: {raw}") from error


def integration_status_axes(status: object) -> tuple[IntegrationLifecycle, IntegrationRuntimeStatus]:
    """Map one legacy fused integration status onto lifecycle and runtime axes."""

    # Legacy callers and migration tests can still hand the pre-split fused value
    # to telemetry code; keep that compatibility at the split-axis owner.
    raw = str(getattr(status, "value", status)).strip()
    lifecycle_member = IntegrationLifecycle.__members__.get(raw)
    if lifecycle_member is not None:
        return cast(IntegrationLifecycle, lifecycle_member), cast(IntegrationRuntimeStatus, IntegrationRuntimeStatus.OK)
    if raw in {"ERROR", "error"}:
        return (
            cast(IntegrationLifecycle, IntegrationLifecycle.ACTIVE),
            cast(IntegrationRuntimeStatus, IntegrationRuntimeStatus.ERROR),
        )
    try:
        return (
            cast(IntegrationLifecycle, IntegrationLifecycle(raw)),
            cast(IntegrationRuntimeStatus, IntegrationRuntimeStatus.OK),
        )
    except ValueError as error:
        raise ValueError(f"Unsupported legacy integration status: {raw}") from error


class IntegrationManager(AngeeManager):
    """Manager factories for invariants that span Integration and its impl row."""

    def impl_class_for_key(self, key: str) -> type[IntegrationImpl]:
        """Return the implementation class registered for ``key`` on this model."""

        return cast(type[IntegrationImpl], self.model.resolve_impl_class("impl_class", key))

    def draft_for(self, user: Any, *, vendor: Any, impl_class: str) -> Any:
        """Return the parent integration row for a user's vendor/implementation draft."""

        with system_context(reason="integrate.integration.draft"):
            integration = (
                self.filter(
                    owner=user,
                    vendor=vendor,
                    impl_class=impl_class,
                    kind=Integration.integration_kind_label,
                )
                .order_by("pk")
                .first()
            )
            if integration is not None:
                return integration
            try:
                with transaction.atomic():
                    return self.create(
                        owner=user,
                        vendor=vendor,
                        impl_class=impl_class,
                        kind=Integration.integration_kind_label,
                        lifecycle=IntegrationLifecycle.DRAFT,
                    )
            except IntegrityError:
                return self.get(
                    owner=user,
                    vendor=vendor,
                    impl_class=impl_class,
                    kind=Integration.integration_kind_label,
                )

    def activate_from_credential(
        self,
        user: Any,
        *,
        vendor: Any,
        credential: Any,
        impl_class: str = "none",
    ) -> Any:
        """Attach ``credential`` to the user's parent integration row and activate it."""

        integration = self.draft_for(user, vendor=vendor, impl_class=impl_class)
        with system_context(reason="integrate.integration.activate_from_credential"), transaction.atomic():
            integration = self.locked_get(pk=integration.pk)
            account = getattr(credential, "external_account", None)
            if str(integration.lifecycle) == str(IntegrationLifecycle.ACTIVE):
                integration.credential = credential
                integration.account = account
                integration.runtime_status = cast(IntegrationRuntimeStatus, IntegrationRuntimeStatus.OK)
                integration.last_error = ""
                integration.last_error_at = None
                integration.save(
                    update_fields=[
                        "account",
                        "credential",
                        "last_error",
                        "last_error_at",
                        "runtime_status",
                        "updated_at",
                    ]
                )
            else:
                integration.activate(credential=credential, account=account)
        return integration

    def sync_kinds(self) -> int:
        """Backfill parent rows with the concrete integration kind they materialize."""

        parent = self.model._base_manager
        count = parent.filter(kind="").update(kind=self.model.integration_kind_value())
        connection = connections[self.db]
        for child_model in _integration_child_models(cast(type[Integration], self.model)):
            if not child_model._meta.can_migrate(connection):
                continue
            kind = child_model.integration_kind_value()
            count += (
                parent.filter(pk__in=child_model._base_manager.values("pk"))
                .exclude(kind=kind)
                .update(kind=kind)
            )
        return count


class Integration(SqidMixin, ImplDefaultsMixin, AuditMixin, AngeeModel):
    """A product/workspace integration to a vendor account.

    The first-class "what we're connected to and what runs over it": it draws a
    ``credential`` (and optionally an ``account``) from the connection substrate to
    authenticate, points at a catalogue ``vendor``, and stores the implementation
    key that owns integration-level behavior. Domain-specific state and config live
    on concrete child models.
    """

    runtime = True

    sqid_prefix = "int_"
    integration_kind_label = "Integration"
    """Human kind label for parent-level integration grouping."""
    # Operator-given label (the connect flow sets it); blank falls back to the
    # vendor-derived :attr:`display_label`. The one human name for every child
    # (directory, channel, …), so it is not re-buried in each child's config.
    display_name = models.CharField(max_length=255, blank=True, default="")
    kind = models.CharField(max_length=80, db_index=True, default=integration_kind_label)
    """Human integration type/kind label, denormalized for server-side grouping."""
    vendor = models.ForeignKey("integrate.Vendor", on_delete=models.PROTECT, related_name="integrations")
    impl_class = ImplClassField(
        base_class=IntegrationImpl,
        registry_setting="ANGEE_INTEGRATION_IMPLS",
        default="none",
    )
    """Registry key for the implementation this integration runs."""
    # PROTECT: a present credential is the integration's authentication. It may
    # belong to a principal other than ``owner`` (an org/app-install credential), so
    # deleting a credential still in use is refused rather than silently breaking
    # the integration. Draft integrations leave it empty.
    credential = models.ForeignKey(
        "integrate.Credential",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="integrations",
    )
    account = models.ForeignKey(
        "integrate.ExternalAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="integrations",
    )
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="integrations")
    lifecycle = StateField(choices_enum=IntegrationLifecycle, default=IntegrationLifecycle.DRAFT)
    """Declared lifecycle journey for the integration connection itself.

    This is the operator-controlled capability axis: draft setup, active use,
    paused, or disabled. Runtime health lives separately on ``runtime_status``.
    """
    runtime_status = StateField(choices_enum=IntegrationRuntimeStatus, default=IntegrationRuntimeStatus.OK)
    """Observed health for the integration's last runtime interaction.

    ``OK`` and ``ERROR`` describe the last credential/sync/use outcome; they do
    not move the lifecycle journey.
    """
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_status = models.CharField(max_length=64, blank=True)
    use_count_24h = models.PositiveIntegerField(default=0)
    error_count_24h = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)

    lifecycle_transitions = StateTransitions(
        lifecycle,
        {
            IntegrationLifecycle.DRAFT: [
                IntegrationLifecycle.ACTIVE,
                IntegrationLifecycle.DISABLED,
            ],
            IntegrationLifecycle.ACTIVE: [
                IntegrationLifecycle.PAUSED,
                IntegrationLifecycle.DISABLED,
            ],
            IntegrationLifecycle.PAUSED: [
                IntegrationLifecycle.ACTIVE,
                IntegrationLifecycle.DISABLED,
            ],
            IntegrationLifecycle.DISABLED: [IntegrationLifecycle.ACTIVE],
        },
    )

    objects = IntegrationManager()

    class Meta:
        """Django model options for integrations."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "integrate/integration"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("owner", "vendor", "impl_class"),
                condition=Q(kind="Integration"),
                name="uniq_integrate_parent_owner_vendor_impl",
            ),
        )

    def __str__(self) -> str:
        """Return a stable vendor-qualified integration label."""

        vendor_slug = getattr(getattr(self, "vendor", None), "slug", "?")
        return f"{vendor_slug}:{self.public_id}"

    @classmethod
    def integration_kind_value(cls) -> str:
        """Return the grouping label this integration concrete model contributes."""

        if _is_integration_child_model(cls):
            for base in cls.__mro__:
                label = base.__dict__.get("integration_kind_label")
                meta = getattr(base, "_meta", None)
                if label and getattr(meta, "label_lower", "") != "integrate.integration":
                    return str(label)
        else:
            own_label = cls.__dict__.get("integration_kind_label")
            if own_label:
                return str(own_label)
        return capfirst(cls._meta.verbose_name)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the parent grouping kind when a concrete child row saves."""

        current_kind = self.kind
        if _is_integration_child_model(type(self)) or not self.kind:
            self.kind = type(self).integration_kind_value()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and self.kind != current_kind:
            kwargs["update_fields"] = {*update_fields, "kind"}
        super().save(*args, **kwargs)

    @property
    def display_label(self) -> str:
        """Return the operator label, or a vendor-derived one when none was given.

        Headers, lists, and relation pickers read this so a named integration shows
        its name and an unnamed one still reads as ``Vendor (lifecycle)``.
        """

        if self.display_name:
            return str(self.display_name)
        vendor = getattr(self, "vendor", None)
        label = str(getattr(vendor, "display_name", "") or getattr(vendor, "slug", "") or "integration")
        return f"{label} ({self.lifecycle})"

    @property
    def impl(self) -> IntegrationImpl:
        """Return this row's integration-level implementation."""

        impl_class = cast(type[IntegrationImpl], self.resolve_impl("impl_class"))
        return impl_class(self)

    @transition(
        lifecycle,
        source=[IntegrationLifecycle.DRAFT, IntegrationLifecycle.PAUSED, IntegrationLifecycle.DISABLED],
        target=IntegrationLifecycle.ACTIVE,
        on_success=save_state,
    )
    def activate(self, *, credential: Any = _UNSET, account: Any = _UNSET) -> None:
        """Mark this integration active, optionally attaching connection rows."""

        fields: set[str] = set()
        if credential is not _UNSET:
            self.credential = credential
            fields.add("credential")
        if account is not _UNSET:
            self.account = account
            fields.add("account")
        self.runtime_status = cast(IntegrationRuntimeStatus, IntegrationRuntimeStatus.OK)
        self.last_error = ""
        self.last_error_at = None
        fields.update({"last_error", "last_error_at", "runtime_status"})
        self._transition_fields = fields

    @transition(
        lifecycle,
        source=IntegrationLifecycle.ACTIVE,
        target=IntegrationLifecycle.PAUSED,
        on_success=save_state,
    )
    def pause(self) -> None:
        """Pause this active integration without changing its runtime health."""

    @transition(
        lifecycle,
        source=[IntegrationLifecycle.DRAFT, IntegrationLifecycle.ACTIVE, IntegrationLifecycle.PAUSED],
        target=IntegrationLifecycle.DISABLED,
        on_success=save_state,
    )
    def disable(self) -> None:
        """Disable this integration without changing its runtime health."""

    def set_lifecycle(self, lifecycle: IntegrationLifecycle | str) -> None:
        """Move to one declared lifecycle state using this model's transition methods."""

        target = IntegrationLifecycle.from_value(lifecycle)
        if str(self.lifecycle) == target.value:
            return
        if target == IntegrationLifecycle.ACTIVE:
            self.activate()
        elif target == IntegrationLifecycle.PAUSED:
            self.pause()
        elif target == IntegrationLifecycle.DISABLED:
            self.disable()
        else:
            self.lifecycle_transitions.not_allowed(self.lifecycle, target)

    def attach_credential(self, credential: Any) -> None:
        """Attach a live credential and activate this draft integration."""

        account = getattr(credential, "external_account", None)
        if self.pk is None:
            self.credential = credential
            self.account = account
            self.runtime_status = cast(IntegrationRuntimeStatus, IntegrationRuntimeStatus.OK)
            self.last_error = ""
            self.last_error_at = None
            if self.lifecycle == IntegrationLifecycle.DRAFT:
                self.lifecycle_transitions.force_state(
                    self,
                    IntegrationLifecycle.ACTIVE,
                    reason="unsaved integration attach resolves initial lifecycle before insert",
                )
            return
        if self.lifecycle == IntegrationLifecycle.DRAFT:
            with system_context(reason="integrate.integration.attach_credential"), transaction.atomic():
                self.activate(credential=credential, account=account)
            return
        self.credential = credential
        self.account = account
        self.runtime_status = cast(IntegrationRuntimeStatus, IntegrationRuntimeStatus.OK)
        self.last_error = ""
        self.last_error_at = None
        with system_context(reason="integrate.integration.attach_credential"), transaction.atomic():
            self.save(
                update_fields=[
                    "account",
                    "credential",
                    "last_error",
                    "last_error_at",
                    "runtime_status",
                    "updated_at",
                ]
            )

    def report_status(self, status: IntegrationRuntimeStatus | IntegrationLifecycle | str, error: str = "") -> None:
        """Record implementation status telemetry and persist this integration."""

        raw_status = str(getattr(status, "value", status)).strip()
        try:
            normalized = IntegrationRuntimeStatus.from_value(status)
        except ValueError:
            _lifecycle, normalized = integration_status_axes(status)
        reported_at = timezone.now()
        self.runtime_status = normalized
        self.last_used_at = reported_at
        self.last_used_status = raw_status or normalized.value
        self.last_error = error
        self.last_error_at = reported_at if error else None

        if self.pk is None:
            return

        with system_context(reason="integrate.integration.status"), transaction.atomic():
            self.save(
                update_fields=[
                    "last_error",
                    "last_error_at",
                    "last_used_at",
                    "last_used_status",
                    "runtime_status",
                    "updated_at",
                ]
            )


def _is_integration_child_model(model: type[models.Model]) -> bool:
    """Return whether ``model`` is an MTI child of the Integration parent."""

    return any(parent._meta.label_lower == "integrate.integration" for parent in model._meta.parents)


def _integration_child_models(parent_model: type[Integration]) -> tuple[type[Integration], ...]:
    """Return concrete Integration children in deterministic model-label order."""

    return tuple(
        cast(type[Integration], model)
        for model in sorted(
            (
                model
                for model in apps.get_models()
                if model is not parent_model
                and not model._meta.abstract
                and not model._meta.proxy
                and issubclass(model, parent_model)
            ),
            key=lambda model: model._meta.label_lower,
        )
    )


class Bridge(AngeeModel):
    """Abstract base for child models that synchronize or subscribe to vendor data.

    Pure bridge state and behavior. A materialized bridge extends
    ``integrate.Integration`` so common identity, credential, status, and audit
    fields stay on the integration parent row while bridge-specific settings stay
    on the child.
    """

    class SyncStage(models.TextChoices):
        """Generic lifecycle stage for bridge sync jobs."""

        IDLE = "idle", "Idle"
        QUEUED = "queued", "Queued"
        DISCOVERING = "discovering", "Discovering"
        SYNCING = "syncing", "Syncing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    config = models.JSONField(default=dict, blank=True)
    """Bridge-scoped settings interpreted by the selected backend."""
    cursor = models.JSONField(default=dict, blank=True)
    poll_interval = models.PositiveIntegerField(default=300)
    subscription_state = models.JSONField(default=dict, blank=True)
    next_subscription_refresh_at = models.DateTimeField(null=True, blank=True)
    last_sync_started_at = models.DateTimeField(null=True, blank=True)
    last_sync_completed_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=64, blank=True)
    last_sync_items = models.PositiveIntegerField(default=0)
    sync_stage = models.CharField(
        max_length=32,
        choices=SyncStage.choices,
        default=SyncStage.IDLE,
        db_index=True,
    )
    sync_error = models.TextField(blank=True, default="")
    sync_progress = models.JSONField(default=dict, blank=True)
    last_sync_summary = models.JSONField(default=dict, blank=True)
    next_sync_at = models.DateTimeField(null=True, blank=True, db_index=True)
    """Next scheduler poll. NULL means unscheduled: a bridge enters the poll loop
    when its first (eager) sync records a result; the scheduler claims a due row
    by pushing this one interval out for the duration of the run."""

    class Meta:
        """Django model options for abstract bridge inheritance."""

        abstract = True

    @property
    def is_syncing(self) -> bool:
        """Return whether a worker currently holds this bridge's live sync lock."""

        return bridge_is_locked(self)

    # The persisted stages that assert a live run. Their whole legitimate lifetime
    # is spent holding the advisory sync lock, so a row carrying one without the
    # lock is a stale record — the worker died before writing an outcome.
    LIVE_SYNC_STAGES: ClassVar[tuple[str, ...]] = (
        str(SyncStage.DISCOVERING),
        str(SyncStage.SYNCING),
    )

    @property
    def effective_sync_stage(self) -> str:
        """Return the sync stage reconciled against the live lock, not the record.

        The persisted ``sync_stage`` is a progress report, not the source of truth
        for "is a run alive" — only the advisory lock is. A crashed worker leaves
        ``syncing`` behind forever; this projection reports such a row as
        ``FAILED`` (the run was interrupted) instead of trusting the stale column.
        ``queued`` is exempt: a queued task legitimately holds no lock until a
        worker picks it up. When the lock backend is process-local (the SQLite
        floor), the web process cannot see a worker's lock at all — reconciling
        there would misreport every healthy run, so the column is trusted as-is.
        """

        stage = str(self.sync_stage)
        if (
            stage in self.LIVE_SYNC_STAGES
            and task_locks_are_cross_process()
            and not self.is_syncing
        ):
            return str(self.SyncStage.FAILED)
        return stage

    def sync_lock_key(self) -> LockKey:
        """Return the advisory task lock key for this bridge sync."""

        return record_lock_key(self._meta.label_lower, self.pk, "sync")

    def mark_sync_started(self, *, now: datetime) -> None:
        """Persist the start timestamp for one scheduler sync attempt."""

        self.last_sync_started_at = now
        self.sync_stage = self.SyncStage.SYNCING
        self.sync_error = ""
        self.sync_progress = {"stage": self.SyncStage.SYNCING, "started_at": now.isoformat()}
        with transaction.atomic():
            self.save(
                update_fields=[
                    "last_sync_started_at",
                    "sync_error",
                    "sync_progress",
                    "sync_stage",
                    "updated_at",
                ]
            )

    def claim_sync(self, *, now: datetime) -> None:
        """Push the next poll one interval out as an in-flight claim.

        The scheduler claims a due row under a row lock before running it, so an
        overlapping scan — a backfill outliving the tick cadence, or a second
        worker — re-reads the row, sees a future ``next_sync_at``, and skips
        instead of double-syncing one source. ``record_sync`` /
        ``record_sync_error`` recompute the real next poll when the run ends.
        """

        self.next_sync_at = self._next_sync_at(now=now)
        with transaction.atomic():
            self.save(update_fields=["next_sync_at", "updated_at"])

    def mark_sync_queued(self, *, now: datetime) -> None:
        """Persist that a worker task has been queued for this bridge."""

        self.sync_stage = self.SyncStage.QUEUED
        self.sync_error = ""
        self.sync_progress = {"stage": self.SyncStage.QUEUED, "queued_at": now.isoformat()}
        with transaction.atomic():
            self.save(update_fields=["sync_error", "sync_progress", "sync_stage", "updated_at"])

    def reset_sync_queue(self, *, now: datetime) -> None:
        """Make a failed queue dispatch due again for the next scheduler pass."""

        self.next_sync_at = now
        self.sync_stage = self.SyncStage.IDLE
        self.sync_progress = {}
        with transaction.atomic():
            self.save(update_fields=["next_sync_at", "sync_progress", "sync_stage", "updated_at"])

    def sync_queue_token_matches(self, timestamp: datetime) -> bool:
        """Return whether a queued task payload still matches this bridge row."""

        if self.sync_stage != self.SyncStage.QUEUED or not isinstance(self.sync_progress, Mapping):
            return False
        return self.sync_progress.get("queued_at") == timestamp.isoformat()

    def record_sync(self, result: int, *, now: datetime) -> None:
        """Persist one successful scheduler sync result and healthy status report."""

        self.last_sync_completed_at = now
        self.last_sync_status = "ok"
        self.last_sync_items = result
        self.sync_stage = self.SyncStage.COMPLETED
        self.sync_error = ""
        progress = dict(self.sync_progress) if isinstance(self.sync_progress, Mapping) else {}
        progress.update(
            {
                "stage": self.SyncStage.COMPLETED,
                "items": result,
                "completed_at": now.isoformat(),
            }
        )
        self.sync_progress = progress
        self.last_sync_summary = {
            "status": "ok",
            "items": result,
            "completed_at": now.isoformat(),
        }
        self.next_sync_at = self._next_sync_at(now=now)
        with transaction.atomic():
            cast(Any, self).report_status(status=IntegrationRuntimeStatus.OK)
            self.save(
                update_fields=[
                    "cursor",
                    "last_sync_summary",
                    "last_sync_completed_at",
                    "last_sync_items",
                    "last_sync_status",
                    "next_sync_at",
                    "sync_error",
                    "sync_progress",
                    "sync_stage",
                    "updated_at",
                ]
            )

    def record_sync_error(self, error: Exception, *, now: datetime) -> None:
        """Persist one failed scheduler sync result and error status report."""

        error_message = f"{type(error).__name__}: {error}"[:500]
        self.last_sync_status = "error"
        self.sync_stage = self.SyncStage.FAILED
        self.sync_error = error_message
        progress = dict(self.sync_progress) if isinstance(self.sync_progress, Mapping) else {}
        progress.update({"stage": self.SyncStage.FAILED, "error": error_message})
        self.sync_progress = progress
        self.next_sync_at = self._next_sync_at(now=now)
        with transaction.atomic():
            cast(Any, self).report_status(status=IntegrationRuntimeStatus.ERROR, error=error_message)
            self.save(
                update_fields=[
                    "last_sync_status",
                    "next_sync_at",
                    "sync_error",
                    "sync_progress",
                    "sync_stage",
                    "updated_at",
                ]
            )

    def run_sync(self, *, now: datetime) -> int:
        """Run one sync attempt and persist its lifecycle telemetry."""

        self.mark_sync_started(now=now)
        try:
            with bridge_sync_context(), bridge_progress_context(self):
                result = self.sync()
            self.record_sync(result, now=now)
        except Exception as error:  # noqa: BLE001 — sync failure is telemetry, then caller policy.
            self.record_sync_error(error, now=now)
            raise
        return result

    def sync(self) -> int:
        """Synchronize this bridge with its external system."""

        raise NotImplementedError("Bridge subclasses must implement sync().")

    def handle_webhook(self, payload: Any) -> None:
        """Apply one verified inbound webhook payload to this bridge."""

        raise NotImplementedError("Bridge subclasses must implement handle_webhook().")

    def verify_webhook(self, request: Any) -> bool:
        """Return whether an inbound webhook request is authentic for this bridge."""

        raise NotImplementedError("Bridge subclasses must implement verify_webhook().")

    def dispatch_inbound(self, request_or_payload: Any) -> bool:
        """Verify one inbound webhook and apply it to this bridge when authentic."""

        if not self.verify_webhook(request_or_payload):
            return False
        self.handle_webhook(request_or_payload)
        return True

    def start_live(self) -> None:
        """Start or renew this bridge's live vendor subscription."""

        raise NotImplementedError("Bridge subclasses must implement start_live().")

    def stop_live(self) -> None:
        """Stop this bridge's live vendor subscription."""

        raise NotImplementedError("Bridge subclasses must implement stop_live().")

    def _next_sync_at(self, *, now: datetime) -> datetime:
        """Return the next polling timestamp from this bridge's interval."""

        return now + timedelta(seconds=int(self.poll_interval))


class RepoVisibility(models.TextChoices):
    """Visibility of a git remote on its host."""

    PUBLIC = "public", "Public"
    PRIVATE = "private", "Private"
    INTERNAL = "internal", "Internal"


class VcsBridge(Bridge):
    """The VCS sync child model over ``Integration``.

    A :class:`Bridge`: the scheduler refreshes its repositories' sources over the
    host REST API and an inbound push webhook triggers the same refresh. The
    host-specific wire format is the integration child row's non-model
    :class:`~angee.integrate.vcs.backend.VCSBackend` implementation — so
    github/gitlab/bitbucket share this one table, differing only in behavior.
    Django keeps the inventory only; the operator performs every git operation,
    consuming :meth:`Source.materialize_spec`.
    """

    runtime = True
    extends = "integrate.Integration"
    integration_kind_label = "VCS bridge"

    backend_class = ImplClassField(
        base_class=VCSBackend,
        registry_setting="ANGEE_VCS_BACKEND_CLASSES",
        default="local",
    )
    """Registry key for the VCS backend bound to this bridge."""
    webhook_secret = EncryptedField(blank=True)
    """Shared secret for verifying inbound push webhooks (per account, not per repo)."""

    objects = AngeeManager()

    class Meta:
        """Django model options for the VCS bridge child model."""

        abstract = True
        rebac_resource_type = "integrate/vcs_bridge"
        rebac_id_attr = "sqid"

    @property
    def backend(self) -> VCSBackend:
        """Return this bridge's selected VCS backend."""

        backend_class = cast(type[VCSBackend], self.resolve_impl("backend_class"))
        return backend_class(self)

    def repositories_by_org(self) -> dict[str, list[Any]]:
        """Return every visible repository grouped and sorted by owning org."""

        groups: dict[str, list[Any]] = {}
        for descriptor in self.backend.ls_repos():
            groups.setdefault(descriptor.org, []).append(descriptor)
        return {org: sorted(repos, key=lambda item: item.name) for org, repos in sorted(groups.items())}

    def discover(self, source: Any, *, marker: str, parse: Callable[[bytes], dict[str, Any]]) -> list[dict[str, Any]]:
        """Return one descriptor per directory under ``source`` bearing ``marker``.

        The single enumeration walk shared by every source kind: list the source's
        subtree, read each ``marker`` blob, parse it, record the bearing directory,
        and return the descriptors in deterministic order. A source kind's output
        manager supplies only its ``marker`` filename and ``parse`` function.
        """

        backend = self.backend
        repository = source.repository
        ref = source.ref or repository.default_branch
        descriptors: list[dict[str, Any]] = []
        for entry in backend.ls_tree(repository, ref=ref, path=source.path, recursive=True):
            if entry.type != "blob" or entry.name != marker:
                continue
            descriptor = dict(parse(backend.cat_file(repository, ref=ref, path=entry.path)))
            descriptor.setdefault("path", _parent_path(entry.path))
            descriptors.append(descriptor)
        return sorted(descriptors, key=_descriptor_key)

    def sync(self) -> int:
        """Refresh every inventoried repository's sources over REST (Bridge contract).

        Repository discovery (creating rows from the account) is the explicit
        ``discoverRepositories`` action; the scheduled/webhook ``sync`` refreshes the
        content of already-inventoried repositories.
        """

        source_model = apps.get_model("integrate", "Source")
        with system_context(reason="integrate.vcs_bridge.sync.sources"):
            sources = tuple(
                source_model.objects.filter(repository__vcs_bridge=self)
                .select_related("repository", "repository__vcs_bridge")
                .order_by("repository_id", "pk")
            )
        return sum(source.refresh() for source in sources)

    def handle_webhook(self, payload: Any) -> None:
        """Re-sync this bridge's inventory on an inbound push webhook."""

        del payload
        self.sync()

    def verify_webhook(self, request: Any) -> bool:
        """Return whether an inbound push webhook is authentic for this bridge."""

        return self.backend.verify_webhook(self, request)

    def search_repositories(self, query: str) -> list[Any]:
        """Return host repositories whose name matches ``query`` (the add typeahead)."""

        backend = self.backend
        return backend.search_repos(query, org=backend.repository_search_scope())

    def import_repository(self, name: str) -> Any:
        """Inventory one repository by its host ``name`` (a picked typeahead result)."""

        repository_model = apps.get_model("integrate", "Repository")
        return repository_model.objects.add(self, self.backend.get_repo(name))

    def discover_repositories(self, *, org: str = "") -> int:
        """Inventory every repository the account exposes (bulk import; prunes vanished)."""

        repository_model = apps.get_model("integrate", "Repository")
        return repository_model.objects.reconcile(self, self.backend.ls_repos(org=org))

class RepositoryManager(AngeeManager):
    """Manager owning the upsert/reconcile of repository rows from a host listing."""

    def reconcile(self, vcs_bridge: Any, descriptors: Iterable[Any]) -> int:
        """Upsert one repository row per descriptor and prune rows that vanished.

        Bulk import for ``discoverRepositories``: prunes against the full listing,
        so the caller must pass every repository (see ``GitHubBackend.ls_repos``
        pagination), never a partial page.
        """

        descriptor_list = list(descriptors)
        descriptors_by_name = {str(descriptor.name): descriptor for descriptor in descriptor_list}
        now = timezone.now()
        with system_context(reason="integrate.repository.reconcile"), transaction.atomic():
            self.bulk_create(
                [
                    self._row_from_descriptor(vcs_bridge, descriptor, now=now)
                    for descriptor in descriptors_by_name.values()
                ],
                update_conflicts=True,
                unique_fields=["vcs_bridge", "name"],
                update_fields=[
                    "org",
                    "remote",
                    "ssh_remote",
                    "remote_id",
                    "default_branch",
                    "visibility",
                    "web_url",
                    "archived",
                    "updated_at",
                ],
            )
            self.filter(vcs_bridge=vcs_bridge).exclude(name__in=descriptors_by_name).delete()
        return len(descriptor_list)

    def add(self, vcs_bridge: Any, descriptor: Any) -> Any:
        """Inventory one repository (no prune) — the typeahead "add this repo" path."""

        with system_context(reason="integrate.repository.add"), transaction.atomic():
            return self._upsert(vcs_bridge, descriptor)

    def _upsert(self, vcs_bridge: Any, descriptor: Any) -> Any:
        """Create or update one repository row from a host descriptor."""

        repository, _created = self.update_or_create(
            vcs_bridge=vcs_bridge,
            name=descriptor.name,
            defaults={
                "org": descriptor.org,
                "remote": descriptor.remote,
                "ssh_remote": descriptor.ssh_remote,
                "remote_id": descriptor.remote_id,
                "default_branch": descriptor.default_branch,
                "visibility": descriptor.visibility,
                "web_url": descriptor.web_url,
                "archived": descriptor.archived,
            },
        )
        return repository

    def _row_from_descriptor(self, vcs_bridge: Any, descriptor: Any, *, now: datetime) -> Any:
        """Return an unsaved repository row projected from one host descriptor."""

        return self.model(
            vcs_bridge=vcs_bridge,
            name=descriptor.name,
            org=descriptor.org,
            remote=descriptor.remote,
            ssh_remote=descriptor.ssh_remote,
            remote_id=descriptor.remote_id,
            default_branch=descriptor.default_branch,
            visibility=descriptor.visibility,
            web_url=descriptor.web_url,
            archived=descriptor.archived,
            created_at=now,
            updated_at=now,
        )


class Repository(SqidMixin, AuditMixin, AngeeModel):
    """Inventory of one git remote, reached through its ``VcsBridge``.

    A plain noun: Django records the remote; the operator clones it. ``org`` groups
    the account's repositories in the browse list.
    """

    runtime = True

    sqid_prefix = "repo_"
    vcs_bridge = models.ForeignKey(
        "integrate.VcsBridge",
        on_delete=models.CASCADE,
        related_name="repositories",
    )
    org = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=255)
    """The repository's ``owner/repo`` path on its remote host."""
    remote = models.CharField(max_length=512)
    """The HTTPS remote URL the operator clones."""
    ssh_remote = models.CharField(max_length=255, blank=True)
    remote_id = models.CharField(max_length=128, blank=True)
    default_branch = models.CharField(max_length=255, default="main")
    visibility = StateField(choices_enum=RepoVisibility, default=RepoVisibility.PRIVATE)
    web_url = models.URLField(blank=True)
    archived = models.BooleanField(default=False)

    objects = RepositoryManager()

    class Meta:
        """Django model options for repository inventory."""

        abstract = True
        ordering = ("org", "name")
        rebac_resource_type = "integrate/repository"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("vcs_bridge", "name"),
                name="uniq_integrate_repository_name",
            ),
        )

    def __str__(self) -> str:
        """Return the repository's host path."""

        return self.name


class Source(SqidMixin, AuditMixin, AngeeModel):
    """A pointer into a ``Repository`` at a ``ref`` and ``path``, with a ``kind``.

    One noun for every source kind. ``kind`` binds the source to an output model
    (``Template``/``Skill``) whose manager reconciles its rows; :meth:`refresh`
    dispatches there. The operator materializes a source from
    :meth:`materialize_spec`.
    """

    runtime = True

    sqid_prefix = "src_"
    repository = models.ForeignKey("integrate.Repository", on_delete=models.CASCADE, related_name="sources")
    kind = models.CharField(max_length=64)
    """The source kind (e.g. ``template``, ``skill``); resolves to an output model."""
    ref = models.CharField(max_length=255, blank=True)
    """Branch, tag, or commit oid; blank resolves to the repository's default branch."""
    path = models.CharField(max_length=1024, blank=True)
    """Pathspec of the subtree this source points at within the repository."""
    last_synced_at = models.DateTimeField(null=True, blank=True)

    objects = AngeeManager()

    class Meta:
        """Django model options for source inventory."""

        abstract = True
        ordering = ("kind", "path")
        rebac_resource_type = "integrate/source"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return a kind-qualified source label."""

        return f"{self.kind}:{self.path or '/'}"

    @classmethod
    def kind_models(cls) -> tuple[type[models.Model], ...]:
        """Return the output models that declare a ``source_kind`` (e.g. ``Template``).

        ``Source`` owns "what a kind resolves to": an output model binds itself to a
        kind with a ``source_kind`` class attribute; the integration registry owns
        the deterministic app scan and contract check.
        """

        return registry.source_kind_models()

    @classmethod
    def available_kinds(cls) -> tuple[str, ...]:
        """Return the source kinds any installed addon contributes an output model for."""

        return tuple(sorted({str(model.source_kind) for model in cls.kind_models()}))

    @classmethod
    def target_for_kind(cls, kind: str) -> type[models.Model]:
        """Return the output model bound to one source ``kind`` or raise."""

        for model in cls.kind_models():
            if model.source_kind == kind:
                return model
        known = ", ".join(cls.available_kinds()) or "none registered"
        raise ValueError(f"No output model for source kind {kind!r} (known: {known}).")

    def refresh(self) -> int:
        """Re-enumerate over REST into the kind's output rows; return the row count."""

        return int(type(self).target_for_kind(self.kind).objects.sync_from_source(self))

    def materialize_spec(self) -> dict[str, str]:
        """Return the operator handoff coordinates to clone and check out this source."""

        repository = self.repository
        return {
            "remote": str(repository.remote),
            "ssh_remote": str(repository.ssh_remote),
            "ref": str(self.ref or repository.default_branch),
            "path": str(self.path),
        }


class TemplateManager(AngeeManager):
    """Manager owning the reconcile of template rows from a template source."""

    def sync_from_source(self, source: Any) -> int:
        """Walk the source for ``copier.yml`` and upsert/prune ``Template`` rows."""

        vcs_bridge = source.repository.vcs_bridge
        descriptors = vcs_bridge.discover(source, marker="copier.yml", parse=parse_template_meta)
        descriptors_by_path = {str(descriptor.get("path", "")): descriptor for descriptor in descriptors}
        now = timezone.now()
        with system_context(reason="integrate.template.sync"), transaction.atomic():
            self.bulk_create(
                [
                    self._row_from_descriptor(source, descriptor, now=now)
                    for descriptor in descriptors_by_path.values()
                ],
                update_conflicts=True,
                unique_fields=["source", "path"],
                update_fields=["name", "kind", "inputs", "updated_at"],
            )
            self.filter(source=source).exclude(path__in=descriptors_by_path).delete()
            source.last_synced_at = now
            source.save(update_fields=["last_synced_at", "updated_at"])
        return len(descriptors)

    def _row_from_descriptor(self, source: Any, descriptor: dict[str, Any], *, now: datetime) -> Any:
        """Return an unsaved template row projected from one discovered descriptor."""

        return self.model(
            source=source,
            path=str(descriptor.get("path", "")),
            name=str(descriptor.get("name", "")),
            kind=str(descriptor.get("kind", "")),
            inputs=list(descriptor.get("inputs", [])),
            created_at=now,
            updated_at=now,
        )


class Template(SqidMixin, AuditMixin, AngeeModel):
    """One Copier template discovered under a ``Source`` (``source_kind="template"``).

    The operator renders these; the kind here is the *template* kind from the
    manifest's ``_angee.kind`` (stack/workspace/service).
    """

    runtime = True
    source_kind = "template"
    """Binds the ``template`` source kind to this output model (see ``registry``)."""

    sqid_prefix = "tpl_"
    source = models.ForeignKey("integrate.Source", on_delete=models.CASCADE, related_name="templates")
    name = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=64, blank=True)
    """The template kind from ``_angee.kind`` (stack/workspace/service)."""
    path = models.CharField(max_length=1024, blank=True)
    inputs = models.JSONField(default=list, blank=True)

    objects = TemplateManager()

    class Meta:
        """Django model options for discovered templates."""

        abstract = True
        ordering = ("kind", "name")
        rebac_resource_type = "integrate/template"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("source", "path"),
                name="uniq_integrate_template_path",
            ),
        )

    def __str__(self) -> str:
        """Return a kind-qualified template label."""

        return f"{self.kind}:{self.name or self.path}"


def _parent_path(path: str) -> str:
    """Return the directory containing ``path`` (empty string at the root)."""

    return path.rsplit("/", 1)[0] if "/" in path else ""


def _descriptor_key(descriptor: dict[str, Any]) -> tuple[str, str]:
    """Return a stable ``(kind, name|path)`` sort key for one discovered descriptor."""

    return (str(descriptor.get("kind", "")), str(descriptor.get("name") or descriptor.get("path", "")))


class WebhookSubscriptionManager(AngeeManager):
    """Manager for webhook subscriptions."""

    def enqueue_event(
        self,
        *,
        kind: EventKind,
        payload: Any,
        impl_app: str = "",
        integration: Any | None = None,
    ) -> None:
        """Queue one event fan-out after the current transaction commits."""

        kind_value = str(kind)
        body = self._event_body(payload)
        integration_pk = getattr(integration, "pk", None)
        transaction.on_commit(
            lambda: self._deliver_event_body(
                kind=kind_value,
                body=body,
                impl_app=impl_app,
                integration_pk=integration_pk,
            )
        )

    def deliver_event(
        self,
        *,
        kind: EventKind,
        payload: Any,
        impl_app: str = "",
        integration: Any | None = None,
    ) -> dict[str, int]:
        """Deliver one integration event to every matching enabled subscription.

        Actor-less framework fan-out: it reads subscriptions across all owners, so
        it runs under ``system_context``. Each subscription matches and delivers
        itself; this method only owns the row-set loop and the success/error tally.
        """

        return self._deliver_event_body(
            kind=str(kind),
            body=self._event_body(payload),
            impl_app=impl_app,
            integration_pk=getattr(integration, "pk", None),
        )

    @staticmethod
    def _event_body(payload: Any) -> bytes:
        """Return the canonical webhook JSON body for an event payload."""

        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _deliver_event_body(
        self,
        *,
        kind: str,
        body: bytes,
        impl_app: str,
        integration_pk: Any | None,
    ) -> dict[str, int]:
        """Deliver a pre-serialized event body to matching enabled subscriptions."""

        delivered = 0
        errors = 0
        with system_context(reason="integrate.webhooks.deliver"):
            for subscription in self._matching_queryset(
                kind=kind,
                impl_app=impl_app,
                integration_pk=integration_pk,
            ).iterator():
                if not subscription.matches(kind=kind, impl_app=impl_app, integration_pk=integration_pk):
                    continue
                ok, _message = subscription.deliver_recorded(body)
                if ok:
                    delivered += 1
                else:
                    errors += 1
        return {"delivered": delivered, "errors": errors}

    def _matching_queryset(self, *, kind: str, impl_app: str, integration_pk: Any | None) -> Any:
        """Return the narrowest portable candidate queryset for one webhook event."""

        queryset = self.filter(enabled=True)
        if integration_pk is None:
            queryset = queryset.filter(integration_filter__isnull=True)
        else:
            queryset = queryset.filter(Q(integration_filter__isnull=True) | Q(integration_filter_id=integration_pk))
        if connections[queryset.db].features.supports_json_field_contains:
            queryset = queryset.filter(event_kinds__contains=[kind])
            queryset = queryset.filter(Q(impl_app_filter=[]) | Q(impl_app_filter__contains=[impl_app]))
        return queryset.order_by("pk")


class WebhookSubscription(SqidMixin, AuditMixin, AngeeModel):
    """Outbound webhook endpoint owned by one user."""

    runtime = True

    sqid_prefix = "whs_"
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="webhook_subscriptions",
    )
    target_url = models.URLField(max_length=2048, validators=(validate_public_url,))
    secret = EncryptedField()
    event_kinds = models.JSONField(default=list, blank=True)
    impl_app_filter = models.JSONField(default=list, blank=True)
    integration_filter = models.ForeignKey(
        "integrate.Integration",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    enabled = models.BooleanField(default=True, db_index=True)
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_delivery_status = models.CharField(max_length=64, blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    consecutive_failures = models.PositiveIntegerField(default=0)

    objects = WebhookSubscriptionManager()

    class Meta:
        """Django model options for webhook subscriptions."""

        abstract = True
        rebac_resource_type = "integrate/webhook_subscription"
        rebac_id_attr = "sqid"

    _delivery_update_fields = (
        "consecutive_failures",
        "last_delivery_at",
        "last_delivery_status",
        "last_error",
        "updated_at",
    )

    def matches(
        self,
        *,
        kind: str,
        impl_app: str,
        integration: Any | None = None,
        integration_pk: Any | None = None,
    ) -> bool:
        """Return whether this subscription should receive one event."""

        if kind not in {str(value) for value in self.event_kinds or ()}:
            return False
        impl_app_filter = tuple(str(value) for value in self.impl_app_filter or ())
        if impl_app_filter and impl_app not in impl_app_filter:
            return False
        if self.integration_filter_id is None:
            return True
        expected_pk = getattr(integration, "pk", None) if integration_pk is None else integration_pk
        return self.integration_filter_id == expected_pk

    def deliver(self, body: bytes) -> str:
        """POST one signed event body to this subscription's pinned target; raise on non-2xx."""

        return PinnedWebhookClient(str(self.target_url)).post(secret=str(self.secret), body=body)

    def deliver_recorded(self, body: bytes) -> tuple[bool, str]:
        """Deliver one body, persist telemetry, and return ``(ok, status_or_error)``."""

        try:
            status = self.deliver(body)
        except Exception as exc:  # noqa: BLE001 — delivery failure is telemetry, not a caller exception.
            logger.exception("Webhook delivery failed for subscription %s.", self.public_id)
            message = self._delivery_error_message(exc)
            self.record_delivery_failure(status=self._delivery_failure_status(exc), error=message)
            return False, message
        self.record_delivery(status)
        return True, status

    def deliver_test(self) -> tuple[bool, str]:
        """Send a test event, persist telemetry, and return an action result tuple."""

        body = json.dumps(
            {"type": "test", "subscription": self.public_id},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        ok, result = self.deliver_recorded(body)
        return (True, f"Delivered (status {result}).") if ok else (False, f"Delivery failed: {result}")

    @staticmethod
    def _delivery_failure_status(exc: Exception) -> str:
        """Return an HTTP status string from a delivery exception when available."""

        if isinstance(exc, WebhookDeliveryError):
            return exc.status
        return ""

    @staticmethod
    def _delivery_error_message(exc: Exception) -> str:
        """Return a compact telemetry message for a delivery exception."""

        if isinstance(exc, ValidationError):
            return "; ".join(str(message) for message in exc.messages)
        return f"{type(exc).__name__}: {exc}"

    def rotate_secret(self) -> str:
        """Generate a new signing secret, persist it, and return the plaintext once.

        The subscription owns its signing material: a console action calls this to
        roll the secret. The plaintext is returned only here (for one-time display);
        reads never expose it.
        """

        new_secret = secrets.token_urlsafe(32)
        self.secret = new_secret  # type: ignore[assignment]  # EncryptedField descriptor unmodeled by django-stubs
        self.save(update_fields=["secret", "updated_at"])
        return new_secret

    def record_delivery(self, status: str) -> None:
        """Persist success telemetry for one delivery attempt (mirrors ``Bridge.record_sync``)."""

        self.last_delivery_at = timezone.now()
        self.last_delivery_status = status
        self.last_error = ""
        self.consecutive_failures = 0
        self.save(update_fields=self._delivery_update_fields)

    def record_delivery_failure(self, *, status: str, error: str) -> None:
        """Persist failure telemetry for one delivery attempt (mirrors ``Bridge.record_sync_error``).

        Takes the already-classified ``status``/``error``: the delivery layer
        owns turning a delivery exception into those strings.
        """

        self.last_delivery_at = timezone.now()
        self.last_delivery_status = status
        self.last_error = error
        # Atomic add so concurrent fan-outs to the same subscription don't lose an
        # increment — this counter is the thing failure policy gates on.
        self.consecutive_failures = models.F("consecutive_failures") + 1
        self.save(update_fields=self._delivery_update_fields)
        self.refresh_from_db(fields=("consecutive_failures",))
