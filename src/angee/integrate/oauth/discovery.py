"""Cached OIDC metadata discovery document fetches."""

from __future__ import annotations

import hashlib
from typing import Any

from django.conf import settings
from django.core.cache import cache

from angee.integrate.oauth.client import oauth_get_json
from angee.integrate.oauth.errors import DISCOVERY_FAILED, OAuthFlowError

_DISCOVERY_CACHE_PREFIX = "angee.integrate.oauth.discovery:"


def discovery_document(discovery_url: str) -> dict[str, Any]:
    """Return cached or freshly fetched OIDC metadata for ``discovery_url``.

    A provider that cannot answer now is a transient ``DISCOVERY_FAILED`` 503
    (``oauth_get_json``); any other refusal, and a document that is not an
    object, a 400. Neither is cached.
    """

    cache_key = _discovery_cache_key(discovery_url)
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    discovery = oauth_get_json(discovery_url, code=DISCOVERY_FAILED)
    if not isinstance(discovery, dict):
        raise OAuthFlowError(DISCOVERY_FAILED, 400)
    cache.set(cache_key, discovery, timeout=settings.ANGEE_OIDC_DISCOVERY_TTL)
    return discovery


def _discovery_cache_key(discovery_url: str) -> str:
    """Return the cache key for one discovery URL."""

    digest = hashlib.sha256(discovery_url.encode("utf-8")).hexdigest()
    return f"{_DISCOVERY_CACHE_PREFIX}{digest}"
