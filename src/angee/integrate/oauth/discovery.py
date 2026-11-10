"""Cached OIDC metadata discovery document fetches."""

from __future__ import annotations

import hashlib
from typing import Any

import httpx2
from django.conf import settings
from django.core.cache import cache

from angee.integrate.errors import RequestNotSentError
from angee.integrate.http import HttpClient
from angee.integrate.oauth.client import OUTBOUND_HEADERS
from angee.integrate.oauth.errors import DISCOVERY_FAILED, OAuthFlowError

_DISCOVERY_CACHE_PREFIX = "angee.integrate.oauth.discovery:"


def discovery_document(discovery_url: str) -> dict[str, Any]:
    """Return cached or freshly fetched OIDC metadata for ``discovery_url``."""

    cache_key = _discovery_cache_key(discovery_url)
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    try:
        discovery = HttpClient().get_json(discovery_url, headers=OUTBOUND_HEADERS, allow_private=True)
    except (RequestNotSentError, httpx2.TransportError) as exc:
        raise OAuthFlowError(DISCOVERY_FAILED, 503) from exc
    except Exception as exc:
        raise OAuthFlowError(DISCOVERY_FAILED, 400) from exc
    if not isinstance(discovery, dict):
        raise OAuthFlowError(DISCOVERY_FAILED, 400)
    cache.set(cache_key, discovery, timeout=settings.ANGEE_OIDC_DISCOVERY_TTL)
    return discovery


def _discovery_cache_key(discovery_url: str) -> str:
    """Return the cache key for one discovery URL."""

    digest = hashlib.sha256(discovery_url.encode("utf-8")).hexdigest()
    return f"{_DISCOVERY_CACHE_PREFIX}{digest}"
