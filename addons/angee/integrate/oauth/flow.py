"""Browser redirect-flow orchestration shared by connect and OIDC login.

The single owner of the OAuth/OIDC browser round-trip plumbing: issuing and
session-binding single-use state, resolving the proposed redirect, the PKCE
challenge, and re-loading the enabled ``OAuthClient`` a pending state names. The
connect flow (this addon) and the login/link flow (``iam_integrate_oidc``) both
build their start/complete on these; only the authorize-URL and claim handling
differ between them (OAuth vs OIDC protocol).
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, cast

from django.apps import apps
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme
from rebac import system_context

from angee.base.models import instance_from_public_id
from angee.integrate.oauth import state
from angee.integrate.oauth.errors import INVALID_STATE, OAuthFlowError

_SESSION_OAUTH_CLIENT_PREFIX = "angee.integrate.oauth.flow:"


def _oauth_client_model() -> Any:
    """Return the concrete OAuth client model."""

    return apps.get_model("integrate", "OAuthClient")


def issue_flow(
    request: HttpRequest,
    oauth_client: Any,
    redirect_uri: str,
    *,
    user_id: str | None = None,
    next_path: str = "/",
    flow: state.StateFlow = state.StateFlow.CONNECT,
    integration_id: str = "",
) -> tuple[str, state.StateRecord, str, str]:
    """Issue and session-bind state for one redirect flow.

    Returns ``(state_token, record, effective_redirect_uri, mode)``. The client
    owns whether the browser-proposed redirect works or a manual paste is needed;
    the effective redirect is what we issue, sign, and exchange.
    """

    effective_redirect_uri, mode = oauth_client.resolve_connect_redirect(redirect_uri)
    state_token, record = state.issue(
        oauth_client,
        effective_redirect_uri,
        user_id=user_id,
        next_path=next_path,
        flow=flow,
        integration_id=integration_id,
    )
    session = cast(Any, request).session
    session[f"{_SESSION_OAUTH_CLIENT_PREFIX}{state_token}"] = str(oauth_client.sqid)
    session.modified = True
    return state_token, record, effective_redirect_uri, mode


def remembered_oauth_client(request: HttpRequest, state_token: str) -> Any:
    """Return the enabled, session-bound OAuth client for one pending state token."""

    session = cast(Any, request).session
    key = f"{_SESSION_OAUTH_CLIENT_PREFIX}{state_token}"
    oauth_client_sqid = session.pop(key, None)
    session.modified = True
    if not oauth_client_sqid:
        raise OAuthFlowError(INVALID_STATE, 400)
    return enabled_oauth_client(str(oauth_client_sqid))


def consume_validated_state(
    oauth_client: Any,
    state_token: str,
    redirect_uri: str,
    *,
    expected_flow: state.StateFlow,
) -> state.StateRecord:
    """Consume one state record and fail closed when it does not match this flow."""

    record = state.consume(state_token)
    oauth_client_id = str(getattr(oauth_client, "sqid", getattr(oauth_client, "pk", "")))
    if (
        record.flow != expected_flow
        or record.oauth_client_id != oauth_client_id
        or record.redirect_uri != redirect_uri
    ):
        raise OAuthFlowError(INVALID_STATE, 400)
    return record


def enabled_oauth_client(oauth_client_sqid: str) -> Any:
    """Return one enabled OAuth client addressed by sqid, or raise."""

    oauth_client = (
        _oauth_client_model()
        .objects.system_context(reason="integrate.graphql.oauth_client")
        .filter(sqid=oauth_client_sqid)
        .first()
    )
    if oauth_client is None or not oauth_client.is_enabled:
        raise ValueError("OAuth client is not enabled.")
    return oauth_client


def oauth_client_from_id(oauth_client_id: Any) -> Any:
    """Return the OAuth client addressed by one GraphQL global id, or raise."""

    model = _oauth_client_model()
    with system_context(reason="integrate.graphql.oauth_client.lookup"):
        oauth_client = instance_from_public_id(
            model,
            oauth_client_id.node_id,
            queryset=model._default_manager.all(),
        )
    if oauth_client is None:
        raise ValueError(f"OAuth client {oauth_client_id!s} was not found.")
    return oauth_client


def enabled_oauth_client_from_id(oauth_client_id: Any) -> Any:
    """Return one enabled OAuth client addressed by a Relay id, or raise."""

    oauth_client = oauth_client_from_id(oauth_client_id)
    if not oauth_client.is_enabled:
        raise ValueError("OAuth client is not enabled.")
    return oauth_client


def coerce_next_path(value: str, request: HttpRequest) -> str:
    """Return a same-host post-flow redirect path, defaulting unsafe values to ``/``."""

    if not value:
        return "/"
    if url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return value
    return "/"


def pkce_challenge(code_verifier: str | None) -> str | None:
    """Return the S256 PKCE challenge for one verifier."""

    if not code_verifier:
        return None
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
