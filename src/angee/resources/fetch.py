"""Fetch remote resource files into the configured local data cache."""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from django.conf import settings

from angee.resources.exceptions import ResourceLoadError

ALLOWED_SCHEMES = frozenset({"http", "https"})
"""URL schemes accepted for remote resource declarations."""


class _SchemeCheckedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects to schemes outside ``ALLOWED_SCHEMES``."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Return a redirected request when the target scheme is allowed."""

        if urlparse(newurl).scheme not in ALLOWED_SCHEMES:
            raise ResourceLoadError(
                f"{newurl!r}: redirect to a disallowed scheme"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url(url: str) -> Path:
    """Return the local cache path for ``url``, downloading it if needed."""

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
    opener = urllib.request.build_opener(_SchemeCheckedRedirectHandler())
    try:
        with opener.open(request) as response:  # noqa: S310
            payload = response.read()
    except OSError as error:
        raise ResourceLoadError(f"{url!r}: fetch failed: {error}") from error
    cache_path.write_bytes(payload)
    return cache_path


def _cache_path(url: str, url_path: str) -> Path:
    """Return the deterministic cache path for one URL."""

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    suffix = PurePosixPath(url_path).suffix.lower()
    cache_dir = Path(settings.ANGEE_DATA_DIR) / "resource-cache"
    return cache_dir / f"{digest}{suffix}"
