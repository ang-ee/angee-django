"""Fetch URL resources into a deterministic local cache."""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from django.conf import settings

from angee.base.resources.exceptions import ResourceLoadError

ALLOWED_SCHEMES = frozenset({"http", "https"})


def fetch_url(url: str) -> Path:
    """Return a cached local path for ``url``, downloading it once.

    Only ``http``/``https`` are permitted. Content is cached by a hash of the
    URL under ``ANGEE_DATA_DIR/resource-cache`` with the source suffix
    preserved so format detection still works; an existing cache entry is
    reused without re-fetching.
    """

    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ResourceLoadError(
            f"{url!r}: only http/https resource URLs are allowed"
        )
    cache_path = _cache_path(url, parsed.path)
    if cache_path.exists():
        return cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 — scheme checked above
            payload = response.read()
    except OSError as exc:
        raise ResourceLoadError(f"{url!r}: fetch failed: {exc}") from exc
    cache_path.write_bytes(payload)
    return cache_path


def _cache_path(url: str, url_path: str) -> Path:
    """Return the content-addressed cache path for one URL."""

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    suffix = PurePosixPath(url_path).suffix.lower()
    cache_dir = Path(settings.ANGEE_DATA_DIR) / "resource-cache"
    return cache_dir / f"{digest}{suffix}"
