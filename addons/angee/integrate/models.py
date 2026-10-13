"""Source models for Angee's integration runtime primitives.

This addon owns the integration layer end to end. The connection substrate — the
``OAuthClient`` registration, the user's ``ExternalAccount`` at a provider, and the
per-user ``Credential`` material — authenticates everything above it. On top of
that sit the third-party ``Vendor`` catalogue, the first-class ``Integration``
an integration runs over, addon-owned concrete child integration kinds, and
outbound ``WebhookSubscription``.

This addon is pure OAuth: it connects *out* to external systems and never
authenticates a session. OIDC login fields and ID-token verification live one
level up in ``iam_integrate_oidc``, which extends this OAuth base and composes the
``iam`` user. Concrete capabilities and their implementation selectors live in
their owning addons; this addon never imports them.
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Iterable, Mapping
from copy import copy, deepcopy
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar, Self, cast
from urllib.parse import urlsplit, urlunsplit

import httpx2
from deepdiff import DeepDiff
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import OperationalError, connection, models, transaction
from django.db.models import OuterRef, Prefetch, Q, Subquery
from django.utils import timezone
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    app_settings,
    delete_relationship,
    subject_id_attr,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.actors import is_sudo
from rebac.mixins import RebacModelBase
from rebac.models import active_relationship_model
from simple_history.signals import post_create_historical_record
from strawberry_django.descriptors import model_property

from angee.base.actors import actor_user_id, instance_actor
from angee.base.changesets import current_changeset
from angee.base.fields import DiagnosticTextField, EncryptedField, StateField
from angee.base.impl import ImplClassField, ImplDefaultsMixin
from angee.base.merge import references
from angee.base.mixins import (
    AuditMixin,
    HistoryMixin,
    SourceKind,
    Statement,
    StatementLinkMixin,
    StatementSourceMixin,
)
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet, AngeeUnscopedManager
from angee.base.refs import (
    MergePolicy,
    RecordRefMixin,
    concrete_child,
    concrete_child_accessor,
    concrete_child_models,
    generic_pointer_target,
)
from angee.base.serialization import canonical_json
from angee.base.transitions import StateTransitions, save_state, transition
from angee.graphql.capabilities import held_permissions
from angee.integrate.connect import enabled_oauth_client_from_hint, oauth_client_slug_from_hint
from angee.integrate.constants import DISCOVERY_ATTEMPT_LIMIT, DISCOVERY_TASK, DISCOVERY_TASK_EXPIRES
from angee.integrate.credentials import CredentialKind, CredentialKindHandler
from angee.integrate.discovery import ConnectionDiscovery
from angee.integrate.errors import (
    INTEGRATION_FAILURE_MESSAGE,
    IntegrationError,
    IntegrationFailure,
    safe_integration_failure,
)
from angee.integrate.events import EventKind
from angee.integrate.impl import IntegrationImpl, LiveBridgeImpl
from angee.integrate.live import PairingProjection, PairingState, armed_material_key
from angee.integrate.locks import bridge_advisory_lock, bridge_is_locked
from angee.integrate.mixins import SyncStateMixin
from angee.integrate.net import validate_public_url
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.discovery import discovery_document as oidc_metadata_document
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.oauth.providers import OAuthProviderType
from angee.integrate.oauth.registrations import oauth_client_is_ready
from angee.integrate.signals import discovery_finished
from angee.integrate.states import (
    UNSET,
    ConflictKeep,
    LinkStatus,
    OutboxState,
    StreamDirection,
    StreamKind,
    StreamPhase,
    StreamState,
    SyncFailure,
    SyncFailureKind,
    SyncFailureStatus,
)
from angee.integrate.streams import begin_stream_cycle, push_stream, read_stream_keys, sync_bridge
from angee.integrate.sync import SyncDispatch, bridge_progress_context, bridge_sync_context
from angee.integrate.webhooks import PinnedWebhookClient, WebhookDeliveryError
from angee.jobs.enqueue import enqueue_task
from angee.jobs.locks import LockKey, record_lock_key, task_lock, task_locks_are_cross_process

logger = logging.getLogger(__name__)

# Renew an OAuth access token this far ahead of its expiry, so a consumer about to
# use it (e.g. provisioning) gets a token with life left rather than one about to lapse.
_OAUTH_REFRESH_MARGIN = timedelta(minutes=5)
_UNSET = object()
_INTEGRATION_FAILURE_MESSAGE = INTEGRATION_FAILURE_MESSAGE
_WEBHOOK_FAILURE_MESSAGE = "Webhook delivery failed."
_DISCOVERY_RETRY_EXHAUSTED_MESSAGE = "Connection discovery exhausted its retries; retry discovery to continue."


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

    def preferred(self, *, environment: str = "prod") -> OAuthClientQuerySet:
        """Select the first registration per slug under a deterministic priority.

        Choose the environment before testing readiness: a disabled production
        registration must not silently fall back to a development registration.
        """

        alternatives = (
            self.model.objects.sudo(reason="integrate.oauth.environment_preference")
            .filter(
                slug=OuterRef("slug"),
            )
            .order_by(
                models.Case(models.When(environment=environment, then=0), default=1),
                "environment",
                "pk",
            )
        )
        return cast(OAuthClientQuerySet, self.filter(pk=Subquery(alternatives.values("pk")[:1])))

    def connectable(self) -> OAuthClientQuerySet:
        """Use the model's readiness rule for the public connection picker."""

        public = self.system_context(reason="integrate.graphql.connectable").preferred()
        ready_ids = [row.pk for row in public.only(*self.model.readiness_fields) if row.configuration_state == "ready"]
        return cast(OAuthClientQuerySet, public.filter(pk__in=ready_ids))

    def enabled_for_slug(self, slug: str, *, environment: str = "prod") -> Any | None:
        """Resolve the same ready registration the picker and projection offer."""

        client = self.filter(slug=slug).preferred(environment=environment).first()
        return client if client is not None and client.configuration_state == "ready" else None


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
        self, entries: Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | None = None
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


class OAuthClient(ImplDefaultsMixin, AuditMixin, AngeeDataModel):
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

    def refine_grant(self, protocol: OAuthClientProtocol, tokens: dict[str, Any]) -> dict[str, Any]:
        """Let the selected provider refine the grant before credential persistence."""

        return self.provider.refine_grant(protocol, tokens)

    @property
    def provider(self) -> type[OAuthProviderType]:
        """Resolve the selected provider preset through its implementation field."""

        return self._meta.get_field("provider_type").resolve_for(self)

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
                oauth_client_id=oauth_client.pk,
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
            user_id_attr = subject_id_attr(UserModel)
            try:
                return UserModel.objects.get(**{user_id_attr: row.subject_id})
            except UserModel.DoesNotExist:
                return None


class ExternalAccount(AuditMixin, AngeeDataModel):
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
    last_error: str = DiagnosticTextField(blank=True)
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

    def connection_health(self) -> Any:
        """Load current health and refresh evidence for an integration-owned projection.

        Native prefetch hints batch non-secret facts. Only an expired OAuth row
        that needs refreshability loads its deferred material through its handler.
        """

        return (
            self.sudo(reason="integrate.connection.health")
            .select_related("oauth_client")
            .only(
                "status",
                "expires_at",
                "last_refresh_status",
                "kind",
                "oauth_client__supports_refresh",
            )
        )

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

    def prepare_disconnect(self, credential: Any) -> None:
        """Validate a disconnect and schedule remote revocation after commit."""

        credential.check_disconnect()
        # Revoking the source grant also releases the resource-specific material
        # it discovered. Direct attachments retain their existing PROTECT policy.
        integrations = (
            apps.get_model("integrate", "Integration")
            .objects.filter(
                discovery_credential=credential,
            )
            .exclude(credential=credential)
            .order_by("pk")
        )
        for integration in integrations:
            target = integration.concrete_capability()
            target.set_lifecycle(IntegrationLifecycle.DISCONNECTED)
            target.attach_credential(None)
        transaction.on_commit(copy(credential).revoke_remote, robust=True)

    def delete_unattached_derived(self, credential_pk: int | None) -> None:
        """Dispose of discovery-created material after its last attachment releases it."""

        if credential_pk is not None:
            self.filter(
                pk=credential_pk,
                integrations__isnull=True,
                discovered_integrations__isnull=True,
            ).delete()

    def live_oauth_for_user(self, user: Any, oauth_client: Any) -> Any | None:
        """Reuse a credential whenever its owner does not require fresh consent."""

        with system_context(reason="integrate.connections.credential.live"):
            row = self.filter(user=user, oauth_client=oauth_client, kind=CredentialKind.OAUTH).first()
            return row if row is not None and not row.is_reconnect_required else None

    def upsert_for_user(
        self,
        user: Any,
        oauth_client: Any,
        kind: str,
        material: dict[str, Any],
        /,
        *,
        external_account: Any | None = None,
        reconnect: bool = True,
        **fields: Any,
    ) -> Any:
        """Create or update one ``(user, oauth_client)`` OAuth credential (connect/login flow)."""

        handler = CredentialKind(kind).handler
        operation_values, update_values = self._assemble_values(handler, material, fields)
        if external_account is not None:
            update_values["external_account_id"] = external_account.pk
        create_values = {
            "external_account_id": external_account.pk if external_account is not None else None,
            **self._blank_create_values(),
            **operation_values,
            **update_values,
        }
        # Give the credential a human label on create (OAuth rows carry no name of their
        # own). Create-only so an admin rename, and token refreshes, are preserved.
        if not str(create_values.get("name") or ""):
            create_values["name"] = self._oauth_credential_name(oauth_client, external_account)
        with system_context(reason=self._REASON), transaction.atomic():
            existing = self.filter(user_id=user.pk, oauth_client_id=oauth_client.pk).only("pk").first()
            integrations = []
            attached = False
            if existing is not None:
                affected = (
                    apps.get_model("integrate", "Integration")
                    .objects.filter(
                        Q(credential_id=existing.pk) | Q(discovery_credential_id=existing.pk),
                    )
                    .order_by("pk")
                )
                if reconnect:
                    # Match Retry/Resume: concrete integration and ancestors,
                    # then the source credential. Refresh-only writes never
                    # touch integrations and may already hold the credential lock.
                    integrations = [integration.lock_capability() for integration in affected]
                existing = self.lock_if_supported().get(pk=existing.pk)
                # An attachment cannot commit while the credential is locked (its
                # foreign-key check needs the row), so this read is final. A late
                # arrival attached between the two reads; it is locked after the
                # credential, as its re-attachment below would lock it anyway.
                if reconnect:
                    locked = {integration.pk for integration in integrations}
                    integrations += [
                        integration.lock_capability() for integration in affected if integration.pk not in locked
                    ]
                    attached = bool(integrations)
                else:
                    attached = affected.exists()
            if (
                existing is not None
                and external_account is not None
                and existing.external_account_id not in (None, external_account.pk)
                and attached
            ):
                raise OAuthFlowError("account_switch_refused", 409)
            instance, _created = self.update_or_create(
                user_id=user.pk,
                oauth_client_id=oauth_client.pk,
                defaults={**operation_values, **update_values},
                create_defaults=create_values,
            )
            if reconnect and existing is not None:
                for integration in integrations:
                    if (
                        existing.external_account_id == instance.external_account_id
                        and integration.credential_id == instance.pk
                    ):
                        continue
                    # Re-consent invalidates resource tokens derived from the old grant.
                    integration.attach_connection(instance, connect_disconnected=False)
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

        operation_values, update_values = self._local_credential_values(
            kind=kind,
            name=name,
            material=material,
            fields=fields,
        )
        create_values = {
            "oauth_client": None,
            "external_account": None,
            **self._blank_create_values(),
            **operation_values,
            **update_values,
        }
        with system_context(reason=self._REASON), transaction.atomic():
            instance, _created = self.update_or_create(
                user_id=user.pk,
                name=name,
                oauth_client=None,
                defaults={**operation_values, **update_values},
                create_defaults=create_values,
            )
        return instance

    def prepare_local_credential(
        self,
        user: Any,
        *,
        kind: str,
        name: str,
        material: dict[str, Any],
        **fields: Any,
    ) -> Any:
        """Return a validated, unsaved provider-less credential for external preflight."""

        operation_values, update_values = self._local_credential_values(
            kind=kind,
            name=name,
            material=material,
            fields=fields,
        )
        create_values = {
            "external_account": None,
            **self._blank_create_values(),
            **operation_values,
            **update_values,
        }
        return self.model(
            user=user,
            name=name,
            oauth_client=None,
            **create_values,
        )

    def _local_credential_values(
        self,
        *,
        kind: str,
        name: str,
        material: dict[str, Any],
        fields: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Validate one local credential and return its owned operation/update values."""

        handler = CredentialKind(kind).handler
        if handler.kind == CredentialKind.OAUTH:
            raise ValueError("OAuth credentials are minted by the connect/login flow, not create_local_credential().")
        if not name:
            raise ValueError("A provider-less credential requires a name.")
        return self._assemble_values(handler, material, fields)

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
        material_value = self.model.encode_material(material)
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


class Credential(AuditMixin, AngeeDataModel):
    """Per-user credential material for acting against a vendor OAuth client."""

    runtime = True

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

        return self.handler.can_refresh(self)

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
        """Return decrypted material through the kind handler."""

        return self.handler.reveal(self)

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
                row.material = self.encode_material(material)  # type: ignore[assignment]
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
        grant — is a no-op. The refresh is serialized and re-checked under a row lock
        (:meth:`_refresh_locked`), so racing consumers issue at most one network refresh.
        A permanent refresh refusal records ``last_refresh_status="failed"`` and leaves
        the existing token for the consumer to try. Transient refusals propagate so its
        retry owner can reschedule without prompting for consent once the access
        token has expired. Before expiry the existing token remains usable. Unexpected errors
        also propagate.
        """

        if self.expires_at is None or self.expires_at > timezone.now() + _OAUTH_REFRESH_MARGIN:
            return
        if not self.handler.can_refresh(self):
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
            self._record_refresh_failure(error)

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
        except (IntegrationError, ValueError) as error:
            self._record_refresh_failure(error)
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

    def _record_refresh_failure(self, error: Exception) -> None:
        """Persist permanent refresh refusals; transport retries do not require consent."""

        if isinstance(error, IntegrationError) and error.transient:
            return
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


class VendorManager(AngeeManager):
    """Manager for the resource-seeded third-party catalogue."""

    def seeded(self, slug: str) -> Any:
        """Return the seeded vendor named by ``slug`` or raise with load guidance."""

        try:
            return self.get(slug=slug)
        except self.model.DoesNotExist as error:
            raise ImproperlyConfigured(
                f"Vendor {slug!r} is missing. Run `manage.py resources load` before "
                "connecting integrations that use it."
            ) from error


class Vendor(AuditMixin, AngeeDataModel):
    """Admin-managed third-party catalogue (GitHub, Google, Slack, …).

    The single source of truth for "what is this third party" — branding and
    reference metadata only. New integration addons add their own row via an
    master-tier resource seed (``adopt: slug``). The connect-side ``OAuthClient``
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

    objects = VendorManager()

    class Meta:
        """Django model options for integration vendors."""

        abstract = True
        ordering = ("slug",)
        rebac_resource_type = "integrate/vendor"

    def __str__(self) -> str:
        """Return the display label used by Django surfaces."""

        return self.display_name or self.slug


class IntegrationLifecycle(models.TextChoices):
    """Durable connection lifecycle shared by every integration."""

    DISCONNECTED = "disconnected", "Disconnected"
    CONNECTED = "connected", "Connected"
    PAUSED = "paused", "Paused"

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
    PENDING = "pending", "Pending"

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


_HISTORICAL_INTEGRATION_STATUS_AXES = {
    "DRAFT": ("draft", "ok"),
    "ACTIVE": ("active", "ok"),
    "PAUSED": ("paused", "ok"),
    "DISABLED": ("disabled", "ok"),
    "ERROR": ("active", "error"),
}


def integration_status_axes(status: object) -> tuple[str, str]:
    """Map the frozen pre-split status vocabulary for historical migrations.

    Runtime integrations use :class:`IntegrationLifecycle` and
    :class:`IntegrationRuntimeStatus` directly. This callable remains at its
    original import path because already-materialized Django migrations import
    it while replaying a fresh database.
    """

    raw = str(getattr(status, "value", status)).strip()
    try:
        return _HISTORICAL_INTEGRATION_STATUS_AXES[raw.upper()]
    except KeyError as error:
        raise ValueError(f"Unsupported legacy integration status: {raw}") from error


class IntegrationQuerySet(AngeeQuerySet[Any]):
    """Chainable collection scopes for integration and bridge rows."""

    def of_concrete_type(self) -> Any:
        """Keep rows whose declared final MTI kind is this queryset's model."""

        return self.filter(concrete_type=ContentType.objects.get_for_model(self.model))

    def ready_for_work(self) -> Any:
        """Exclude pending or unsuccessful discovery through one admission predicate."""

        return self.filter(discovery_retry_at__isnull=True).filter(
            Q(discovery_generation=0) | Q(discovery_completed_at__isnull=False),
        )

    def due_for_enqueue(self, *, timestamp: datetime, stale_before: datetime) -> Any:
        """Return bridge rows due for a new queue attempt or stale recovery."""

        return self.ready_for_work().filter(
            Q(lifecycle=IntegrationLifecycle.CONNECTED, next_sync_at__lte=timestamp)
            | Q(
                lifecycle__in=(IntegrationLifecycle.CONNECTED, IntegrationLifecycle.PAUSED),
                sync_stage="queued",
                updated_at__lte=stale_before,
            )
        )

    def pending_discoveries(self, *, now: datetime) -> Any:
        """Select due connection discovery, including paused rows."""

        return (
            self.filter(discovery_retry_at__lte=now)
            .exclude(
                lifecycle=IntegrationLifecycle.DISCONNECTED,
            )
            .order_by("pk")
        )

    def live_account_owners(
        self,
        bridge: Any,
        impl_key: str,
        external_id: str,
        *,
        identity_key: str,
    ) -> Any:
        """Return other rows retaining the same backend account claim."""

        return (
            self.filter(
                **{
                    bridge.live_impl_field: impl_key,
                    f"subscription_state__{identity_key}": external_id,
                    "lifecycle__in": (str(bridge.Lifecycle.CONNECTED), str(bridge.Lifecycle.PAUSED)),
                }
            )
            .exclude(pk=bridge.pk)
            .order_by("pk")
        )

    def with_concrete_children(self, *, actor: Any, exposed_model_labels: set[str]) -> Any:
        """Prefetch installed and actor-readable MTI descendants in fixed queries."""

        prefetches: list[Prefetch] = []

        def add_children(parent_model: type[models.Model], prefix: str = "") -> None:
            for child_model in concrete_child_models(parent_model):
                accessor = concrete_child_accessor(parent_model, child_model)
                path = f"{prefix}__{accessor}" if prefix else accessor
                integrity_attr = self.model.concrete_child_cache_attr(child_model, authorized=False)
                prefetches.append(
                    Prefetch(
                        path,
                        queryset=child_model.objects.sudo(reason="integrate.integration.child_integrity"),
                        to_attr=integrity_attr,
                    )
                )
                if child_model._meta.label in exposed_model_labels:
                    prefetches.append(
                        Prefetch(
                            path,
                            queryset=child_model.objects.with_actor(actor),
                            to_attr=self.model.concrete_child_cache_attr(child_model, authorized=True),
                        )
                    )
                add_children(child_model, f"{prefix}__{integrity_attr}" if prefix else integrity_attr)

        add_children(self.model)
        return self.prefetch_related(*prefetches)


class IntegrationManager(AngeeManager.from_queryset(IntegrationQuerySet)):  # type: ignore[misc]
    """Manager for integration and bridge collection scopes."""


def _save_lifecycle(instance: Integration, source: Any, target: Any, **kwargs: Any) -> None:
    """Acquire the final capability before the transition primitive locks its caller."""

    instance.lock_capability()
    save_state(instance, source, target, **kwargs)


def _save_connected(instance: Integration, source: Any, target: Any, **kwargs: Any) -> None:
    """The connected transition owns discovery and the resulting first poll."""

    _save_lifecycle(instance, source, target, **kwargs)
    instance.await_discovery()
    instance.refresh_from_db()


class Integration(StatementSourceMixin, ImplDefaultsMixin, AuditMixin, AngeeDataModel):
    """A product/workspace integration to a vendor account.

    The first-class "what we're connected to and what runs over it": it draws a
    ``credential`` (and optionally an ``account``) from the connection substrate to
    authenticate, points at a catalogue ``vendor``, and stores the implementation
    capability. Domain-specific behavior, state, and config live on concrete child
    models.
    """

    runtime = True
    rebac_grantable = {"reader": "write"}
    integration_create_mode: ClassVar[IntegrationCreateMode | None] = None

    Lifecycle = IntegrationLifecycle
    """Expose the lifecycle vocabulary off the row for callers that cannot import this module."""

    sqid_prefix = "int_"
    # Operator-given label (the connect flow sets it); blank falls back to the
    # vendor-derived :attr:`display_label`. The one human name for every child
    # (directory, channel, …), so it is not re-buried in each child's config.
    display_name = models.CharField(max_length=255, blank=True, default="")
    concrete_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, null=True, editable=False)
    """Concrete MTI model identity used for server-side grouping."""

    def declared_concrete_model(self) -> type[Integration] | None:
        """Resolve the saved final MTI kind through Django's ContentType cache."""

        if self.concrete_type_id is None:
            return None
        return cast(type[Integration] | None, ContentType.objects.get_for_id(self.concrete_type_id).model_class())

    @property
    def source_kind(self) -> SourceKind | None:
        """The capability child owns classification of this persisted principal."""

        model = self.declared_concrete_model()
        if model is None or model is type(self):
            return super().source_kind
        # The declared kind may sit several MTI levels down (an Odoo instance is a
        # Channel, a Channel an Integration): resolve it along the parent chain.
        child = self._concrete_child(model, actor=None, authorized=False)
        return child.source_kind if child is not None else None

    @classmethod
    def check(cls, **kwargs: Any) -> list[Any]:
        """Require one unambiguous primary-key path for each capability child."""

        errors = super().check(**kwargs)
        for child in (model for model in cls._meta.apps.get_models() if model is not cls and issubclass(model, cls)):
            if len(child._meta.parents) > 1:
                errors.append(
                    checks.Error(
                        f"{child._meta.label} has more than one Integration parent path.",
                        hint="Declare one primary-key MTI parent for a routed capability.",
                        obj=child,
                        id="integrate.E004",
                    )
                )
        return errors

    @classmethod
    def concrete_child_models(cls) -> tuple[type[Integration], ...]:
        """Return installed direct children in stable model-label order."""

        return cast(tuple[type[Integration], ...], concrete_child_models(cls))

    @classmethod
    def concrete_child_cache_attr(cls, child_model: type[Integration], *, authorized: bool) -> str:
        """Return the private per-query cache name for one concrete child type."""

        scope = "authorized" if authorized else "integrity"
        return f"_angee_integration_child_{scope}_{child_model._meta.app_label}_{child_model._meta.model_name}"

    def _concrete_child(self, model: type[Integration], *, actor: Any, authorized: bool) -> Integration | None:
        """Return one child from this row's collection cache or scoped storage."""

        queryset = (
            model.objects.with_actor(actor)
            if authorized
            else model.objects.sudo(reason="integrate.integration.child_integrity")
        )
        if type(self) not in model._meta.parents:
            parent_model = next(iter(model._meta.parents))
            parent = self._concrete_child(cast(type[Integration], parent_model), actor=actor, authorized=False)
            if parent is None:
                return None
            return cast(
                Integration | None,
                concrete_child(
                    parent,
                    model,
                    queryset=queryset,
                    cache_attr=type(self).concrete_child_cache_attr(model, authorized=authorized),
                ),
            )
        return cast(
            Integration | None,
            concrete_child(
                self,
                model,
                queryset=queryset,
                cache_attr=type(self).concrete_child_cache_attr(model, authorized=authorized),
            ),
        )

    def concrete_children(
        self, *, actor: Any, exposed_model_labels: set[str]
    ) -> tuple[list[Integration], list[Integration]]:
        """Return installed and actor-readable child rows without leaking denied siblings."""

        integrity: list[Integration] = []
        authorized: list[Integration] = []
        declared = self.declared_concrete_model()
        for child_model in type(self).concrete_child_models():
            if declared is not None and declared is not child_model and issubclass(declared, child_model):
                child_model = cast(type[Integration], declared)
            child = self._concrete_child(child_model, actor=actor, authorized=False)
            if child is None:
                continue
            integrity.append(child)
            if child_model._meta.label in exposed_model_labels:
                visible = self._concrete_child(child_model, actor=actor, authorized=True)
                if visible is not None:
                    authorized.append(visible)
        return integrity, authorized

    @classmethod
    def capability_impl_field(cls) -> ImplClassField:
        """Use the nearest capability's selector, including MTI refinements."""

        for owner in (cls, *cls._meta.get_parent_list()):
            fields = [field for field in owner._meta.local_fields if isinstance(field, ImplClassField)]
            if len(fields) == 1:
                return fields[0]
            if fields:
                raise ImproperlyConfigured(
                    f"{owner._meta.label} must declare one capability implementation field; found {len(fields)}."
                )
        raise ImproperlyConfigured(f"{cls._meta.label} has no capability implementation field.")

    @property
    def capability_impl_class(self) -> type[Any]:
        """Resolve the selected implementation without opening its transport."""

        model = self.declared_concrete_model() or type(self)
        field = model.capability_impl_field()
        key = self.__dict__.get("_connection_impl_key")
        return field.resolve_class(key) if key is not None else field.resolve_for(self.concrete_capability())

    @property
    def capability_impl(self) -> Any:
        """Construct the runtime selected by this concrete capability."""

        return self.capability_impl_class(self)

    @property
    def oauth_client_hint(self) -> str:
        """Select connection intent without constructing the capability runtime."""

        return self.capability_impl_class.oauth_client

    def connect_oauth_client(self, owner_label: str) -> Any:
        """Resolve the registration for this capability's declared connection."""

        return enabled_oauth_client_from_hint(
            self.oauth_client_hint,
            owner_label=owner_label,
            reason="integrate.integration.connect_oauth_client",
            vendor_slug=self.connection_vendor_slug(),
        )

    @classmethod
    def credential_projection_expressions(cls) -> dict[str, Any]:
        """Public health scalars from a protected credential, without a joined object."""

        credential = (
            apps.get_model("integrate", "Credential")
            .objects.sudo(
                reason="integrate.connection.public_health",
            )
            .filter(pk=OuterRef("credential_id"))
        )
        return {
            f"_connection_{name}": Subquery(credential.values(name)[:1])
            for name in ("status", "expires_at", "last_refresh_status")
        }

    @classmethod
    def oauth_projection_expressions(cls) -> dict[str, Any]:
        """Read the vendor hint and nearest concrete selector as elevated scalars."""

        vendor = (
            apps.get_model("integrate", "Vendor")
            .objects.sudo(
                reason="integrate.connection.public_vendor",
            )
            .filter(pk=OuterRef("vendor_id"))
        )
        selectors = []
        base = apps.get_model("integrate", "Integration")
        for model in sorted(apps.get_models(), key=lambda model: model._meta.label_lower):
            if model is base or not issubclass(model, base):
                continue
            if not any(isinstance(field, ImplClassField) for field in model._meta.fields):
                continue
            field = model.capability_impl_field()
            rows = model.objects.sudo(reason="integrate.connection.public_selector").filter(pk=OuterRef("pk"))
            selectors.append(
                models.When(
                    concrete_type_id=ContentType.objects.get_for_model(model).pk,
                    then=Subquery(rows.values(field.name)[:1]),
                )
            )
        return {
            "_connection_vendor_slug": Subquery(vendor.values("slug")[:1]),
            "_connection_impl_key": models.Case(*selectors, default=models.Value(""), output_field=models.CharField()),
        }

    def connection_vendor_slug(self) -> str:
        """Read branding used by connection selection without requiring Vendor read."""

        if "_connection_vendor_slug" in self.__dict__:
            return str(self.__dict__["_connection_vendor_slug"] or "")
        vendor = apps.get_model("integrate", "Vendor")
        return str(
            vendor.objects.sudo(reason="integrate.connection.vendor")
            .values_list(
                "slug",
                flat=True,
            )
            .get(pk=self.vendor_id)
        )

    def is_oauth_connectable(self) -> bool:
        """Use registration readiness without constructing a runtime or loading secrets."""

        if (self.declared_concrete_model() or type(self)) is apps.get_model("integrate", "Integration"):
            return False
        try:
            slug = oauth_client_slug_from_hint(
                self.oauth_client_hint,
                owner_label=self._meta.label,
                vendor_slug=self.connection_vendor_slug(),
            )
        except OAuthFlowError:
            return False
        return oauth_client_is_ready(slug)

    def can_connect(self, actor: Any = None, *, write_access: bool | None = None) -> bool:
        """OAuth Connect requires ownership, write, registration and consent need.

        A healthy grant with failed discovery is repaired by Retry, so that row
        never offers both Retry and Connect.
        """

        row = copy(self).with_actor(actor) if actor is not None else self
        return (
            row.owner_id == actor_user_id(instance_actor(row))
            and (row.has_access("write") if write_access is None else write_access)
            and (row.credential_id is None or row.is_reconnect_required)
            and row.is_oauth_connectable()
        )

    def require_connect(self, actor: Any) -> None:
        """Enforce the same OAuth admission projected to the record client."""

        self.require_access("write", actor)
        if not self.can_connect(actor):
            raise PermissionDenied("Integration OAuth Connect is unavailable to the current user.")

    def can_resume(self) -> bool:
        """Operator Resume reuses an attached grant and never substitutes for consent."""

        return (
            "resume"
            in held_permissions(
                self,
                ("resume",),
                model=apps.get_model("integrate", "Integration"),
            )
            and not self.is_reconnect_required
            and (
                self.lifecycle == IntegrationLifecycle.PAUSED
                or (self.lifecycle == IntegrationLifecycle.DISCONNECTED and self.credential_id is not None)
            )
        )

    def can_retry_discovery(self) -> bool:
        """Retry only failed, settled discovery with an attached healthy grant."""

        return (
            "retry_discovery"
            in held_permissions(
                self,
                ("retry_discovery",),
                model=apps.get_model("integrate", "Integration"),
            )
            and self.lifecycle
            in (
                IntegrationLifecycle.CONNECTED,
                IntegrationLifecycle.PAUSED,
            )
            and self.credential_id is not None
            and not self.is_reconnect_required
            and not self.discovery_ready
            and not self.discovery_pending
        )

    def lock_capability(self) -> Integration:
        """Lock the final MTI row and its ancestors in the same order as bridge work.

        Call inside an atomic block. Parent-addressed verbs resolve the child
        before acquiring any row lock, so they cannot invert the worker order.
        """

        # The physical lock target is an integrity fact, independent of whether
        # the caller can read the child. The locked queryset retains its scope.
        target = self
        if self.declared_concrete_model() is not type(self):
            target = copy(self).sudo(reason="integrate.connection.lock_target").concrete_capability()
        return cast(Integration, type(target).objects.lock_if_supported().get(pk=self.pk))

    def resume(self) -> None:
        """Recheck Resume admission under the integration lock before connecting."""

        actor = instance_actor(self)
        with transaction.atomic():
            with system_context(reason="integrate.connection.resume"):
                row = self.lock_capability()
            if actor is not None:
                row.with_actor(actor)
            if not row.can_resume():
                raise ValidationError("This integration cannot resume in its current state.")
            row.connect()
        self.refresh_from_db()

    def _credential_health(self) -> Credential | None:
        """Consume optimized public facts, or fetch the same narrow elevated projection."""

        if self.credential_id is None:
            return None
        model = apps.get_model("integrate", "Credential")
        if "_connection_credential" in self.__dict__:
            return cast(Credential | None, self.__dict__["_connection_credential"])
        fields = ("status", "expires_at", "last_refresh_status")
        if all(f"_connection_{field}" in self.__dict__ for field in fields):
            values = {field: self.__dict__[f"_connection_{field}"] for field in fields}
            if values["status"] is None:
                return None
            if values["expires_at"] is None or values["expires_at"] > timezone.now():
                return cast(Credential, model(**values))
        return model.objects.connection_health().get(pk=self.credential_id)

    @property
    def is_reconnect_required(self) -> bool:
        """Delegate consent health to the credential owner's non-secret predicate."""

        credential = self._credential_health()
        return credential is not None and credential.is_reconnect_required

    @property
    def credential_status(self) -> str:
        """Expose credential health without granting read access to its protected row."""

        if "_connection_status" in self.__dict__:
            return str(self.__dict__["_connection_status"] or "")
        credential = self._credential_health()
        return "" if credential is None else str(credential.status)

    def fresh_credential(self) -> Credential | None:
        """Reload the attached credential, including changes to its FK or material."""

        # Workers must observe credential rotation or repointing between operations.
        self.refresh_from_db(fields=["credential"])
        return cast(Credential | None, self.credential)

    def concrete_capability(self) -> Integration:
        """Return the installed concrete child row for this integration, or itself.

        A parent-addressed action (the console targets ``integrate.Integration``
        by public id) reaches the capability's own behavior through this: the
        child row is the same primary key with the subtype's methods. A row no
        installed child claims stays the neutral parent.
        """

        actor, unscoped = self.effective_actor(strict=True)
        declared = self.declared_concrete_model()
        if declared is not None and declared is not type(self) and issubclass(declared, type(self)):
            child = self._concrete_child(cast(type[Integration], declared), actor=actor, authorized=not unscoped)
            return child if child is not None else self
        for child_model in type(self).concrete_child_models():
            child = self._concrete_child(child_model, actor=actor, authorized=not unscoped)
            if child is not None:
                return cast(Integration, child)
        return self

    def probe_credential(self) -> str:
        """Prove the attached credential is usable; raise :class:`IntegrationError` when not.

        The credential-only connection test: no vendor round-trip, just the kind
        handler producing authorization material. Returns the operator message.
        """

        credential = self.credential
        if credential is None:
            raise IntegrationError("No credential is attached.")
        try:
            credential.auth_headers()
        except Exception as error:  # noqa: BLE001 — handler diagnostics stay outside the operator message.
            raise IntegrationError("Credential is not usable.") from error
        return "Credential is usable."

    def test_connection(self) -> str:
        """Exercise this integration's connection and return the operator message.

        The parent proves only its credential; a capability child that owns a
        transport (a channel backend, a directory backend) overrides this to
        perform the vendor's real handshake. Failures raise
        :class:`IntegrationError` with the operator-safe reason.
        """

        return self.probe_credential()

    vendor = models.ForeignKey("integrate.Vendor", on_delete=models.PROTECT, related_name="integrations")
    # PROTECT: a present credential is the integration's authentication. It may
    # belong to a principal other than ``owner`` (an org/app-install credential), so
    # deleting a credential still in use is refused rather than silently breaking
    # the integration. Disconnected integrations may leave it empty.
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
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="integrations",
    )
    """User owner for personal connections; null marks a platform-managed install resource."""
    lifecycle = StateField(choices_enum=IntegrationLifecycle, default=IntegrationLifecycle.DISCONNECTED)
    """Declared connection intent for this integration.

    Connected means configured and enabled, paused retains configuration while
    intentionally stopped, and disconnected releases the connection. Runtime
    health lives separately on ``runtime_status``.
    """
    runtime_status = StateField(
        choices_enum=IntegrationRuntimeStatus,
        default=IntegrationRuntimeStatus.OK,
        status_display="dot",
    )
    """Observed health for the integration's last runtime interaction.

    ``PENDING`` marks discovery in flight; ``OK`` and ``ERROR`` describe the
    last credential/sync/use outcome. None moves the lifecycle journey.
    """
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_status = models.CharField(max_length=64, blank=True)
    use_count_24h = models.PositiveIntegerField(default=0)
    error_count_24h = models.PositiveIntegerField(default=0)
    last_error: str = DiagnosticTextField(blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    discovery_credential = models.ForeignKey(
        "integrate.Credential",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="discovered_integrations",
    )
    """The original grant retained when discovery attaches a derived credential."""
    discovery_retry_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)
    """Durable pending discovery and the earliest provider retry deadline."""
    discovery_completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    """Evidence that this connection generation applied successfully."""
    discovery_generation = models.PositiveIntegerField(default=0, editable=False)
    """Fence older callbacks when OAuth rotates material on the same credential row."""

    discovery_attempts = models.PositiveIntegerField(default=0, editable=False)
    """Remote attempts in this generation; lock contention does not consume one."""

    lifecycle_transitions = StateTransitions(
        lifecycle,
        {
            IntegrationLifecycle.DISCONNECTED: [IntegrationLifecycle.CONNECTED],
            IntegrationLifecycle.CONNECTED: [
                IntegrationLifecycle.PAUSED,
                IntegrationLifecycle.DISCONNECTED,
            ],
            IntegrationLifecycle.PAUSED: [
                IntegrationLifecycle.CONNECTED,
                IntegrationLifecycle.DISCONNECTED,
            ],
        },
    )

    objects = IntegrationManager()
    unscoped_objects = AngeeUnscopedManager()

    class Meta:
        """Django model options for integrations."""

        abstract = True
        base_manager_name = "unscoped_objects"
        ordering = ("-updated_at",)
        rebac_resource_type = "integrate/integration"

    def __str__(self) -> str:
        """Return a stable vendor-qualified integration label."""

        vendor_slug = getattr(getattr(self, "vendor", None), "slug", "?")
        return f"{vendor_slug}:{self.public_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep the grouping content type aligned with the row's concrete model."""

        if self.concrete_type_id is None or type(self) is not self._meta.apps.get_model("integrate", "Integration"):
            content_type = ContentType.objects.get_for_model(type(self))
            changed = self.concrete_type_id != content_type.pk
            self.concrete_type = content_type
            update_fields = kwargs.get("update_fields")
            if changed and update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "concrete_type"}
        adding = self._state.adding
        super().save(*args, **kwargs)
        if adding and self.credential_id is not None and self.requires_connection_discovery:
            self.await_discovery()

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
    def record_display_label(self) -> str:
        """Use the same operator label as the Integration GraphQL record."""

        return self.display_label

    @transition(
        lifecycle,
        source=[IntegrationLifecycle.DISCONNECTED, IntegrationLifecycle.PAUSED],
        target=IntegrationLifecycle.CONNECTED,
        on_success=_save_connected,
    )
    def connect(self, *, credential: Any = _UNSET, account: Any = _UNSET) -> None:
        """Mark this integration connected, optionally attaching connection rows."""

        self._transition_fields = self._set_connection_fields(credential=credential, account=account)

    def _set_connection_fields(self, *, credential: Any = _UNSET, account: Any = _UNSET) -> set[str]:
        """Attach connection rows and reset health, returning the fields changed."""

        fields: set[str] = set()
        for name, value in (("credential", credential), ("account", account)):
            if value is _UNSET:
                continue
            if value is not None and value._state.adding:
                setattr(self, name, value)
            else:
                field = self._meta.get_field(name)
                setattr(self, field.attname, None if value is None else value.pk)
                if field.is_cached(self):
                    field.delete_cached_value(self)
            fields.add(name)
            if name == "credential":
                self.discovery_credential_id = None if value is None else value.pk
                fields.add("discovery_credential")
        self.runtime_status = cast(IntegrationRuntimeStatus, IntegrationRuntimeStatus.OK)
        self.last_error = ""
        self.last_error_at = None
        fields.update({"last_error", "last_error_at", "runtime_status"})
        return fields

    def attach_connection(
        self,
        credential: Any | None,
        *,
        account: Any = _UNSET,
        connect_disconnected: bool = True,
        discovery_generation: int | None = None,
    ) -> None:
        """Own every credential attachment, including a fenced derived credential.

        ``discovery_generation`` is reserved for the apply step already holding
        this row's lock. Ordinary callers always enter connection discovery.
        """

        resolved_account = (
            (None if credential is None else credential.external_account) if account is _UNSET else account
        )
        if self.pk is None:
            self._set_connection_fields(credential=credential, account=resolved_account)
            if credential is not None and connect_disconnected and self.lifecycle == IntegrationLifecycle.DISCONNECTED:
                self.lifecycle_transitions.force_state(
                    self,
                    IntegrationLifecycle.CONNECTED,
                    reason="unsaved integration attachment declares initial connection intent",
                )
            return
        with system_context(reason="integrate.connection.attach"), transaction.atomic():
            row = self.lock_capability()
            derived_pk = row.credential_id if row.discovery_credential_id not in (None, row.credential_id) else None
            if discovery_generation is not None:
                if row.discovery_generation != discovery_generation or not row.discovery_pending:
                    raise IntegrationError("Connection discovery has been superseded.")
                original_grant_id = row.discovery_credential_id
                fields = row._set_connection_fields(credential=credential, account=resolved_account)
                row.discovery_credential_id = original_grant_id
                row.save(update_fields={*fields, "updated_at"})
            elif credential is not None and connect_disconnected and row.lifecycle == IntegrationLifecycle.DISCONNECTED:
                row.connect(credential=credential, account=resolved_account)
            else:
                fields = row._set_connection_fields(credential=credential, account=resolved_account)
                row.save(update_fields={*fields, "updated_at"})
                row.await_discovery()
            apps.get_model("integrate", "Credential").objects.delete_unattached_derived(derived_pk)
        self.refresh_from_db()

    @transition(
        lifecycle,
        source=IntegrationLifecycle.CONNECTED,
        target=IntegrationLifecycle.PAUSED,
        on_success=_save_lifecycle,
    )
    def pause(self) -> None:
        """Pause this connected integration without changing its runtime health."""

    @transition(
        lifecycle,
        source=[IntegrationLifecycle.CONNECTED, IntegrationLifecycle.PAUSED],
        target=IntegrationLifecycle.DISCONNECTED,
        on_success=_save_lifecycle,
    )
    def disconnect(self) -> None:
        """Disconnect this integration without changing its runtime health."""

        self.discovery_retry_at = None
        self._transition_fields = {"discovery_retry_at"}

    def set_lifecycle(self, lifecycle: IntegrationLifecycle | str) -> None:
        """Move to one declared lifecycle state using this model's transition methods."""

        target = IntegrationLifecycle.from_value(lifecycle)
        if str(self.lifecycle) == target.value:
            return
        if target == IntegrationLifecycle.CONNECTED:
            self.connect()
        elif target == IntegrationLifecycle.PAUSED:
            self.pause()
        elif target == IntegrationLifecycle.DISCONNECTED:
            self.disconnect()
        else:
            self.lifecycle_transitions.not_allowed(self.lifecycle, target)

    @property
    def requires_connection_discovery(self) -> bool:
        """Credential-only capabilities settle attachment without a worker."""

        return False

    @property
    def discovery_pending(self) -> bool:
        """Whether this connection generation is awaiting discovery or a retry."""

        return self.discovery_retry_at is not None

    @property
    def discovery_ready(self) -> bool:
        """One admission rule for sync, workflow dispatch and outbound delivery.

        Generation zero preserves established integrations until their next
        attachment or Resume. Every new generation needs successful discovery.
        """

        return not self.discovery_pending and (
            self.discovery_generation == 0 or self.discovery_completed_at is not None
        )

    def prepare_connection_discovery(self) -> None:
        """Database-only preparation; concrete capabilities may suspend their work."""

    def connection_discovery_finished(self, *, succeeded: bool) -> None:
        """Database-only completion; concrete capabilities may arm their work."""

    def await_discovery(self) -> None:
        """Invalidate the previous result and dispatch one durable discovery attempt."""

        with system_context(reason="integrate.connection.await"), transaction.atomic():
            row = self.lock_capability()
            source_lost = (
                row.discovery_generation > 0 and row.discovery_credential_id is None and row.credential_id is not None
            )
            if row.discovery_credential_id is not None and row.credential_id != row.discovery_credential_id:
                derived_pk = row.credential_id
                credential = apps.get_model("integrate", "Credential").objects.get(pk=row.discovery_credential_id)
                fields = row._set_connection_fields(credential=credential, account=row.account)
                row.save(update_fields={*fields, "updated_at"})
                type(credential).objects.delete_unattached_derived(derived_pk)
            if row.discovery_credential_id is None and not source_lost:
                row.discovery_credential_id = row.credential_id
            row.discovery_generation += 1
            row.discovery_attempts = 0
            row.discovery_completed_at = None
            row.discovery_retry_at = timezone.now()
            row.prepare_connection_discovery()
            row.save(
                update_fields=[
                    "discovery_generation",
                    "discovery_attempts",
                    "discovery_completed_at",
                    "discovery_retry_at",
                    "discovery_credential",
                    "updated_at",
                ]
            )
            if source_lost:
                row.finish_discovery(
                    credential_pk=row.credential_id,
                    generation=row.discovery_generation,
                    error=IntegrationFailure("The source credential for connection discovery is no longer available."),
                )
            elif row.requires_connection_discovery and row.lifecycle == IntegrationLifecycle.DISCONNECTED:
                row.discovery_retry_at = None
                row.save(update_fields=["discovery_retry_at", "updated_at"])
            elif not row.requires_connection_discovery:
                row.finish_discovery(credential_pk=row.credential_id, generation=row.discovery_generation)
            elif row.credential_id is None:
                row.finish_discovery(
                    credential_pk=None,
                    generation=row.discovery_generation,
                    error=IntegrationFailure("No credential is attached."),
                )
            else:
                row.report_status(cast(IntegrationRuntimeStatus, IntegrationRuntimeStatus.PENDING))
                enqueue_task(
                    DISCOVERY_TASK,
                    kwargs={
                        "integration_pk": row.pk,
                        "credential_pk": row.credential_id,
                        "generation": row.discovery_generation,
                    },
                    expires=DISCOVERY_TASK_EXPIRES,
                    robust=True,
                )
        self.refresh_from_db()

    def retry_discovery(self) -> None:
        """Retry connection discovery, resetting the bounded attempt budget."""

        actor = instance_actor(self)
        with transaction.atomic():
            with system_context(reason="integrate.connection.retry"):
                row = self.lock_capability()
            if actor is not None:
                row.with_actor(actor)
            if not row.can_retry_discovery():
                raise ValidationError("This integration cannot retry discovery in its current state.")
            row.await_discovery()
        self.refresh_from_db()

    def discovery_is_pending(self, credential_pk: int | None, *, generation: int) -> bool:
        """Fence both credential replacement and rotation on the same credential row."""

        return (
            self.discovery_pending and self.credential_id == credential_pk and self.discovery_generation == generation
        )

    def defer_discovery(self, *, credential_pk: int, generation: int, retry_after: timedelta) -> bool:
        """Retain a provider deadline unless this generation exhausted its attempts."""

        with system_context(reason="integrate.connection.defer"), transaction.atomic():
            row = self.lock_capability()
            if not row.discovery_is_pending(credential_pk, generation=generation):
                return False
            if row.discovery_attempts >= DISCOVERY_ATTEMPT_LIMIT:
                row.finish_discovery(
                    credential_pk=credential_pk,
                    generation=generation,
                    error=IntegrationFailure(_DISCOVERY_RETRY_EXHAUSTED_MESSAGE),
                )
            else:
                row.discovery_retry_at = max(row.discovery_retry_at, timezone.now() + retry_after)
                row.save(update_fields=["discovery_retry_at", "updated_at"])
        self.refresh_from_db()
        return True

    def apply_discovery(self, discovery: ConnectionDiscovery) -> None:
        """Apply a credential-only result; bridges add domain database work."""

        if discovery.data or discovery.credential is not None:
            raise IntegrationError("This integration does not accept external resource discovery.")

    def finish_discovery(
        self,
        *,
        credential_pk: int | None,
        generation: int,
        discovery: ConnectionDiscovery | None = None,
        error: IntegrationFailure | None = None,
    ) -> bool:
        """Apply remote results only after validating the task's original fence.

        The adapter receives the freshly locked integration. Derived material
        lands through the credential manager and ``attach_connection`` here,
        never in the adapter's network phase.
        """

        with system_context(reason="integrate.connection.finish"), transaction.atomic():
            row = self.lock_capability()
            if not row.discovery_is_pending(credential_pk, generation=generation):
                return False
            if error is None:
                result = discovery or ConnectionDiscovery()
                row.apply_discovery(result)
                if result.credential is not None:
                    source = apps.get_model("integrate", "Credential").objects.get(pk=credential_pk)
                    derived = result.credential
                    credential = type(source).objects.create_local_credential(
                        source.user,
                        kind=derived.kind,
                        name=derived.name,
                        material=dict(derived.material),
                    )
                    row.attach_connection(
                        credential,
                        account=source.external_account,
                        connect_disconnected=False,
                        discovery_generation=generation,
                    )
            row.discovery_retry_at = None
            row.discovery_completed_at = timezone.now() if error is None else None
            row.save(update_fields=["discovery_retry_at", "discovery_completed_at", "updated_at"])
            status = IntegrationRuntimeStatus.ERROR if error else IntegrationRuntimeStatus.OK
            row.report_status(cast(IntegrationRuntimeStatus, status), error or "")
            row.connection_discovery_finished(succeeded=error is None)
            if error is None:
                transaction.on_commit(
                    lambda: discovery_finished.send(sender=type(row), instance=row),
                    robust=True,
                )
        self.refresh_from_db()
        return True

    def attach_credential(self, credential: Any | None) -> None:
        """Route parent-addressed attachment through the concrete capability owner."""

        with system_context(reason="integrate.integration.attach_credential"), transaction.atomic():
            self.concrete_capability().attach_connection(credential)
        self.refresh_from_db()

    def run_discovery(self, *, credential_pk: int | None, generation: int) -> dict[str, Any]:
        """Discover in autocommit under the sync lock, then apply under the row fence."""

        if connection.in_atomic_block:
            raise RuntimeError("Connection discovery requires autocommit.")
        target = self.concrete_capability()
        with bridge_advisory_lock(target) as acquired:
            if not acquired:
                return {"ok": False, "reason": "locked"}
            with transaction.atomic():
                row = target.lock_capability()
                if not row.discovery_is_pending(credential_pk, generation=generation):
                    return {"ok": False, "reason": "stale-discovery"}
                if row.discovery_retry_at > timezone.now():
                    return {"ok": False, "reason": "deferred"}
                if row.discovery_attempts >= DISCOVERY_ATTEMPT_LIMIT:
                    row.finish_discovery(
                        credential_pk=credential_pk,
                        generation=generation,
                        error=IntegrationFailure(_DISCOVERY_RETRY_EXHAUSTED_MESSAGE),
                    )
                    return {"ok": False, "reason": "attempt-limit"}
                row.discovery_attempts += 1
                row.save(update_fields=["discovery_attempts", "updated_at"])
            target = row
            try:
                if credential_pk is None:
                    raise IntegrationError("No credential is attached.")
                discovery = target.discover_connection(row.credential)
                if not isinstance(discovery, ConnectionDiscovery):
                    raise IntegrationError("The adapter returned an invalid connection discovery result.")
                applied = target.finish_discovery(
                    credential_pk=credential_pk,
                    generation=generation,
                    discovery=discovery,
                )
                self.refresh_from_db()
                return {"ok": applied, "reason": "applied" if applied else "stale-discovery"}
            except Exception as error:  # noqa: BLE001 — provider diagnostics never become public telemetry.
                if not isinstance(error, IntegrationError):
                    logger.exception("Connection discovery failed for integration %s.", target.pk)
                retryable = (
                    isinstance(error, IntegrationError)
                    and error.transient
                    or isinstance(error, (httpx2.TransportError, OperationalError))
                    or isinstance(error, httpx2.HTTPStatusError)
                    and error.response.status_code >= 500
                    or isinstance(error, OAuthFlowError)
                    and error.http_status >= 500
                )
                if retryable and credential_pk is not None:
                    hint = error.retry_after if isinstance(error, IntegrationError) else None
                    backoff = min(timedelta(minutes=2 ** (target.discovery_attempts - 1)), timedelta(hours=1))
                    target.defer_discovery(
                        credential_pk=credential_pk,
                        generation=generation,
                        retry_after=max(backoff, hint) if hint is not None else backoff,
                    )
                    self.refresh_from_db()
                    return {"ok": False, "reason": "deferred" if target.discovery_pending else "attempt-limit"}
                target.finish_discovery(
                    credential_pk=credential_pk,
                    generation=generation,
                    error=safe_integration_failure(error),
                )
                self.refresh_from_db()
                return {"ok": False, "reason": "discovery-failed"}

    def discover_connection(self, credential: Credential) -> ConnectionDiscovery:
        """Credential-only discovery is synchronous and has no external resource."""

        return ConnectionDiscovery()

    def report_status(self, status: IntegrationRuntimeStatus | str, error: str | IntegrationFailure = "") -> None:
        """Record implementation status telemetry and persist this integration.

        ``status`` is a runtime status, resolved by member name (``ERROR`` — the
        shape GraphQL serializes) or stored value (``error``); anything else
        fails at the vocabulary that could not read it. Only the runtime axis is
        written — ``lifecycle`` is the operator's to declare, and a status report
        is not the operator. ``last_used_status`` keeps what was reported
        verbatim.
        """

        raw_status = str(getattr(status, "value", status)).strip()
        normalized = IntegrationRuntimeStatus.from_value(status)
        reported_at = timezone.now()
        self.runtime_status = normalized
        usage_fields: list[str] = []
        if normalized != IntegrationRuntimeStatus.PENDING:
            self.last_used_at = reported_at
            self.last_used_status = raw_status or normalized.value
            usage_fields = ["last_used_at", "last_used_status"]
        self.last_error = (
            error.message if isinstance(error, IntegrationFailure) else (_INTEGRATION_FAILURE_MESSAGE if error else "")
        )
        self.last_error_at = reported_at if error else None

        if self.pk is None:
            return

        with system_context(reason="integrate.integration.status"), transaction.atomic():
            self.save(
                update_fields=[
                    "last_error",
                    "last_error_at",
                    *usage_fields,
                    "runtime_status",
                    "updated_at",
                ],
            )


def merge_json_state(
    instance: Integration,
    field_name: str,
    values: Mapping[str, Any],
    *,
    path: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Merge keys at a JSON path under the row lock, preserving sibling owners.

    Each supplied value replaces its key; omitted keys survive. This is shared by
    subscription state and cursor slices. Full opaque cursor replacement belongs
    to SyncStreamManager.advance. The instance receives the committed document.
    """

    if not isinstance(instance._meta.get_field(field_name), models.JSONField):
        raise TypeError("JSON state merge requires a JSONField.")
    with system_context(reason="integrate.json_state.merge"), transaction.atomic():
        row = instance.lock_capability()
        value = getattr(row, field_name)
        document = deepcopy(value) if isinstance(value, dict) else {}
        node = document
        for key in path:
            if not isinstance(node.get(key), dict):
                node[key] = {}
            node = node[key]
        node.update(deepcopy(values))
        setattr(row, field_name, document)
        row.save(update_fields=[field_name, "updated_at"])
    setattr(instance, field_name, document)
    return document


class IntegrationCreateMode(StrEnum):
    """How the console starts creation of a concrete integration."""

    FORM = "form"
    CONNECT = "connect"


class Bridge(models.Model, metaclass=RebacModelBase):
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

    class LiveState(models.TextChoices):
        """Desired live-ingest state persisted in ``subscription_state["desired"]``.

        The base owns this vocabulary: bridge start/stop methods write it, and a
        live implementation's session loop and reconciler read it. Stopping is
        cooperative - a running session notices ``STOPPED`` and exits.
        """

        LIVE = "live", "Live"
        STOPPED = "stopped", "Stopped"

    live_impl_field: ClassVar[str | None] = None
    """Name the ImplClassField that may select a long-lived session implementation.

    Periodic-only bridges leave this unset. Their periodic sync remains in the due
    scheduler, but the live-session reconciler does not inspect or dispatch them.
    """

    config = models.JSONField(default=dict, blank=True)
    """Bridge-scoped settings interpreted by the selected backend."""
    cursor = models.JSONField(default=dict, blank=True)
    poll_interval = models.PositiveIntegerField(default=300)
    subscription_state = models.JSONField(default=dict, blank=True)
    next_subscription_refresh_at = models.DateTimeField(null=True, blank=True)
    last_sync_started_at = models.DateTimeField(null=True, blank=True)
    last_sync_completed_at = models.DateTimeField(null=True, blank=True)
    last_sync_items = models.PositiveIntegerField(default=0)
    sync_stage = StateField(choices_enum=SyncStage, max_length=32, default=SyncStage.IDLE, db_index=True)
    """Messaging channels keep their existing varchar(32) column on upgrade."""
    sync_error: str = DiagnosticTextField(blank=True, default="")
    sync_run_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    """Opaque execution-owner run identity, retained after terminal settlement."""
    sync_progress = models.JSONField(default=dict, blank=True)
    last_sync_summary = models.JSONField(default=dict, blank=True)
    next_sync_at = models.DateTimeField(null=True, blank=True, db_index=True)
    """Next scheduler poll. NULL means unscheduled: a bridge enters the poll loop
    when its first connected sync records a result; paused one-shot syncs keep it
    NULL. The scheduler claims a connected due row by pushing this one interval
    out for the duration of the run."""

    class Meta:
        """Django model options for abstract bridge inheritance."""

        abstract = True

    @classmethod
    def live_implementation_field(cls) -> ImplClassField | None:
        """Return this bridge's explicitly declared live implementation field."""

        field_name = cls.live_impl_field
        if field_name is None:
            return None
        field = cls._meta.get_field(field_name)
        if not isinstance(field, ImplClassField):
            raise ImproperlyConfigured(
                f"{cls._meta.label}.{field_name} must be an ImplClassField to select live implementations."
            )
        return field

    @property
    def live_impl(self) -> IntegrationImpl:
        """Return this bridge's selected runtime implementation."""

        field = type(self).live_implementation_field()
        if field is None:
            raise TypeError(f"{self._meta.label} is a periodic-only bridge.")
        impl_class = cast(type[IntegrationImpl], self.resolve_impl(field.name))
        return impl_class(self)

    @property
    def is_syncing(self) -> bool:
        """Return whether a worker currently holds this bridge's live sync lock."""

        return bridge_is_locked(self)

    def live_account_lock_key(self, impl_key: str, external_id: str) -> LockKey:
        """Return the cross-worker lock for one backend-normalized account id."""

        if not external_id:
            raise ValueError("A live bridge account id is required.")
        return LockKey(f"{impl_key}-account", (external_id,))

    def live_account_lock(self, impl_key: str, external_id: str) -> Any:
        """Try to hold the account-scoped ownership lock."""

        return task_lock(self.live_account_lock_key(impl_key, external_id))

    def live_account_owners(
        self,
        impl_key: str,
        external_id: str,
        *,
        identity_key: str,
        queryset: Any | None = None,
    ) -> Any:
        """Return other rows that retain the same durable live-account claim."""

        scope = queryset if queryset is not None else type(self).objects.all()
        return scope.live_account_owners(
            self,
            impl_key,
            external_id,
            identity_key=identity_key,
        )

    def claim_live_account(self, impl_key: str, external_id: str, *, identity_key: str) -> bool:
        """Persist this row's account claim when no live sibling owns it."""

        if not external_id:
            raise ValueError("A live bridge account id is required.")
        model = type(self)
        with system_context(reason="integrate.live.account.claim"), transaction.atomic():
            row = model.objects.sudo(reason="integrate.live.account.claim.row").lock_if_supported().get(pk=self.pk)
            owners = row.live_account_owners(
                impl_key,
                external_id,
                identity_key=identity_key,
                queryset=model.objects.sudo(reason="integrate.live.account.claim.owner"),
            )
            if owners.exists():
                return False
            state = dict(row.subscription_state)
            state[identity_key] = external_id
            row.subscription_state = state
            row.save(update_fields=["subscription_state", "updated_at"])
        self.refresh_from_db()
        return True

    def update_live_state(
        self,
        *,
        identity_key: str,
        drop_identity: bool = False,
        drop_pairing_report: bool = False,
        desired: Any | None = None,
    ) -> None:
        """Apply durable live-session state changes and save only changed fields."""

        fields: list[str] = []
        state = dict(self.subscription_state)
        if drop_identity:
            state.pop(identity_key, None)
        if desired is not None:
            state["desired"] = str(getattr(desired, "value", desired))
        if state != self.subscription_state:
            self.subscription_state = state
            fields.append("subscription_state")
        if drop_pairing_report:
            progress = dict(self.sync_progress) if isinstance(self.sync_progress, Mapping) else {}
            details = dict(progress.get("details") or {}) if isinstance(progress.get("details"), Mapping) else {}
            if details.pop("pairing", None) is not None:
                progress["details"] = details
                self.sync_progress = progress
                fields.append("sync_progress")
        if fields:
            self.save(update_fields=[*fields, "updated_at"])

    def disconnect_live_account(self, *, identity_key: str, clear_identity: bool) -> None:
        """Disconnect under a row lock and optionally clear claim and pairing state."""

        model = type(self)
        with system_context(reason="integrate.live.account.disconnect"), transaction.atomic():
            row = model.objects.sudo(reason="integrate.live.account.disconnect.row").lock_if_supported().get(pk=self.pk)
            row.set_lifecycle(type(row).Lifecycle.DISCONNECTED)
            if clear_identity:
                row.update_live_state(identity_key=identity_key, drop_identity=True, drop_pairing_report=True)
        self.refresh_from_db()

    def release_live_account(self, *, identity_key: str, desired: Any) -> None:
        """Clear a void account claim without changing operator-owned lifecycle."""

        model = type(self)
        with system_context(reason="integrate.live.account.release"), transaction.atomic():
            row = model.objects.sudo(reason="integrate.live.account.release.row").lock_if_supported().get(pk=self.pk)
            row.update_live_state(identity_key=identity_key, drop_identity=True, desired=desired)
        self.refresh_from_db()

    def live_pairing(self, impl: Any) -> Any:
        """Project pairing state from durable row facts and backend identity formatting."""

        progress: Mapping[str, Any] = self.sync_progress if isinstance(self.sync_progress, Mapping) else {}
        details_value = progress.get("details")
        details: Mapping[str, Any] = details_value if isinstance(details_value, Mapping) else {}
        report_value = details.get("pairing")
        report: Mapping[str, Any] = report_value if isinstance(report_value, Mapping) else {}
        reported = PairingState.from_report(report.get("state"))
        raw_identity = self.subscription_state.get(impl.state_identity_key) or report.get("own_id") or ""
        own_id = impl.normalize_account_id(str(raw_identity))
        lifecycle = type(self).Lifecycle.from_value(self.lifecycle)
        if lifecycle is type(self).Lifecycle.PAUSED:
            state = PairingState.PAUSED
        elif lifecycle is type(self).Lifecycle.DISCONNECTED:
            state = PairingState.STOPPED
        elif reported in (PairingState.AWAITING_PASSWORD, PairingState.LOGGED_OUT, PairingState.DUPLICATE_ACCOUNT):
            state = reported
        elif own_id:
            state = PairingState.PAIRED
        elif self.subscription_state.get("desired") != self.LiveState.LIVE:
            state = PairingState.STOPPED
        elif reported is PairingState.AWAITING_SCAN:
            state = PairingState.AWAITING_SCAN
        else:
            state = PairingState.STARTING
        duplicate = None
        if own_id and state is PairingState.DUPLICATE_ACCOUNT:
            duplicate = self.live_account_owners(
                impl.key,
                own_id,
                identity_key=impl.state_identity_key,
            ).first()
        return PairingProjection(
            state=state,
            qr=str(report.get("qr") or "") if state is PairingState.AWAITING_SCAN else "",
            message=str(report.get("message") or "") if state is PairingState.AWAITING_PASSWORD else "",
            can_skip=(
                bool(report.get("can_skip")) and bool(armed_material_key(self.subscription_state))
                if state is PairingState.AWAITING_PASSWORD
                else False
            ),
            own_id=own_id,
            account_label=impl.account_label(own_id) if own_id else "",
            duplicate_channel_id="" if duplicate is None else str(duplicate.sqid),
            duplicate_channel_name="" if duplicate is None else str(duplicate.display_name),
        )

    # Direct syncs hold their advisory lock; dispatched syncs retain a run pointer
    # until their execution owner durably reports the terminal outcome.
    LIVE_SYNC_STAGES: ClassVar[tuple[SyncStage, ...]] = (
        SyncStage.DISCOVERING,
        SyncStage.SYNCING,
    )

    @property
    def effective_sync_stage(self) -> SyncStage:
        """Reconcile direct workers against their lock; dispatched runs settle durably.

        Queued work has not acquired a lock yet. A retained dispatch is owned by
        its execution engine until terminal delivery. Process-local locks cannot
        prove another worker's liveness, so that backend trusts the stored stage.
        """

        stage = self.SyncStage(self.sync_stage)
        if (
            stage in self.LIVE_SYNC_STAGES
            and not self.sync_is_dispatched
            and task_locks_are_cross_process()
            and not self.is_syncing
        ):
            return self.SyncStage.FAILED
        return stage

    @property
    def sync_is_dispatched(self) -> bool:
        """Whether a retained run owns terminal reporting for this bridge."""

        return self.sync_run_id is not None and self.sync_stage in (self.SyncStage.QUEUED, *self.LIVE_SYNC_STAGES)

    def claim_dispatch(self, run_id: int, *, now: datetime | None = None) -> bool:
        """Claim a run only while the caller's observed execution owner is current.

        Admission owns run eligibility. This compare-and-set may replace a
        terminal run awaiting delivery, but never a concurrently claimed run.
        Repeating a busy claim leaves its start time and telemetry intact. A
        settled run may reclaim the bridge when its execution owner retries it.
        """

        if type(run_id) is not int or run_id <= 0:
            raise ValueError("A positive run identity is required.")
        expected_run_id = self.sync_run_id
        with system_context(reason="integrate.bridge.claim_dispatch"), transaction.atomic():
            row = cast(Any, self).lock_capability()
            if not cast(Any, row).discovery_ready:
                return False
            if row.sync_run_id != expected_run_id or (row.sync_run_id == run_id and row.sync_is_dispatched):
                return False
            row.sync_run_id = run_id
            row.next_sync_at = None
            row.mark_sync_started(now=now or timezone.now())
            row.save(update_fields=["sync_run_id", "next_sync_at", "updated_at"])
        self.refresh_from_db()
        return True

    def settle_dispatch(
        self,
        run_id: int,
        *,
        result: int = 0,
        error: Exception | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Settle the expected busy dispatch once, retaining its inspection ID."""

        with system_context(reason="integrate.bridge.settle_dispatch"), transaction.atomic():
            row = cast(Any, self).lock_capability()
            if row.sync_run_id != run_id or not row.sync_is_dispatched:
                return False
            at = now or timezone.now()
            if error is None:
                row.record_sync(result, now=at)
            else:
                row.record_sync_error(error, now=at)
        self.refresh_from_db()
        return True

    def sync_lock_key(self) -> LockKey:
        """Return the advisory task lock key for this bridge sync."""

        return record_lock_key(self._meta.label_lower, self.pk, "sync")

    def live_session_host_lock_key(self) -> LockKey:
        """Deduplicate process hosts without borrowing the child's store lock."""

        return record_lock_key(self._meta.label_lower, self.pk, "live-host")

    def report_live_session_interrupted(self, *, exitcode: int | None) -> None:
        """Report a reaped child failure while keeping automatic recovery eligible.

        The caller holds the bridge lock after the child exits. Native crashes
        are host failures, not evidence that the account needs fresh credentials.
        Terminal session outcomes and a concurrent operator stop remain intact.
        """

        with system_context(reason="integrate.live.interrupted"), transaction.atomic():
            row = cast(Any, self).lock_capability()
            if (
                row.lifecycle != row.Lifecycle.CONNECTED
                or row.runtime_status != IntegrationRuntimeStatus.OK
                or row.subscription_state.get("desired") != row.LiveState.LIVE
            ):
                return
            with bridge_progress_context(row) as reporter:
                reporter.report(
                    row.SyncStage.FAILED,
                    message="The live session process stopped unexpectedly; automatic recovery will retry.",
                    details={"session": {"state": "interrupted", "exitcode": exitcode}},
                )

    _SYNC_OUTCOME_KEYS = ("error", "items", "completed_at")
    """Marker keys that describe one run's outcome; a new marker starts without them."""

    def _sync_marker(self, **values: Any) -> dict[str, Any]:
        """Return ``sync_progress`` with this run's marker keys set, others kept.

        ``sync_progress`` has disjoint owners: the scheduler writes the lifecycle
        marker (``stage``/``queued_at``/``started_at`` and the run's outcome),
        while a progress reporter writes ``details`` — a live session's pairing
        report lives there, and it is the QR the operator is currently looking
        at. Replacing the whole dict drops it, so a marker merges the way
        :class:`~angee.integrate.sync.BridgeProgressReporter` already does and
        sets only the keys it owns. A previous run's outcome is the marker's own
        to drop: a failed run's ``error`` must not ride into the next run's
        ``completed`` marker.
        """

        existing = self.sync_progress if isinstance(self.sync_progress, Mapping) else {}
        kept = {key: value for key, value in existing.items() if key not in self._SYNC_OUTCOME_KEYS}
        return {**kept, **values}

    def mark_sync_started(self, *, now: datetime) -> bool:
        """Admit one attempt under the connection gate, preserving repeated starts."""

        fields = ["last_sync_started_at", "sync_error", "sync_progress", "sync_stage"]
        with system_context(reason="integrate.bridge.start"), transaction.atomic():
            row = cast(Any, self).lock_capability()
            if not cast(Any, row).discovery_ready:
                return False
            if row.sync_stage == row.SyncStage.SYNCING and row.last_sync_started_at == now:
                return True
            row.last_sync_started_at = now
            row.sync_stage = row.SyncStage.SYNCING
            row.sync_error = ""
            row.sync_progress = row._sync_marker(stage=row.SyncStage.SYNCING, started_at=now.isoformat())
            row.save(update_fields=[*fields, "updated_at"])
        self.refresh_from_db(fields=fields)
        return True

    def claim_sync(self, *, now: datetime) -> bool:
        """Claim the next interval only after the connection is ready for work."""

        with system_context(reason="integrate.bridge.claim"), transaction.atomic():
            row = cast(Any, self).lock_capability()
            if not cast(Any, row).discovery_ready:
                return False
            row.next_sync_at = row._next_sync_at(now=now)
            row.save(update_fields=["next_sync_at", "updated_at"])
        self.next_sync_at = row.next_sync_at
        return True

    def mark_sync_queued(self, *, now: datetime) -> None:
        """Persist that a worker task has been queued for this bridge."""

        with system_context(reason="integrate.bridge.queue"), transaction.atomic():
            row = cast(Any, self).lock_capability()
            if not cast(Any, row).discovery_ready:
                raise IntegrationError("Connection discovery must finish before syncing.")
            if not row.sync_is_dispatched:
                row.sync_stage = self.SyncStage.QUEUED
                row.sync_error = ""
                row.sync_run_id = None
                row.sync_progress = row._sync_marker(stage=self.SyncStage.QUEUED, queued_at=now.isoformat())
                row.save(
                    update_fields=["sync_error", "sync_run_id", "sync_progress", "sync_stage", "updated_at"],
                )
        self.sync_stage, self.sync_error, self.sync_progress = row.sync_stage, row.sync_error, row.sync_progress
        self.sync_run_id = row.sync_run_id

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

    def release_sync_queue(self, *, now: datetime) -> bool:
        """Release a queue claim this run declined; return whether it was still ours.

        A declined run is terminal for its queue token — the work is not happening.
        Leaving the row ``QUEUED`` makes the stale-queue recovery in
        ``angee.integrate.scheduler`` re-queue it every ``_QUEUED_RECOVERY_SECONDS``
        forever, because that recovery exists to repair a *lost* enqueue and cannot
        tell one from a declined run. So the task that declines the work is the one
        that clears the claim.

        Guarded by the queue token and re-read under a row lock: whoever holds the
        sync lock may already have moved this row on (a concurrent
        :meth:`mark_sync_started`), and the decliner must never overwrite the
        holder's stage. Mirrors :meth:`merge_subscription_state`.
        """

        with system_context(reason="integrate.bridge.release_sync_queue"), transaction.atomic():
            row = cast(Any, self).lock_capability()
            if not row.sync_queue_token_matches(now):
                return False
            row.sync_stage = row.SyncStage.IDLE
            row.save(update_fields=["sync_stage", "updated_at"])
        self.sync_stage = self.SyncStage.IDLE
        return True

    @property
    def requires_connection_discovery(self) -> bool:
        """Whether the selected implementation declares asynchronous discovery."""

        if not any(isinstance(field, ImplClassField) for field in self._meta.fields):
            return False
        return cast(Any, self).capability_impl_class.requires_connection_discovery

    def discover_connection(self, credential: Credential) -> ConnectionDiscovery:
        """Invoke the adapter's read-only remote hook outside a transaction."""

        adapter = cast(Any, self).capability_impl
        try:
            return adapter.discover_connection(credential)
        finally:
            adapter.close()

    def apply_discovery(self, discovery: ConnectionDiscovery) -> None:
        """Compose the domain's database apply hook under the integration-owned fence."""

        if not cast(Any, self).requires_connection_discovery:
            if discovery.data or discovery.credential is not None:
                raise IntegrationError("This bridge does not accept connection discovery results.")
            return
        adapter = cast(Any, self).capability_impl
        try:
            adapter.apply_discovery(discovery)
        finally:
            adapter.close()

    def prepare_connection_discovery(self) -> None:
        """Suspend polling and cooperatively stop a live session without changing sync ownership."""

        self.next_sync_at = None
        self.save(update_fields=["next_sync_at", "updated_at"])
        if not cast(Any, self).requires_connection_discovery:
            return
        impl = cast(Any, self).capability_impl_class
        if issubclass(impl, LiveBridgeImpl):
            self.update_live_state(identity_key=impl.state_identity_key, desired=self.LiveState.STOPPED)

    def connection_discovery_finished(self, *, succeeded: bool) -> None:
        """Arm the first eligible poll; retained workflow runs keep their scheduling claim."""

        now = timezone.now()
        self.next_sync_at = now if succeeded and not self.sync_is_dispatched and self._next_sync_at(now=now) else None
        self.save(update_fields=["next_sync_at", "updated_at"])

    def record_sync(self, result: int, *, now: datetime) -> None:
        """Persist one successful scheduler sync result and healthy status report."""

        self.last_sync_completed_at = now
        self.last_sync_items = result
        self.sync_stage = self.SyncStage.COMPLETED
        self.sync_error = ""
        self.sync_progress = self._sync_marker(
            stage=self.SyncStage.COMPLETED,
            items=result,
            completed_at=now.isoformat(),
        )
        self.last_sync_summary = {
            "status": "ok",
            "items": result,
            "completed_at": now.isoformat(),
        }
        with transaction.atomic():
            # Credential attachment may have started discovery during extraction.
            cast(Any, self).lock_capability()
            self.refresh_from_db(
                fields=[
                    "discovery_retry_at",
                    "discovery_generation",
                    "discovery_completed_at",
                    "lifecycle",
                ]
            )
            self.next_sync_at = self._next_sync_at(now=now)
            if cast(Any, self).discovery_ready:
                cast(Any, self).report_status(status=IntegrationRuntimeStatus.OK)
            self.save(
                update_fields=[
                    "cursor",
                    "last_sync_summary",
                    "last_sync_completed_at",
                    "last_sync_items",
                    "next_sync_at",
                    "sync_error",
                    "sync_progress",
                    "sync_stage",
                    "updated_at",
                ],
            )

    def record_sync_error(self, error: Exception, *, now: datetime) -> None:
        """Persist one failed scheduler sync result and error status report."""

        failure = safe_integration_failure(error)
        error_message = failure.message
        self.sync_stage = self.SyncStage.FAILED
        self.sync_error = error_message
        self.sync_progress = self._sync_marker(stage=self.SyncStage.FAILED, error=error_message)
        with transaction.atomic():
            cast(Any, self).lock_capability()
            self.refresh_from_db(
                fields=[
                    "discovery_retry_at",
                    "discovery_generation",
                    "discovery_completed_at",
                    "lifecycle",
                ]
            )
            self.next_sync_at = self._next_sync_at(now=now)
            if self.next_sync_at is not None and isinstance(error, IntegrationError) and error.retry_after is not None:
                self.next_sync_at = max(self.next_sync_at, max(now, timezone.now()) + error.retry_after)
            if cast(Any, self).discovery_ready:
                cast(Any, self).report_status(status=IntegrationRuntimeStatus.ERROR, error=failure)
            self.save(
                update_fields=[
                    "next_sync_at",
                    "sync_error",
                    "sync_progress",
                    "sync_stage",
                    "updated_at",
                ],
            )

    def clear_sync_error(self) -> None:
        """Clear a stale failure once a live session proves the account healthy.

        The mirror of :meth:`record_sync_error` — that method is the only writer
        of ``sync_error``, ``sync_stage == FAILED``, and the ``error``
        outcome key, and only the poll scheduler's start/finish markers drop them
        again. A live-desired bridge never enters the poll loop
        (:meth:`Channel._next_sync_at` returns ``None`` while ``desired`` is
        ``live``), so a months-old failure would otherwise outlive a successful
        reconnect forever. :meth:`LiveSession._mark_paired` calls this the moment
        it reports ``OK``: reaching ``PAIRED`` is the proof the failure is no
        longer true, and resume/start is not — a failure stays true until the
        session actually pairs.

        Re-reads under a row lock because the session's pairing report
        (:class:`~angee.integrate.sync.BridgeProgressReporter`) is the concurrent
        writer of the same ``sync_progress`` dict: the pairing ``details`` (the QR
        the operator is looking at) and the scheduler's ``stage``/``queued_at``
        markers must survive. Dropping the failure is exactly :meth:`_sync_marker`
        with no new values — it keeps everything but this run's outcome keys. A row
        with nothing to clear is left untouched so a healthy reconnect writes
        nothing to the change feed.
        """

        with system_context(reason="integrate.bridge.clear_sync_error"), transaction.atomic():
            row = cast(Any, self).lock_capability()
            progress = row.sync_progress if isinstance(row.sync_progress, Mapping) else {}
            if not row.sync_error and row.sync_stage != row.SyncStage.FAILED and "error" not in progress:
                return
            row.sync_stage = row.SyncStage.COMPLETED if row.last_sync_completed_at else row.SyncStage.IDLE
            row.sync_error = ""
            row.sync_progress = row._sync_marker()
            row.save(
                update_fields=[
                    "sync_stage",
                    "sync_error",
                    "sync_progress",
                    "updated_at",
                ],
            )
        self.sync_stage = row.sync_stage
        self.sync_error = row.sync_error
        self.sync_progress = row.sync_progress

    def run_sync(self, *, now: datetime) -> int | SyncDispatch:
        """Run one sync attempt and persist its lifecycle telemetry."""

        if self.sync_is_dispatched:
            return SyncDispatch.DISPATCHED
        if not self.mark_sync_started(now=now):
            raise IntegrationError("Connection discovery must finish before syncing.")
        try:
            with bridge_sync_context(), bridge_progress_context(self):
                result = self.sync()
            if result is SyncDispatch.DISPATCHED:
                return result
            # Partitioned syncs report through thread-local Bridge instances.
            # Re-read their last merged payload before this parent writes the
            # terminal marker, or its stale in-memory value drops budget/cursor
            # detail. The scheduler timestamp identifies the attempt; completion
            # is when the external work actually finished.
            self.refresh_from_db(fields=["sync_progress"])
            self.record_sync(result, now=timezone.now())
        except Exception as error:  # noqa: BLE001 — sync failure is telemetry, then caller policy.
            self.refresh_from_db(fields=["sync_progress"])
            self.record_sync_error(error, now=timezone.now())
            raise
        return result

    def sync(self) -> int | SyncDispatch:
        """Drive backend streams; concrete bridges may override this sync seam."""

        return sync_bridge(self)

    def handle_webhook(self, payload: Any) -> None:
        """Apply one verified inbound webhook payload to this bridge."""

        raise NotImplementedError("Bridge subclasses must implement handle_webhook().")

    def verify_webhook(self, request: Any) -> bool:
        """Return whether an inbound webhook request is authentic for this bridge."""

        raise NotImplementedError("Bridge subclasses must implement verify_webhook().")

    def dispatch_inbound(self, request_or_payload: Any) -> bool:
        """Land an authentic webhook under the polling and settlement lock.

        False refuses verification; contention raises a transient refusal so
        the verified delivery can retry. Call outside atomic blocks: the session
        advisory lock must cover the concrete capability's committed landing.
        """

        if connection.in_atomic_block:
            raise IntegrationError("Inbound dispatch requires autocommit.")
        with system_context(reason="integrate.inbound.capability"):
            capability = cast(Any, self).concrete_capability()
        if not capability.verify_webhook(request_or_payload):
            return False
        with bridge_advisory_lock(capability) as acquired:
            if not acquired:
                raise IntegrationError("The bridge is busy; retry inbound delivery.", transient=True)
            capability.handle_webhook(request_or_payload)
        return True

    def start_live(self) -> None:
        """Start or renew this bridge's live vendor subscription."""

        raise NotImplementedError("Bridge subclasses must implement start_live().")

    def stop_live(self) -> None:
        """Stop this bridge's live vendor subscription."""

        raise NotImplementedError("Bridge subclasses must implement stop_live().")

    def merge_subscription_state(self, **values: Any) -> dict[str, Any]:
        """Merge sub-keys into ``subscription_state`` under a row lock; return it.

        This method merges only — a key can never be removed; consumed markers
        settle to a sentinel value defined by their owning flow.
        ``subscription_state`` has disjoint owners — the operator writes
        ``desired`` (start/stop), a live session writes its own pairing facts —
        that may write concurrently from separately-loaded instances. A full
        read-modify-write from a stale instance would clobber the other owner's
        key, so this re-reads the row locked, merges only the given keys, and
        saves through the shared nested-JSON owner.
        """

        return merge_json_state(cast(Any, self), "subscription_state", values)

    def _next_sync_at(self, *, now: datetime) -> datetime | None:
        """Return the next polling timestamp from this bridge's interval.

        ``None`` keeps paused/disconnected bridges unscheduled after an explicit
        one-shot sync. A push-mode child (a live chat channel) may also override
        this to stay out of the poll loop while its live ingest owns delivery.
        """

        if (
            IntegrationLifecycle.from_value(self.lifecycle) is not IntegrationLifecycle.CONNECTED
            or not cast(Any, self).discovery_ready
        ):
            return None
        return now + timedelta(seconds=int(self.poll_interval))


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
                kind=kind_value, body=body, impl_app=impl_app, integration_pk=integration_pk
            ),
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

        return canonical_json(payload).encode("utf-8")

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
                kind=kind, impl_app=impl_app, integration_pk=integration_pk
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
        if connection.features.supports_json_field_contains:
            queryset = queryset.filter(event_kinds__contains=[kind])
            queryset = queryset.filter(Q(impl_app_filter=[]) | Q(impl_app_filter__contains=[impl_app]))
        return queryset.order_by("pk")


class WebhookSubscription(AuditMixin, AngeeDataModel):
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
    last_error: str = DiagnosticTextField(blank=True, default="")
    consecutive_failures = models.PositiveIntegerField(default=0)

    objects = WebhookSubscriptionManager()

    class Meta:
        """Django model options for webhook subscriptions."""

        abstract = True
        rebac_resource_type = "integrate/webhook_subscription"

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
            logger.error(
                "Webhook delivery failed for subscription %s (%s).",
                self.public_id,
                type(exc).__name__,
            )
            message = self._delivery_error_message(exc)
            self.record_delivery_failure(status=self._delivery_failure_status(exc), error=message)
            return False, message
        self.record_delivery(status)
        return True, status

    def deliver_test(self) -> tuple[bool, str]:
        """Send a test event, persist telemetry, and return an action result tuple."""

        body = canonical_json({"type": "test", "subscription": self.public_id}).encode("utf-8")
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

        if isinstance(exc, WebhookDeliveryError) and exc.status:
            return f"Webhook returned HTTP {exc.status}."
        if isinstance(exc, ValidationError):
            return "Webhook target is invalid."
        return _WEBHOOK_FAILURE_MESSAGE

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


class SyncStreamManager(AngeeManager):
    """Serialize stream discovery and epoch changes on the integration row."""

    def lock_current(self, stream: Any) -> Any:
        """Fence one page against epoch changes inside the caller's transaction.

        The stream row lock is shared with epoch retirement. A stale extracted
        page must fail before any domain records or cursor state are applied.
        """

        if not connection.in_atomic_block:
            raise RuntimeError("Locking a stream requires the page transaction.")
        with system_context(reason="integrate.stream.lock_current"):
            locked = self.filter(pk=stream.pk).lock_if_supported().get()
            if self.filter(
                integration_id=locked.integration_id,
                key=locked.key,
                partition=locked.partition,
                generation__gt=locked.generation,
            ).exists():
                raise RuntimeError("The extracted page belongs to a retired stream generation.")
            return locked

    def current_for_bridge(self, bridge: models.Model, key: str) -> Any:
        """Return one current epoch per partition for the bridge's named stream."""

        rows = self.sudo(reason="integrate.stream.current_for_bridge").filter(
            integration_id=bridge.pk,
            key=key,
        )
        latest = rows.filter(partition=OuterRef("partition")).order_by("-generation").values("generation")[:1]
        return rows.filter(generation=Subquery(latest)).order_by("partition")

    def current(
        self,
        bridge: models.Model,
        key: str,
        partition: str = "",
        *,
        kind: StreamKind = StreamKind.EVENT_FEED,
        direction: StreamDirection = StreamDirection.PULL,
        snapshot: bool = False,
        authoritative: bool = False,
        cursor: Any = None,
        reconcile_interval: timedelta | None = None,
        absence_threshold: int = 2,
        tombstone_retention: timedelta | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> Any:
        """Return the latest epoch, creating its baseline once per partition.

        Cursor and policy arguments seed only the first epoch; subsequent calls
        preserve persisted progress and policy. Kind is immutable; direction or
        snapshot changes start a new baseline generation with the persisted policy.
        ``authoritative`` is the declaration's fact: a changed declaration flips the
        current generation through :meth:`set_authoritative`, so an adapter never
        flips it from inside a page.
        """

        if absence_threshold < 1:
            raise ValidationError("A stream absence threshold must be positive.")
        integration = apps.get_model("integrate", "Integration")
        if bridge._meta.proxy or integration not in bridge._meta.get_parent_list():
            raise ValidationError("A stream bridge must be a concrete Integration child.")
        with system_context(reason="integrate.stream.current"), transaction.atomic():
            cast(Any, bridge).lock_capability()
            identity = {"integration_id": bridge.pk, "key": key, "partition": partition}
            stream = self.filter(**identity).order_by("-generation").first()
            if stream is None:
                return self.create(
                    **identity,
                    kind=kind,
                    direction=direction,
                    snapshot=snapshot,
                    authoritative=authoritative,
                    cursor={} if cursor is None else cursor,
                    reconcile_interval=reconcile_interval,
                    absence_threshold=absence_threshold,
                    tombstone_retention=tombstone_retention,
                    config=dict(config or {}),
                )
            if stream.kind != kind:
                raise ValidationError("A stream's kind cannot change between declarations.")
            if stream.direction != direction or stream.snapshot != snapshot:
                stream = self.bump_generation(stream, direction=direction, snapshot=snapshot)
            if stream.authoritative != authoritative:
                stream = self.set_authoritative(stream, authoritative)
            return stream

    def bump_generation(
        self,
        stream: Any,
        *,
        cursor: dict[str, Any] | None = None,
        direction: StreamDirection | None = None,
        snapshot: bool | None = None,
    ) -> Any:
        """Create one new baseline while retaining identities and history.

        Concurrent requests against a retired epoch return its successor. Links
        follow the current epoch without changing their verification marker.
        """

        with system_context(reason="integrate.stream.bump_generation"), transaction.atomic():
            latest = self._lock_latest(stream)
            if latest.pk != stream.pk:
                return latest
            successor = self.create(
                integration_id=latest.integration_id,
                key=latest.key,
                partition=latest.partition,
                kind=latest.kind,
                direction=latest.direction if direction is None else direction,
                snapshot=latest.snapshot if snapshot is None else snapshot,
                authoritative=latest.authoritative,
                generation=latest.generation + 1,
                cursor={} if cursor is None else cursor,
                last_advanced_at=timezone.now() if cursor else None,
                reconcile_interval=latest.reconcile_interval,
                absence_threshold=latest.absence_threshold,
                tombstone_retention=latest.tombstone_retention,
                config=deepcopy(latest.config),
                inbound=deepcopy(latest.inbound),
            )
            apps.get_model("integrate", "RecordLink").objects.filter(stream=latest).update(stream=successor)
            latest.state = StreamState.CLOSED
            latest.inbound = []
            latest.save(update_fields=["state", "inbound", "updated_at"])
            return successor

    def request_resync(self, stream: Any) -> Any:
        """Request a baseline on the next cycle, even from a retained old epoch.

        The driver owns the generation bump. Serialize this intent with that
        transition so a concurrent bump cannot lose the request on the old row.
        """

        with system_context(reason="integrate.stream.request_resync"), transaction.atomic():
            latest = self._lock_latest(stream)
            if not latest.resync_required:
                latest.resync_required = True
                latest.save(update_fields=["resync_required", "updated_at"])
            return latest

    def set_authoritative(self, stream: Any, authoritative: bool) -> Any:
        """Flip whether the stream's source is the system of record, then re-derive its subjects.

        The knob lives on the stream's current generation. Statements are retained,
        so the flip is a re-derivation: survivorship re-runs for every subject with
        a current statement linked through this stream, under the caller's changeset.
        The caller authorizes the flip on the stream's integration.
        """

        with system_context(reason="integrate.stream.set_authoritative"), transaction.atomic():
            latest = self._lock_latest(stream)
            if latest.authoritative != authoritative:
                latest.authoritative = authoritative
                latest.save(update_fields=["authoritative", "updated_at"])
            for reference in references(apps.get_model("integrate", "RecordLink")):
                if not issubclass(reference.model, Statement):
                    continue
                linked = reference.model._base_manager.filter(
                    **{f"{reference.name}__stream": latest}, superseded__isnull=True
                )
                subjects = reference.model.statement_subject_model._base_manager.filter(
                    pk__in=linked.values("subject_id")
                )
                for subject in subjects.order_by("pk").iterator():
                    reference.model.objects.resolve(subject=subject)
            return latest

    def _lock_latest(self, stream: Any) -> Any:
        """Lock the integration and its latest epoch in generation-change order."""

        stream.integration.lock_capability()
        return (
            self.filter(
                integration_id=stream.integration_id,
                key=stream.key,
                partition=stream.partition,
            )
            .order_by("-generation")
            .lock_if_supported()
            .first()
        )

    def advance(
        self,
        stream: Any,
        cursor: Any,
        *,
        exhausted: bool = False,
        cursor_expires_at: datetime | None = None,
    ) -> Any:
        """Persist an opaque page cursor inside the caller's apply transaction."""

        with system_context(reason="integrate.stream.advance"), transaction.atomic():
            locked = self.lock_current(stream)
            if locked.state == StreamState.CLOSED:
                raise ValidationError("A closed stream requires a new generation before advancement.")
            locked.cursor = cursor
            locked.cursor_expires_at = cursor_expires_at
            locked.last_advanced_at = timezone.now()
            if exhausted:
                locked.phase = StreamPhase.DELTA
                if locked.snapshot and locked.reconcile_interval is None:
                    locked.state = StreamState.CLOSED
            fields = ["cursor", "cursor_expires_at", "last_advanced_at", "phase", "state"]
            locked.save(update_fields=[*fields, "updated_at"])
            for field in fields:
                setattr(stream, field, getattr(locked, field))
            return stream


class SyncStream(SyncStateMixin, AuditMixin, AngeeDataModel):
    """An epoch's opaque progress and adapter-owned policy for one partition."""

    runtime = True
    sqid_prefix = "sst_"
    integration = models.ForeignKey("integrate.Integration", on_delete=models.PROTECT, related_name="sync_streams")
    key = models.CharField(max_length=160)
    partition = models.CharField(max_length=255, blank=True)
    kind = StateField(choices_enum=StreamKind)
    direction = StateField(choices_enum=StreamDirection)
    snapshot = models.BooleanField(default=False)
    authoritative = models.BooleanField(default=False)
    """Whether this stream's source is the system of record for the aspects it asserts.

    Set on a SYSTEM_OF_RECORD source, its statements outrank people's ordinary
    (normal-rank) edits in every aspect whose precedence lists that kind; unset,
    the stream is a system of reference below people. A person's confirm or
    dismiss stays on top either way. Flip through
    :meth:`SyncStreamManager.set_authoritative`: statements are retained, so the
    flip is a re-derivation, never a migration.
    """
    generation = models.PositiveIntegerField(default=1)
    phase = StateField(choices_enum=StreamPhase, default=StreamPhase.BASELINE)
    state = StateField(choices_enum=StreamState, default=StreamState.OPEN)
    cursor = models.JSONField(default=dict, blank=True)
    reconcile_state = models.JSONField(default=dict, blank=True)
    config = models.JSONField(default=dict, blank=True)
    cursor_expires_at = models.DateTimeField(null=True, blank=True)
    resync_required = models.BooleanField(default=False)
    last_advanced_at = models.DateTimeField(null=True, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    reconcile_interval = models.DurationField(null=True, blank=True)
    absence_threshold = models.PositiveIntegerField(default=2)
    tombstone_retention = models.DurationField(null=True, blank=True)
    objects = SyncStreamManager()
    unscoped_objects = AngeeUnscopedManager()

    def __str__(self) -> str:
        """Label this stream by its adapter key and optional partition."""

        return f"{self.key} ({self.partition})" if self.partition else self.key

    @property
    def writes_back(self) -> bool:
        """Whether this stream writes local changes to its remote: a replica that is not pull-only."""

        return (
            self.state == StreamState.OPEN
            and self.kind == StreamKind.RECORD_REPLICA
            and self.direction != StreamDirection.PULL
        )

    def has_completed_baseline(self) -> bool:
        """Read this generation's persisted completion; earlier baselines do not count."""

        return type(self).unscoped_objects.filter(pk=self.pk, phase=StreamPhase.DELTA).exists()

    @property
    def failure_stream(self) -> SyncStream:
        return self

    def sync_failures(self, *, open_only: bool = False) -> tuple[SyncFailure, ...]:
        """Read stream and linked failures through their owning row permissions."""
        actor, unscoped = self.effective_actor(strict=True)
        links = self.links.all()
        if unscoped:
            links = links.sudo(reason="integrate.stream.sync_failures")
        else:
            links = links.with_actor(actor)
        if open_only:
            prefetched = getattr(self, "open_failure_links", None)
            links = prefetched if prefetched is not None else links.with_open_failures()
        else:
            links = links.only("stream_id", "inbound", "outbox_last_error").order_by("pk")
        rows = list(self.failures(open_only=open_only))
        for link in links:
            link.stream = self
            rows.extend(link.failures(open_only=open_only))
        return tuple(rows)

    class Meta:
        abstract = True
        base_manager_name = "unscoped_objects"
        rebac_resource_type = "integrate/sync_stream"
        rebac_id_attr = "pk"
        constraints = (
            models.UniqueConstraint(
                fields=("integration", "key", "partition", "generation"), name="uniq_sync_stream_generation"
            ),
            models.CheckConstraint(condition=Q(absence_threshold__gte=1), name="sync_stream_absence_positive"),
        )


class RecordLinkQuerySet(AngeeQuerySet[Any]):
    """Load comparison evidence for a bounded collection of replica identities."""

    def with_sync_evidence(self) -> Self:
        """Load the protected application projection; state is already on the link."""
        return self.select_related("applied_base", "parent", "stream").defer(
            "source_payload", "mapped_payload", "applied_base__source_payload"
        )

    def with_open_failures(self) -> Self:
        """Select open-state owners in stable order without their payloads."""
        return (
            self.filter(Q(status=LinkStatus.FAILED) | Q(outbox_state__isnull=False))
            .order_by("pk")
            .only(
                "stream_id",
                "parent_id",
                "external_key",
                "status",
                "inbound",
                "outbox_state",
                "outbox_retry_at",
                "outbox_last_error",
            )
        )

    def open_failures(self) -> tuple[SyncFailure, ...]:
        """Project state after the caller's actor-scoped link selection."""
        return tuple(
            row
            for link in self.with_open_failures().select_related("stream").iterator(chunk_size=500)
            for row in link.failures(open_only=True)
        )

    def rescan(self, *, limit: int) -> tuple[SyncFailure, ...]:
        """Bound due inbound failures after excluding every conflicted aggregate."""
        if limit < 1:
            raise ValueError("A failure rescan limit must be positive.")
        links = self.with_open_failures().select_related("stream")
        conflicts = {
            link.parent_id or link.pk
            for link in links.iterator(chunk_size=500)
            if any(row.kind == SyncFailureKind.CONFLICT for row in link.failures(open_only=True))
        }
        now = timezone.now()
        due = []
        for link in links.iterator(chunk_size=500):
            if (link.parent_id or link.pk) in conflicts:
                continue
            for row in link.failures(open_only=True):
                if (
                    row.slot is not None
                    and row.kind != SyncFailureKind.REMOTE_REJECTED
                    and (row.retry_at is None or row.retry_at <= now)
                ):
                    due.append(row)
                    if len(due) == limit:
                        return tuple(due)
        return tuple(due)


class RecordLinkManager(AngeeManager.from_queryset(RecordLinkQuerySet)):  # type: ignore[misc]
    """Own replica identity, applied bases and count-based absence policy."""

    def observe(
        self,
        stream: Any,
        external_key: str,
        *,
        remote_version: str | None = None,
        source_payload: Any = UNSET,
        source_hash: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        parent: Any = UNSET,
        target: Any = UNSET,
    ) -> Any:
        """Retain identity without advancing applied bases.

        A child belongs to one root link in the same stream. Omitted bindings
        remain unchanged; ``target=None`` withdraws the projection explicitly.
        """

        with system_context(reason="integrate.record.observe"), transaction.atomic():
            stream = type(stream).objects.lock_current(stream)
            if stream.kind != StreamKind.RECORD_REPLICA:
                raise ValidationError("Event feeds do not create record links.")
            link, _ = self.lock_if_supported().get_or_create(stream=stream, external_key=external_key)
            if parent is not UNSET:
                if parent is not None:
                    parent = self.filter(pk=parent.pk).lock_if_supported().get()
                    if parent.stream_id != stream.pk or parent.parent_id is not None or parent.pk == link.pk:
                        raise ValidationError("A record parent must be another root link in the same stream.")
                    if self.filter(parent=link).exists():
                        raise ValidationError("A root with children cannot become a child link.")
                parent_id = None if parent is None else parent.pk
                if link.parent_id is not None and link.parent_id != parent_id:
                    raise ValidationError("A record link's aggregate parent is immutable.")
                link.parent_id = parent_id
            if (
                remote_version is not None
                and remote_version != link.remote_version
                and link.status != LinkStatus.FAILED
            ):
                link.status = LinkStatus.OBSERVED
            if remote_version is not None:
                link.remote_version = remote_version
            if source_payload is not UNSET:
                link.source_payload = source_payload
            if source_hash is not None:
                link.source_hash = source_hash
            # The application facts and protected base never change here.
            link.last_seen_at = timezone.now()
            link.last_verified_generation = stream.generation
            link.absence_count = 0
            if metadata is not None:
                link.metadata = dict(metadata)
            if target is not UNSET:
                self._set_target(link, target)
                if target is None:
                    link.status = LinkStatus.WITHDRAWN
            link.update_sync_status()
            link.save(
                update_fields=[
                    "status",
                    "source_payload",
                    "source_hash",
                    "remote_version",
                    "last_seen_at",
                    "last_verified_generation",
                    "absence_count",
                    "metadata",
                    "parent_id",
                    "target_content_type_id",
                    "target_object_id",
                    "updated_at",
                ],
            )
            return link

    def promote(
        self,
        link: Any,
        *,
        source_payload: Any,
        source_hash: str,
        mapped_payload: Any,
        mapping_version: int = 1,
        dependency_digest: str = "",
        target: Any = UNSET,
        remote_version: str = "",
        origin: str = "remote",
        tombstone: bool = False,
    ) -> Any:
        """Append applied evidence and pin its base; the driver owns promotion.

        Adapters return their evidence to the driver, never promote its link
        themselves. Omit ``target`` to retain its binding; pass ``None`` to clear
        it and retain a WITHDRAWN identity.
        """

        with system_context(reason="integrate.record.promote"), transaction.atomic():
            locked = self.filter(pk=link.pk).lock_if_supported().get()
            previous = locked.applied_base
            if mapped_payload is UNSET:
                mapped_payload = previous.mapped_payload if previous is not None else {}
            locked.source_payload = {} if source_payload is None else source_payload
            locked.source_hash, locked.mapped_payload = source_hash, mapped_payload
            locked.mapping_version, locked.dependency_digest = mapping_version, dependency_digest
            locked.applied_at = timezone.now()
            if target is not UNSET:
                self._set_target(locked, target)
            locked.remote_version, locked.origin, locked.status = remote_version, origin, LinkStatus.CURRENT
            if target is None:
                locked.status = LinkStatus.WITHDRAWN
            locked.tombstoned_at = timezone.now() if tombstone else None
            if tombstone:
                locked.status = LinkStatus.TOMBSTONE
            fields = [
                "source_payload",
                "source_hash",
                "mapped_payload",
                "mapping_version",
                "dependency_digest",
                "applied_at",
                "target_content_type_id",
                "target_object_id",
                "remote_version",
                "origin",
                "status",
                "tombstoned_at",
            ]
            locked.update_sync_status()
            application = None
            context = current_changeset()

            def pin_application(instance: Any, history_instance: Any, **kwargs: Any) -> None:
                nonlocal application
                if instance.pk == locked.pk and current_changeset() is context:
                    application = history_instance
                    locked.applied_base = history_instance
                    # This pointer completes the application save, not a new observation.
                    locked.save_without_historical_record(update_fields=["applied_base"])

            sender = locked.history.model
            post_create_historical_record.connect(pin_application, sender=sender, weak=False)
            try:
                locked.save(update_fields=[*fields, "updated_at"])
            finally:
                post_create_historical_record.disconnect(pin_application, sender=sender)
            if application is None:
                raise RuntimeError("Application save did not create its historical evidence.")
            for field in [*fields, "applied_base"]:
                setattr(link, field, getattr(locked, field))
            return application

    def _set_target(self, link: Any, target: models.Model | None) -> None:
        if target is None:
            link.target_content_type_id = link.target_object_id = None
        else:
            if target.pk is None:
                raise ValidationError("A record target must be saved.")
            # Provenance names any applied row, a plain domain sink included.
            link.target_content_type, object_id = generic_pointer_target(target)
            link.target_object_id = str(object_id)

    def bind_target(self, link: Any, target: models.Model) -> None:
        """Bind the source before assertion admission inside its application transaction.

        The driver's promotion records this binding in the application snapshot.
        Like applied_base, this completes that write without a pointer-only snapshot.
        """

        if not transaction.get_connection(self.db).in_atomic_block or not is_sudo():
            raise PermissionDenied("Target binding requires the trusted application transaction.")
        self._set_target(link, target)
        link.save_without_historical_record(using=self.db, update_fields=("target_content_type", "target_object_id"))

    def revalidate(self, link: Any, *, target: Any = UNSET) -> Any:
        """Restore a verified identity without advancing its application base.

        A link already current on the same target is left unsaved: the delivery's
        observation recorded it, and a save would repeat that snapshot in history.
        """
        fields = ("target_content_type_id", "target_object_id", "status", "tombstoned_at")
        locked = self.lock_if_supported().get(pk=link.pk)
        before = [getattr(locked, name) for name in fields]
        if target is not UNSET:
            self._set_target(locked, target)
        locked.status = LinkStatus.CURRENT
        locked.tombstoned_at = None
        locked.update_sync_status()
        if [getattr(locked, name) for name in fields] != before:
            locked.save(
                update_fields=["target_content_type", "target_object_id", "status", "tombstoned_at", "updated_at"]
            )
        for name in fields:
            setattr(link, name, getattr(locked, name))
        return locked

    def mark_absent(self, stream: Any, keys: Iterable[str]) -> int:
        """Count a bounded batch of missing keys, retaining tombstones.

        Children may be included only after their parent has absence evidence;
        the sweep supplies bounded child batches after marking missing roots.
        """

        with system_context(reason="integrate.record.mark_absent"), transaction.atomic():
            stream = type(stream).objects.lock_current(stream)
            rows = (
                self.filter(stream=stream, external_key__in=tuple(keys))
                .filter(Q(parent__isnull=True) | Q(parent_id__in=self.filter(absence_count__gt=0).values("pk")))
                .exclude(
                    status=LinkStatus.TOMBSTONE,
                )
                .order_by("pk")
                .lock_if_supported()
            )
            count = 0
            for link in rows:
                link.absence_count += 1
                if link.status != LinkStatus.FAILED:
                    link.status = LinkStatus.UNAVAILABLE
                    if link.absence_count >= stream.absence_threshold:
                        link.status, link.tombstoned_at = LinkStatus.TOMBSTONE, timezone.now()
                link.save(update_fields=["absence_count", "status", "tombstoned_at", "updated_at"])
                count += 1
            return count

    def tombstone(self, link: Any) -> Any:
        """Retain a confirmed remote deletion; never delete the identity."""

        with system_context(reason="integrate.record.tombstone"), transaction.atomic():
            locked = self.filter(pk=link.pk).lock_if_supported().get()
            if locked.status != LinkStatus.TOMBSTONE:
                locked.status, locked.tombstoned_at = LinkStatus.TOMBSTONE, timezone.now()
                locked.save(update_fields=["status", "tombstoned_at", "updated_at"])
            link.status, link.tombstoned_at = locked.status, locked.tombstoned_at
            return link


class RecordLink(RecordRefMixin, SyncStateMixin, AuditMixin, HistoryMixin, StatementLinkMixin, AngeeDataModel):
    """A remote identity retaining observations, application facts and run state.

    ``applied_base`` pins this link's own historical application snapshot; an
    observation never advances it. Its protected FK keeps the active comparison
    base out of native history pruning. ``pinned`` withholds automatic updates.

    ``inbound`` retains a list of failures, including closed evidence. Each entry
    carries kind, code, source_hash, mapping_version, details, status, attempts,
    retry_at, resolved_at, created_at and updated_at. Times are ISO 8601 strings
    or null. Several open failure identities can coexist; resolution updates an
    entry instead of removing it. History captures each change to the list.
    ``outbox_last_error`` retains the same evidence shape for the latest refused
    or failed write; a null outbox state means no write is pending.
    """

    merge_policy = MergePolicy.MOVE
    runtime = True
    sqid_prefix = "rlk_"
    authority_related = ("stream",)
    stream = models.ForeignKey("integrate.SyncStream", on_delete=models.PROTECT, related_name="links")

    @property
    def authoritative(self) -> bool:
        """The stream decides authority for every statement this link backs."""

        return bool(self.stream.authoritative)

    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    external_key = models.CharField(max_length=512)
    asserted_by = models.TextField(blank=True, editable=False)
    target_content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    target_object_id = models.CharField(max_length=255, null=True, blank=True)
    target = GenericForeignKey("target_content_type", "target_object_id")
    status = StateField(choices_enum=LinkStatus, default=LinkStatus.OBSERVED)
    source_payload = models.JSONField(default=dict, blank=True)
    source_hash = models.CharField(max_length=64, blank=True)
    mapped_payload = models.JSONField(default=dict, blank=True, null=True)
    mapping_version = models.PositiveIntegerField(default=1)
    dependency_digest = models.CharField(max_length=64, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_base = models.ForeignKey(
        "integrate.HistoricalRecordLink", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    pinned = models.BooleanField(default=False)
    outbox_state = StateField(choices_enum=OutboxState, null=True, blank=True)
    outbox_retry_at = models.DateTimeField(null=True, blank=True)
    outbox_last_error = models.JSONField(default=dict, blank=True)
    remote_version = models.CharField(max_length=512, blank=True)
    origin = models.CharField(max_length=16, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_verified_generation = models.PositiveIntegerField(default=0)
    absence_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    tombstoned_at = models.DateTimeField(null=True, blank=True)
    objects = RecordLinkManager()
    unscoped_objects = AngeeUnscopedManager()

    def __str__(self) -> str:
        """Label this remote identity by its external key."""

        return self.external_key

    @property
    def failure_stream(self) -> SyncStream:
        return self.stream

    def _lock_sync_state(self) -> Self:
        """Follow a link's current epoch before fencing a retained failure mutation."""
        self.refresh_from_db(fields=("stream",))
        return super()._lock_sync_state()

    def failures(self, *, open_only: bool = False) -> tuple[SyncFailure, ...]:
        rows = list(super().failures(open_only=open_only))
        if self.outbox_last_error:
            row = SyncFailure.from_state(self, None, self.outbox_last_error)
            if not open_only or row.is_open:
                rows.append(row)
        return tuple(rows)

    def asserting_source(self) -> SubjectRef:
        """The principal this link's observations assert as: its retained stamp, else its integration."""

        return SubjectRef.parse(self.asserted_by) if self.asserted_by else to_subject_ref(self.stream.integration)

    def withdraw_statements(self) -> int:
        """Withdraw the source assertions no other link of the source still evidences.

        One source can observe a record through several links (an instance behind
        several company streams, a contact in two address books) while each of its
        assertions names one of them. While another of the source's links still
        observes the record, the assertions move to it through the shared reference
        move, which keeps them asserted; the last link's tombstone withdraws them.
        Two siblings tombstoned at once could otherwise move the assertions onto each
        other: this relies on the bridge advisory lock, held by every sync job, webhook
        landing and live session of the source's integration, to serialize them.
        """

        source = self.asserting_source()
        statements = [
            reference
            for reference in references(type(self))
            if issubclass(reference.model, Statement) and reference.columns == ("link",)
        ]
        observing = (
            type(self)
            ._base_manager.using(self._state.db)
            .filter(
                target_content_type_id=self.target_content_type_id,
                target_object_id=self.target_object_id,
                tombstoned_at__isnull=True,
            )
            .exclude(pk=self.pk)
            .exclude(status__in=(LinkStatus.TOMBSTONE, LinkStatus.WITHDRAWN))
            .select_related("stream__integration")
            .order_by("pk")
        )
        targeted = self.target_content_type_id is not None and bool(self.target_object_id)
        sibling = next((link for link in observing if link.asserting_source() == source), None) if targeted else None
        if sibling is None:
            return sum(
                reference.model.objects.db_manager(self._state.db).withdraw(source, link=self)
                for reference in statements
            )
        with system_context(reason="integrate.link_evidence_move"):
            for reference in statements:
                rows = reference.rows(self).using(self._state.db).filter(asserted_by=str(source))
                if rows.exists():
                    reference.move(rows, survivor=sibling)
        return 0

    def update_sync_status(self) -> None:
        """Open failures dominate observation/application status until resolved."""
        if self.failures(open_only=True):
            self.status = LinkStatus.FAILED
        elif self.status == LinkStatus.FAILED:
            if self.tombstoned_at is not None:
                self.status = LinkStatus.TOMBSTONE
            else:
                self.status = LinkStatus.CURRENT if self.applied_base_id else LinkStatus.OBSERVED

    def on_sync_state_saved(self, locked: Self) -> None:
        locked.update_sync_status()
        fields = ["inbound", "outbox_state", "outbox_retry_at", "outbox_last_error", "status"]
        locked.save(update_fields=[*fields, "updated_at"])
        for name in fields:
            setattr(self, name, getattr(locked, name))

    def record_failure(
        self,
        *,
        kind: SyncFailureKind,
        code: str,
        source_hash: str = "",
        mapping_version: int = 1,
        details: Mapping[str, Any] | None = None,
        outbox: bool = False,
    ) -> SyncFailure:
        """Retain an inbound slot or the latest refused conditional write."""
        if not outbox:
            return super().record_failure(
                kind=kind, code=code, source_hash=source_hash, mapping_version=mapping_version, details=details
            )
        with transaction.atomic():
            locked = self._lock_sync_state()
            data = deepcopy(locked.outbox_last_error)
            identity = dict(kind=kind, code=code, source_hash=source_hash, mapping_version=mapping_version)
            if data.get("status") == SyncFailureStatus.RESOLVED or any(
                data.get(name) != value for name, value in identity.items()
            ):
                if data and data["status"] != SyncFailureStatus.RESOLVED:
                    locked.resolve_failure(None, conflict=True)
                data = {}
            data = locked._failure_data(
                data, kind=kind, code=code, source_hash=source_hash, mapping_version=mapping_version, details=details
            )
            locked.outbox_last_error, locked.outbox_state = data, OutboxState.REFUSED
            locked.outbox_retry_at = datetime.fromisoformat(data["retry_at"]) if data["retry_at"] else None
            self.on_sync_state_saved(locked)
            return SyncFailure.from_state(self, None, data)

    def _transition_failure(self, slot: int | None, *, retry: bool, conflict: bool = False) -> SyncFailure:
        if slot is not None:
            return super()._transition_failure(slot, retry=retry, conflict=conflict)
        with transaction.atomic():
            locked = self._lock_sync_state()
            data = locked.outbox_last_error
            locked._transition_state(data, retry=retry, conflict=conflict)
            locked.outbox_state = cast(Any, OutboxState.PENDING if retry else None)
            locked.outbox_retry_at = datetime.fromisoformat(data["retry_at"]) if data["retry_at"] else None
            self.on_sync_state_saved(locked)
            return SyncFailure.from_state(self, None, data)

    def local_changed(self, projection: Any) -> bool:
        """Compare a JSON-native projection with this link's protected applied base."""
        if self.applied_base is None:
            return projection is not None
        return bool(
            DeepDiff(
                json.loads(canonical_json(self.applied_base.mapped_payload)),
                json.loads(canonical_json(projection)),
            )
        )

    def pin(self) -> None:
        """Withhold automatic inbound application and write-back."""
        self._set_pinned(True)

    def unpin(self) -> None:
        """Resume automatic synchronization on the next cycle."""
        self._set_pinned(False)

    def _set_pinned(self, pinned: bool) -> None:
        with transaction.atomic():
            locked = self._lock_sync_state()
            locked.pinned = pinned
            locked.save(update_fields=["pinned", "updated_at"])
            self.pinned = pinned

    @property
    def write_holds(self) -> tuple[SyncFailure, ...]:
        """Conflicts hold indefinitely; an outbox refusal holds until due."""
        now = timezone.now()
        return tuple(
            row
            for row in self.failures(open_only=True)
            if row.kind == SyncFailureKind.CONFLICT
            or (row.slot is None and row.retry_at is not None and row.retry_at > now)
        )

    @property
    def sync_precondition(self) -> dict[str, Any]:
        """The retained state fenced across an external conditional write."""
        return {
            "applied_base": self.applied_base_id,
            "source_hash": self.source_hash,
            "remote_version": self.remote_version,
        }

    def resolve_conflict(self, slot: int | None, *, keep: ConflictKeep) -> SyncFailure:
        """Fresh-read the chosen side, retaining transport's version fence."""
        if keep not in ConflictKeep.values:
            raise ValidationError("Choose remote or local changes.")
        if connection.in_atomic_block:
            raise RuntimeError("Conflict resolution must run outside a database transaction.")
        with system_context(reason="integrate.record.resolve_conflict"):
            link = type(self).objects.with_sync_evidence().get(pk=self.pk)
            row = next(row for row in link.failures() if row.slot == slot)
            if row.kind != SyncFailureKind.CONFLICT:
                raise ValidationError("Conflict resolution requires a linked conflict.")
            if not row.is_open:
                return row
            stream = link.stream
            if stream.state == StreamState.CLOSED:
                raise ValidationError("Resync the closed stream before resolving its conflict.")
            owner = link.parent if link.parent is not None else link
            adapter = stream.integration.concrete_capability().backend
            keys = frozenset((owner.external_key,))
            resolved = False
            try:
                if not adapter.supports_identity_reads:
                    raise ValidationError("Conflict resolution requires an adapter with identity reads.")
                if stream.resync_required:
                    raise ValidationError("Complete the requested stream baseline before resolving its conflict.")
                if keep == ConflictKeep.LOCAL and (not stream.writes_back or not stream.has_completed_baseline()):
                    raise ValidationError("Keeping local changes requires a completed writable baseline.")
                base = owner.sync_precondition
                remote = read_stream_keys(adapter, stream, tuple(keys))[0] if keep == ConflictKeep.LOCAL else None
                with transaction.atomic():
                    type(stream).objects.lock_current(stream)
                    locked = type(owner).objects.lock_if_supported().get(pk=owner.pk)
                    current_link = type(link).objects.lock_if_supported().get(pk=link.pk)
                    current = next(row for row in current_link.failures() if row.slot == slot)
                    if not current.is_open:
                        return current
                    if locked.sync_precondition != base:
                        raise RuntimeError("Record bases changed during conflict resolution; retry the choice.")
                    if remote is not None:
                        type(locked).objects.observe(
                            stream,
                            locked.external_key,
                            source_payload=remote.source_payload,
                            source_hash=remote.source_hash,
                            remote_version=remote.remote_version,
                        )
                    current_link.resolve_failure(slot, conflict=True)
                resolved = True
                if keep == ConflictKeep.REMOTE:
                    prepared = begin_stream_cycle(stream, adapter, force_apply=keys)
                    if prepared.resync_required:
                        raise ValidationError("Complete the requested stream baseline before resolving its conflict.")
                elif not push_stream(stream, adapter, external_keys=keys).count:
                    raise ValidationError("The selected local record could not be written; its failure remains open.")
                if type(link).objects.filter(pk__in=(link.pk, owner.pk)).open_failures():
                    raise ValidationError("The record could not be synchronized; its failure remains open.")
                fresh = type(link).objects.get(pk=link.pk)
                return next(row for row in fresh.failures() if row.slot == slot)
            except Exception:
                if resolved:
                    fresh = type(link).objects.get(pk=link.pk)
                    if not type(link).objects.filter(pk__in=(link.pk, owner.pk)).open_failures():
                        fresh.record_failure(
                            kind=row.kind,
                            code=row.code,
                            source_hash=row.source_hash,
                            mapping_version=row.mapping_version,
                            details=row.details,
                            outbox=slot is None,
                        )
                raise
            finally:
                adapter.close()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Retain the integration's native SubjectRef when the identity is saved."""

        if not self.asserted_by:
            self.asserted_by = str(self.asserting_source())
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "asserted_by"}
        super().save(*args, **kwargs)

    class Meta:
        abstract = True
        base_manager_name = "unscoped_objects"
        rebac_resource_type = "integrate/record_link"
        rebac_id_attr = "pk"
        constraints = (models.UniqueConstraint(fields=("stream", "external_key"), name="uniq_stream_record_key"),)
        indexes = (models.Index(fields=("target_content_type", "target_object_id")),)
