"""OIDC state records stored in Django's cache."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from angee.iam.oidc.errors import INVALID_STATE, OidcFlowError

_DEFAULT_STATE_TTL_SECONDS = 600
_CACHE_PREFIX = "angee.iam.oidc.state:"


class StateFlow(StrEnum):
    """Which redirect mutation may consume one OIDC state token.

    A token minted for a login must not complete a link (and vice versa);
    ``identity._validate_state_record`` fails closed when the flow does not
    match, so a leaked state cannot be replayed across flows.
    """

    LOGIN = "login"
    LINK = "link"


@dataclass(frozen=True, slots=True)
class StateRecord:
    """Cached data needed to complete one OIDC redirect."""

    oauth_client_id: str
    redirect_uri: str
    user_id: str | None
    nonce: str
    code_verifier: str | None
    created_at: datetime
    next_path: str = ""
    flow: StateFlow = StateFlow.LOGIN


def issue(
    oauth_client: object,
    redirect_uri: str,
    *,
    user_id: str | None = None,
    next_path: str = "",
    flow: StateFlow = StateFlow.LOGIN,
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
        next_path=next_path,
        flow=flow,
    )
    cache.set(_cache_key(state_token), record, timeout=_state_ttl_seconds())
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


def _state_ttl_seconds() -> int:
    """Return the configured lifetime for one OIDC state record."""

    return int(getattr(settings, "ANGEE_IAM_OIDC_STATE_TTL", _DEFAULT_STATE_TTL_SECONDS))
