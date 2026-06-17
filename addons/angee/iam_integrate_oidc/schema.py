"""GraphQL surface for the OIDC login addon.

OIDC, end to end: the public login/link redirect flow + login-provider picker,
and the admin surface for the ``OidcClient`` refinement (CRUD + discovery). It
extends ``integrate``'s OAuth (the substrate types, the OAuth protocol, the
browser-flow plumbing) and composes the ``iam`` session — connect-for-API and the
OAuth base stay in ``integrate`` and never reference any of this.
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.conf import settings
from django.contrib.auth import login as auth_login
from django.db.models import Q
from rebac import system_context
from strawberry import auto, relay
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.base.models import instance_from_public_id
from angee.graphql.actions import ActionResult
from angee.graphql.aggregates import rebac_aggregate_builder
from angee.graphql.crud import crud
from angee.graphql.extension import extends_type
from angee.graphql.node import AngeeNode
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.permissions import request_from_info as _request
from angee.iam.permissions import session_user as _session_user
from angee.iam.schema import UserType
from angee.iam_integrate_oidc import identity
from angee.iam_integrate_oidc.protocol import OidcClientProtocol
from angee.integrate.oauth import flow as oauth_flow
from angee.integrate.oauth import state as oauth_state
from angee.integrate.oauth.errors import CLIENT_NOT_CONFIGURED, OAuthFlowError
from angee.integrate.schema import ExternalAccountType, OAuthClientType, OAuthStartPayload

OAuthClient = apps.get_model("integrate", "OAuthClient")
OidcClient = apps.get_model("iam_integrate_oidc", "OidcClient")


# --- Public login/link flow ------------------------------------------------------


@strawberry.type
class AvailableConnection:
    """Picker-safe OAuth client fields for the public OIDC login picker."""

    @strawberry.field
    def oauth_client_sqid(self) -> strawberry.ID:
        """Return the OAuth client sqid accepted by login/link mutations."""

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

    @strawberry.field
    def is_oidc(self) -> bool:
        """Return whether this connection can run OIDC login/link flows (always true here)."""

        return True


@strawberry.type
class LoginCompletePayload:
    """Result returned by OIDC login completion."""

    ok: bool
    user: UserType | None = None
    intent: str = "login"
    next: str = "/"
    claims: JSON | None = None
    error: str | None = None
    error_code: str | None = None


@strawberry.type
class LinkAccountResult:
    """Result returned by OIDC account-link completion."""

    account: ExternalAccountType | None = None
    user: UserType | None = None
    intent: str = ""
    next: str = "/"
    claims: JSON | None = None
    error: str | None = None
    error_code: str | None = None


def _available_connections(info: strawberry.Info) -> Any:
    """Return enabled, configured, login-capable OIDC clients for the public picker.

    A row is shown only when it can actually start a login: enabled, with a client
    id, an OIDC refinement, and usable endpoints — either explicit authorize+token
    endpoints or an OIDC ``discovery_url`` to resolve them from.
    """

    del info
    return (
        cast(Any, OAuthClient.objects)
        .system_context(reason="iam_integrate_oidc.available_connections")
        .filter(is_enabled=True, oidc__isnull=False)
        .exclude(client_id="")
        .filter(Q(authorize_endpoint__gt="", token_endpoint__gt="") | Q(oidc__discovery_url__gt=""))
    )


def _enabled_oidc_oauth_client(oauth_client_sqid: str) -> Any:
    """Return one enabled, OIDC-capable OAuth client addressed by sqid, or raise.

    Raises a typed ``OAuthFlowError`` (surfaced by the start mutation as an error
    payload) when the client is disabled or has no OIDC refinement.
    """

    try:
        oauth_client = oauth_flow.enabled_oauth_client(oauth_client_sqid)
    except ValueError as error:
        raise OAuthFlowError(CLIENT_NOT_CONFIGURED, 400, "OAuth client is not enabled for OIDC.") from error
    if getattr(oauth_client, "oidc", None) is None:
        raise OAuthFlowError(CLIENT_NOT_CONFIGURED, 400, "OAuth client is not enabled for OIDC.")
    return oauth_client


def _start_login_flow(
    request: Any,
    oauth_client: Any,
    redirect_uri: str,
    *,
    user_id: str | None = None,
    next_path: str = "/",
    flow: oauth_state.StateFlow = oauth_state.StateFlow.LOGIN,
) -> OAuthStartPayload:
    """Issue state and return the OIDC authorize URL for a login or link flow."""

    state_token, record, effective_redirect_uri, mode = oauth_flow.issue_flow(
        request,
        oauth_client,
        redirect_uri,
        user_id=user_id,
        next_path=next_path,
        flow=flow,
    )
    authorize_url = OidcClientProtocol(oauth_client.oidc).authorize_url(
        state=state_token,
        redirect_uri=effective_redirect_uri,
        scopes=oauth_client.default_scope_values,
        nonce=record.nonce,
        code_challenge=oauth_flow.pkce_challenge(record.code_verifier),
    )
    return OAuthStartPayload(
        authorize_url=authorize_url,
        state=state_token,
        mode=mode,
        redirect_uri=effective_redirect_uri,
    )


def _flow_error_message(error: OAuthFlowError) -> str:
    """Return the best safe human message for one OAuth flow error."""

    return error.provider_message or str(error)


def _session_backend(user: Any) -> str:
    """Return the Django auth backend path to store in the login session.

    Django requires an explicit backend when a user did not come from
    ``authenticate()`` and multiple backends are installed. Prefer the non-REBAC
    backend for normal session auth.
    """

    bound = getattr(user, "backend", None)
    if bound:
        return str(bound)
    for path in getattr(settings, "AUTHENTICATION_BACKENDS", ()):
        if "rebac" not in path.lower():
            return str(path)
    return "django.contrib.auth.backends.ModelBackend"


@strawberry.type
class OidcLoginQuery:
    """Public picker of login-capable OIDC providers."""

    available_connections: OffsetPaginated[AvailableConnection] = strawberry_django.offset_paginated(
        resolver=_available_connections,
    )


@strawberry.type
class OidcLoginMutation:
    """OIDC login and authenticated account-link mutations."""

    @strawberry.mutation
    def login_start(
        self,
        info: strawberry.Info,
        oauth_client_sqid: str,
        redirect_uri: str,
        next: str = "/",
    ) -> OAuthStartPayload:
        """Start an OIDC login flow for an enabled login-capable OAuth client."""

        request = _request(info)
        try:
            oauth_client = _enabled_oidc_oauth_client(oauth_client_sqid)
            return _start_login_flow(
                request,
                oauth_client,
                redirect_uri,
                next_path=oauth_flow.coerce_next_path(next, request),
            )
        except OAuthFlowError as error:
            return OAuthStartPayload(error=_flow_error_message(error), error_code=error.code)

    @strawberry.mutation
    def login_complete(
        self,
        info: strawberry.Info,
        code: str,
        state: str,
        redirect_uri: str,
    ) -> LoginCompletePayload:
        """Complete an OIDC login flow and bind the user to the session."""

        request = _request(info)
        try:
            oauth_client = oauth_flow.remembered_oauth_client(request, state)
            result = identity.complete_login(
                oauth_client,
                code=code,
                state_token=state,
                redirect_uri=redirect_uri,
            )
            with system_context(reason="iam_integrate_oidc.login"):
                auth_login(request, result.user, backend=_session_backend(result.user))
        except OAuthFlowError as error:
            return LoginCompletePayload(ok=False, error=_flow_error_message(error), error_code=error.code)
        return LoginCompletePayload(
            ok=True,
            user=cast(UserType, result.user),
            next=result.next_path,
            claims=cast(JSON, result.claims),
        )

    @strawberry.mutation
    def link_account_start(
        self,
        info: strawberry.Info,
        oauth_client_sqid: str,
        redirect_uri: str,
        next: str = "/",
    ) -> OAuthStartPayload:
        """Start an authenticated OIDC account-link flow."""

        user = _session_user(info)
        request = _request(info)
        try:
            oauth_client = _enabled_oidc_oauth_client(oauth_client_sqid)
            return _start_login_flow(
                request,
                oauth_client,
                redirect_uri,
                user_id=str(user.pk),
                next_path=oauth_flow.coerce_next_path(next, request),
                flow=oauth_state.StateFlow.LINK,
            )
        except OAuthFlowError as error:
            return OAuthStartPayload(error=_flow_error_message(error), error_code=error.code)

    @strawberry.mutation
    def link_account_complete(
        self,
        info: strawberry.Info,
        code: str,
        state: str,
        redirect_uri: str,
    ) -> LinkAccountResult:
        """Complete an authenticated OIDC account-link flow."""

        request = _request(info)
        _session_user(info)
        try:
            oauth_client = oauth_flow.remembered_oauth_client(request, state)
            result = identity.complete_link(
                oauth_client,
                code=code,
                state_token=state,
                redirect_uri=redirect_uri,
            )
        except OAuthFlowError as error:
            return LinkAccountResult(error=_flow_error_message(error), error_code=error.code)
        return LinkAccountResult(
            account=cast(ExternalAccountType, result.account),
            user=cast(UserType, result.user),
            intent="link",
            next=result.next_path,
            claims=cast(JSON, result.claims),
        )


# --- Admin: the OIDC refinement (extends integrate's OAuth client) ---------------


@strawberry_django.type(OidcClient)
class OidcClientType(AngeeNode):
    """Admin projection of an OAuth client's OIDC login refinement."""

    oauth_client: OAuthClientType
    issuer: auto
    discovery_url: auto
    jwks_uri: auto
    link_on_email_match: auto
    create_on_login: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["allowed_email_domains"])
    def allowed_email_domains(self) -> list[str]:
        """Return the login domain allow-list."""

        return cast(list[str], cast(Any, self).allowed_email_domain_values)

    @strawberry_django.field(only=["oauth_client"], select_related=["oauth_client"])
    def oauth_enabled(self) -> bool:
        """Whether the underlying OAuth client is enabled — the provider's effective on/off.

        The OAuth base owns the ``is_enabled`` flag; OIDC surfaces it (the relation is
        select-related, no per-row query) so the admin list and detail show whether a
        login provider is actually live without opening the OAuth client.
        """

        return bool(cast(Any, self).oauth_client.is_enabled)


# Grouped aggregates for the admin list: fold OIDC providers by their OAuth
# client's enabled flag. ``oauth_client__is_enabled`` is a *to-one* relation-path
# axis — the OAuth base owns the flag, the OIDC refinement groups across the 1:1
# join (no row multiplication; supported since strawberry-django-aggregates 0.5.0).
# Count is the only measure (``id``).
_oidc_aggregates = rebac_aggregate_builder(
    model=OidcClient,
    aggregate_fields=["id"],
    group_by_fields=["oauth_client__is_enabled"],
    pagination_style="offset",
).build()

_AGGREGATE_TYPES: list[type] = [
    _oidc_aggregates.aggregate_type,
    _oidc_aggregates.grouped_type,
    _oidc_aggregates.grouped_result_type,
    _oidc_aggregates.group_key_type,
]


@strawberry.input
class OidcClientInput:
    """Admin-write fields accepted when adding an OIDC refinement to an OAuth client."""

    oauth_client: relay.GlobalID
    issuer: str = ""
    discovery_url: str = ""
    jwks_uri: str = ""
    link_on_email_match: bool = False
    create_on_login: bool = False
    allowed_email_domains: list[str] = strawberry.field(default_factory=list)


@strawberry.input
class OidcClientPatch:
    """Admin-write fields accepted when updating an OIDC refinement."""

    id: relay.GlobalID
    issuer: str | None = strawberry.UNSET
    discovery_url: str | None = strawberry.UNSET
    jwks_uri: str | None = strawberry.UNSET
    link_on_email_match: bool | None = strawberry.UNSET
    create_on_login: bool | None = strawberry.UNSET
    allowed_email_domains: list[str] | None = strawberry.UNSET


@strawberry.type
class OidcConsoleQuery:
    """Admin OIDC refinement queries."""

    oidc_clients: OffsetPaginated[OidcClientType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    oidc_client: OidcClientType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    oidc_client_aggregate = _oidc_aggregates.aggregate_field
    oidc_client_groups = _oidc_aggregates.group_by_field


_OIDC_CLIENT_MUTATION = crud(
    OidcClientType,
    create=OidcClientInput,
    update=OidcClientPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="oidc_client",
    write_context="iam_integrate_oidc.graphql.oidc_client",
)
"""Admin OIDC-refinement CRUD: ``oauth_client`` FK resolves via strawberry-django."""


@strawberry.type
class OidcClientActionMutation:
    """Operational actions on an OIDC login provider."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def discover_oidc_endpoints(self, id: relay.GlobalID) -> ActionResult:
        """Fetch the provider's OIDC discovery document and fill blank endpoints.

        Addressed by the ``OidcClient`` refinement id (discovery is an OIDC concern);
        the resolved endpoints are persisted across the OAuth client and its
        refinement.
        """

        with system_context(reason="iam_integrate_oidc.graphql.discover_oidc_endpoints"):
            oidc_client = instance_from_public_id(
                OidcClient, id.node_id, queryset=OidcClient._default_manager.all()
            )
            if oidc_client is None:
                raise ValueError(f"OIDC client {id!s} was not found")
            if not str(getattr(oidc_client, "discovery_url", "") or ""):
                return ActionResult(ok=False, message="Set a discovery URL first.")
            try:
                discovery = OidcClientProtocol(oidc_client).ensure_endpoints()
            except Exception as error:  # noqa: BLE001 — surface discovery failure to the operator
                return ActionResult(ok=False, message=f"Discovery failed: {error}")
            oidc_client.oauth_client.save()
            oidc_client.save()
        issuer = discovery.get("issuer") if isinstance(discovery, dict) else None
        return ActionResult(ok=True, message=f"Discovered endpoints for {issuer or 'provider'}.")


@extends_type(OAuthClientType)
@strawberry_django.type(OAuthClient)
class OAuthClientOidcExtension:
    """Contributes the ``oidc`` refinement projection onto integrate's ``OAuthClientType``.

    The GraphQL type-extension that gives the admin console back
    ``oauthClient { oidc { issuer … } }`` without ``integrate`` (which owns
    ``OAuthClientType``) referencing this addon: the composer merges this field onto
    the target after composition, and strawberry-django resolves the reverse 1:1
    relation to ``OidcClientType`` from its model registry.
    """

    oidc: OidcClientType | None


_PUBLIC_TYPES: list[type] = [
    AvailableConnection,
    LoginCompletePayload,
    LinkAccountResult,
    OAuthStartPayload,
    ExternalAccountType,
    UserType,
]

_CONSOLE_TYPES: list[type] = [*_PUBLIC_TYPES, OidcClientType, OAuthClientType, *_AGGREGATE_TYPES]

schemas = {
    "public": {
        "query": [OidcLoginQuery],
        "mutation": [OidcLoginMutation],
        "types": _PUBLIC_TYPES,
    },
    "console": {
        "query": [OidcLoginQuery, OidcConsoleQuery],
        "mutation": [OidcLoginMutation, _OIDC_CLIENT_MUTATION, OidcClientActionMutation],
        "types": _CONSOLE_TYPES,
        "type_extensions": [OAuthClientOidcExtension],
    },
}
"""GraphQL contributions installed by the OIDC login addon."""
