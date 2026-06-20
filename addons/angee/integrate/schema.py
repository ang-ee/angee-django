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
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.utils import timezone
from rebac import PermissionDenied, system_context
from strawberry import auto
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.base.fields import ImplClassField
from angee.base.models import instance_from_public_id
from angee.graphql.actions import ActionResult, resolve_action_target
from angee.graphql.aggregates import rebac_aggregate_builder
from angee.graphql.crud import crud
from angee.graphql.deletion import DeletePreview, delete_by_public_id
from angee.graphql.ids import PublicID
from angee.graphql.impl import ImplChoice
from angee.graphql.impl import impl_choices as resolve_impl_choices
from angee.graphql.node import AngeeNode, detail
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

    @strawberry_django.field
    def credential_status(self) -> str:
        """Return the current OAuth credential status, if this account has one."""

        return str(cast(Any, self).credential_status)

    @strawberry_django.field
    def provider_slug(self) -> str:
        """Return the originating OAuth client's slug (the provider key)."""

        return str(getattr(cast(Any, self).oauth_client, "slug", "") or "")

    @strawberry_django.field
    def provider_environment(self) -> str:
        """Return the originating OAuth client's environment.

        ``(slug, environment)`` is the OAuth client's unique key, so the console
        can resolve an account back to its exact client without ambiguity.
        """

        return str(getattr(cast(Any, self).oauth_client, "environment", "") or "")

    @strawberry_django.field
    def provider_label(self) -> str:
        """Return the originating OAuth client's display label."""

        return str(getattr(cast(Any, self).oauth_client, "display_name", "") or "")

    @strawberry_django.field
    def provider_icon(self) -> str:
        """Return the originating OAuth client's branding icon."""

        return str(getattr(cast(Any, self).oauth_client, "icon", "") or "")


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
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["oauth_client"])
    def oauth_client(self) -> CredentialOAuthClientType | None:
        """Return a public-safe projection of the OAuth client (``None`` for local kinds)."""

        return cast("CredentialOAuthClientType | None", cast(Any, self).oauth_client)

    @strawberry_django.field(only=["oauth_client", "external_account", "name"])
    def display_name(self) -> str:
        """Return a human label for the list, form title, and relation pickers.

        The stored ``name`` is the label (OAuth rows are named on create from their
        provider + subject; see ``CredentialManager._oauth_credential_name``). It is the
        ``name`` column the relation-picker representation reads, so preferring it keeps
        the picker, list, and form consistent without dereferencing related rows. A
        legacy unnamed OAuth row falls back to ``provider: subject``.
        """

        name = str(cast(Any, self).name or "")
        if name:
            return name
        client = getattr(cast(Any, self), "oauth_client", None)
        if client is not None:
            provider = str(getattr(client, "slug", "") or getattr(client, "display_name", "") or "credential")
            account = getattr(cast(Any, self), "external_account", None)
            subject = str(getattr(account, "external_id", "") or "") if account else ""
            return f"{provider}: {subject}" if subject else provider
        return "credential"


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

    @strawberry_django.field(only=["client_secret"])
    def client_secret(self) -> str:
        """Return the decrypted client secret for the admin console."""

        return str(cast(Any, self).client_secret or "")

    @strawberry_django.field
    def configuration_state(self) -> str:
        """Return this OAuth client's operator-facing configuration readiness."""

        return str(cast(Any, self).configuration_state)


@strawberry.input
class OAuthClientInput:
    """Admin-write fields accepted when creating an OAuth client (base, connect)."""

    display_name: str
    client_id: str
    slug: str
    provider_type: str = "generic_oauth2"
    icon: str = ""
    client_secret: str = ""
    environment: str = "prod"
    discovery_url: str = ""
    authorize_endpoint: str = ""
    token_endpoint: str = ""
    revoke_endpoint: str = ""
    userinfo_endpoint: str = ""
    manual_redirect_uri: str = ""
    token_request_format: str = "form"
    is_enabled: bool = True
    scopes_catalogue: list[str] = strawberry.field(default_factory=list)
    default_scopes: list[str] = strawberry.field(default_factory=list)
    supports_refresh: bool = True
    refresh_rotates: bool = False
    supports_pkce: bool = True
    max_refresh_age_seconds: int | None = None
    authorize_params: JSON = strawberry.field(default_factory=dict)
    token_params: JSON = strawberry.field(default_factory=dict)
    external_id_claim: str = "sub"
    email_claim: str = "email"
    display_name_claim: str = ""
    avatar_url_claim: str = ""


@strawberry.input
class OAuthClientPatch:
    """Admin-write fields accepted when updating an OAuth client."""

    id: PublicID
    slug: str | None = strawberry.UNSET
    provider_type: str | None = strawberry.UNSET
    icon: str | None = strawberry.UNSET
    display_name: str | None = strawberry.UNSET
    client_id: str | None = strawberry.UNSET
    client_secret: str | None = strawberry.UNSET
    environment: str | None = strawberry.UNSET
    discovery_url: str | None = strawberry.UNSET
    authorize_endpoint: str | None = strawberry.UNSET
    token_endpoint: str | None = strawberry.UNSET
    revoke_endpoint: str | None = strawberry.UNSET
    userinfo_endpoint: str | None = strawberry.UNSET
    manual_redirect_uri: str | None = strawberry.UNSET
    token_request_format: str | None = strawberry.UNSET
    is_enabled: bool | None = strawberry.UNSET
    scopes_catalogue: list[str] | None = strawberry.UNSET
    default_scopes: list[str] | None = strawberry.UNSET
    supports_refresh: bool | None = strawberry.UNSET
    refresh_rotates: bool | None = strawberry.UNSET
    supports_pkce: bool | None = strawberry.UNSET
    max_refresh_age_seconds: int | None = strawberry.UNSET
    authorize_params: JSON | None = strawberry.UNSET
    token_params: JSON | None = strawberry.UNSET
    external_id_claim: str | None = strawberry.UNSET
    email_claim: str | None = strawberry.UNSET
    display_name_claim: str | None = strawberry.UNSET
    avatar_url_claim: str | None = strawberry.UNSET


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
class ExternalAccountPatch:
    """Admin-write fields accepted when updating an external account (scalars only)."""

    id: PublicID
    email: str | None = strawberry.UNSET
    display_name: str | None = strawberry.UNSET
    avatar_url: str | None = strawberry.UNSET
    status: str | None = strawberry.UNSET


@strawberry.input
class CredentialInput:
    """Admin-write fields for a provider-less credential (OAuth ones arrive via connect).

    ``kind`` discriminates the material: ``static_token`` reads ``api_key``,
    ``ssh_key`` reads ``private_key``. ``user`` defaults to the calling admin.
    """

    name: str
    kind: str
    user: PublicID | None = None
    api_key: str = ""
    private_key: str = ""


@strawberry.input
class CredentialPatch:
    """Admin-write fields accepted when updating a credential."""

    id: PublicID
    status: str | None = strawberry.UNSET


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


@strawberry.type
class ConnectAccountResult:
    """Result returned by OAuth account-connect completion."""

    account: ExternalAccountType | None = None
    credential: CredentialType | None = None
    user: UserType | None = None
    intent: str = "connect"
    next: str = "/"
    claims: JSON | None = None
    error: str | None = None
    error_code: str | None = None


@strawberry.type
class ConnectIntegrationResult:
    """Result returned by one-click integration connect/attach."""

    integration: "IntegrationType | None" = None
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


def _console_oauth_clients(info: strawberry.Info) -> Any:
    """Return admin-visible OAuth clients (self-describing; no vendor join)."""

    del info
    return cast(Any, OAuthClient.objects).console_oauth_clients()


def _console_external_accounts(info: strawberry.Info) -> Any:
    """Return admin-visible external accounts with guarded FK joins."""

    del info
    return cast(Any, ExternalAccount.objects).console_external_accounts()


def _console_credentials(info: strawberry.Info) -> Any:
    """Return admin-visible credential health with guarded FK joins."""

    del info
    return cast(Any, Credential.objects).console_credentials()


def _oauth_client_from_id(oauth_client_id: PublicID) -> Any:
    """Return the OAuth client addressed by one public GraphQL id."""

    return flow.oauth_client_from_id(oauth_client_id)


def _credential_material(data: CredentialInput) -> dict[str, str]:
    """Read the secret the kind's handler names out of the discriminated input."""

    field = handler_for(data.kind).material_field
    if not hasattr(data, field):
        raise ValueError(f"Cannot create a credential of kind {data.kind!r}.")
    return {field: getattr(data, field)}


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


def _flow_error_message(error: OAuthFlowError) -> str:
    """Return the best safe human message for one OAuth flow error."""

    return error.provider_message or str(error)


def _integration_impl_class(impl_class: str) -> type[IntegrationImpl]:
    """Return the configured implementation class for one integration key."""

    return cast(type[IntegrationImpl], Integration.objects.impl_class_for_key(impl_class))


def _oauth_client_for_integration(integration: Any) -> Any:
    """Return the OAuth client this integration implementation connects through."""

    impl = integration.impl
    hint = str(getattr(impl, "oauth_client", "") or "")
    if not hint:
        vendor = getattr(integration, "vendor", None)
        hint = str(getattr(vendor, "slug", "") or "")
    if not hint:
        raise OAuthFlowError("oauth_client_not_connectable", 400, "Integration has no OAuth client.")
    vendor_slug = str(getattr(getattr(integration, "vendor", None), "slug", "") or "")
    slug = hint.format(vendor=vendor_slug)
    with system_context(reason="integrate.graphql.connect_integration.oauth_client"):
        oauth_client = OAuthClient.objects.enabled_for_slug(slug)
    if oauth_client is None:
        raise OAuthFlowError("oauth_client_not_connectable", 400, "Integration has no enabled OAuth client.")
    return oauth_client


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
    impl_field = cast(ImplClassField, Integration._meta.get_field("impl_class"))
    impl_key = impl_field.key_for(impl_class) or ""
    if not (vendor_key and impl_key):
        raise ValueError("connectIntegration requires integrationId or vendorSlug and implClass.")
    impl_field.resolve_class(impl_key)
    vendor = _vendor_by_slug(vendor_key)
    with system_context(reason="integrate.graphql.connect_integration.draft"):
        integration = Integration.objects.filter(owner=user, vendor=vendor, impl_class=impl_key).first()
        if integration is not None:
            return integration
        try:
            integration = Integration.objects.create(
                owner=user,
                vendor=vendor,
                impl_class=impl_key,
                status="draft",
            )
        except IntegrityError:
            integration = Integration.objects.filter(owner=user, vendor=vendor, impl_class=impl_key).first()
            if integration is None:
                raise
    return integration


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
        return ConnectIntegrationResult(integration=cast("IntegrationType", integration), attached=True)
    if not redirect_uri:
        raise OAuthFlowError("redirect_uri_required", 400, "OAuth redirect URI is required.")
    if oauth_client.configuration_state != "ready":
        raise OAuthFlowError(
            CLIENT_NOT_CONFIGURED,
            400,
            f"OAuth client is not fully configured ({oauth_client.configuration_state}).",
        )
    request = _request(info)
    state_token, record, effective_redirect_uri, mode = flow.issue_flow(
        request,
        oauth_client,
        redirect_uri,
        user_id=str(user.pk),
        next_path=flow.coerce_next_path(next_path, request),
        flow=state.StateFlow.CONNECT,
        integration_id=str(integration.sqid),
    )
    authorize_url = OAuthClientProtocol(oauth_client).authorize_url(
        state=state_token,
        redirect_uri=effective_redirect_uri,
        scopes=oauth_client.default_scope_values,
        code_challenge=flow.pkce_challenge(record.code_verifier),
    )
    return ConnectIntegrationResult(
        integration=cast("IntegrationType", integration),
        authorize_url=authorize_url,
        state=state_token,
        mode=mode,
        redirect_uri=effective_redirect_uri,
    )


@strawberry.type
class IntegrateConnectionsQuery:
    """Public account-connect picker and self-service connected-account queries."""

    connectable_accounts: OffsetPaginated[ConnectableAccount] = strawberry_django.offset_paginated(
        resolver=_connectable_accounts,
    )
    my_connected_accounts: OffsetPaginated[CredentialType] = strawberry_django.offset_paginated(
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
            oauth_client = flow.enabled_oauth_client_from_id(id)
            if oauth_client.configuration_state != "ready":
                # Enabled but missing a client_id/endpoints would otherwise build an
                # authorize URL the provider rejects opaquely; surface it as a typed
                # start-flow error instead.
                raise OAuthFlowError(
                    CLIENT_NOT_CONFIGURED,
                    400,
                    f"OAuth client is not fully configured ({oauth_client.configuration_state}).",
                )
            state_token, record, effective_redirect_uri, mode = flow.issue_flow(
                request,
                oauth_client,
                redirect_uri,
                user_id=str(user.pk),
                next_path=flow.coerce_next_path(next, request),
                flow=state.StateFlow.CONNECT,
            )
            authorize_url = OAuthClientProtocol(oauth_client).authorize_url(
                state=state_token,
                redirect_uri=effective_redirect_uri,
                scopes=oauth_client.default_scope_values,
                code_challenge=flow.pkce_challenge(record.code_verifier),
            )
        except OAuthFlowError as error:
            return OAuthStartPayload(error=_flow_error_message(error), error_code=error.code)
        return OAuthStartPayload(
            authorize_url=authorize_url,
            state=state_token,
            mode=mode,
            redirect_uri=effective_redirect_uri,
        )

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def discover_oauth_endpoints(self, id: PublicID) -> ActionResult:
        """Fetch the provider's discovery document and fill this client's blank endpoints.

        The resolved authorize/token/userinfo endpoints (and any composed extension
        endpoints, e.g. OIDC issuer/JWKS) are persisted on the OAuth client row, so
        the operator never types them by hand. Requires a discovery URL on the row.
        """

        with system_context(reason="integrate.graphql.discover_oauth_endpoints"):
            oauth_client = instance_from_public_id(
                OAuthClient, str(id), queryset=OAuthClient._default_manager.all()
            )
            if oauth_client is None:
                raise ValueError(f"OAuth client {id!s} was not found")
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
            return ConnectIntegrationResult(error=_flow_error_message(error), error_code=error.code)

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
            return ConnectAccountResult(error=_flow_error_message(error), error_code=error.code)
        return ConnectAccountResult(
            account=cast(ExternalAccountType, result.account),
            credential=cast(CredentialType, result.credential),
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
                ExternalAccount.objects.revoke_owner(external_account, user)
                # Revoke at the provider only if the delete commits. The login addon's
                # pre_delete guard can veto (a passwordless user's last sign-in), rolling
                # this back — we must not revoke a token whose local credential we keep.
                transaction.on_commit(lambda: _revoke_remote_oauth_token(credential))
                deleted, _details = (
                    Credential.objects.filter(pk=credential.pk).with_action("delete").delete()
                )
            return UnlinkAccountResult(ok=deleted > 0)
        except OAuthFlowError as error:
            return UnlinkAccountResult(ok=False, error=_flow_error_message(error), error_code=error.code)


@strawberry.type
class IntegrateConnectionConsoleQuery:
    """Admin OAuth client, external account, and credential queries."""

    oauth_clients: OffsetPaginated[OAuthClientType] = strawberry_django.offset_paginated(
        resolver=_console_oauth_clients,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    oauth_client: OAuthClientType | None = detail(
        OAuthClientType,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    external_accounts: OffsetPaginated[ExternalAccountType] = strawberry_django.offset_paginated(
        resolver=_console_external_accounts,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    external_account: ExternalAccountType | None = detail(
        ExternalAccountType,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    credential_health: OffsetPaginated[CredentialType] = strawberry_django.offset_paginated(
        resolver=_console_credentials,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    credential: CredentialType | None = detail(
        CredentialType,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )


_OAUTH_CLIENT_MUTATION = crud(
    OAuthClientType,
    create=OAuthClientInput,
    update=OAuthClientPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="oauth_client",
    write_context="integrate.graphql.oauth_client",
)
"""Admin OAuth-client CRUD: const-admin gated by ``PlatformAdminPermission``, written elevated."""


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

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_external_account(self, data: ExternalAccountPatch) -> ExternalAccountType:
        """Update one external account's scalar identity fields."""

        with system_context(reason="integrate.graphql.external_account.update"), transaction.atomic():
            account = instance_from_public_id(
                ExternalAccount, str(data.id), queryset=ExternalAccount._default_manager.all()
            )
            if account is None:
                raise ValueError(f"External account {data.id!s} was not found")
            for field in ("email", "display_name", "avatar_url", "status"):
                value = getattr(data, field)
                if value is not strawberry.UNSET:
                    setattr(account, field, value)
            account.save()
        return cast(ExternalAccountType, account)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
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
    def reveal_credential(self, id: PublicID) -> RevealedCredentialSecret:
        """Return one credential's decrypted secret for an admin to copy."""

        with system_context(reason=f"integrate.graphql.credential.reveal:{str(id)}"):
            credential = instance_from_public_id(
                Credential, str(id), queryset=Credential._default_manager.all()
            )
            if credential is None:
                raise ValueError(f"Credential {id!s} was not found")
            return RevealedCredentialSecret(secret=str(credential.secret_value() or ""))

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

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_credential(self, data: CredentialPatch) -> CredentialType:
        """Update one credential's status."""

        with system_context(reason="integrate.graphql.credential.update"), transaction.atomic():
            credential = instance_from_public_id(
                Credential, str(data.id), queryset=Credential._default_manager.all()
            )
            if credential is None:
                raise ValueError(f"Credential {data.id!s} was not found")
            if data.status is not strawberry.UNSET and data.status is not None:
                credential.status = data.status
                credential.save(update_fields=["status", "updated_at"])
        return cast(CredentialType, credential)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def delete_credential(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Best-effort remote revoke, then delete the credential when unblocked."""

        return delete_by_public_id(
            Credential,
            str(id),
            reason="integrate.graphql.credential.delete",
            confirm=confirm,
            before_delete=_revoke_remote_oauth_token,
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


@strawberry_django.type(Integration)
class IntegrationType(AngeeNode):
    """Admin projection of an integration.

    Exposes the catalogue/identity associations as nested relations so the
    console form's ``many2one`` pickers auto-wire (mirrors iam's
    ``CredentialType.external_account``); safe because the surface is admin-gated.
    """

    vendor: VendorType
    credential: CredentialType | None
    account: ExternalAccountType | None
    owner: UserType
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

        try:
            return cast("VcsBridgeType", getattr(cast(Any, self), "vcsbridge"))
        except ObjectDoesNotExist:
            return None

    @strawberry_django.field(only=["vendor", "status"])
    def display_name(self) -> str:
        """Return a human label for the record header and relation pickers.

        Integration has no natural string column; this gives ``recordRepresentation``
        a value (vendor + status) to show.
        """

        vendor = getattr(cast(Any, self), "vendor", None)
        label = str(getattr(vendor, "display_name", "") or getattr(vendor, "slug", "") or "integration")
        return f"{label} ({cast(Any, self).status})"

    @strawberry_django.field(only=["impl_class"])
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

    @strawberry_django.field(only=["vendor"])
    def vendor_slug(self) -> str:
        """Return the vendor slug as a flat grouping field."""

        vendor = getattr(cast(Any, self), "vendor", None)
        return str(getattr(vendor, "slug", "") or "")

    @strawberry_django.field(only=["vendor"])
    def vendor_label(self) -> str:
        """Return the vendor display label as a flat grouping field."""

        vendor = getattr(cast(Any, self), "vendor", None)
        return str(getattr(vendor, "display_name", "") or getattr(vendor, "slug", "") or "")


@strawberry.input
class VendorInput:
    """Fields accepted when creating a vendor."""

    display_name: str
    slug: str
    website_url: str = ""
    icon: str = ""
    description: str = ""


@strawberry.input
class VendorPatch:
    """Fields accepted when updating a vendor."""

    id: PublicID
    slug: str | None = strawberry.UNSET
    display_name: str | None = strawberry.UNSET
    website_url: str | None = strawberry.UNSET
    icon: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET


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


@strawberry.input
class WebhookSubscriptionInput:
    """Fields accepted when creating a webhook subscription."""

    owner: PublicID
    target_url: str
    secret: str
    event_kinds: JSON | None = None
    impl_app_filter: JSON | None = None
    integration_filter: PublicID | None = None
    enabled: bool = True


@strawberry.input
class WebhookSubscriptionPatch:
    """Fields accepted when updating a webhook subscription."""

    id: PublicID
    target_url: str | None = strawberry.UNSET
    secret: str | None = strawberry.UNSET
    event_kinds: JSON | None = strawberry.UNSET
    impl_app_filter: JSON | None = strawberry.UNSET
    integration_filter: PublicID | None = strawberry.UNSET
    enabled: bool | None = strawberry.UNSET


@strawberry.input
class IntegrationInput:
    """Fields accepted when creating an integration.

    FK public ids resolve to instances via the GraphQL write boundary (like storage's
    ``DriveInput.backend``); ``owner`` is field-backed REBAC, so writing it
    derives the owner tuple.
    """

    vendor: PublicID
    owner: PublicID
    credential: PublicID | None = None
    account: PublicID | None = strawberry.UNSET
    impl_class: str | None = strawberry.UNSET
    status: str | None = strawberry.UNSET


@strawberry.input
class IntegrationPatch:
    """Fields accepted when updating an integration."""

    id: PublicID
    vendor: PublicID | None = strawberry.UNSET
    credential: PublicID | None = strawberry.UNSET
    account: PublicID | None = strawberry.UNSET
    owner: PublicID | None = strawberry.UNSET
    status: str | None = strawberry.UNSET


@strawberry_django.filter_type(Integration, lookups=True)
class IntegrationFilter:
    """Field lookups accepted when filtering the integrations list.

    The grouped integrations view needs filter echo for each server aggregate
    axis so expanded buckets can page their rows through the normal list root.
    Keep this to direct model fields; relation-path axes cannot echo filters.
    """

    vendor: auto
    impl_class: auto
    status: auto


@strawberry_django.order_type(Integration)
class IntegrationOrder:
    """Orderings accepted by the integrations connection."""

    vendor: auto
    impl_class: auto
    status: auto
    created_at: auto
    updated_at: auto


_integration_aggregates = rebac_aggregate_builder(
    model=Integration,
    name_prefix="IntegrationAggregate",
    aggregate_fields=["id"],
    group_by_fields=["impl_class", "vendor", "status"],
    filter_type=IntegrationFilter,
    pagination_style="offset",
    enable_filter_echo=True,
).build()


@strawberry.type
class IntegrateConsoleQuery:
    """Admin integration catalogue and integration queries."""

    vendors: OffsetPaginated[VendorType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    vendor: VendorType | None = detail(
        VendorType,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    integrations: OffsetPaginated[IntegrationType] = strawberry_django.offset_paginated(
        filters=IntegrationFilter,
        order=IntegrationOrder,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    integration: IntegrationType | None = detail(
        IntegrationType,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    integration_aggregate = _integration_aggregates.aggregate_field
    integration_groups = _integration_aggregates.group_by_field
    webhook_subscriptions: OffsetPaginated[WebhookSubscriptionType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    webhook_subscription: WebhookSubscriptionType | None = detail(
        WebhookSubscriptionType,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )


_VENDOR_MUTATION = crud(
    VendorType,
    create=VendorInput,
    update=VendorPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="vendor",
    write_context="integrate.graphql.vendor",
)
"""Admin vendor CRUD: const-admin gated by ``PlatformAdminPermission``, written elevated."""

@strawberry.type
class IntegrationCreateMutation:
    """Admin create for a generic Integration parent row."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_integration(self, data: IntegrationInput) -> IntegrationType:
        """Create a draft integration parent row."""

        vendor = resolve_action_target(
            Vendor,
            data.vendor,
            reason="integrate.graphql.integration.create.vendor",
        )
        owner = _user_from_public_id(data.owner)
        credential = (
            None
            if data.credential is None
            else resolve_action_target(
                Credential,
                data.credential,
                reason="integrate.graphql.integration.create.credential",
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
                    reason="integrate.graphql.integration.create.account",
                )
            )
        )
        impl_key = _create_impl_key(data.impl_class)
        attrs: dict[str, Any] = {
            "vendor": vendor,
            "owner": owner,
            "impl_class": impl_key,
        }
        if credential is not None:
            attrs["credential"] = credential
        if account is not strawberry.UNSET:
            attrs["account"] = account
        if data.status not in (strawberry.UNSET, None):
            attrs["status"] = data.status
        with system_context(reason="integrate.graphql.integration.create"):
            integration = Integration.objects.create(**attrs)
        return cast(IntegrationType, integration)


_INTEGRATION_MUTATION = crud(
    IntegrationType,
    update=IntegrationPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="integration",
    write_context="integrate.graphql.integration",
)
"""Admin integration update/delete; generic create writes the parent row only."""

_WEBHOOK_MUTATION = crud(
    WebhookSubscriptionType,
    create=WebhookSubscriptionInput,
    update=WebhookSubscriptionPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="webhook_subscription",
    write_context="integrate.graphql.webhook_subscription",
)
"""Admin outbound-webhook CRUD: secret is write-only; written elevated."""


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


def _create_impl_key(value: str | None) -> str:
    """Return the implementation key stored by an integration create."""

    if value is None or value is strawberry.UNSET:
        return "none"
    field = cast(Any, Integration._meta.get_field("impl_class"))
    key = str(field.key_for(value) or "none")
    field.resolve_class(key)
    return key


@strawberry.type
class IntegrationCredentialMutation:
    """Self-service integration creation from connected credentials."""

    @strawberry.mutation
    def create_integration_from_credential(
        self,
        info: strawberry.Info,
        credential: PublicID,
        vendor_slug: str,
    ) -> IntegrationType:
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
        with system_context(reason="integrate.graphql.integration_from_credential"), transaction.atomic():
            # Self-service links reuse the user's draft row for this vendor; concrete
            # child models own any domain-specific uniqueness.
            integration = (
                Integration.objects.filter(owner=user, vendor=vendor, impl_class="none").order_by("pk").first()
            )
            if integration is None:
                integration = Integration.objects.create(
                    owner=user,
                    vendor=vendor,
                    impl_class="none",
                    credential=oauth_credential,
                    account=oauth_credential.external_account,
                    status="active",
                )
            else:
                integration.credential = oauth_credential
                integration.account = oauth_credential.external_account
                integration.status = "active"
                integration.save(update_fields=["credential", "account", "status", "updated_at"])
        return cast(IntegrationType, integration)


@strawberry.type
class IntegrationActionMutation:
    """Operational actions on an integration (sync, connection test)."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def sync_integration(self, id: PublicID) -> ActionResult:
        """Run every bridge of one integration now (eager variant of the scheduler)."""

        integration = resolve_action_target(Integration, id, reason="integrate.graphql.sync_integration")
        now = timezone.now()
        ran = 0
        errors = 0
        items = 0
        with system_context(reason="integrate.graphql.sync_integration"):
            for model in bridge_models():
                for bridge in model._default_manager.filter(pk=integration.pk).order_by("pk"):
                    ran += 1
                    bridge.mark_sync_started(now=now)
                    try:
                        result = bridge.sync()
                    except Exception as error:  # noqa: BLE001 — report any bridge failure as telemetry
                        bridge.record_sync_error(error, now=now)
                        errors += 1
                    else:
                        bridge.record_sync(result, now=now)
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

        integration = resolve_action_target(Integration, id, reason="integrate.graphql.test_connection")
        with system_context(reason="integrate.graphql.test_connection"):
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

        subscription = resolve_action_target(WebhookSubscription, id, reason="integrate.graphql.test_webhook_delivery")
        with system_context(reason="integrate.graphql.test_webhook_delivery"):
            ok, message = subscription.deliver_test()
        return ActionResult(ok=ok, message=message)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def rotate_webhook_secret(self, id: PublicID) -> RotatedSecret:
        """Roll one subscription's signing secret and return the new value once."""

        subscription = resolve_action_target(WebhookSubscription, id, reason="integrate.graphql.rotate_webhook_secret")
        with system_context(reason="integrate.graphql.rotate_webhook_secret"):
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


@strawberry_django.filter_type(Source, lookups=True)
class SourceFilter:
    """Field lookups accepted when filtering the sources list.

    ``kind`` lets a consumer surface scope a list to its own source kind (e.g. the
    agents console filtering to ``skill`` sources).
    """

    kind: auto
    ref: auto
    repository: auto


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


@strawberry.input
class SourceInput:
    """Fields accepted when creating a source."""

    repository: PublicID
    kind: str
    ref: str = ""
    path: str = ""


@strawberry.input
class SourcePatch:
    """Fields accepted when updating a source."""

    id: PublicID
    kind: str | None = strawberry.UNSET
    ref: str | None = strawberry.UNSET
    path: str | None = strawberry.UNSET


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


@strawberry.type
class VCSConsoleQuery:
    """Admin VCS inventory queries."""

    vcs_bridges: OffsetPaginated[VcsBridgeType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    vcs_bridge: VcsBridgeType | None = detail(
        VcsBridgeType,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    repositories: OffsetPaginated[RepositoryType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    repository: RepositoryType | None = detail(
        RepositoryType,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    sources: OffsetPaginated[SourceType] = strawberry_django.offset_paginated(
        filters=SourceFilter,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    source: SourceType | None = detail(
        SourceType,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    templates: OffsetPaginated[TemplateType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    template: TemplateType | None = detail(
        TemplateType,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def search_repositories(self, vcs_bridge_id: PublicID, query: str) -> list[RepoCandidate]:
        """Return host repositories matching ``query`` for the add typeahead."""

        vcs = resolve_action_target(
            VcsBridge,
            vcs_bridge_id,
            reason="integrate.graphql.search_repositories",
        )
        with system_context(reason="integrate.graphql.search_repositories"):
            return [_repo_candidate(descriptor) for descriptor in vcs.search_repositories(query)]


@strawberry.type
class VcsBridgeCreateMutation:
    """Admin create for a VCS bridge child, validating backend-owned fields."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_vcs_bridge(self, data: VcsBridgeInput) -> VcsBridgeType:
        """Create a VCS child row directly."""

        vendor = resolve_action_target(
            Vendor,
            data.vendor,
            reason="integrate.graphql.vcs_bridge.create.vendor",
        )
        owner = _user_from_public_id(data.owner)
        credential = (
            None
            if data.credential is None
            else resolve_action_target(
                Credential,
                data.credential,
                reason="integrate.graphql.vcs_bridge.create.credential",
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
                    reason="integrate.graphql.vcs_bridge.create.account",
                )
            )
        )
        attrs: dict[str, Any] = {
            "vendor": vendor,
            "owner": owner,
            "backend_class": VcsBridge.impl_key_for(
                "backend_class",
                None if data.backend_class is strawberry.UNSET else data.backend_class,
                default="local",
            ),
            "webhook_secret": data.webhook_secret,
        }
        if credential is not None:
            attrs["credential"] = credential
        if account is not strawberry.UNSET:
            attrs["account"] = account
        if data.config is not strawberry.UNSET:
            attrs["config"] = data.config
        if data.status not in (strawberry.UNSET, None):
            attrs["status"] = data.status
        with system_context(reason="integrate.graphql.vcs_bridge.create"), transaction.atomic():
            bridge = VcsBridge.objects.create(**attrs)
        return cast(VcsBridgeType, bridge)


@strawberry.type
class VcsBridgeUpdateMutation:
    """Admin update for a VCS bridge child, validating backend-owned fields."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_vcs_bridge(self, data: VcsBridgePatch) -> VcsBridgeType:
        """Update a VCS child row, rematerializing backend defaults on backend change."""

        bridge = resolve_action_target(
            VcsBridge,
            data.id,
            reason="integrate.graphql.vcs_bridge.update",
        )
        provided: set[str] = set()
        backend_changed = False
        with system_context(reason="integrate.graphql.vcs_bridge.update"), transaction.atomic():
            if data.vendor is not strawberry.UNSET:
                bridge.vendor = resolve_action_target(
                    Vendor,
                    data.vendor,
                    reason="integrate.graphql.vcs_bridge.update.vendor",
                )
                provided.add("vendor")
            if data.owner is not strawberry.UNSET:
                bridge.owner = _user_from_public_id(data.owner)
                provided.add("owner")
            if data.credential is not strawberry.UNSET:
                bridge.credential = (
                    None
                    if data.credential is None
                    else resolve_action_target(
                        Credential,
                        data.credential,
                        reason="integrate.graphql.vcs_bridge.update.credential",
                    )
                )
                provided.add("credential")
            if data.account is not strawberry.UNSET:
                bridge.account = (
                    None
                    if data.account is None
                    else resolve_action_target(
                        ExternalAccount,
                        data.account,
                        reason="integrate.graphql.vcs_bridge.update.account",
                    )
                )
                provided.add("account")
            if data.backend_class is not strawberry.UNSET:
                backend_changed = bridge.set_impl_key("backend_class", data.backend_class, default="local")
            if data.status is not strawberry.UNSET:
                bridge.status = data.status
                provided.add("status")
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


_VCS_BRIDGE_MUTATION = crud(
    VcsBridgeType,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="vcs_bridge",
    write_context="integrate.graphql.vcs_bridge",
)
"""Admin VCS bridge CRUD: webhook_secret is write-only; written elevated."""

_SOURCE_MUTATION = crud(
    SourceType,
    create=SourceInput,
    update=SourcePatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="source",
    write_context="integrate.graphql.source",
)
"""Admin source CRUD: FK input resolves via strawberry-django; written elevated."""

_REPOSITORY_MUTATION = crud(
    RepositoryType,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="repository",
    write_context="integrate.graphql.repository",
)
"""Admin repository delete: rows arrive via ``addRepository``/``discoverRepositories``."""


@strawberry.type
class VCSActionMutation:
    """Operational actions on a VCS bridge and its inventory."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def add_repository(self, vcs_bridge_id: PublicID, name: str) -> RepositoryType:
        """Inventory one repository by its host ``name`` (a picked typeahead result)."""

        vcs = resolve_action_target(VcsBridge, vcs_bridge_id, reason="integrate.graphql.add_repository")
        with system_context(reason="integrate.graphql.add_repository"):
            return cast(RepositoryType, vcs.import_repository(name))

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def discover_repositories(self, vcs_bridge_id: PublicID, org: str = "") -> ActionResult:
        """Inventory every repository the account exposes (bulk import; prunes vanished)."""

        vcs = resolve_action_target(VcsBridge, vcs_bridge_id, reason="integrate.graphql.discover_repositories")
        with system_context(reason="integrate.graphql.discover_repositories"):
            count = vcs.discover_repositories(org=org)
        return ActionResult(ok=True, message=f"Inventoried {count} repository(ies).")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def sync_vcs_bridge(self, id: PublicID) -> ActionResult:
        """Refresh every repository's sources for one VCS bridge now."""

        vcs = resolve_action_target(VcsBridge, id, reason="integrate.graphql.sync_vcs_bridge")
        now = timezone.now()
        with system_context(reason="integrate.graphql.sync_vcs_bridge"):
            vcs.mark_sync_started(now=now)
            try:
                result = vcs.sync()
            except Exception as error:  # noqa: BLE001 — sync failure is the result, not a 500
                vcs.record_sync_error(error, now=now)
                return ActionResult(ok=False, message=f"Sync failed: {error}")
            vcs.record_sync(result, now=now)
        return ActionResult(ok=True, message=f"Synced {result} item(s).")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def refresh_source(self, id: PublicID) -> ActionResult:
        """Re-enumerate one source's output rows now."""

        source = resolve_action_target(Source, id, reason="integrate.graphql.refresh_source")
        with system_context(reason="integrate.graphql.refresh_source"):
            count = source.refresh()
        return ActionResult(ok=True, message=f"Synced {count} item(s).")


# Extracted with an explicit annotation: a bare homogeneous list of two
# AngeeNode-decorated types infers as ``list[type[AngeeNode]]`` and trips mypy's
# invariance check; ``list[type]`` widens it. (iam's inline lists are heterogeneous,
# so they don't hit this.)
_CONSOLE_TYPES: list[type] = [
    OAuthClientType,
    CredentialOAuthClientType,
    ExternalAccountType,
    CredentialType,
    ConnectableAccount,
    OAuthStartPayload,
    ConnectAccountResult,
    ConnectIntegrationResult,
    UnlinkAccountResult,
    RevealedCredentialSecret,
    VendorType,
    IntegrationType,
    _integration_aggregates.aggregate_type,
    _integration_aggregates.grouped_type,
    _integration_aggregates.grouped_result_type,
    _integration_aggregates.group_key_type,
    WebhookSubscriptionType,
    VcsBridgeType,
    RepositoryType,
    SourceType,
    TemplateType,
    RepoCandidate,
]

schemas = {
    "public": {
        "query": [IntegrateConnectionsQuery],
        "mutation": [ConnectionMutation, IntegrationCredentialMutation],
        "types": [
            CredentialOAuthClientType,
            ExternalAccountType,
            CredentialType,
            ConnectableAccount,
            OAuthStartPayload,
            ConnectAccountResult,
            ConnectIntegrationResult,
            UnlinkAccountResult,
            VendorType,
            IntegrationType,
            UserType,
        ],
    },
    "console": {
        # The impl-picker lookup (Integration.impl_class / VcsBridge.backend_class /
        # OAuthClient.provider_type live here); a generic framework query contributed
        # where its models do.
        "query": [ConsoleImplChoicesQuery, IntegrateConnectionConsoleQuery, IntegrateConsoleQuery, VCSConsoleQuery],
        "mutation": [
            _OAUTH_CLIENT_MUTATION,
            IntegrateExternalAccountMutation,
            IntegrateCredentialMutation,
            ConnectionMutation,
            _VENDOR_MUTATION,
            IntegrationCreateMutation,
            _INTEGRATION_MUTATION,
            _WEBHOOK_MUTATION,
            VcsBridgeCreateMutation,
            VcsBridgeUpdateMutation,
            _VCS_BRIDGE_MUTATION,
            _SOURCE_MUTATION,
            _REPOSITORY_MUTATION,
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
