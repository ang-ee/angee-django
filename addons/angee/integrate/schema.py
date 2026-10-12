"""GraphQL schema contributions for Angee integrations.

Owns the admin console surface for the third-party ``Vendor`` catalogue (moved
here from iam) and the first-class ``Integration`` an integration runs over. The
console is platform-admin gated, so ``Integration``'s REBAC-guarded relations
(credential/account from iam) are safe to expose — the const-admin reaches every
related row.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from functools import partial
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch
from django.utils import timezone
from django.utils.text import capfirst
from django.utils.translation import gettext as _
from rebac import MissingActorError, PermissionDenied, system_context
from strawberry import auto
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.base.errors import exception_text
from angee.base.identity import public_id_of
from angee.graphql.actions import (
    ActionResult,
    action_guard,
    action_target,
    authorized_action_target,
    resolve_action_target,
)
from angee.graphql.capabilities import PermissionsExtension, held_permissions
from angee.graphql.data import (
    AngeeHasuraWriteBackend,
    declared_hasura_resource_fields,
    hasura_model_resource,
    public_pk_decoder,
)
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata, delete_by_public_id
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.impl import ImplChoice, can_read_impl_choices
from angee.graphql.impl import impl_choices as resolve_impl_choices
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.graphql.writes import instance_for_write, write_queryset
from angee.iam.identity import user_from_public_id as _user_from_public_id
from angee.iam.identity import user_principal as _user_principal
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.permissions import is_platform_admin
from angee.iam.permissions import request_from_info as _request
from angee.iam.permissions import session_user as _session_user
from angee.iam.schema import UserType
from angee.integrate import connect as _connect
from angee.integrate.credentials import CredentialKind
from angee.integrate.errors import IntegrationError
from angee.integrate.models import Bridge, IntegrationCreateMode, IntegrationLifecycle
from angee.integrate.oauth import flow, state
from angee.integrate.oauth.errors import CLIENT_NOT_CONFIGURED, INVALID_STATE, PROVIDER_UNAVAILABLE, OAuthFlowError
from angee.integrate.oauth.registrations import OAuthRegistrationExtension
from angee.integrate.queue import queue_bridge_sync
from angee.integrate.registry import models_with
from angee.integrate.states import ConflictKeep, SyncFailure, SyncFailureKind, SyncFailureStatus

logger = logging.getLogger(__name__)

Vendor = apps.get_model("integrate", "Vendor")
Integration = apps.get_model("integrate", "Integration")
OAuthClient = apps.get_model("integrate", "OAuthClient")
ExternalAccount = apps.get_model("integrate", "ExternalAccount")
Credential = apps.get_model("integrate", "Credential")
WebhookSubscription = apps.get_model("integrate", "WebhookSubscription")
SyncStream = apps.get_model("integrate", "SyncStream")
RecordLink = apps.get_model("integrate", "RecordLink")
User = get_user_model()
_OPEN_FAILURE_LINKS = Prefetch(
    "links",
    queryset=RecordLink.objects.with_open_failures(),
    to_attr="open_failure_links",
)


@strawberry.type
class ConsoleImplChoicesQuery:
    """Administrator metadata with explicit model-owned author access."""

    @strawberry.field
    def impl_choices(self, info: strawberry.Info, model: str, field: str) -> list[ImplChoice]:
        """Return registry choices under the administrator or owning model's policy."""
        actor = _session_user(info)
        if not is_platform_admin(actor) and not can_read_impl_choices(model, field, actor):
            raise PermissionDenied("Implementation choices are not readable by this actor.")
        return resolve_impl_choices(model, field)


strawberry.enum(IntegrationCreateMode)


@strawberry.type
class IntegrationCapability:
    """One installed, actor-creatable concrete Integration capability."""

    resource: str
    label: str
    icon: str | None
    create_mode: IntegrationCreateMode


@strawberry.type
class ConsoleIntegrationCapabilitiesQuery:
    """Runtime integration capabilities visible to the console actor."""

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def integration_capabilities(self, info: strawberry.Info) -> list[IntegrationCapability]:
        """Return installed capability owners with an implemented create ingress."""

        capabilities: list[IntegrationCapability] = []
        exposed = _exposed_model_labels(info)
        for model in Integration.concrete_child_models():
            if model._meta.label not in exposed:
                continue
            mode = model.integration_create_mode
            if mode is None:
                continue
            try:
                model.objects.check_create()
            except MissingActorError, PermissionDenied:
                continue
            capabilities.append(
                IntegrationCapability(
                    resource=model._meta.label,
                    label=capfirst(str(model._meta.verbose_name)),
                    icon=None,
                    create_mode=mode,
                )
            )
        return capabilities


@strawberry.enum
class ConcreteIntegrationTargetState(StrEnum):
    """Permission-safe resolution state for an Integration parent row."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"


@strawberry.type
class ConcreteIntegrationTarget:
    """Authorized concrete resource identity behind an Integration parent."""

    state: ConcreteIntegrationTargetState
    resource: str | None = None
    id: str | None = None


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
    ``password``, ``app_keys`` reads ``app_id`` + ``app_secret``. ``user``
    defaults to the calling admin.
    """

    name: str
    kind: str
    user: PublicID | None = None
    api_key: str = ""
    private_key: str = ""
    username: str = ""
    password: str = ""
    app_id: str = ""
    app_secret: str = ""


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


def _exposed_model_labels(info: strawberry.Info) -> set[str]:
    """Return model resources carried by this exact composed GraphQL schema."""

    return {resource.model_label for resource in getattr(info.schema, "angee_resources", ())}


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
    for field in CredentialKind(data.kind).handler.input_material_fields():
        if not hasattr(data, field):
            raise ValueError(f"Cannot create a credential of kind {data.kind!r}.")
        material[field] = getattr(data, field)
    return material


def integration_create_attrs(
    data: Any,
    *,
    reason: str,
) -> dict[str, Any]:
    """Resolve inherited ``Integration`` create fields from GraphQL public ids."""

    credential = (
        None
        if data.credential is None
        else resolve_action_target(Credential, data.credential, reason=f"{reason}.credential")
    )
    account = (
        strawberry.UNSET
        if data.account is strawberry.UNSET
        else (
            None
            if data.account is None
            else resolve_action_target(ExternalAccount, data.account, reason=f"{reason}.account")
        )
    )
    attrs: dict[str, Any] = {
        "vendor": resolve_action_target(Vendor, data.vendor, reason=f"{reason}.vendor"),
        "owner": _user_from_public_id(data.owner),
    }
    if hasattr(data, "display_name"):
        attrs["display_name"] = data.display_name
    if credential is not None:
        attrs["credential"] = credential
    if account is not strawberry.UNSET:
        attrs["account"] = account
    if data.lifecycle not in (strawberry.UNSET, None):
        attrs["lifecycle"] = IntegrationLifecycle.from_value(data.lifecycle)
    return attrs


def apply_integration_patch_fields(
    target: Any,
    data: Any,
    *,
    reason: str,
    ignore_null_lifecycle: bool = False,
) -> set[str]:
    """Apply inherited ``Integration`` patch fields and return caller-save names.

    Lifecycle is guarded by ``Integration.set_lifecycle()``, which eagerly saves
    through the transition hook. It is therefore deliberately omitted from the
    returned set so child update callers do not re-emit the state write.
    Credential attachment and lifecycle transitions save eagerly, before unsaved
    patch values are set.
    """

    provided: set[str] = set()
    if data.credential is not strawberry.UNSET:
        credential = (
            None
            if data.credential is None
            else resolve_action_target(Credential, data.credential, reason=f"{reason}.credential")
        )
        target.attach_credential(credential)
    if data.lifecycle is not strawberry.UNSET and (data.lifecycle is not None or not ignore_null_lifecycle):
        lifecycle = IntegrationLifecycle.from_value(data.lifecycle)
        target.set_lifecycle(lifecycle)
    if hasattr(data, "display_name") and data.display_name is not strawberry.UNSET:
        target.display_name = data.display_name or ""
        provided.add("display_name")
    if data.vendor is not strawberry.UNSET:
        target.vendor = resolve_action_target(Vendor, data.vendor, reason=f"{reason}.vendor")
        provided.add("vendor")
    if data.owner is not strawberry.UNSET:
        target.owner = _user_from_public_id(data.owner)
        provided.add("owner")
    if data.account is not strawberry.UNSET:
        target.account = (
            None
            if data.account is None
            else resolve_action_target(ExternalAccount, data.account, reason=f"{reason}.account")
        )
        provided.add("account")
    return provided


def save_provided_fields(target: Any, provided: set[str]) -> None:
    """Persist provided fields once, skipping the save when a transition already did all work."""

    if provided:
        target.save(update_fields={*provided, "updated_at"})


def _concrete_integration_target(info: strawberry.Info, user: Any, resource: str, id: PublicID) -> Any:
    """Resolve one exposed concrete child through the actor's write scope."""

    exposed = _exposed_model_labels(info)
    model = next(
        (
            child
            for child in Integration.concrete_child_models()
            if child._meta.label == resource and child._meta.label in exposed
        ),
        None,
    )
    if model is None:
        raise PermissionDenied("Integration capability is unavailable.")
    target = instance_for_write(model, id)
    if target is None:
        raise PermissionDenied("Integration capability is unavailable.")
    if target.owner_id != user.pk:
        raise PermissionDenied("Integration does not belong to the current user.")
    return target


def _attach_completed_integration(info: strawberry.Info, integration_sqid: str, user: Any, credential: Any) -> None:
    """Attach a freshly connected credential to the integration named in OAuth state."""

    if not integration_sqid:
        return
    with system_context(reason="integrate.graphql.connect_integration.complete"):
        integration = Integration.objects.filter(sqid=integration_sqid).first()
    if integration is None or integration.owner_id != user.pk:
        raise OAuthFlowError(INVALID_STATE, 400)
    if credential.user_id != user.pk:
        raise PermissionDenied("Credential does not belong to the current user.")
    exposed = _exposed_model_labels(info)
    integrity, _authorized = integration.concrete_children(actor=user, exposed_model_labels=exposed)
    if len(integrity) != 1:
        raise OAuthFlowError(INVALID_STATE, 400)
    child = integrity[0]
    try:
        target = _concrete_integration_target(info, user, child._meta.label, PublicID(public_id_of(child)))
    except (PermissionDenied, ValueError) as error:
        raise OAuthFlowError(INVALID_STATE, 400) from error
    if credential.pk != target.credential_id or target.lifecycle == IntegrationLifecycle.DISCONNECTED:
        target.attach_credential(credential)


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
    integration.require_connect(user)
    if oauth_client.configuration_state != "ready":
        raise OAuthFlowError(
            CLIENT_NOT_CONFIGURED,
            400,
            f"OAuth client is not fully configured ({oauth_client.configuration_state}).",
        )
    credential = Credential.objects.live_oauth_for_user(user, oauth_client)
    if credential is not None:
        if credential.user_id != user.pk:
            raise PermissionDenied("Credential does not belong to the current user.")
        integration.attach_credential(credential)
        return ConnectIntegrationResult(integration=cast("ConnectedIntegrationType", integration), attached=True)
    if not redirect_uri:
        raise OAuthFlowError("redirect_uri_required", 400, "OAuth redirect URI is required.")
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
    def connect_integration(
        self,
        info: strawberry.Info,
        resource: str,
        id: PublicID,
        redirect_uri: str = "",
        next: str = "/",
    ) -> ConnectIntegrationResult:
        """Attach OAuth to one explicit authorized concrete integration child."""

        user = _session_user(info)
        try:
            integration = _concrete_integration_target(info, user, resource, id)
            oauth_client = integration.connect_oauth_client(capfirst(str(integration._meta.verbose_name)))
            return connect_integration_target(
                info, integration, oauth_client, redirect_uri=redirect_uri, next_path=next
            )
        except OAuthFlowError as error:
            return ConnectIntegrationResult(error=error.public_message, error_code=error.code)

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
        """Fetch OIDC metadata and fill this client's blank endpoints.

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
                return ActionResult(ok=False, message="Set an OIDC metadata discovery URL first.")
            try:
                discovery = oauth_client.discover_endpoints()
            except OAuthFlowError as error:
                return ActionResult(ok=False, message=error.public_message)
            except Exception:  # noqa: BLE001 — provider diagnostics stay outside the action payload.
                return ActionResult(ok=False, message="OIDC metadata discovery failed.")
            oauth_client.save()
        issuer = discovery.get("issuer") if isinstance(discovery, dict) else None
        return ActionResult(ok=True, message=f"Discovered endpoints for {issuer or 'provider'}.")

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
                oauth_client, code=code, state_token=state, redirect_uri=redirect_uri
            )
            _attach_completed_integration(info, result.integration_id, result.user, result.credential)
        except OAuthFlowError as error:
            if error.transient:
                error = OAuthFlowError(PROVIDER_UNAVAILABLE, 503)
            return ConnectAccountResult(error=error.public_message, error_code=error.code)
        except IntegrationError as error:
            if error.transient:
                outage = OAuthFlowError(PROVIDER_UNAVAILABLE, 503)
                return ConnectAccountResult(error=outage.public_message, error_code=outage.code)
            return ConnectAccountResult(error=error.public_message, error_code="connection_refused")
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

        ``Credential.check_disconnect()`` validates the model invariant before
        deletion. The login addon's model contribution vetoes removing a user's
        last sign-in account by raising a coded :class:`ValidationError`, surfaced here
        as a typed error rather than a 500.
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
                Credential.objects.prepare_disconnect(credential)
                ExternalAccount.objects.revoke_owner(external_account, user)
                deleted, _details = Credential.objects.filter(pk=credential.pk).with_action("delete").delete()
            return UnlinkAccountResult(ok=deleted > 0)
        except ValidationError as error:
            return UnlinkAccountResult(ok=False, error=exception_text(error), error_code=error.code)


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

        with transaction.atomic():
            return delete_by_public_id(
                ExternalAccount,
                str(id),
                reason="integrate.graphql.external_account.delete",
                confirm=confirm,
                before_delete=revoke,
                queryset=write_queryset(ExternalAccount),
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
            except OAuthFlowError as error:
                return ActionResult(ok=False, message=error.public_message)
            except ValueError:
                return ActionResult(ok=False, message="The credential cannot be refreshed; reconnect it.")
        return ActionResult(ok=True, message="Token refreshed.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_credential(self, info: strawberry.Info, data: CredentialInput) -> CredentialType:
        """Create one provider-less credential, dispatching material by ``kind``."""

        user = _session_user(info) if data.user is None else _user_from_public_id(data.user)
        credential = Credential.objects.create_local_credential(
            user, kind=data.kind, name=data.name, material=_credential_material(data)
        )
        return cast(CredentialType, credential)

    @strawberry.mutation(name="delete_credential", permission_classes=_ADMIN_PERMISSION_CLASSES)
    def delete_credential(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Delete the credential, then best-effort revoke remotely after commit."""

        def prepare_delete(credential: Any) -> None:
            Credential.objects.prepare_disconnect(credential)

        with transaction.atomic():
            return delete_by_public_id(
                Credential,
                str(id),
                reason="integrate.graphql.credential.delete",
                confirm=confirm,
                before_delete=prepare_delete,
                queryset=write_queryset(Credential),
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


def _credential_projection(name: str, info: Any) -> Any:
    return Integration.credential_projection_expressions()[f"_connection_{name}"]


def _oauth_projection(name: str, info: Any) -> Any:
    return Integration.oauth_projection_expressions()[name]


def _connection_credential(info: Any) -> Prefetch:
    """Batch the credential owner's current facts without projecting its secrets."""

    del info
    return Prefetch(
        "credential",
        queryset=Credential.objects.connection_health(),
        to_attr="_connection_credential",
    )


@strawberry.type
class IntegrationConnectionMixin:
    """Connection admissions consume batched authority and credential-owned health."""

    @strawberry_django.field(
        only=["owner_id", "vendor_id", "concrete_type_id", "credential_id"],
        extensions=[PermissionsExtension(("write",))],
        prefetch_related=[_connection_credential],
        annotate={
            **{name: partial(_oauth_projection, name) for name in ("_connection_vendor_slug", "_connection_impl_key")},
        },
    )
    def can_connect(self) -> bool:
        """Project the model's owner-scoped OAuth Connect admission."""

        return bool(cast(Any, self).can_connect(write_access="write" in held_permissions(cast(Any, self), ("write",))))

    @strawberry_django.field(
        only=[
            "owner_id",
            "lifecycle",
            "credential_id",
            "discovery_retry_at",
            "discovery_generation",
            "discovery_completed_at",
        ],
        extensions=[PermissionsExtension(("resume",), model=Integration)],
        prefetch_related=[_connection_credential],
    )
    def can_resume(self) -> bool:
        """Project the model's admission to resume its current grant."""

        return bool(cast(Any, self).can_resume())

    @strawberry_django.field(
        only=[
            "owner_id",
            "lifecycle",
            "credential_id",
            "discovery_retry_at",
            "discovery_generation",
            "discovery_completed_at",
        ],
        extensions=[PermissionsExtension(("retry_discovery",), model=Integration)],
        prefetch_related=[_connection_credential],
    )
    def can_retry_discovery(self) -> bool:
        """Project the model's admission to retry settled discovery."""

        return bool(cast(Any, self).can_retry_discovery())

    @strawberry_django.field(
        only=["credential_id"],
        extensions=[PermissionsExtension(("write",))],
        annotate={
            f"_connection_{name}": partial(_credential_projection, name)
            for name in ("status", "expires_at", "last_refresh_status")
        },
    )
    def credential_status(self) -> str | None:
        """Credential health is visible only to actors who may write this integration."""

        if "write" not in held_permissions(cast(Any, self), ("write",)):
            return None
        return str(cast(Any, self).credential_status)

    @strawberry_django.field(
        only=["credential_id"],
        extensions=[PermissionsExtension(("write",))],
        prefetch_related=[_connection_credential],
    )
    def is_reconnect_required(self) -> bool | None:
        """Project consent health only to actors who may write this integration."""

        if "write" not in held_permissions(cast(Any, self), ("write",)):
            return None
        return bool(cast(Any, self).is_reconnect_required)

    @strawberry_django.field(only=["discovery_retry_at"])
    def discovery_pending(self) -> bool:
        return bool(cast(Any, self).discovery_pending)

    @strawberry_django.field(only=["discovery_retry_at", "discovery_generation", "discovery_completed_at"])
    def discovery_ready(self) -> bool:
        return bool(cast(Any, self).discovery_ready)


@strawberry.type
class IntegrationOAuthMixin:
    """Registration readiness for capabilities with a declared implementation."""

    @strawberry_django.field(
        only=["vendor_id", "concrete_type_id"],
        annotate={
            name: partial(_oauth_projection, name) for name in ("_connection_vendor_slug", "_connection_impl_key")
        },
    )
    def is_oauth_connectable(self) -> bool:
        """Read public registration readiness without resolving protected relations."""

        return bool(cast(Any, self).is_oauth_connectable())


@strawberry.type
class IntegrationLabelMixin(IntegrationConnectionMixin):
    """Project Integration identity, credential health and saved-stream visibility.

    Compose alongside the node base, e.g. ``class ChannelType(IntegrationLabelMixin,
    AngeeNode)``, to surface the operator label (falling back to ``Vendor
    (lifecycle)``) and annotated ``stream_count`` on every ``Integration`` child
    type. A ``@strawberry.type`` (not an interface): merges fields into the concrete
    type without adding a GraphQL interface to the SDL.
    """

    @strawberry_django.field(only=["display_name", "vendor", "lifecycle"], prefetch_related=["vendor"])
    def display_name(self) -> str:
        """Return the operator label, falling back to the vendor-derived one."""

        return cast(Any, self).record_display_label

    @strawberry_django.field(only=["concrete_type_id"])
    def kind(self) -> str:
        """Return the concrete model's human name from Django metadata."""

        concrete_type_id = cast(Any, self).concrete_type_id
        model = ContentType.objects.get_for_id(concrete_type_id).model_class() if concrete_type_id else Integration
        return capfirst(str(model._meta.verbose_name)) if model is not None else "Integration"

    @strawberry_django.field(annotate=Count("sync_streams", distinct=True))
    def stream_count(self) -> int:
        """Count retained streams through the same Integration permission owner."""

        return cast(int, cast(Any, self).stream_count)


@strawberry.type
class BridgeSyncStatusMixin:
    """Project live sync status shared by all ``Bridge`` child resources."""

    @strawberry_django.field(name="is_syncing", only=["id"])
    def is_syncing(self) -> bool:
        """Return whether a worker currently holds this bridge's live sync lock."""

        return bool(cast(Any, self).is_syncing)

    @strawberry_django.field(name="sync_stage", only=["id", "sync_stage", "sync_run_id"])
    def sync_stage(self) -> Bridge.SyncStage:
        """Reconcile direct workers against their lock; retained runs settle durably."""

        return cast(Any, self).effective_sync_stage


@strawberry.type
class BridgeTypeMixin(IntegrationLabelMixin, IntegrationOAuthMixin, BridgeSyncStatusMixin):
    """Project the persisted fields shared by every ``Bridge`` child type."""

    owner: UserType | None
    backend_class: auto
    lifecycle: auto
    runtime_status: auto
    config: strawberry.scalars.JSON
    last_sync_completed_at: auto
    last_sync_items: auto
    last_sync_summary: strawberry.scalars.JSON
    sync_error: auto
    sync_progress: strawberry.scalars.JSON
    created_at: auto
    updated_at: auto


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
    owner: UserType | None
    lifecycle: auto
    runtime_status: auto
    last_used_at: auto
    last_used_status: auto
    use_count_24h: auto
    error_count_24h: auto
    last_error: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["concrete_type_id"])
    def concrete_type(self) -> str | None:
        """Return the concrete content type key used by collection grouping."""

        return str(cast(Any, self).concrete_type_id) if cast(Any, self).concrete_type_id else None

    @classmethod
    def get_queryset(cls, queryset: Any, info: strawberry.Info) -> Any:
        """Batch authorized concrete children for root and nested projections."""

        return queryset.with_concrete_children(
            actor=_session_user(info),
            exposed_model_labels=_exposed_model_labels(info),
        )

    @strawberry_django.field(only=["concrete_type_id"])
    def concrete_target(self, info: strawberry.Info) -> ConcreteIntegrationTarget:
        """Return this parent's one authorized concrete child without leaking denied rows."""

        actor = _session_user(info)
        exposed = _exposed_model_labels(info)
        integrity, authorized = cast(Any, self).concrete_children(
            actor=actor,
            exposed_model_labels=exposed,
        )
        if len(integrity) == 1 and len(authorized) == 1:
            child = authorized[0]
            return ConcreteIntegrationTarget(
                state=ConcreteIntegrationTargetState.AVAILABLE,
                resource=child._meta.label,
                id=str(public_id_of(child)),
            )
        if len(integrity) > 1 and len(authorized) == len(integrity):
            return ConcreteIntegrationTarget(state=ConcreteIntegrationTargetState.AMBIGUOUS)
        return ConcreteIntegrationTarget(state=ConcreteIntegrationTargetState.UNAVAILABLE)


@strawberry_django.type(Integration)
class ConnectedIntegrationType(IntegrationLabelMixin, AngeeNode):
    """Public projection of a current-user integration connection."""

    vendor: VendorType
    credential: ConnectedCredentialType | None
    account: ConnectedExternalAccountType | None
    owner: UserType | None
    lifecycle: auto
    runtime_status: auto
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


strawberry.enum(cast(Any, SyncFailureKind))
strawberry.enum(cast(Any, SyncFailureStatus))


@strawberry.experimental.pydantic.type(model=SyncFailure, all_fields=True)
class SyncFailureType:
    """A failure inherits its stream/link's read and write permissions."""

    details: JSON

    @strawberry.field
    def slot(self) -> int | None:
        return cast(SyncFailure, cast(Any, self)._original_model).slot

    @strawberry.field
    def is_open(self) -> bool:
        return cast(SyncFailure, cast(Any, self)._original_model).is_open


@strawberry_django.type(SyncStream)
class SyncStreamType(AngeeNode):
    """Read-only inspection of a bridge partition's retained epoch."""

    display_name = strawberry_django.field(resolver=AngeeNode.display_name, only=["key", "partition"])
    integration: IntegrationType
    key: auto
    partition: auto
    kind: auto
    direction: auto
    snapshot: auto
    generation: auto
    phase: auto
    state: auto
    cursor: JSON = strawberry_django.field(metadata={"angee_widget": "angee.integrate.integrationSyncCursor"})
    cursor_expires_at: auto
    resync_required: auto
    last_advanced_at: auto
    last_reconciled_at: auto
    absence_threshold: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(
        name="openFailureCount",
        only=["inbound"],
        prefetch_related=[_OPEN_FAILURE_LINKS],
    )
    def open_failure_count(self) -> int:
        """Count retained inbound/outbox failures using the batched related rows."""
        return len(cast(Any, self).sync_failures(open_only=True))

    @strawberry_django.field(
        only=["inbound"],
        prefetch_related=[_OPEN_FAILURE_LINKS],
    )
    def failures(self, open_only: bool = True) -> list[SyncFailureType]:
        return [
            cast(Any, SyncFailureType).from_pydantic(row) for row in cast(Any, self).sync_failures(open_only=open_only)
        ]

    @strawberry_django.field(annotate=Count("links", distinct=True))
    def link_count(self) -> int:
        """Count replica identities without multiplying the failure join."""

        return cast(int, cast(Any, self).link_count)


@strawberry_django.type(RecordLink)
class RecordLinkType(AngeeNode):
    """Read-only identity, comparison bases and deletion evidence."""

    display_name = strawberry_django.field(resolver=AngeeNode.display_name, only=["external_key"])
    stream: SyncStreamType
    external_key: auto
    status: auto
    remote_version: auto
    source_hash: auto
    applied_at: auto
    pinned: auto
    outbox_state: auto
    outbox_retry_at: auto
    origin: auto
    last_seen_at: auto
    last_verified_generation: auto
    absence_count: auto
    metadata: JSON
    tombstoned_at: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["stream_id", "inbound", "outbox_last_error"], select_related=["stream"])
    def failures(self, open_only: bool = True) -> list[SyncFailureType]:
        return [cast(Any, SyncFailureType).from_pydantic(row) for row in cast(Any, self).failures(open_only=open_only)]

    @strawberry_django.field(only=["target_content_type_id", "target_object_id"])
    def model_label(self) -> str:
        """Project target identity through the shared record-reference owner."""

        return cast(Any, self).record_model_label

    @strawberry_django.field(only=["target_content_type_id", "target_object_id"])
    def record_id(self) -> PublicID:
        """Return the target public id; navigation rechecks the target's read policy."""

        return PublicID(cast(Any, self).record_public_id)


_SYNC_STREAM_RESOURCE = hasura_model_resource(
    SyncStreamType,
    model=SyncStream,
    name="sync_streams",
    filterable=[
        "id",
        "integration",
        "key",
        "partition",
        "kind",
        "direction",
        "snapshot",
        "phase",
        "state",
        "generation",
        "resync_required",
        "last_advanced_at",
        "last_reconciled_at",
    ],
    sortable=[
        "key",
        "partition",
        "kind",
        "direction",
        "snapshot",
        "phase",
        "state",
        "generation",
        "last_advanced_at",
        "last_reconciled_at",
    ],
    aggregatable=["id"],
    groupable=["key", "partition", "kind", "direction", "snapshot", "phase", "state", "resync_required"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={"integration": public_pk_decoder(Integration)},
)
_RECORD_LINK_RESOURCE = hasura_model_resource(
    RecordLinkType,
    model=RecordLink,
    name="record_links",
    filterable=[
        "id",
        "stream",
        "stream__integration",
        "external_key",
        "status",
        "origin",
        "remote_version",
        "last_seen_at",
    ],
    sortable=["external_key", "last_seen_at", "status", "origin", "remote_version"],
    aggregatable=["id"],
    groupable=["status", "origin"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={"stream": public_pk_decoder(SyncStream)},
)
strawberry.enum(cast(Any, ConflictKeep))


def _authorized_failure(
    info: strawberry.Info,
    stream: PublicID,
    link: PublicID | None,
    slot: int | None,
) -> SyncFailure:
    """Authorize typed owners; the model interprets the selected retained slot."""
    owner = authorized_action_target(info, SyncStream, stream, "write")
    if link is not None:
        epoch = owner
        owner = authorized_action_target(info, RecordLink, link, "write")
        if owner.stream_id != epoch.pk:
            raise ValidationError("The failure link does not belong to the selected stream.")
    for failure in owner.failures():
        if failure.slot == slot:
            return failure
    raise ValidationError("The retained sync failure no longer exists.")


@strawberry.type
class SyncRecordActionMutation:
    """Admin-gated operational transitions on the bridge-owned record protocol."""

    @strawberry.mutation(name="resolveSyncFailure", permission_classes=_ADMIN_PERMISSION_CLASSES)
    @action_guard("Could not resolve the failure.")
    def resolve_sync_failure(
        self,
        info: strawberry.Info,
        stream: PublicID,
        link: PublicID | None = None,
        slot: int | None = None,
        keep: ConflictKeep | None = None,
    ) -> ActionResult:
        """Dispatch resolution through retained state after owner authorization."""

        failure = _authorized_failure(info, stream, link, slot)
        if keep is None:
            failure.resolve()
        else:
            failure.resolve_conflict(keep=keep)
        return ActionResult(ok=True, message=_("Failure resolved."))

    @strawberry.mutation(name="retrySyncFailure", permission_classes=_ADMIN_PERMISSION_CLASSES)
    @action_guard("Could not retry the failure.")
    def retry_sync_failure(
        self,
        info: strawberry.Info,
        stream: PublicID,
        link: PublicID | None = None,
        slot: int | None = None,
    ) -> ActionResult:
        """Make quarantine due now through its retained-state owner."""

        _authorized_failure(info, stream, link, slot).retry()
        return ActionResult(ok=True, message=_("Failure retry requested."))

    @strawberry.mutation(name="pinRecordLink", permission_classes=_ADMIN_PERMISSION_CLASSES)
    @action_guard("Could not pin the record link.")
    def pin_record_link(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Withhold automatic sync through the authorized link owner."""
        authorized_action_target(info, RecordLink, id, "write").pin()
        return ActionResult(ok=True, message=_("Record link pinned."))

    @strawberry.mutation(name="unpinRecordLink", permission_classes=_ADMIN_PERMISSION_CLASSES)
    @action_guard("Could not unpin the record link.")
    def unpin_record_link(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Resume automatic sync through the authorized link owner."""
        authorized_action_target(info, RecordLink, id, "write").unpin()
        return ActionResult(ok=True, message=_("Record link unpinned."))

    @strawberry.mutation(name="resyncSyncStream", permission_classes=_ADMIN_PERMISSION_CLASSES)
    @action_guard("Could not request a stream resync.")
    def resync_sync_stream(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Request the driver-owned baseline transition without running a cycle."""

        stream = authorized_action_target(info, SyncStream, id, "write")
        SyncStream.objects.request_resync(stream)
        return ActionResult(ok=True, message=_("Stream resync requested."))


class IntegrationWriteBackend(AngeeHasuraWriteBackend):
    """Compose native patch authorization with model-owned credential attachment."""

    def update(
        self,
        info: strawberry.Info,
        pk: str,
        data: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> Any:
        fields = dict(data)
        credential_id = fields.pop("credential", strawberry.UNSET)
        with transaction.atomic():
            target = require_instance_for_id(self.model, pk, queryset=self.write_target_queryset())
            target.require_access("write", _session_user(info))
            # Native patching locks the addressed model. Acquire the final child
            # first so parent-addressed patches share the bridge worker order.
            with system_context(reason="integrate.graphql.patch.lock"):
                target.lock_capability()
            instance = super().update(info, pk, fields, expected_revision=expected_revision)
            if credential_id is not strawberry.UNSET:
                credential = (
                    None
                    if credential_id is None
                    else require_instance_for_id(
                        Credential,
                        credential_id,
                        queryset=write_queryset(Credential),
                    )
                )
                instance.attach_credential(credential)
            return instance


_INTEGRATION_RESOURCE = hasura_model_resource(
    IntegrationType,
    model=Integration,
    name="integrations",
    filterable=["id", "display_name", "vendor", "concrete_type", "lifecycle", "runtime_status", "updated_at"],
    sortable=[
        "display_name",
        "vendor",
        "concrete_type",
        "lifecycle",
        "runtime_status",
        "created_at",
        "updated_at",
    ],
    aggregatable=["id"],
    groupable=[
        "concrete_type",
        "concrete_type__model",
        "vendor",
        "vendor__display_name",
        "lifecycle",
        "runtime_status",
    ],
    updatable=["vendor", "credential", "account", "owner"],
    insert=False,
    field_id_decode={
        "vendor": public_pk_decoder(Vendor),
        "owner": public_pk_decoder(User),
        "credential": public_pk_decoder(Credential),
        "account": public_pk_decoder(ExternalAccount),
    },
    get_queryset=partial(IntegrationType.get_queryset, Integration.objects),
    write_backend=IntegrationWriteBackend(
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


@strawberry.type
class IntegrationCredentialMutation:
    """Credential-first attachment to an explicit concrete integration child."""

    @strawberry.mutation
    def attach_integration_credential(
        self,
        info: strawberry.Info,
        resource: str,
        id: PublicID,
        credential: PublicID,
    ) -> ConnectedIntegrationType:
        """Attach an owned credential without creating a neutral parent row."""

        user = _session_user(info)
        target = _concrete_integration_target(info, user, resource, id)
        owned_credential = resolve_action_target(
            Credential,
            credential,
            reason="integrate.graphql.attach_integration_credential.credential",
        )
        if owned_credential.user_id != user.pk:
            raise PermissionDenied("Credential does not belong to the current user.")
        target.attach_credential(owned_credential)
        return cast(ConnectedIntegrationType, target)


@strawberry.type
class IntegrationActionMutation:
    """Operational actions on an integration (sync, connection test)."""

    @strawberry.mutation
    @action_guard("Could not retry connection discovery.")
    def retry_discovery(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Retry discovery through the integration's bounded handshake owner."""

        integration = authorized_action_target(info, Integration, id, "retry_discovery")
        integration.retry_discovery()
        return ActionResult(ok=True, message="Connection discovery requested.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    @action_guard("Could not resume integration.")
    def mark_integration_connected(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Move an integration to CONNECTED through its guarded transition."""

        integration = authorized_action_target(info, Integration, id, "write")
        integration.resume()
        return ActionResult(ok=True, message="Connected integration.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def pause_integration(self, id: PublicID) -> ActionResult:
        """Move an integration to PAUSED through its guarded transition."""

        with action_target(Integration, id, reason="integrate.graphql.pause_integration") as integration:
            integration.pause()
        return ActionResult(ok=True, message="Paused integration.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def mark_integration_disconnected(self, id: PublicID) -> ActionResult:
        """Move an integration to DISCONNECTED through its guarded transition."""

        with action_target(
            Integration,
            id,
            reason="integrate.graphql.mark_integration_disconnected",
        ) as integration:
            integration.disconnect()
        return ActionResult(ok=True, message="Disconnected integration.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def sync_integration(self, id: PublicID) -> ActionResult:
        """Queue every bridge of one integration for sync now."""

        queued = 0
        with action_target(Integration, id, reason="integrate.graphql.sync_integration") as integration:
            now = timezone.now()
            for model in models_with(base=Bridge):
                for bridge in model._default_manager.of_concrete_type().filter(pk=integration.pk).order_by("pk"):
                    queue_bridge_sync(bridge, now=now)
                    queued += 1
        if queued == 0:
            return ActionResult(ok=True, message="No bridges to sync.")
        return ActionResult(ok=True, message=f"Queued {queued} bridge sync(s).")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def test_connection(self, id: PublicID) -> ActionResult:
        """Exercise the integration's connection so the operator sees whether it works.

        Dispatches to the concrete capability's own ``test_connection`` — a
        channel backend logs in to its server, a parent-only integration proves
        its credential — and reports the outcome in band. Only an
        ``IntegrationError`` carries its reason to the operator; any other
        failure is logged here and projected to a bounded message.
        """

        with action_target(Integration, id, reason="integrate.graphql.test_connection") as integration:
            try:
                message = integration.concrete_capability().test_connection()
            except IntegrationError as error:
                return ActionResult(ok=False, message=error.public_message)
            except Exception:  # noqa: BLE001 — vendor diagnostics stay in the log, outside the action payload.
                logger.exception("integration %s: connection test failed", integration.pk)
                return ActionResult(ok=False, message="Connection test failed.")
        return ActionResult(ok=True, message=message)


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


# Extracted with an explicit annotation: a bare homogeneous list of two
# AngeeNode-decorated types infers as ``list[type[AngeeNode]]`` and trips mypy's
# invariance check; ``list[type]`` widens it. (iam's inline lists are heterogeneous,
# so they don't hit this.)
_CONSOLE_TYPES: list[object] = [
    SyncStreamType,
    RecordLinkType,
    SyncFailureType,
    *_SYNC_STREAM_RESOURCE.types,
    *_RECORD_LINK_RESOURCE.types,
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
]

schemas = {
    "public": {
        "extensions": [OAuthRegistrationExtension],
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
        "extensions": [OAuthRegistrationExtension],
        # Concrete impl-picker lookups (VcsBridge.backend_class /
        # OAuthClient.provider_type) live here; a generic framework query contributed
        # where its models do.
        "query": [
            ConsoleImplChoicesQuery,
            ConsoleIntegrationCapabilitiesQuery,
            _OAUTH_CLIENT_RESOURCE.query,
            _EXTERNAL_ACCOUNT_RESOURCE.query,
            _CREDENTIAL_RESOURCE.query,
            _VENDOR_RESOURCE.query,
            _INTEGRATION_RESOURCE.query,
            _WEBHOOK_SUBSCRIPTION_RESOURCE.query,
            _SYNC_STREAM_RESOURCE.query,
            _RECORD_LINK_RESOURCE.query,
        ],
        "mutation": [
            _OAUTH_CLIENT_RESOURCE.mutation,
            _EXTERNAL_ACCOUNT_RESOURCE.mutation,
            _CREDENTIAL_RESOURCE.mutation,
            _VENDOR_RESOURCE.mutation,
            _INTEGRATION_RESOURCE.mutation,
            _WEBHOOK_SUBSCRIPTION_RESOURCE.mutation,
            IntegrateExternalAccountMutation,
            IntegrateCredentialMutation,
            ConnectionMutation,
            IntegrationCredentialMutation,
            IntegrationActionMutation,
            WebhookActionMutation,
            SyncRecordActionMutation,
        ],
        "subscription": [
            changes(Integration, field="integrationChanged"),
        ],
        "types": _CONSOLE_TYPES,
    },
}
"""GraphQL contributions installed by the integrate addon."""
