"""Fetch remote resource files into the configured local data cache."""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError

from angee.base.net import validate_public_url
from angee.resources.exceptions import ResourceLoadError


class _PublicUrlRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects to non-HTTP(S) or SSRF-unsafe targets."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Return a redirected request when the target is an allowed public URL."""

        _reject_unsafe_target(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url(url: str) -> Path:
    """Return the local cache path for ``url``, downloading it if needed."""

    _reject_unsafe_target(url)
    cache_path = _cache_path(url, urlparse(url).path)
    if cache_path.exists():
        return cache_path

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, method="GET")
    opener = urllib.request.build_opener(_PublicUrlRedirectHandler())
    try:
        with opener.open(request) as response:  # noqa: S310
            payload = response.read()
    except OSError as error:
        raise ResourceLoadError(f"{url!r}: fetch failed: {error}") from error
    cache_path.write_bytes(payload)
    return cache_path


def _reject_unsafe_target(url: str) -> None:
    """Reject non-HTTP(S) or SSRF-unsafe outbound targets as a ``ResourceLoadError``.

    Pre-flight (and re-run on each redirect): the URL must resolve only to public
    IPs. urllib re-resolves on connect, so this is a best-effort guard, not the
    IP-pinning the webhook delivery layer uses — proportionate here because
    resource URLs come from operator-authored manifests, not untrusted input.
    """

    try:
        validate_public_url(url)
    except ValidationError as error:
        raise ResourceLoadError(f"{url!r}: {'; '.join(error.messages)}") from error


def _cache_path(url: str, url_path: str) -> Path:
    """Return the deterministic cache path for one URL."""

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    suffix = PurePosixPath(url_path).suffix.lower()
    cache_dir = Path(settings.ANGEE_DATA_DIR) / "resource-cache"
    return cache_dir / f"{digest}{suffix}"
