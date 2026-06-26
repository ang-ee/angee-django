"""Single-use redirect state records stored in Django's cache.

Shared by every browser redirect flow: account-connect (OAuth) and login/link
(OIDC). State is keyed on the ``OAuthClient`` the picker selected; the OIDC layer
loads that client's ``oidc`` refinement when completing a login.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from angee.integrate.oauth.errors import INVALID_STATE, OAuthFlowError

_DEFAULT_STATE_TTL_SECONDS = 600
_CACHE_PREFIX = "angee.integrate.oauth.state:"


class StateFlow(StrEnum):
    """Which redirect mutation may consume one state token.

    A token minted for a login must not complete a link/connect (and vice versa);
    the completion path fails closed when the flow does not match, so a leaked
    state cannot be replayed across flows.
    """

    LOGIN = "login"
    LINK = "link"
    CONNECT = "connect"


@dataclass(frozen=True, slots=True)
class StateRecord:
    """Cached data needed to complete one OAuth/OIDC redirect."""

    oauth_client_id: str
    redirect_uri: str
    user_id: str | None
    nonce: str
    code_verifier: str | None
    created_at: datetime
    next_path: str = ""
    flow: StateFlow = StateFlow.LOGIN
    integration_id: str = ""


def issue(
    oauth_client: object,
    redirect_uri: str,
    *,
    user_id: str | None = None,
    next_path: str = "",
    flow: StateFlow = StateFlow.LOGIN,
    integration_id: str = "",
) -> tuple[str, StateRecord]:
    """Create and cache one single-use redirect state record."""

    state_token = secrets.token_urlsafe(32)
    record = StateRecord(
        oauth_client_id=str(getattr(oauth_client, "sqid", getattr(oauth_client, "pk", ""))),
        redirect_uri=redirect_uri,
        user_id=user_id,
        nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(64) if getattr(oauth_client, "supports_pkce", False) else None,
        created_at=timezone.now(),
        next_path=next_path,
        flow=flow,
        integration_id=integration_id,
    )
    cache.set(_cache_key(state_token), record, timeout=_state_ttl_seconds())
    return state_token, record


def consume(state_token: str) -> StateRecord:
    """Return and remove one cached state record."""

    key = _cache_key(state_token)
    record = cache.get(key)
    if not isinstance(record, StateRecord):
        raise OAuthFlowError(INVALID_STATE, 400)
    cache.delete(key)
    return record


def _cache_key(state_token: str) -> str:
    """Return the cache key for one opaque state token."""

    return f"{_CACHE_PREFIX}{state_token}"


def _state_ttl_seconds() -> int:
    """Return the configured lifetime for one redirect state record."""

    return int(getattr(settings, "ANGEE_INTEGRATE_OAUTH_STATE_TTL", _DEFAULT_STATE_TTL_SECONDS))
