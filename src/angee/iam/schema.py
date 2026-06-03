"""GraphQL schema contributions for Angee IAM."""

from __future__ import annotations

import base64
import hashlib
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest
from rebac import PermissionDenied, system_context
from rebac.managers import RebacManager
from strawberry import auto, relay
from strawberry.permission import BasePermission

from angee.base.deletion import DeletionPreview
from angee.base.graphql import AngeeNode, OffsetPaginated
from angee.base.graphql.crud import DeletePreview
from angee.iam import identity
from angee.iam.oidc import client as client_module
from angee.iam.oidc import state

try:
    User = apps.get_model("iam", "User")
except LookupError:  # pragma: no cover - source-addon unit tests may not build runtime models.
    User = get_user_model()
Vendor = apps.get_model("iam", "Vendor")
OAuthClient = apps.get_model("iam", "OAuthClient")
ExternalAccount = apps.get_model("iam", "ExternalAccount")
Credential = apps.get_model("iam", "Credential")

_OIDC_SESSION_OAUTH_CLIENT_PREFIX = "angee.iam.oidc.oauth_client:"


@strawberry_django.type(User)
class UserType(AngeeNode):
    """GraphQL projection of an Angee user."""

    username: auto
    first_name: auto
    last_name: auto
    email: auto
    is_staff: auto
    is_active: auto


@strawberry_django.type(Vendor)
class VendorType(AngeeNode):
    """GraphQL projection of an IAM vendor catalogue row."""

    slug: auto
    display_name: auto
    website_url: auto
    icon: auto
    description: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(OAuthClient)
class OAuthClientType(AngeeNode):
    """Admin GraphQL projection of non-secret IAM OAuth client registration."""

    display_name: auto
    vendor: VendorType
    environment: auto
    client_id: auto
    issuer: auto
    authorize_endpoint: auto
    token_endpoint: auto
    revoke_endpoint: auto
    userinfo_endpoint: auto
    jwks_uri: auto
    discovery_url: auto
    is_oidc: auto
    is_enabled: auto
    supports_refresh: auto
    refresh_rotates: auto
    supports_pkce: auto
    max_refresh_age_seconds: auto
    link_on_email_match: auto
    create_on_login: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["default_scopes"])
    def default_scopes(self) -> list[str]:
        """Return the configured default OAuth scopes."""

        return _string_list(cast(Any, self).default_scopes)

    @strawberry_django.field(only=["scopes_catalogue"])
    def scopes_catalogue(self) -> list[str]:
        """Return the advertised OAuth scopes."""

        return _string_list(cast(Any, self).scopes_catalogue)

    @strawberry_django.field(only=["allowed_email_domains"])
    def allowed_email_domains(self) -> list[str]:
        """Return the login domain allow-list."""

        return _string_list(cast(Any, self).allowed_email_domains)


@strawberry_django.type(ExternalAccount)
class ExternalAccountType(AngeeNode):
    """GraphQL projection of a linked external identity."""

    vendor: VendorType
    external_id: auto
    email: auto
    display_name: auto
    avatar_url: auto
    status: auto
    last_used_at: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Credential)
class CredentialType(AngeeNode):
    """GraphQL projection of credential health without secret values."""

    kind: auto
    status: auto
    expires_at: auto
    last_refresh_at: auto
    last_refresh_status: auto
    oauth_client: OAuthClientType
    external_account: ExternalAccountType | None
    created_at: auto
    updated_at: auto


@strawberry.type
class AvailableConnectionVendor:
    """Picker-safe vendor fields for public connection selection."""

    slug: str
    display_name: str
    icon: str


@strawberry.type
class AvailableConnection:
    """Picker-safe OAuth client fields for public connection selection."""

    @strawberry.field
    def oauth_client_sqid(self) -> strawberry.ID:
        """Return the OAuth client sqid accepted by connection mutations."""

        return strawberry.ID(str(cast(Any, self).sqid))

    @strawberry.field
    def oauth_client_display_name(self) -> str:
        """Return the OAuth client display label."""

        return str(cast(Any, self).display_name)

    @strawberry.field
    def is_oidc(self) -> bool:
        """Return whether this connection can run OIDC login/link flows."""

        return bool(cast(Any, self).is_oidc)

    @strawberry.field
    def vendor(self) -> AvailableConnectionVendor:
        """Return the picker-safe vendor projection."""

        with system_context(reason="iam.graphql.available_connection_vendor"):
            vendor = Vendor.objects.get(pk=cast(Any, self).vendor_id)
        return AvailableConnectionVendor(
            slug=str(vendor.slug),
            display_name=str(vendor.display_name),
            icon=str(vendor.icon),
        )


@strawberry.type
class OidcStartPayload:
    """Result returned by OIDC login/link start mutations."""

    authorize_url: str
    state: str


@strawberry.type
class LoginPayload:
    """Result returned by the session login mutation."""

    ok: bool
    user: UserType | None = None


@strawberry.input
class VendorInput:
    """Fields accepted when creating a vendor."""

    slug: str
    display_name: str
    website_url: str = ""
    icon: str = ""
    description: str = ""


@strawberry.input
class VendorPatch:
    """Fields accepted when updating a vendor."""

    id: relay.GlobalID
    slug: str | None = strawberry.UNSET
    display_name: str | None = strawberry.UNSET
    website_url: str | None = strawberry.UNSET
    icon: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET


@strawberry.input
class OAuthClientInput:
    """Non-secret fields accepted when creating an OAuth client."""

    vendor: relay.GlobalID
    display_name: str
    client_id: str
    environment: str = "prod"
    issuer: str = ""
    authorize_endpoint: str = ""
    token_endpoint: str = ""
    revoke_endpoint: str = ""
    userinfo_endpoint: str = ""
    jwks_uri: str = ""
    discovery_url: str = ""
    is_oidc: bool = False
    is_enabled: bool = True
    scopes_catalogue: list[str] = strawberry.field(default_factory=list)
    default_scopes: list[str] = strawberry.field(default_factory=list)
    supports_refresh: bool = True
    refresh_rotates: bool = False
    supports_pkce: bool = True
    max_refresh_age_seconds: int | None = None
    link_on_email_match: bool = False
    create_on_login: bool = False
    allowed_email_domains: list[str] = strawberry.field(default_factory=list)


@strawberry.input
class OAuthClientPatch:
    """Non-secret fields accepted when updating an OAuth client."""

    id: relay.GlobalID
    vendor: relay.GlobalID | None = strawberry.UNSET
    display_name: str | None = strawberry.UNSET
    client_id: str | None = strawberry.UNSET
    environment: str | None = strawberry.UNSET
    issuer: str | None = strawberry.UNSET
    authorize_endpoint: str | None = strawberry.UNSET
    token_endpoint: str | None = strawberry.UNSET
    revoke_endpoint: str | None = strawberry.UNSET
    userinfo_endpoint: str | None = strawberry.UNSET
    jwks_uri: str | None = strawberry.UNSET
    discovery_url: str | None = strawberry.UNSET
    is_oidc: bool | None = strawberry.UNSET
    is_enabled: bool | None = strawberry.UNSET
    scopes_catalogue: list[str] | None = strawberry.UNSET
    default_scopes: list[str] | None = strawberry.UNSET
    supports_refresh: bool | None = strawberry.UNSET
    refresh_rotates: bool | None = strawberry.UNSET
    supports_pkce: bool | None = strawberry.UNSET
    max_refresh_age_seconds: int | None = strawberry.UNSET
    link_on_email_match: bool | None = strawberry.UNSET
    create_on_login: bool | None = strawberry.UNSET
    allowed_email_domains: list[str] | None = strawberry.UNSET


class PlatformAdminPermission(BasePermission):
    """Allow only actors that reach IAM's const-backed platform admin role."""

    message = "Platform admin permission required."
    error_extensions = {"code": "PERMISSION_DENIED"}

    def has_permission(
        self,
        source: Any,
        info: strawberry.Info,
        **kwargs: Any,
    ) -> bool:
        """Return whether the request user has platform-admin reach."""

        del source, kwargs
        user = getattr(_request(info), "user", None)
        if not _is_authenticated(user):
            return False
        user_pk = cast(Any, user).pk
        if isinstance(User._default_manager, RebacManager):
            return cast(bool, User.objects.filter(pk=user_pk).exists())
        return bool(getattr(user, "is_superuser", False))


_ADMIN_PERMISSION_CLASSES: list[type[BasePermission]] = [PlatformAdminPermission]


def _available_connections(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return enabled OAuth clients for the public connection picker."""

    del info
    return cast(
        QuerySet[Any],
        OAuthClient.objects.system_context(reason="iam.graphql.available_connections").filter(is_enabled=True),
    )


def _my_connected_accounts(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return this session user's credential-backed connected accounts."""

    user = _session_user(info)
    return cast(
        QuerySet[Any],
        Credential.objects.filter(
            user=user,
            external_account__isnull=False,
        ).rebac_select_related("external_account"),
    )


def _console_oauth_clients(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return admin-visible OAuth clients with guarded vendor joins."""

    del info
    return cast(QuerySet[Any], OAuthClient.objects.rebac_select_related("vendor"))


def _console_external_accounts(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return admin-visible external accounts with guarded vendor joins."""

    del info
    return cast(QuerySet[Any], ExternalAccount.objects.rebac_select_related("vendor"))


def _console_credentials(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return admin-visible credential health with guarded FK joins."""

    del info
    return cast(
        QuerySet[Any],
        Credential.objects.rebac_select_related(
            "oauth_client",
            "oauth_client__vendor",
            "external_account",
            "external_account__vendor",
        ),
    )


@strawberry.type
class IAMQuery:
    """Session-backed IAM queries."""

    @strawberry.field
    def current_user(self, info: strawberry.Info) -> UserType | None:
        """Return the authenticated session user, if any."""

        user = getattr(_request(info), "user", None)
        if isinstance(user, AnonymousUser) or not getattr(
            user,
            "is_authenticated",
            False,
        ):
            return None
        return cast(UserType, user)


@strawberry.type
class IAMConnectionsQuery:
    """Public connection picker and self-service account queries."""

    available_connections: OffsetPaginated[AvailableConnection] = strawberry_django.offset_paginated(
        resolver=_available_connections,
    )
    my_connected_accounts: OffsetPaginated[CredentialType] = strawberry_django.offset_paginated(
        resolver=_my_connected_accounts,
    )


@strawberry.type
class IAMConsoleQuery:
    """Admin IAM connection queries."""

    vendors: OffsetPaginated[VendorType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    vendor: VendorType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    oauth_clients: OffsetPaginated[OAuthClientType] = strawberry_django.offset_paginated(
        resolver=_console_oauth_clients,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    oauth_client: OAuthClientType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    external_accounts: OffsetPaginated[ExternalAccountType] = strawberry_django.offset_paginated(
        resolver=_console_external_accounts,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    credential_health: OffsetPaginated[CredentialType] = strawberry_django.offset_paginated(
        resolver=_console_credentials,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )


@strawberry.type
class IAMMutation:
    """Session-backed IAM mutations."""

    @strawberry.mutation
    def login(
        self,
        info: strawberry.Info,
        username: str,
        password: str,
    ) -> LoginPayload:
        """Authenticate credentials and bind the user to the session."""

        request = _request(info)
        user = authenticate(
            request,
            username=username,
            password=password,
        )
        if user is None:
            return LoginPayload(ok=False)
        with system_context(reason="iam.login"):
            auth_login(request, user)
        return LoginPayload(ok=True, user=cast(UserType, user))

    @strawberry.mutation
    def logout(self, info: strawberry.Info) -> bool:
        """Clear the current session."""

        auth_logout(_request(info))
        return True

    @strawberry.mutation
    def login_start(
        self,
        info: strawberry.Info,
        oauth_client_sqid: str,
        redirect_uri: str,
    ) -> OidcStartPayload:
        """Start an OIDC login flow for an enabled login-capable OAuth client."""

        oauth_client = _enabled_oidc_oauth_client(oauth_client_sqid)
        return _start_oidc_flow(_request(info), oauth_client, redirect_uri)

    @strawberry.mutation
    def login_complete(
        self,
        info: strawberry.Info,
        code: str,
        state: str,
        redirect_uri: str,
    ) -> LoginPayload:
        """Complete an OIDC login flow and bind the user to the session."""

        request = _request(info)
        oauth_client = _oauth_client_for_remembered_state(request, state)
        user = identity.complete_login(
            oauth_client,
            code=code,
            state_token=state,
            redirect_uri=redirect_uri,
        )
        with system_context(reason="iam.oidc.login"):
            auth_login(request, user)
        return LoginPayload(ok=True, user=cast(UserType, user))

    @strawberry.mutation
    def link_account_start(
        self,
        info: strawberry.Info,
        oauth_client_sqid: str,
        redirect_uri: str,
    ) -> OidcStartPayload:
        """Start an authenticated OIDC account-link flow."""

        _session_user(info)
        oauth_client = _enabled_oidc_oauth_client(oauth_client_sqid)
        return _start_oidc_flow(_request(info), oauth_client, redirect_uri)

    @strawberry.mutation
    def link_account_complete(
        self,
        info: strawberry.Info,
        code: str,
        state: str,
        redirect_uri: str,
    ) -> ExternalAccountType:
        """Complete an authenticated OIDC account-link flow."""

        request = _request(info)
        user = _session_user(info)
        oauth_client = _oauth_client_for_remembered_state(request, state)
        account = identity.complete_link(
            oauth_client,
            user,
            code=code,
            state_token=state,
            redirect_uri=redirect_uri,
        )
        return cast(ExternalAccountType, account)

    @strawberry.mutation
    def unlink_account(
        self,
        info: strawberry.Info,
        external_account_sqid: str,
    ) -> bool:
        """Remove this session user's credential link to an external account."""

        user = _session_user(info)
        deleted, _details = (
            Credential.objects.filter(
                user=user,
                external_account__sqid=external_account_sqid,
            )
            .with_action("delete")
            .delete()
        )
        return deleted > 0


@strawberry.type
class IAMVendorMutation:
    """Admin mutations for the vendor catalogue."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_vendor(self, data: VendorInput) -> VendorType:
        """Create one vendor after the console admin gate passes."""

        with system_context(reason="iam.graphql.vendor.create"):
            return cast(VendorType, Vendor.objects.create(**_input_values(data, _VENDOR_FIELDS)))

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_vendor(self, data: VendorPatch) -> VendorType:
        """Update one vendor after the console admin gate passes."""

        with system_context(reason="iam.graphql.vendor.update"):
            vendor = _resolve_public_id(Vendor, data.id)
            _assign_values(vendor, _input_values(data, _VENDOR_FIELDS))
            vendor.save()
        return cast(VendorType, vendor)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def delete_vendor(self, id: relay.GlobalID) -> DeletePreview:
        """Delete one vendor after the console admin gate passes."""

        return _delete_instance(Vendor, id, reason="iam.graphql.vendor.delete")


@strawberry.type
class IAMOAuthClientMutation:
    """Admin mutations for non-secret OAuth/OIDC client registration."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_oauth_client(self, data: OAuthClientInput) -> OAuthClientType:
        """Create one OAuth client after the console admin gate passes."""

        with system_context(reason="iam.graphql.oauth_client.create"):
            values = _input_values(data, _OAUTH_CLIENT_FIELDS)
            values["vendor"] = _resolve_public_id(Vendor, data.vendor)
            return cast(OAuthClientType, OAuthClient.objects.create(**values))

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_oauth_client(self, data: OAuthClientPatch) -> OAuthClientType:
        """Update one OAuth client after the console admin gate passes."""

        with system_context(reason="iam.graphql.oauth_client.update"):
            oauth_client = _resolve_public_id(OAuthClient, data.id)
            values = _input_values(data, _OAUTH_CLIENT_FIELDS)
            if data.vendor is not strawberry.UNSET and data.vendor is not None:
                values["vendor"] = _resolve_public_id(Vendor, data.vendor)
            _assign_values(oauth_client, values)
            oauth_client.save()
        return cast(OAuthClientType, oauth_client)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def delete_oauth_client(self, id: relay.GlobalID) -> DeletePreview:
        """Delete one OAuth client after the console admin gate passes."""

        return _delete_instance(OAuthClient, id, reason="iam.graphql.oauth_client.delete")


def _request(info: strawberry.Info) -> HttpRequest:
    """Return the Django request from Strawberry's context."""

    return cast(HttpRequest, info.context.request)


def _session_user(info: strawberry.Info) -> Any:
    """Return the authenticated session user or raise a REBAC denial."""

    user = getattr(_request(info), "user", None)
    if not _is_authenticated(user):
        raise PermissionDenied("Authentication required.")
    return user


def _is_authenticated(user: Any) -> bool:
    """Return whether ``user`` is a real authenticated session user."""

    return not isinstance(user, AnonymousUser) and bool(
        getattr(user, "is_authenticated", False)
    )


def _enabled_oidc_oauth_client(oauth_client_sqid: str) -> Any:
    """Return one enabled OIDC OAuth client addressed by sqid, or raise."""

    oauth_client = (
        OAuthClient.objects.system_context(reason="iam.graphql.oidc_oauth_client")
        .select_related("vendor")
        .filter(sqid=oauth_client_sqid)
        .first()
    )
    if oauth_client is None or not oauth_client.is_enabled or not oauth_client.is_oidc:
        raise ValueError("OAuth client is not enabled for OIDC.")
    return oauth_client


def _start_oidc_flow(
    request: HttpRequest,
    oauth_client: Any,
    redirect_uri: str,
) -> OidcStartPayload:
    """Issue state, remember its OAuth client, and return the authorize URL."""

    state_token, record = state.issue(oauth_client, redirect_uri)
    _remember_flow_oauth_client(request, state_token, oauth_client)
    authorize_url = client_module.build_authorize_url(
        oauth_client,
        state=state_token,
        nonce=record.nonce,
        redirect_uri=redirect_uri,
        scopes=_string_list(oauth_client.default_scopes),
        code_challenge=_pkce_challenge(record.code_verifier),
    )
    return OidcStartPayload(authorize_url=authorize_url, state=state_token)


def _remember_flow_oauth_client(
    request: HttpRequest,
    state_token: str,
    oauth_client: Any,
) -> None:
    """Bind one OIDC state token to an OAuth client sqid in the browser session."""

    session = cast(Any, request).session
    session[f"{_OIDC_SESSION_OAUTH_CLIENT_PREFIX}{state_token}"] = str(oauth_client.sqid)
    session.modified = True


def _oauth_client_for_remembered_state(
    request: HttpRequest,
    state_token: str,
) -> Any:
    """Return the session-bound OAuth client for one pending OIDC state token."""

    session = cast(Any, request).session
    key = f"{_OIDC_SESSION_OAUTH_CLIENT_PREFIX}{state_token}"
    oauth_client_sqid = session.pop(key, None)
    session.modified = True
    if not oauth_client_sqid:
        raise ValueError("OIDC state is not bound to this session.")
    return _enabled_oidc_oauth_client(str(oauth_client_sqid))


def _pkce_challenge(code_verifier: str | None) -> str | None:
    """Return the S256 PKCE challenge for one verifier."""

    if not code_verifier:
        return None
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _string_list(value: object) -> list[str]:
    """Return ``value`` as a string list for JSON-backed scope fields."""

    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


_VENDOR_FIELDS = frozenset({"slug", "display_name", "website_url", "icon", "description"})
_OAUTH_CLIENT_FIELDS = frozenset(
    {
        "display_name",
        "environment",
        "client_id",
        "issuer",
        "authorize_endpoint",
        "token_endpoint",
        "revoke_endpoint",
        "userinfo_endpoint",
        "jwks_uri",
        "discovery_url",
        "is_oidc",
        "is_enabled",
        "scopes_catalogue",
        "default_scopes",
        "supports_refresh",
        "refresh_rotates",
        "supports_pkce",
        "max_refresh_age_seconds",
        "link_on_email_match",
        "create_on_login",
        "allowed_email_domains",
    }
)


def _input_values(data: object, fields: frozenset[str]) -> dict[str, Any]:
    """Return set Strawberry input values for one field set."""

    values: dict[str, Any] = {}
    for name in fields:
        value = getattr(data, name, strawberry.UNSET)
        if value is strawberry.UNSET:
            continue
        values[name] = value
    return values


def _assign_values(instance: Any, values: dict[str, Any]) -> None:
    """Assign values to a Django model instance."""

    for name, value in values.items():
        setattr(instance, name, value)


def _resolve_public_id(model: Any, global_id: relay.GlobalID) -> Any:
    """Resolve one Relay global ID to a model instance."""

    instance = model.from_public_id(global_id.node_id)
    if instance is None:
        raise ValueError(f"{model._meta.object_name} {global_id.node_id!r} was not found")
    return instance


def _delete_instance(model: Any, global_id: relay.GlobalID, *, reason: str) -> DeletePreview:
    """Delete one instance and return the standard deletion preview payload."""

    with system_context(reason=reason), transaction.atomic():
        instance = _resolve_public_id(model, global_id)
        preview = DeletionPreview.from_instance(instance)
        if not preview.has_blockers:
            instance.delete()
    return DeletePreview.from_domain(preview)


schemas = {
    "public": {
        "query": [IAMQuery, IAMConnectionsQuery],
        "mutation": [IAMMutation],
        "types": [
            UserType,
            VendorType,
            OAuthClientType,
            ExternalAccountType,
            CredentialType,
            AvailableConnection,
            AvailableConnectionVendor,
            OidcStartPayload,
        ],
    },
    "console": {
        "query": [IAMQuery, IAMConsoleQuery],
        "mutation": [IAMMutation, IAMVendorMutation, IAMOAuthClientMutation],
        "types": [
            UserType,
            VendorType,
            OAuthClientType,
            ExternalAccountType,
            CredentialType,
            AvailableConnection,
            AvailableConnectionVendor,
            OidcStartPayload,
        ],
    },
}
"""GraphQL contributions installed by the IAM addon."""
