"""Cached OAuth/OIDC discovery document fetches."""

from __future__ import annotations

import hashlib
from typing import Any

from django.conf import settings
from django.core.cache import cache

from angee.integrate.oauth.client import _get_json
from angee.integrate.oauth.errors import DISCOVERY_FAILED, OAuthFlowError

_DEFAULT_DISCOVERY_TTL_SECONDS = 3600
_DISCOVERY_CACHE_PREFIX = "angee.integrate.oauth.discovery:"


def discovery_document(discovery_url: str) -> dict[str, Any]:
    """Return a cached or freshly fetched discovery document for ``discovery_url``."""

    cache_key = _discovery_cache_key(discovery_url)
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    try:
        discovery = _get_json(discovery_url, error_code=DISCOVERY_FAILED)
    except OAuthFlowError:
        raise
    except Exception as exc:
        raise OAuthFlowError(DISCOVERY_FAILED, 400) from exc
    cache.set(cache_key, discovery, timeout=_discovery_ttl_seconds())
    return discovery


def _discovery_cache_key(discovery_url: str) -> str:
    """Return the cache key for one discovery URL."""

    digest = hashlib.sha256(discovery_url.encode("utf-8")).hexdigest()
    return f"{_DISCOVERY_CACHE_PREFIX}{digest}"


def _discovery_ttl_seconds() -> int:
    """Return the configured lifetime for cached discovery documents."""

    return int(getattr(settings, "ANGEE_OIDC_DISCOVERY_TTL", _DEFAULT_DISCOVERY_TTL_SECONDS))
