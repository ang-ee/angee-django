"""OIDC state records stored in Django's cache."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime

from django.core.cache import cache
from django.utils import timezone

from angee.iam.oidc.errors import INVALID_STATE, OidcFlowError

STATE_TTL_SECONDS = 600
_CACHE_PREFIX = "angee.iam.oidc.state:"


@dataclass(frozen=True, slots=True)
class StateRecord:
    """Cached data needed to complete one OIDC redirect."""

    oauth_client_id: str
    redirect_uri: str
    user_id: str | None
    nonce: str
    code_verifier: str | None
    created_at: datetime


def issue(
    oauth_client: object,
    redirect_uri: str,
    *,
    user_id: str | None = None,
) -> tuple[str, StateRecord]:
    """Create and cache one single-use OIDC state record."""

    state_token = secrets.token_urlsafe(32)
    record = StateRecord(
        oauth_client_id=str(
            getattr(oauth_client, "sqid", getattr(oauth_client, "pk", ""))
        ),
        redirect_uri=redirect_uri,
        user_id=user_id,
        nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(64)
        if getattr(oauth_client, "supports_pkce", False)
        else None,
        created_at=timezone.now(),
    )
    cache.set(_cache_key(state_token), record, timeout=STATE_TTL_SECONDS)
    return state_token, record


def consume(state_token: str) -> StateRecord:
    """Return and remove one cached state record."""

    key = _cache_key(state_token)
    record = cache.get(key)
    if not isinstance(record, StateRecord):
        raise OidcFlowError(INVALID_STATE, 400)
    cache.delete(key)
    return record


def _cache_key(state_token: str) -> str:
    """Return the cache key for one opaque state token."""

    return f"{_CACHE_PREFIX}{state_token}"
