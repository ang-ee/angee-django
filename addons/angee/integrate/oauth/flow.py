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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from django.apps import apps
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme

from angee.base.models import instance_from_public_id
from angee.integrate.oauth import state
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.errors import INVALID_STATE, OAuthFlowError

_SESSION_OAUTH_CLIENT_PREFIX = "angee.integrate.oauth.flow:"


@dataclass(frozen=True, slots=True)
class OAuthStart:
    """Browser redirect-flow start data shared by OAuth connect and OIDC login."""

    authorize_url: str
    state: str
    mode: str
    redirect_uri: str


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


def start(
    request: HttpRequest,
    oauth_client: Any,
    redirect_uri: str,
    *,
    user_id: str | None = None,
    next_path: str = "/",
    flow: state.StateFlow = state.StateFlow.CONNECT,
    integration_id: str = "",
    authorize_url_builder: Callable[[Any, str, state.StateRecord, str], str] | None = None,
) -> OAuthStart:
    """Issue state and return the browser redirect facts for one OAuth/OIDC flow."""

    state_token, record, effective_redirect_uri, mode = issue_flow(
        request,
        oauth_client,
        redirect_uri,
        user_id=user_id,
        next_path=next_path,
        flow=flow,
        integration_id=integration_id,
    )
    builder = authorize_url_builder or _default_authorize_url
    return OAuthStart(
        authorize_url=builder(oauth_client, state_token, record, effective_redirect_uri),
        state=state_token,
        mode=mode,
        redirect_uri=effective_redirect_uri,
    )


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
    if record.flow != expected_flow or record.oauth_client_id != oauth_client_id or record.redirect_uri != redirect_uri:
        raise OAuthFlowError(INVALID_STATE, 400)
    return record


def enabled_oauth_client(oauth_client_sqid: str) -> Any:
    """Return one enabled OAuth client addressed by sqid, or raise."""

    queryset = _oauth_client_model().objects.system_context(reason="integrate.oauth.flow.oauth_client")
    oauth_client = instance_from_public_id(queryset.model, oauth_client_sqid, queryset=queryset)
    if oauth_client is None or not oauth_client.is_enabled:
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


def _default_authorize_url(
    oauth_client: Any,
    state_token: str,
    record: state.StateRecord,
    redirect_uri: str,
) -> str:
    """Return the standard OAuth authorize URL for one started flow."""

    return OAuthClientProtocol(oauth_client).authorize_url(
        state=state_token,
        redirect_uri=redirect_uri,
        scopes=oauth_client.default_scope_values,
        code_challenge=pkce_challenge(record.code_verifier),
    )
