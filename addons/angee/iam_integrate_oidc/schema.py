"""GraphQL surface for the OIDC login addon.

OIDC, end to end: the public login/link redirect flow + login-provider picker.
It extends ``integrate``'s OAuth (the substrate types, the OAuth protocol, the
browser-flow plumbing) and composes the ``iam`` session — connect-for-API and the
OAuth base stay in ``integrate`` and never reference any of this.
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import login as auth_login
from django.db.models import Q
from rebac import system_context
from strawberry import auto
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.iam.permissions import request_from_info as _request
from angee.iam.permissions import session_user as _session_user
from angee.iam.schema import UserType
from angee.iam_integrate_oidc import identity
from angee.iam_integrate_oidc.protocol import OAuthClientOidcProtocol
from angee.integrate.oauth import flow as oauth_flow
from angee.integrate.oauth import state as oauth_state
from angee.integrate.oauth.errors import CLIENT_NOT_CONFIGURED, OAuthFlowError
from angee.integrate.schema import (
    ConnectedExternalAccountType,
    OAuthClientType,
    OAuthStartPayload,
)

OAuthClient = apps.get_model("integrate", "OAuthClient")


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

    account: ConnectedExternalAccountType | None = None
    user: UserType | None = None
    intent: str = ""
    next: str = "/"
    claims: JSON | None = None
    error: str | None = None
    error_code: str | None = None


def _available_connections(info: strawberry.Info) -> Any:
    """Return enabled, configured, login-capable OAuth clients for the public picker.

    A row is shown only when it can actually start a login: enabled, with a client
    id, login enabled, and usable endpoints — either explicit authorize+token
    endpoints or an OIDC ``discovery_url`` to resolve them from.
    """

    del info
    return (
        cast(Any, OAuthClient.objects)
        .system_context(reason="iam_integrate_oidc.available_connections")
        .filter(is_enabled=True, login_enabled=True)
        .exclude(client_id="")
        .filter(Q(authorize_endpoint__gt="", token_endpoint__gt="") | Q(discovery_url__gt=""))
    )


def _enabled_oidc_oauth_client(oauth_client_sqid: str) -> Any:
    """Return one enabled, OIDC-capable OAuth client addressed by sqid, or raise.

    Raises a typed ``OAuthFlowError`` (surfaced by the start mutation as an error
    payload) when the client is disabled or not login-enabled.
    """

    try:
        oauth_client = oauth_flow.enabled_oauth_client(oauth_client_sqid)
    except ValueError as error:
        raise OAuthFlowError(CLIENT_NOT_CONFIGURED, 400, "OAuth client is not enabled for OIDC.") from error
    if not bool(getattr(oauth_client, "login_enabled", False)):
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
    authorize_url = OAuthClientOidcProtocol(oauth_client).authorize_url(
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
            return OAuthStartPayload(error=error.public_message, error_code=error.code)

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
                auth_login(request, result.user)
        except OAuthFlowError as error:
            return LoginCompletePayload(ok=False, error=error.public_message, error_code=error.code)
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
            return OAuthStartPayload(error=error.public_message, error_code=error.code)

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
            return LinkAccountResult(error=error.public_message, error_code=error.code)
        return LinkAccountResult(
            account=cast(ConnectedExternalAccountType, result.account),
            user=cast(UserType, result.user),
            intent="link",
            next=result.next_path,
            claims=cast(JSON, result.claims),
        )


# --- Admin: direct OAuth-client OIDC fields -------------------------------------


@strawberry_django.type(OAuthClient, name="OAuthClientType", extend=True)
class OAuthClientOidcExtension:
    """Contributes OIDC login fields onto integrate's ``OAuthClientType``.

    The composer has already folded the model extension into the runtime
    ``OAuthClient`` class, so these fields read as native scalar fields.
    """

    issuer: auto
    jwks_uri: auto
    login_enabled: auto
    link_on_email_match: auto
    create_on_login: auto

    @strawberry_django.field(only=["allowed_email_domains"])
    def allowed_email_domains(self) -> list[str]:
        """Return the login domain allow-list."""

        return cast(list[str], cast(Any, self).allowed_email_domain_values)


@strawberry.input(name="OAuthClientInput", extend=True)
class OAuthClientOidcInput:
    """OIDC login write fields added to integrate's OAuth client create input.

    Native Strawberry input extension keeps ``integrate`` OIDC-agnostic while the
    console create mutation receives these fields on its ``OAuthClientInput`` value.
    """

    issuer: str = ""
    jwks_uri: str = ""
    login_enabled: bool = False
    link_on_email_match: bool = False
    create_on_login: bool = False
    allowed_email_domains: list[str] = strawberry.field(default_factory=list)


@strawberry.input(name="OAuthClientPatch", extend=True)
class OAuthClientOidcPatch:
    """OIDC login write fields added to integrate's OAuth client update input."""

    issuer: str | None = strawberry.UNSET
    jwks_uri: str | None = strawberry.UNSET
    login_enabled: bool | None = strawberry.UNSET
    link_on_email_match: bool | None = strawberry.UNSET
    create_on_login: bool | None = strawberry.UNSET
    allowed_email_domains: list[str] | None = strawberry.UNSET


_PUBLIC_TYPES: list[type] = [
    AvailableConnection,
    LoginCompletePayload,
    LinkAccountResult,
    OAuthStartPayload,
    ConnectedExternalAccountType,
    UserType,
]

_CONSOLE_TYPES: list[type] = [*_PUBLIC_TYPES, OAuthClientType]

schemas = {
    "public": {
        "query": [OidcLoginQuery],
        "mutation": [OidcLoginMutation],
        "types": _PUBLIC_TYPES,
    },
    "console": {
        "query": [OidcLoginQuery],
        "mutation": [OidcLoginMutation],
        "types": _CONSOLE_TYPES,
        "type_extensions": [OAuthClientOidcExtension],
        "input_extensions": [OAuthClientOidcInput, OAuthClientOidcPatch],
    },
}
"""GraphQL contributions installed by the OIDC login addon."""
