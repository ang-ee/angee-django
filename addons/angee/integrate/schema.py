"""GraphQL schema contributions for Angee integrations.

Owns the admin console surface for the third-party ``Vendor`` catalogue (moved
here from iam) and the first-class ``Integration`` an integration runs over. The
console is platform-admin gated, so ``Integration``'s REBAC-guarded relations
(credential/account from iam) are safe to expose — the const-admin reaches every
related row.
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from rebac import PermissionDenied, system_context
from strawberry import auto
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.graphql.actions import ActionResult, action_target, resolve_action_target
from angee.graphql.data import (
    AngeeHasuraWriteBackend,
    declared_hasura_resource_fields,
    hasura_model_resource,
    public_pk_decoder,
)
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata, delete_by_public_id
from angee.graphql.ids import PublicID
from angee.graphql.impl import ImplChoice
from angee.graphql.impl import impl_choices as resolve_impl_choices
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.identity import user_from_public_id as _user_from_public_id
from angee.iam.identity import user_principal as _user_principal
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.permissions import request_from_info as _request
from angee.iam.permissions import session_user as _session_user
from angee.iam.schema import UserType
from angee.integrate import connect as _connect
from angee.integrate.credentials import handler_for
from angee.integrate.impl import IntegrationImpl
from angee.integrate.models import Bridge, IntegrationStatus
from angee.integrate.oauth import flow, state
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.errors import CLIENT_NOT_CONFIGURED, INVALID_STATE, OAuthFlowError
from angee.integrate.registry import bridge_models

Vendor = apps.get_model("integrate", "Vendor")
Integration = apps.get_model("integrate", "Integration")
OAuthClient = apps.get_model("integrate", "OAuthClient")
ExternalAccount = apps.get_model("integrate", "ExternalAccount")
Credential = apps.get_model("integrate", "Credential")
WebhookSubscription = apps.get_model("integrate", "WebhookSubscription")
VcsBridge = apps.get_model("integrate", "VcsBridge")
Repository = apps.get_model("integrate", "Repository")
Source = apps.get_model("integrate", "Source")
Template = apps.get_model("integrate", "Template")
User = get_user_model()


@strawberry.type
class ConsoleImplChoicesQuery:
    """Admin-gated impl-choice metadata for console forms."""

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def impl_choices(self, model: str, field: str) -> list[ImplChoice]:
        """Return registry choices for an ``ImplClassField``."""

        return resolve_impl_choices(model, field)


# --- Connection substrate: OAuth/OIDC clients, external accounts, credentials ----


@strawberry.type
class CredentialOAuthClientType:
    """Public-safe OAuth client projection for credential health rows."""

    @strawberry.field
    def display_name(self) -> str:
        """Return the configured OAuth client display name."""

        return str(cast(Any, self).display_name)


@strawberry_django.type(ExternalAccount)
class ExternalAccountType(AngeeNode):
    """GraphQL projection of a linked external identity."""

    external_id: auto
    email: auto
    display_name: auto
    avatar_url: auto
    status: auto
    last_used_at: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["credential__status"])
    def credential_status(self) -> str:
        """Return this account credential's current status when it is loaded."""

        return str(cast(Any, self).credential_status)

    @strawberry_django.field(only=["oauth_client__slug"])
    def provider_slug(self) -> str:
        """Return the originating OAuth client's slug."""

        return str(cast(Any, self).provider_slug)

    @strawberry_django.field(only=["oauth_client__environment"])
    def provider_environment(self) -> str:
        """Return the originating OAuth client's environment."""

        return str(cast(Any, self).provider_environment)

    @strawberry_django.field(only=["oauth_client__display_name"])
    def provider_label(self) -> str:
        """Return the originating OAuth client's display label."""

        return str(cast(Any, self).provider_label)

    @strawberry_django.field(only=["oauth_client__icon"])
    def provider_icon(self) -> str:
        """Return the originating OAuth client's branding icon."""

        return str(cast(Any, self).provider_icon)


@strawberry_django.type(Credential)
class CredentialType(AngeeNode):
    """GraphQL projection of credential health without secret values."""

    kind: auto
    name: auto
    status: auto
    expires_at: auto
    last_refresh_at: auto
    last_refresh_status: auto
    external_account: ExternalAccountType | None
    display_name: str
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["oauth_client"])
    def oauth_client(self) -> CredentialOAuthClientType | None:
        """Return a public-safe projection of the OAuth client (``None`` for local kinds)."""

        return cast("CredentialOAuthClientType | None", cast(Any, self).oauth_client)


@strawberry_django.type(ExternalAccount)
class ConnectedExternalAccountType(AngeeNode):
    """Public projection of the current user's connected external account."""

    external_id: auto
    email: auto
    display_name: auto
    avatar_url: auto
    status: auto
    last_used_at: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["credential__status"])
    def credential_status(self) -> str:
        """Return this account credential's current status when it is loaded."""

        return str(cast(Any, self).credential_status)


@strawberry_django.type(Credential)
class ConnectedCredentialType(AngeeNode):
    """Public projection of one current-user connected credential."""

    kind: auto
    name: auto
    status: auto
    expires_at: auto
    last_refresh_at: auto
    last_refresh_status: auto
    external_account: ConnectedExternalAccountType | None
    created_at: auto
    updated_at: auto

    @strawberry_django.field(
        only=[
            "name",
            "external_account__email",
            "external_account__display_name",
            "external_account__external_id",
        ]
    )
    def display_name(self) -> str:
        """Return the public-safe connected credential label."""

        return str(cast(Any, self).connected_display_name)


@strawberry_django.type(OAuthClient)
class OAuthClientType(AngeeNode):
    """Admin GraphQL projection of an OAuth client registration."""

    display_name: auto
    slug: auto
    provider_type: auto
    icon: auto
    environment: auto
    client_id: auto
    discovery_url: auto
    authorize_endpoint: auto
    token_endpoint: auto
    revoke_endpoint: auto
    userinfo_endpoint: auto
    manual_redirect_uri: auto
    loopback_redirect_path: auto
    token_request_format: auto
    is_enabled: auto
    supports_refresh: auto
    refresh_rotates: auto
    supports_pkce: auto
    max_refresh_age_seconds: auto
    external_id_claim: auto
    email_claim: auto
    display_name_claim: auto
    avatar_url_claim: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["default_scopes"])
    def default_scopes(self) -> list[str]:
        """Return the configured default OAuth scopes."""

        return cast(list[str], cast(Any, self).default_scope_values)

    @strawberry_django.field(only=["scopes_catalogue"])
    def scopes_catalogue(self) -> list[str]:
        """Return the advertised OAuth scopes."""

        return cast(list[str], cast(Any, self).scopes_catalogue_values)

    @strawberry_django.field(only=["authorize_params"])
    def authorize_params(self) -> JSON:
        """Return provider-specific OAuth authorize parameters."""

        return cast(JSON, cast(Any, self).authorize_params)

    @strawberry_django.field(only=["token_params"])
    def token_params(self) -> JSON:
        """Return provider-specific OAuth token parameters."""

        return cast(JSON, cast(Any, self).token_params)

    @strawberry_django.field
    def configuration_state(self) -> str:
        """Return this OAuth client's operator-facing configuration readiness."""

        return str(cast(Any, self).configuration_state)


@strawberry.input
class ExternalAccountInput:
    """Fields accepted when manually linking an external account."""

    oauth_client: PublicID
    external_id: str
    owner: str | None = None
    email: str = ""
    display_name: str = ""
    avatar_url: str = ""
    status: str = "active"


@strawberry.input
class CredentialInput:
    """Admin-write fields for a provider-less credential (OAuth ones arrive via connect).

    ``kind`` discriminates the material: ``static_token`` reads ``api_key``,
    ``ssh_key`` reads ``private_key``, ``basic_auth`` reads ``username`` +
    ``password``. ``user`` defaults to the calling admin.
    """

    name: str
    kind: str
    user: PublicID | None = None
    api_key: str = ""
    private_key: str = ""
    username: str = ""
    password: str = ""


@strawberry.type
class ConnectableAccount:
    """Picker-safe OAuth client fields for the public account-connect picker.

    The OAuth client is self-describing (``slug``/``display_name``/``icon`` are
    its own columns), so the picker reads them straight off each row — one query
    for the whole page, no per-row fetch and no catalogue join.
    """

    @strawberry.field
    def oauth_client_sqid(self) -> strawberry.ID:
        """Return the OAuth client sqid accepted by connect mutations."""

        return strawberry.ID(str(cast(Any, self).sqid))

    @strawberry.field
    def oauth_client_display_name(self) -> str:
        """Return the OAuth client display label."""

        return str(cast(Any, self).display_name)

    @strawberry.field
    def oauth_client_slug(self) -> str:
        """Return the OAuth client slug (the provider key)."""

        return str(cast(Any, self).slug)

    @strawberry.field
    def oauth_client_icon(self) -> str:
        """Return the OAuth client branding icon."""

        return str(cast(Any, self).icon)


@strawberry.type
class OAuthStartPayload:
    """Result returned by OAuth/OIDC redirect-start mutations (connect, login, link)."""

    authorize_url: str = ""
    state: str = ""
    error: str | None = None
    error_code: str | None = None
    mode: str = "auto"
    """``"auto"`` to redirect the browser back, or ``"manual"`` to paste the code."""
    redirect_uri: str = ""
    """The effective redirect URI the flow used (resent verbatim at completion)."""


def _oauth_start_payload(started: flow.OAuthStart) -> OAuthStartPayload:
    """Project the flow-owned start facts onto the GraphQL payload."""

    return OAuthStartPayload(
        authorize_url=started.authorize_url,
        state=started.state,
        mode=started.mode,
        redirect_uri=started.redirect_uri,
    )


@strawberry.type
class ConnectAccountResult:
    """Result returned by OAuth account-connect completion."""

    account: ConnectedExternalAccountType | None = None
    credential: ConnectedCredentialType | None = None
    user: UserType | None = None
    intent: str = "connect"
    next: str = "/"
    claims: JSON | None = None
    error: str | None = None
    error_code: str | None = None


@strawberry.type
class ConnectIntegrationResult:
    """Result returned by one-click integration connect/attach."""

    integration: "ConnectedIntegrationType | None" = None
    authorize_url: str = ""
    state: str = ""
    error: str | None = None
    error_code: str | None = None
    mode: str = "auto"
    redirect_uri: str = ""
    attached: bool = False


@strawberry.type
class UnlinkAccountResult:
    """Result returned by the account-disconnect mutation."""

    ok: bool
    error: str | None = None
    error_code: str | None = None


@strawberry.type
class RevealedCredentialSecret:
    """One credential's decrypted secret, returned only on explicit admin request.

    The secret is never part of :class:`CredentialType` (the normal read projection);
    it is disclosed solely by the audited ``reveal_credential`` mutation.
    """

    secret: str = ""


def _connectable_accounts(info: strawberry.Info) -> Any:
    """Return enabled OAuth clients for the public account-connect picker."""

    del info
    return cast(Any, OAuthClient.objects).connectable()


def _my_connected_accounts(info: strawberry.Info) -> Any:
    """Return this session user's credential-backed connected accounts."""

    return cast(Any, Credential.objects).connected_for(_session_user(info))


def _console_external_accounts(info: strawberry.Info) -> Any:
    """Return admin-visible external accounts with guarded FK joins."""

    del info
    return cast(Any, ExternalAccount.objects).console_external_accounts()


def _console_credentials(info: strawberry.Info) -> Any:
    """Return admin-visible credential health with guarded FK joins."""

    del info
    return cast(Any, Credential.objects).console_credentials()


def _console_integrations(info: strawberry.Info) -> Any:
    """Return admin-visible integrations with bridge children prefetched."""

    del info
    return Integration.objects.all().prefetch_related(
        Prefetch(
            "vcsbridge",
            queryset=VcsBridge._base_manager.all(),
            to_attr="_angee_prefetched_bridge",
        )
    )


_OAUTH_CLIENT_EXTENSION_INSERT_FIELDS = declared_hasura_resource_fields(
    OAuthClient,
    "hasura_insertable_fields",
)
_OAUTH_CLIENT_EXTENSION_READ_FIELDS = declared_hasura_resource_fields(
    OAuthClient,
    "hasura_readable_fields",
)
_OAUTH_CLIENT_EXTENSION_UPDATE_FIELDS = declared_hasura_resource_fields(
    OAuthClient,
    "hasura_updatable_fields",
)


_OAUTH_CLIENT_RESOURCE = hasura_model_resource(
    OAuthClientType,
    model=OAuthClient,
    name="oauth_clients",
    filterable=["id", "slug", "provider_type", "environment", "display_name", "is_enabled", "updated_at"],
    sortable=["slug", "environment", "display_name", "is_enabled", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["provider_type", "environment", "is_enabled"],
    declared_fields=_OAUTH_CLIENT_EXTENSION_READ_FIELDS,
    insertable=[
        "display_name",
        "client_id",
        "slug",
        "provider_type",
        "icon",
        "client_secret",
        "environment",
        "discovery_url",
        "authorize_endpoint",
        "token_endpoint",
        "revoke_endpoint",
        "userinfo_endpoint",
        "manual_redirect_uri",
        "loopback_redirect_path",
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
        "external_id_claim",
        "email_claim",
        "display_name_claim",
        "avatar_url_claim",
        *_OAUTH_CLIENT_EXTENSION_INSERT_FIELDS,
    ],
    updatable=[
        "slug",
        "provider_type",
        "icon",
        "display_name",
        "client_id",
        "client_secret",
        "environment",
        "discovery_url",
        "authorize_endpoint",
        "token_endpoint",
        "revoke_endpoint",
        "userinfo_endpoint",
        "manual_redirect_uri",
        "loopback_redirect_path",
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
        "external_id_claim",
        "email_claim",
        "display_name_claim",
        "avatar_url_claim",
        *_OAUTH_CLIENT_EXTENSION_UPDATE_FIELDS,
    ],
)
_EXTERNAL_ACCOUNT_RESOURCE = hasura_model_resource(
    ExternalAccountType,
    model=ExternalAccount,
    name="external_accounts",
    filterable=["id", "oauth_client", "external_id", "email", "display_name", "status", "updated_at"],
    sortable=["oauth_client", "external_id", "email", "display_name", "status", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["oauth_client", "oauth_client__display_name", "status"],
    insert=False,
    delete=False,
    updatable=["email", "display_name", "avatar_url", "status"],
    field_id_decode={"oauth_client": public_pk_decoder(OAuthClient)},
    get_queryset=_console_external_accounts,
)
_CREDENTIAL_RESOURCE = hasura_model_resource(
    CredentialType,
    model=Credential,
    name="credentials",
    filterable=[
        "id",
        "oauth_client",
        "external_account",
        "kind",
        "name",
        "status",
        "last_refresh_status",
        "updated_at",
    ],
    sortable=["oauth_client", "kind", "name", "status", "expires_at", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["kind", "status", "last_refresh_status"],
    insert=False,
    delete=False,
    updatable=["status"],
    field_id_decode={
        "oauth_client": public_pk_decoder(OAuthClient),
        "external_account": public_pk_decoder(ExternalAccount),
    },
    get_queryset=_console_credentials,
)


def _oauth_client_from_id(oauth_client_id: PublicID) -> Any:
    """Return the OAuth client addressed by one public GraphQL id."""

    return resolve_action_target(
        OAuthClient,
        oauth_client_id,
        reason="integrate.graphql.oauth_client.lookup",
    )


def _enabled_oauth_client_from_id(oauth_client_id: PublicID) -> Any:
    """Return the enabled OAuth client addressed by one public GraphQL id."""

    oauth_client = _oauth_client_from_id(oauth_client_id)
    if not oauth_client.is_enabled:
        raise ValueError("OAuth client is not enabled.")
    return oauth_client


def _credential_material(data: CredentialInput) -> dict[str, str]:
    """Read the secret(s) the kind's handler names out of the discriminated input."""

    material: dict[str, str] = {}
    for field in handler_for(data.kind).input_material_fields():
        if not hasattr(data, field):
            raise ValueError(f"Cannot create a credential of kind {data.kind!r}.")
        material[field] = getattr(data, field)
    return material


def _revoke_remote_oauth_token(credential: Any) -> None:
    """Best-effort remote revocation before removing a local OAuth credential."""

    try:
        oauth_client = credential.oauth_client
        # Provider-less (static/ssh) credentials have nothing to revoke remotely.
        if oauth_client is None or not getattr(oauth_client, "revoke_endpoint", ""):
            return
        token = str(credential.reveal().get("access_token") or "")
        if token:
            OAuthClientProtocol(oauth_client).revoke_token(token)
    except Exception:
        return


def _integration_impl_class(impl_class: str) -> type[IntegrationImpl]:
    """Return the configured implementation class for one integration key."""

    return cast(type[IntegrationImpl], Integration.objects.impl_class_for_key(impl_class))


def integration_create_attrs(
    data: Any,
    *,
    reason: str,
) -> dict[str, Any]:
    """Resolve inherited ``Integration`` create fields from GraphQL public ids."""

    credential = (
        None
        if data.credential is None
        else resolve_action_target(
            Credential,
            data.credential,
            reason=f"{reason}.credential",
        )
    )
    account = (
        strawberry.UNSET
        if data.account is strawberry.UNSET
        else (
            None
            if data.account is None
            else resolve_action_target(
                ExternalAccount,
                data.account,
                reason=f"{reason}.account",
            )
        )
    )
    attrs: dict[str, Any] = {
        "vendor": resolve_action_target(Vendor, data.vendor, reason=f"{reason}.vendor"),
        "owner": _user_from_public_id(data.owner),
    }
    if credential is not None:
        attrs["credential"] = credential
    if account is not strawberry.UNSET:
        attrs["account"] = account
    if data.status not in (strawberry.UNSET, None):
        attrs["status"] = IntegrationStatus.from_value(data.status)
    return attrs


def apply_integration_patch_fields(
    target: Any,
    data: Any,
    *,
    reason: str,
    ignore_null_status: bool = False,
) -> set[str]:
    """Apply inherited ``Integration`` patch fields and return provided names."""

    provided: set[str] = set()
    if data.vendor is not strawberry.UNSET:
        target.vendor = resolve_action_target(Vendor, data.vendor, reason=f"{reason}.vendor")
        provided.add("vendor")
    if data.owner is not strawberry.UNSET:
        target.owner = _user_from_public_id(data.owner)
        provided.add("owner")
    if data.credential is not strawberry.UNSET:
        target.credential = (
            None
            if data.credential is None
            else resolve_action_target(Credential, data.credential, reason=f"{reason}.credential")
        )
        provided.add("credential")
    if data.account is not strawberry.UNSET:
        target.account = (
            None
            if data.account is None
            else resolve_action_target(ExternalAccount, data.account, reason=f"{reason}.account")
        )
        provided.add("account")
    if data.status is not strawberry.UNSET and (data.status is not None or not ignore_null_status):
        target.status = IntegrationStatus.from_value(data.status)
        provided.add("status")
    return provided


def _oauth_client_for_integration(integration: Any) -> Any:
    """Return the OAuth client this integration implementation connects through."""

    return integration.impl.connect_oauth_client("Integration")


def _current_user_integration(
    user: Any,
    *,
    integration_id: PublicID | None,
    vendor_slug: str,
    impl_class: str,
) -> Any:
    """Return the current user's target integration, creating the draft selector row when needed."""

    if integration_id is not None:
        integration = resolve_action_target(
            Integration,
            integration_id,
            reason="integrate.graphql.connect_integration",
        )
        if integration.owner_id != user.pk:
            raise PermissionDenied("Integration does not belong to the current user.")
        return integration

    vendor_key = vendor_slug.strip()
    if not (vendor_key and impl_class.strip()):
        raise ValueError("connectIntegration requires integrationId or vendorSlug and implClass.")
    impl_key = Integration.impl_key_for("impl_class", impl_class)
    vendor = _vendor_by_slug(vendor_key)
    return Integration.objects.draft_for(user, vendor=vendor, impl_class=impl_key)


def _attach_completed_integration(integration_sqid: str, user: Any, credential: Any) -> None:
    """Attach a freshly connected credential to the integration named in OAuth state."""

    if not integration_sqid:
        return
    with system_context(reason="integrate.graphql.connect_integration.complete"):
        integration = Integration.objects.filter(sqid=integration_sqid).first()
    if integration is None or integration.owner_id != user.pk:
        raise OAuthFlowError(INVALID_STATE, 400)
    if credential.user_id != user.pk:
        raise PermissionDenied("Credential does not belong to the current user.")
    integration.attach_credential(credential)


def connect_integration_target(
    info: strawberry.Info,
    integration: Any,
    oauth_client: Any,
    *,
    redirect_uri: str,
    next_path: str,
) -> ConnectIntegrationResult:
    """Attach the user's live credential to an integration-like MTI row or start OAuth."""

    user = _session_user(info)
    if integration.owner_id != user.pk:
        raise PermissionDenied("Integration does not belong to the current user.")
    credential = Credential.objects.live_oauth_for_user(user, oauth_client)
    if credential is not None:
        if credential.user_id != user.pk:
            raise PermissionDenied("Credential does not belong to the current user.")
        integration.attach_credential(credential)
        return ConnectIntegrationResult(integration=cast("ConnectedIntegrationType", integration), attached=True)
    if not redirect_uri:
        raise OAuthFlowError("redirect_uri_required", 400, "OAuth redirect URI is required.")
    if oauth_client.configuration_state != "ready":
        raise OAuthFlowError(
            CLIENT_NOT_CONFIGURED,
            400,
            f"OAuth client is not fully configured ({oauth_client.configuration_state}).",
        )
    request = _request(info)
    started = flow.start(
        request,
        oauth_client,
        redirect_uri,
        user_id=str(user.pk),
        next_path=flow.coerce_next_path(next_path, request),
        flow=state.StateFlow.CONNECT,
        integration_id=str(integration.sqid),
    )
    return ConnectIntegrationResult(
        integration=cast("ConnectedIntegrationType", integration),
        authorize_url=started.authorize_url,
        state=started.state,
        mode=started.mode,
        redirect_uri=started.redirect_uri,
    )


@strawberry.type
class IntegrateConnectionsQuery:
    """Public account-connect picker and self-service connected-account queries."""

    connectable_accounts: OffsetPaginated[ConnectableAccount] = strawberry_django.offset_paginated(
        resolver=_connectable_accounts,
    )
    my_connected_accounts: OffsetPaginated[ConnectedCredentialType] = strawberry_django.offset_paginated(
        resolver=_my_connected_accounts,
    )


@strawberry.type
class ConnectionMutation:
    """Authenticated OAuth account-connect / disconnect mutations."""

    @strawberry.mutation
    def connect_account_start(
        self,
        info: strawberry.Info,
        id: PublicID,
        redirect_uri: str,
        next: str = "/",
    ) -> OAuthStartPayload:
        """Start an authenticated OAuth account-connect flow."""

        user = _session_user(info)
        request = _request(info)
        try:
            oauth_client = _enabled_oauth_client_from_id(id)
            if oauth_client.configuration_state != "ready":
                # Enabled but missing a client_id/endpoints would otherwise build an
                # authorize URL the provider rejects opaquely; surface it as a typed
                # start-flow error instead.
                raise OAuthFlowError(
                    CLIENT_NOT_CONFIGURED,
                    400,
                    f"OAuth client is not fully configured ({oauth_client.configuration_state}).",
                )
            started = flow.start(
                request,
                oauth_client,
                redirect_uri,
                user_id=str(user.pk),
                next_path=flow.coerce_next_path(next, request),
                flow=state.StateFlow.CONNECT,
            )
        except OAuthFlowError as error:
            return OAuthStartPayload(error=error.public_message, error_code=error.code)
        return _oauth_start_payload(started)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def discover_oauth_endpoints(self, id: PublicID) -> ActionResult:
        """Fetch the provider's discovery document and fill this client's blank endpoints.

        The resolved authorize/token/userinfo endpoints (and any composed extension
        endpoints, e.g. OIDC issuer/JWKS) are persisted on the OAuth client row, so
        the operator never types them by hand. Requires a discovery URL on the row.
        """

        with action_target(
            OAuthClient,
            id,
            reason="integrate.graphql.discover_oauth_endpoints",
        ) as oauth_client:
            if not str(getattr(oauth_client, "discovery_url", "") or ""):
                return ActionResult(ok=False, message="Set a discovery URL first.")
            try:
                discovery = oauth_client.discover_endpoints()
            except Exception as error:  # noqa: BLE001 — surface discovery failure to the operator
                return ActionResult(ok=False, message=f"Discovery failed: {error}")
            oauth_client.save()
        issuer = discovery.get("issuer") if isinstance(discovery, dict) else None
        return ActionResult(ok=True, message=f"Discovered endpoints for {issuer or 'provider'}.")

    @strawberry.mutation
    def connect_integration(
        self,
        info: strawberry.Info,
        integration_id: PublicID | None = None,
        vendor_slug: str = "",
        impl_class: str = "",
        redirect_uri: str = "",
        next: str = "/",
    ) -> ConnectIntegrationResult:
        """Attach this user's live credential to an integration, or start OAuth."""

        user = _session_user(info)
        try:
            integration = _current_user_integration(
                user,
                integration_id=integration_id,
                vendor_slug=vendor_slug,
                impl_class=impl_class,
            )
            oauth_client = _oauth_client_for_integration(integration)
            return connect_integration_target(
                info,
                integration,
                oauth_client,
                redirect_uri=redirect_uri,
                next_path=next,
            )
        except OAuthFlowError as error:
            return ConnectIntegrationResult(error=error.public_message, error_code=error.code)

    @strawberry.mutation
    def connect_account_complete(
        self,
        info: strawberry.Info,
        code: str,
        state: str,
        redirect_uri: str,
    ) -> ConnectAccountResult:
        """Complete an authenticated OAuth account-connect flow."""

        request = _request(info)
        _session_user(info)
        try:
            oauth_client = flow.remembered_oauth_client(request, state)
            result = _connect.complete_account_connect(
                oauth_client,
                code=code,
                state_token=state,
                redirect_uri=redirect_uri,
            )
            _attach_completed_integration(result.integration_id, result.user, result.credential)
        except OAuthFlowError as error:
            return ConnectAccountResult(error=error.public_message, error_code=error.code)
        return ConnectAccountResult(
            account=cast(ConnectedExternalAccountType, result.account),
            credential=cast(ConnectedCredentialType, result.credential),
            user=cast(UserType, result.user),
            next=result.next_path,
            claims=cast(JSON, result.claims),
        )

    @strawberry.mutation
    def disconnect_account(
        self,
        info: strawberry.Info,
        external_account_sqid: str,
    ) -> UnlinkAccountResult:
        """Remove this session user's credential link to an external account.

        The credential delete fires ``pre_delete`` — the login addon, when
        installed, vetoes removing a user's last sign-in account by raising an
        :class:`OAuthFlowError`, surfaced here as a typed error rather than a 500.
        """

        user = _session_user(info)
        try:
            with system_context(reason="integrate.graphql.disconnect_account.lookup"):
                credential = (
                    Credential.objects.select_related("oauth_client", "external_account")
                    .filter(user=user, external_account__sqid=external_account_sqid)
                    .first()
                )
            if credential is None:
                return UnlinkAccountResult(ok=False)
            external_account = credential.external_account
            with system_context(reason="integrate.graphql.disconnect_account"), transaction.atomic():
                Credential.objects.check_disconnect(credential)
                ExternalAccount.objects.revoke_owner(external_account, user)
                # Revoke at the provider only if the delete commits; local guards run
                # before scheduling the remote side effect.
                transaction.on_commit(lambda: _revoke_remote_oauth_token(credential))
                deleted, _details = Credential.objects.filter(pk=credential.pk).with_action("delete").delete()
            return UnlinkAccountResult(ok=deleted > 0)
        except OAuthFlowError as error:
            return UnlinkAccountResult(ok=False, error=error.public_message, error_code=error.code)


@strawberry.type
class IntegrateExternalAccountMutation:
    """Admin mutations for manually linked external identities."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_external_account(self, data: ExternalAccountInput) -> ExternalAccountType:
        """Create or update one external account via the account manager owner."""

        oauth_client = _oauth_client_from_id(data.oauth_client)
        owner = _user_principal(data.owner) if data.owner is not None else None
        account = ExternalAccount.objects.link(
            oauth_client,
            data.external_id,
            owner=owner,
            email=data.email,
            display_name=data.display_name,
            avatar_url=data.avatar_url,
            status=data.status,
        )
        return cast(ExternalAccountType, account)

    @strawberry.mutation(name="delete_external_account", permission_classes=_ADMIN_PERMISSION_CLASSES)
    def delete_external_account(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Revoke the owner grant, then delete the account (owner is a REBAC tuple)."""

        def revoke(account: Any) -> None:
            owner = ExternalAccount.objects.owner_for(account)
            if owner is not None:
                ExternalAccount.objects.revoke_owner(account, owner)

        return delete_by_public_id(
            ExternalAccount,
            str(id),
            reason="integrate.graphql.external_account.delete",
            confirm=confirm,
            before_delete=revoke,
        )


@strawberry.type
class IntegrateCredentialMutation:
    """Admin CRUD for credentials; create mints provider-less kinds (OAuth arrives via connect)."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def reveal_oauth_client_secret(self, id: PublicID) -> RevealedCredentialSecret:
        """Return one OAuth client's decrypted client secret for an admin."""

        with action_target(
            OAuthClient,
            id,
            reason=f"integrate.graphql.oauth_client.reveal:{str(id)}",
        ) as oauth_client:
            return RevealedCredentialSecret(secret=str(oauth_client.client_secret or ""))

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def reveal_credential(self, id: PublicID) -> RevealedCredentialSecret:
        """Return one credential's decrypted secret for an admin to copy."""

        with action_target(
            Credential,
            id,
            reason=f"integrate.graphql.credential.reveal:{str(id)}",
        ) as credential:
            return RevealedCredentialSecret(secret=str(credential.secret_value() or ""))

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def refresh_credential(self, id: PublicID) -> ActionResult:
        """Force an OAuth credential to renew its token now and report the outcome.

        The interactive counterpart to the lazy on-use refresh: it renews regardless of
        remaining lifetime (a still-valid token is rotated) and surfaces success or the
        reason to reconnect, rather than silently swallowing a dead refresh token.
        """

        with action_target(
            Credential,
            id,
            reason=f"integrate.graphql.credential.refresh:{str(id)}",
        ) as credential:
            try:
                credential.refresh_now()
            except (OAuthFlowError, ValueError) as error:
                return ActionResult(ok=False, message=f"Refresh failed: {error}")
        return ActionResult(ok=True, message="Token refreshed.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_credential(self, info: strawberry.Info, data: CredentialInput) -> CredentialType:
        """Create one provider-less credential, dispatching material by ``kind``."""

        user = _session_user(info) if data.user is None else _user_from_public_id(data.user)
        credential = Credential.objects.create_local_credential(
            user,
            kind=data.kind,
            name=data.name,
            material=_credential_material(data),
        )
        return cast(CredentialType, credential)

    @strawberry.mutation(name="delete_credential", permission_classes=_ADMIN_PERMISSION_CLASSES)
    def delete_credential(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Delete the credential, then best-effort revoke remotely after commit."""

        def prepare_delete(credential: Any) -> None:
            Credential.objects.check_disconnect(credential)
            transaction.on_commit(lambda: _revoke_remote_oauth_token(credential))

        return delete_by_public_id(
            Credential,
            str(id),
            reason="integrate.graphql.credential.delete",
            confirm=confirm,
            before_delete=prepare_delete,
        )


attach_delete_preview_metadata(
    IntegrateExternalAccountMutation,
    model=ExternalAccount,
    node=ExternalAccountType,
    field="delete_external_account",
)
attach_delete_preview_metadata(
    IntegrateCredentialMutation,
    model=Credential,
    node=CredentialType,
    field="delete_credential",
)


@strawberry_django.type(Vendor)
class VendorType(AngeeNode):
    """GraphQL projection of an integration vendor catalogue row."""

    slug: auto
    display_name: auto
    website_url: auto
    icon: auto
    description: auto
    created_at: auto
    updated_at: auto


@strawberry.type
class IntegrationLabelMixin:
    """Project ``Integration.display_label`` as the ``display_name`` field for a type.

    Compose alongside the node base, e.g. ``class ChannelType(IntegrationLabelMixin,
    AngeeNode)``, to surface the operator label (falling back to ``Vendor
    (status)``) on every ``Integration`` child type without re-declaring the
    resolver. A ``@strawberry.type`` (not an interface): merges the field into the
    concrete type without adding a GraphQL interface to the SDL.
    """

    @strawberry_django.field(only=["display_name", "vendor", "status"])
    def display_name(self) -> str:
        """Return the operator label, falling back to the vendor-derived one."""

        return cast(Any, self).display_label


@strawberry_django.type(Integration)
class IntegrationType(IntegrationLabelMixin, AngeeNode):
    """Admin projection of an integration.

    Exposes the catalogue/identity associations as nested relations so the
    console form's ``many2one`` pickers auto-wire (mirrors iam's
    ``CredentialType.external_account``); safe because the surface is admin-gated.
    """

    vendor: VendorType
    credential: CredentialType | None
    account: ExternalAccountType | None
    owner: UserType
    kind: auto
    impl_class: auto
    status: auto
    last_used_at: auto
    last_used_status: auto
    use_count_24h: auto
    error_count_24h: auto
    last_error: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["id"])
    def bridge(self) -> VcsBridgeType | None:
        """Return this integration's VCS child row when present."""

        prefetched = getattr(self, "_angee_prefetched_bridge", None)
        if prefetched is not None:
            if isinstance(prefetched, (list, tuple)):
                return cast("VcsBridgeType | None", prefetched[0] if prefetched else None)
            return cast("VcsBridgeType | None", prefetched)
        with system_context(reason="integrate.integration.bridge"):
            bridge = VcsBridge._base_manager.filter(pk=cast(Any, self).pk).first()
        return cast("VcsBridgeType | None", bridge)

    @strawberry_django.field(only=["impl_class"], description="Implementation")
    def impl_category(self) -> str:
        """Return this integration implementation's board grouping category.

        Reads the class-level metadata off the resolved impl class — no instance,
        no child model fetch — so a board/list render does not N+1 over child models.
        """

        impl_class = _integration_impl_class(cast(Any, self).impl_class)
        return str(getattr(impl_class, "category", "") or "none")

    @strawberry_django.field(only=["impl_class"])
    def impl_label(self) -> str:
        """Return this integration implementation's human label."""

        impl_class = _integration_impl_class(cast(Any, self).impl_class)
        display_label = getattr(impl_class, "display_label", None)
        if callable(display_label):
            return str(display_label())
        return str(getattr(impl_class, "label", "") or cast(Any, self).impl_class)


@strawberry_django.type(Integration)
class ConnectedIntegrationType(IntegrationLabelMixin, AngeeNode):
    """Public projection of a current-user integration connection."""

    vendor: VendorType
    credential: ConnectedCredentialType | None
    account: ConnectedExternalAccountType | None
    owner: UserType
    kind: auto
    impl_class: auto
    status: auto
    last_used_at: auto
    last_used_status: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(WebhookSubscription)
class WebhookSubscriptionType(AngeeNode):
    """Admin projection of an outbound webhook subscription.

    The signing ``secret`` is deliberately omitted (write-only) — unlike
    OAuthClient's revealed ``client_secret``, a webhook secret is never read back.
    """

    owner: UserType
    integration_filter: IntegrationType | None
    target_url: auto
    event_kinds: JSON
    impl_app_filter: JSON
    enabled: auto
    last_delivery_at: auto
    last_delivery_status: auto
    last_error: auto
    consecutive_failures: auto
    created_at: auto
    updated_at: auto


_VENDOR_RESOURCE = hasura_model_resource(
    VendorType,
    model=Vendor,
    name="vendors",
    filterable=["id", "slug", "display_name", "updated_at"],
    sortable=["slug", "display_name", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["created_at"],
    insertable=["display_name", "slug", "website_url", "icon", "description"],
    updatable=["slug", "display_name", "website_url", "icon", "description"],
)
_INTEGRATION_RESOURCE = hasura_model_resource(
    IntegrationType,
    model=Integration,
    name="integrations",
    filterable=["id", "display_name", "vendor", "kind", "impl_class", "status", "updated_at"],
    sortable=["display_name", "vendor", "kind", "impl_class", "status", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["kind", "impl_class", "vendor", "vendor__display_name", "status"],
    insertable=["vendor", "owner", "credential", "account", "impl_class", "status"],
    updatable=["vendor", "credential", "account", "owner", "status"],
    field_id_decode={
        "vendor": public_pk_decoder(Vendor),
        "owner": public_pk_decoder(User),
        "credential": public_pk_decoder(Credential),
        "account": public_pk_decoder(ExternalAccount),
    },
    get_queryset=_console_integrations,
    write_backend=AngeeHasuraWriteBackend(
        Integration,
        public_id_fields=("vendor", "owner", "credential", "account"),
    ),
)
_WEBHOOK_SUBSCRIPTION_RESOURCE = hasura_model_resource(
    WebhookSubscriptionType,
    model=WebhookSubscription,
    name="webhook_subscriptions",
    filterable=[
        "id",
        "owner",
        "integration_filter",
        "target_url",
        "enabled",
        "last_delivery_status",
        "updated_at",
    ],
    sortable=[
        "target_url",
        "enabled",
        "last_delivery_at",
        "consecutive_failures",
        "created_at",
        "updated_at",
    ],
    aggregatable=["id", "consecutive_failures"],
    groupable=["enabled", "last_delivery_status"],
    insertable=["owner", "target_url", "secret", "event_kinds", "impl_app_filter", "integration_filter", "enabled"],
    updatable=["target_url", "secret", "event_kinds", "impl_app_filter", "integration_filter", "enabled"],
    field_id_decode={
        "owner": public_pk_decoder(User),
        "integration_filter": public_pk_decoder(Integration),
    },
    write_backend=AngeeHasuraWriteBackend(
        WebhookSubscription,
        public_id_fields=("owner", "integration_filter"),
    ),
)


@strawberry.type
class RotatedSecret:
    """A freshly rotated webhook signing secret, returned once for display."""

    ok: bool
    secret: str


def _vendor_by_slug(slug: str) -> Any:
    """Return a vendor catalogue row by slug, or raise."""

    with system_context(reason="integrate.graphql.vendor_slug"):
        vendor = Vendor.objects.filter(slug=slug).first()
    if vendor is None:
        raise ValueError(f"Vendor {slug!r} was not found.")
    return vendor


@strawberry.type
class IntegrationCredentialMutation:
    """Self-service integration creation from connected credentials."""

    @strawberry.mutation
    def create_integration_from_credential(
        self,
        info: strawberry.Info,
        credential: PublicID,
        vendor_slug: str,
    ) -> ConnectedIntegrationType:
        """Create or update this user's integration from a connected credential.

        Self-service, not platform-admin: the authorization is *ownership of the
        credential*. ``resolve_action_target`` reads the credential elevated, then the
        ``user_id`` check below is the actual gate. This deliberately bypasses the
        ``create = admin->member`` arm in ``integrate/permissions.zed`` (which
        governs the admin-console Integration CRUD), so a credential owner can wire
        up their own integration without an admin.
        """

        user = _session_user(info)
        oauth_credential = resolve_action_target(
            Credential,
            credential,
            reason="integrate.graphql.integration_from_credential.credential",
        )
        if oauth_credential.user_id != user.pk:
            raise PermissionDenied("Credential does not belong to the current user.")
        vendor = _vendor_by_slug(vendor_slug)
        integration = Integration.objects.activate_from_credential(
            user,
            vendor=vendor,
            credential=oauth_credential,
        )
        return cast(ConnectedIntegrationType, integration)


@strawberry.type
class IntegrationActionMutation:
    """Operational actions on an integration (sync, connection test)."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def sync_integration(self, id: PublicID) -> ActionResult:
        """Run every bridge of one integration now (eager variant of the scheduler)."""

        ran = 0
        errors = 0
        items = 0
        with action_target(Integration, id, reason="integrate.graphql.sync_integration") as integration:
            now = timezone.now()
            for model in bridge_models(Bridge):
                for bridge in model._default_manager.filter(pk=integration.pk).order_by("pk"):
                    ran += 1
                    try:
                        result = bridge.run_sync(now=now)
                    except Exception:  # noqa: BLE001 — run_sync recorded the bridge failure as telemetry.
                        errors += 1
                    else:
                        items += result
        if ran == 0:
            return ActionResult(ok=True, message="No bridges to sync.")
        return ActionResult(
            ok=errors == 0,
            message=f"Synced {items} item(s) across {ran} bridge(s); {errors} error(s).",
        )

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def test_connection(self, id: PublicID) -> ActionResult:
        """Probe the integration's credential so the operator sees it is usable."""

        with action_target(Integration, id, reason="integrate.graphql.test_connection") as integration:
            credential = integration.credential
            if credential is None:
                return ActionResult(ok=False, message="No credential is attached.")
            try:
                credential.auth_headers()
            except Exception as error:  # noqa: BLE001 — surface any handler failure to the operator
                return ActionResult(ok=False, message=f"Credential is not usable: {error}")
        return ActionResult(ok=True, message="Credential is usable.")


@strawberry.type
class WebhookActionMutation:
    """Operational actions on an outbound webhook subscription."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def test_webhook_delivery(self, id: PublicID) -> ActionResult:
        """Send a test event to one subscription and report the delivery outcome."""

        with action_target(
            WebhookSubscription,
            id,
            reason="integrate.graphql.test_webhook_delivery",
        ) as subscription:
            ok, message = subscription.deliver_test()
        return ActionResult(ok=ok, message=message)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def rotate_webhook_secret(self, id: PublicID) -> RotatedSecret:
        """Roll one subscription's signing secret and return the new value once."""

        with action_target(
            WebhookSubscription,
            id,
            reason="integrate.graphql.rotate_webhook_secret",
        ) as subscription:
            secret = subscription.rotate_secret()
        return RotatedSecret(ok=True, secret=secret)


# --- VCS inventory: integrations, repositories, sources, templates ----------


@strawberry_django.type(VcsBridge)
class VcsBridgeType(AngeeNode):
    """Admin projection of a VCS bridge child model."""

    vendor: VendorType
    credential: CredentialType | None
    account: ExternalAccountType | None
    owner: UserType
    backend_class: auto
    status: auto
    config: JSON
    last_sync_completed_at: auto
    last_sync_status: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["backend_class", "status"])
    def display_name(self) -> str:
        """Return a human label for the record header and relation pickers."""

        bridge = cast(Any, self)
        return f"{bridge.backend_class} ({bridge.status})"


@strawberry_django.type(Repository)
class RepositoryType(AngeeNode):
    """Admin projection of one inventoried repository."""

    vcs_bridge: VcsBridgeType
    org: auto
    name: auto
    remote: auto
    ssh_remote: auto
    remote_id: auto
    default_branch: auto
    visibility: auto
    web_url: auto
    archived: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Source)
class SourceType(AngeeNode):
    """Admin projection of one source (a ref+path pointer into a repository)."""

    repository: RepositoryType
    kind: auto
    ref: auto
    path: auto
    last_synced_at: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Template)
class TemplateType(AngeeNode):
    """Admin projection of one discovered template."""

    source: SourceType
    name: auto
    kind: auto
    path: auto
    inputs: JSON
    created_at: auto
    updated_at: auto


@strawberry.type
class RepoCandidate:
    """A repository the host returns for the add typeahead (not yet inventoried)."""

    name: str
    org: str
    remote: str
    ssh_remote: str
    default_branch: str
    visibility: str
    web_url: str
    archived: bool


@strawberry.input
class VcsBridgeInput:
    """Fields accepted when creating a VCS bridge child row."""

    vendor: PublicID
    owner: PublicID
    credential: PublicID | None = None
    account: PublicID | None = strawberry.UNSET
    backend_class: str | None = strawberry.UNSET
    status: str | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET
    webhook_secret: str = ""


@strawberry.input
class VcsBridgePatch:
    """Fields accepted when updating a VCS bridge child model."""

    id: PublicID
    vendor: PublicID | None = strawberry.UNSET
    credential: PublicID | None = strawberry.UNSET
    account: PublicID | None = strawberry.UNSET
    owner: PublicID | None = strawberry.UNSET
    backend_class: str | None = strawberry.UNSET
    status: str | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET
    webhook_secret: str | None = strawberry.UNSET


def _repo_candidate(descriptor: Any) -> RepoCandidate:
    """Project a host ``RepoDescriptor`` into a typeahead candidate."""

    return RepoCandidate(
        name=str(descriptor.name),
        org=str(descriptor.org),
        remote=str(descriptor.remote),
        ssh_remote=str(descriptor.ssh_remote),
        default_branch=str(descriptor.default_branch),
        visibility=str(descriptor.visibility),
        web_url=str(descriptor.web_url),
        archived=bool(descriptor.archived),
    )


_VCS_BRIDGE_RESOURCE = hasura_model_resource(
    VcsBridgeType,
    model=VcsBridge,
    name="vcs_bridges",
    filterable=["id", "vendor", "backend_class", "status", "last_sync_status", "updated_at"],
    sortable=["vendor", "backend_class", "status", "last_sync_completed_at", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["vendor", "vendor__display_name", "backend_class", "status", "last_sync_status"],
    insert=False,
    update=False,
    delete=True,
    field_id_decode={"vendor": public_pk_decoder(Vendor)},
)
_REPOSITORY_RESOURCE = hasura_model_resource(
    RepositoryType,
    model=Repository,
    name="repositories",
    filterable=["id", "vcs_bridge", "org", "name", "visibility", "archived", "updated_at"],
    sortable=["vcs_bridge", "org", "name", "visibility", "archived", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["vcs_bridge", "vcs_bridge__backend_class", "org", "visibility", "archived"],
    insert=False,
    update=False,
    delete=True,
    field_id_decode={"vcs_bridge": public_pk_decoder(VcsBridge)},
)
_SOURCE_RESOURCE = hasura_model_resource(
    SourceType,
    model=Source,
    name="sources",
    filterable=["id", "repository", "kind", "ref", "updated_at"],
    sortable=["repository", "kind", "ref", "path", "last_synced_at", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["repository", "repository__name", "kind", "last_synced_at"],
    insertable=["repository", "kind", "ref", "path"],
    updatable=["kind", "ref", "path"],
    field_id_decode={"repository": public_pk_decoder(Repository)},
    write_backend=AngeeHasuraWriteBackend(Source, public_id_fields=("repository",)),
)
_TEMPLATE_RESOURCE = hasura_model_resource(
    TemplateType,
    model=Template,
    name="templates",
    filterable=["id", "source", "name", "kind", "path", "updated_at"],
    sortable=["source", "name", "kind", "path", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["source", "source__path", "kind", "updated_at"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={"source": public_pk_decoder(Source)},
)


@strawberry.type
class VCSConsoleQuery:
    """Admin VCS inventory queries."""

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def search_repositories(self, vcs_bridge_id: PublicID, query: str) -> list[RepoCandidate]:
        """Return host repositories matching ``query`` for the add typeahead."""

        with action_target(
            VcsBridge,
            vcs_bridge_id,
            reason="integrate.graphql.search_repositories",
        ) as vcs:
            return [_repo_candidate(descriptor) for descriptor in vcs.search_repositories(query)]


@strawberry.type
class VcsBridgeCreateMutation:
    """Admin create for a VCS bridge child, validating backend-owned fields."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_vcs_bridge(self, data: VcsBridgeInput) -> VcsBridgeType:
        """Create a VCS child row directly."""

        attrs = {
            **integration_create_attrs(data, reason="integrate.graphql.vcs_bridge.create"),
            "backend_class": VcsBridge.impl_key_for(
                "backend_class",
                None if data.backend_class is strawberry.UNSET else data.backend_class,
                default="local",
            ),
            "webhook_secret": data.webhook_secret,
        }
        if data.config is not strawberry.UNSET:
            attrs["config"] = data.config
        with system_context(reason="integrate.graphql.vcs_bridge.create"), transaction.atomic():
            bridge = VcsBridge.objects.create(**attrs)
        return cast(VcsBridgeType, bridge)


@strawberry.type
class VcsBridgeUpdateMutation:
    """Admin update for a VCS bridge child, validating backend-owned fields."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_vcs_bridge(self, data: VcsBridgePatch) -> VcsBridgeType:
        """Update a VCS child row, rematerializing backend defaults on backend change."""

        backend_changed = False
        with (
            action_target(
                VcsBridge,
                data.id,
                reason="integrate.graphql.vcs_bridge.update",
            ) as bridge,
            transaction.atomic(),
        ):
            provided = apply_integration_patch_fields(
                bridge,
                data,
                reason="integrate.graphql.vcs_bridge.update",
            )
            if data.backend_class is not strawberry.UNSET:
                backend_changed = bridge.set_impl_key("backend_class", data.backend_class, default="local")
            if data.config is not strawberry.UNSET:
                bridge.config = data.config
                provided.add("config")
            if data.webhook_secret is not strawberry.UNSET:
                bridge.webhook_secret = data.webhook_secret or ""
                provided.add("webhook_secret")
            if backend_changed:
                bridge.materialize_impl_defaults("backend_class", provided=frozenset(provided))
            bridge.save()
        return cast(VcsBridgeType, bridge)


@strawberry.type
class VCSActionMutation:
    """Operational actions on a VCS bridge and its inventory."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def add_repository(self, vcs_bridge_id: PublicID, name: str) -> RepositoryType:
        """Inventory one repository by its host ``name`` (a picked typeahead result)."""

        with action_target(VcsBridge, vcs_bridge_id, reason="integrate.graphql.add_repository") as vcs:
            return cast(RepositoryType, vcs.import_repository(name))

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def discover_repositories(self, vcs_bridge_id: PublicID, org: str = "") -> ActionResult:
        """Inventory every repository the account exposes (bulk import; prunes vanished)."""

        with action_target(VcsBridge, vcs_bridge_id, reason="integrate.graphql.discover_repositories") as vcs:
            count = vcs.discover_repositories(org=org)
        return ActionResult(ok=True, message=f"Inventoried {count} repository(ies).")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def sync_vcs_bridge(self, id: PublicID) -> ActionResult:
        """Refresh every repository's sources for one VCS bridge now."""

        with action_target(VcsBridge, id, reason="integrate.graphql.sync_vcs_bridge") as vcs:
            now = timezone.now()
            try:
                result = vcs.run_sync(now=now)
            except Exception as error:  # noqa: BLE001 — sync failure is the result, not a 500
                return ActionResult(ok=False, message=f"Sync failed: {error}")
        return ActionResult(ok=True, message=f"Synced {result} item(s).")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def refresh_source(self, id: PublicID) -> ActionResult:
        """Re-enumerate one source's output rows now."""

        with action_target(Source, id, reason="integrate.graphql.refresh_source") as source:
            count = source.refresh()
        return ActionResult(ok=True, message=f"Synced {count} item(s).")


# Extracted with an explicit annotation: a bare homogeneous list of two
# AngeeNode-decorated types infers as ``list[type[AngeeNode]]`` and trips mypy's
# invariance check; ``list[type]`` widens it. (iam's inline lists are heterogeneous,
# so they don't hit this.)
_CONSOLE_TYPES: list[object] = [
    OAuthClientType,
    CredentialOAuthClientType,
    ExternalAccountType,
    CredentialType,
    ConnectedExternalAccountType,
    ConnectedCredentialType,
    ConnectableAccount,
    OAuthStartPayload,
    ConnectAccountResult,
    ConnectIntegrationResult,
    UnlinkAccountResult,
    RevealedCredentialSecret,
    VendorType,
    *_VENDOR_RESOURCE.types,
    ConnectedIntegrationType,
    IntegrationType,
    *_INTEGRATION_RESOURCE.types,
    WebhookSubscriptionType,
    *_WEBHOOK_SUBSCRIPTION_RESOURCE.types,
    *_OAUTH_CLIENT_RESOURCE.types,
    *_EXTERNAL_ACCOUNT_RESOURCE.types,
    *_CREDENTIAL_RESOURCE.types,
    VcsBridgeType,
    *_VCS_BRIDGE_RESOURCE.types,
    RepositoryType,
    *_REPOSITORY_RESOURCE.types,
    SourceType,
    *_SOURCE_RESOURCE.types,
    TemplateType,
    *_TEMPLATE_RESOURCE.types,
    RepoCandidate,
]

schemas = {
    "public": {
        "query": [IntegrateConnectionsQuery],
        "mutation": [ConnectionMutation, IntegrationCredentialMutation],
        "types": [
            ConnectedExternalAccountType,
            ConnectedCredentialType,
            ConnectableAccount,
            OAuthStartPayload,
            ConnectAccountResult,
            ConnectIntegrationResult,
            UnlinkAccountResult,
            VendorType,
            ConnectedIntegrationType,
            UserType,
        ],
    },
    "console": {
        # The impl-picker lookup (Integration.impl_class / VcsBridge.backend_class /
        # OAuthClient.provider_type live here); a generic framework query contributed
        # where its models do.
        "query": [
            ConsoleImplChoicesQuery,
            _OAUTH_CLIENT_RESOURCE.query,
            _EXTERNAL_ACCOUNT_RESOURCE.query,
            _CREDENTIAL_RESOURCE.query,
            _VENDOR_RESOURCE.query,
            _INTEGRATION_RESOURCE.query,
            _WEBHOOK_SUBSCRIPTION_RESOURCE.query,
            _VCS_BRIDGE_RESOURCE.query,
            _REPOSITORY_RESOURCE.query,
            _SOURCE_RESOURCE.query,
            _TEMPLATE_RESOURCE.query,
            VCSConsoleQuery,
        ],
        "mutation": [
            _OAUTH_CLIENT_RESOURCE.mutation,
            _EXTERNAL_ACCOUNT_RESOURCE.mutation,
            _CREDENTIAL_RESOURCE.mutation,
            _VENDOR_RESOURCE.mutation,
            _INTEGRATION_RESOURCE.mutation,
            _WEBHOOK_SUBSCRIPTION_RESOURCE.mutation,
            _VCS_BRIDGE_RESOURCE.mutation,
            _REPOSITORY_RESOURCE.mutation,
            _SOURCE_RESOURCE.mutation,
            IntegrateExternalAccountMutation,
            IntegrateCredentialMutation,
            ConnectionMutation,
            VcsBridgeCreateMutation,
            VcsBridgeUpdateMutation,
            IntegrationCredentialMutation,
            IntegrationActionMutation,
            WebhookActionMutation,
            VCSActionMutation,
        ],
        "subscription": [changes(Integration, field="integrationChanged")],
        "types": _CONSOLE_TYPES,
    },
}
"""GraphQL contributions installed by the integrate addon."""
