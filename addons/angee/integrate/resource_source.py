"""The ``url`` resource source — integrate's outbound-network extension to resources.

The ``resources`` base addon owns the source-type seam but knows only local files.
``integrate`` owns outbound HTTP, so it contributes the ``url`` source here: a remote
resource file is fetched once through the SSRF-pinned
:class:`~angee.integrate.http.HttpClient` into the local data cache. ``integrate``
contributes this source through ``ANGEE_RESOURCE_SOURCE_CLASSES`` so ``resources``
never reaches up into this addon.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ValidationError

from angee.integrate.http import HttpClient
from angee.resources import sources
from angee.resources.exceptions import ResourceLoadError

if TYPE_CHECKING:
    from angee.resources.entries import ResourceEntry

_CACHE_SUBDIR = "resource-cache"


def _normalize_url(app_config: AppConfig, value: Any) -> str:
    """Return the remote URL string as the stored source value."""

    del app_config
    return str(value)


def _materialize_url(entry: ResourceEntry) -> Path:
    """Return the cached local path for a ``url`` entry, fetching it once.

    The fetch rides the SSRF-pinned :class:`HttpClient` (public-only, redirects
    re-validated). The SSRF gate (``ValidationError``), a transport failure
    (``OSError``), and a non-2xx response all surface as ``ResourceLoadError``.
    """

    url = entry.source_value
    cache_path = _cache_path(url)
    if cache_path.exists():
        return cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = HttpClient().get(url, follow_redirects=True)
    except ValidationError as error:
        raise ResourceLoadError(f"{url!r}: {'; '.join(error.messages)}") from error
    except OSError as error:
        raise ResourceLoadError(f"{url!r}: fetch failed: {error}") from error
    if not response.ok:
        raise ResourceLoadError(f"{url!r}: fetch failed: HTTP {response.status}")
    cache_path.write_bytes(response.body)
    return cache_path


def _cache_path(url: str) -> Path:
    """Return the deterministic local cache path for one URL."""

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    suffix = PurePosixPath(urlparse(url).path).suffix.lower()
    return Path(settings.ANGEE_DATA_DIR) / _CACHE_SUBDIR / f"{digest}{suffix}"


def url_source() -> sources.ResourceSource:
    """Return integrate's networked ``url`` resource source."""

    return sources.ResourceSource(key="url", normalize=_normalize_url, materialize=_materialize_url)
