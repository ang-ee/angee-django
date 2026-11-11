"""Tests for integrate's ``url`` resource source — the networked extension to resources."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2
import pytest
from django.core.exceptions import ValidationError

from angee.integrate.errors import RequestNotSentError
from angee.integrate.testing.http import mock_transport
from angee.resources import sources
from angee.resources.entries import ResourceEntry
from angee.resources.exceptions import ResourceLoadError

_PAYLOAD = b"_xref,username\nu1,alice\n"


class _Addon:
    """Minimal addon stand-in (the url source never touches addon.path)."""

    name = "tests.url_addon"
    label = "url_addon"
    path = "/tmp"


def _entry(url: str) -> ResourceEntry:
    return ResourceEntry(addon=_Addon(), tier="master", source_key="url", source_value=url)


def test_url_source_is_registered() -> None:
    """integrate contributes the ``url`` source into the resources registry."""

    assert "url" in sources.ResourceSource.registered_keys()


def test_url_source_fetches_then_reads_from_cache(tmp_path: Path, settings: Any) -> None:
    """A url entry is fetched once through the pinned client, then served from cache."""

    settings.ANGEE_DATA_DIR = tmp_path
    with mock_transport(lambda request: httpx2.Response(200, content=_PAYLOAD)) as requests:
        first = _entry("https://example.test/data.csv").materialize()
        second = _entry("https://example.test/data.csv").materialize()

    assert first == second
    assert first.suffix == ".csv"
    assert first.read_bytes() == _PAYLOAD
    assert [str(request.url) for request in requests] == ["https://example.test/data.csv"]  # then the cache


def test_url_source_maps_ssrf_rejection_to_resource_load_error(tmp_path: Path, settings: Any) -> None:
    """The pinned transport's refusal of a cloud-metadata address surfaces as a ResourceLoadError."""

    settings.ANGEE_DATA_DIR = tmp_path
    with pytest.raises(ResourceLoadError) as refused:
        _entry("http://169.254.169.254/data.csv").materialize()
    assert isinstance(refused.value.__cause__, ValidationError)


@pytest.mark.parametrize(
    "error",
    [ConnectionRefusedError("down"), RequestNotSentError(), httpx2.ConnectError("down")],
    ids=["os-error", "not-sent", "httpx2"],
)
def test_url_source_maps_transport_failure_to_resource_load_error(
    tmp_path: Path, settings: Any, error: Exception,
) -> None:
    """Unsent requests, native httpx2 and legacy transport failures remain ResourceLoadError."""

    settings.ANGEE_DATA_DIR = tmp_path

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise error

    with mock_transport(refuse), pytest.raises(ResourceLoadError, match="fetch failed"):
        _entry("https://example.test/data.csv").materialize()


def test_url_source_maps_non_2xx_to_resource_load_error(tmp_path: Path, settings: Any) -> None:
    """A non-2xx response surfaces as a ResourceLoadError without caching."""

    settings.ANGEE_DATA_DIR = tmp_path
    with (
        mock_transport(lambda request: httpx2.Response(404, content=b"not found")),
        pytest.raises(ResourceLoadError, match="HTTP 404"),
    ):
        _entry("https://example.test/missing.csv").materialize()
