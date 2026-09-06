"""Tests for integrate's ``url`` resource source — the networked extension to resources."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from django.core.exceptions import ValidationError

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


def _patch_get(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    monkeypatch.setattr("angee.integrate.resource_source.HttpClient.get", handler)


def test_url_source_is_registered() -> None:
    """integrate contributes the ``url`` source into the resources registry."""

    assert "url" in sources.source_keys()


def test_url_source_fetches_then_reads_from_cache(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A url entry is fetched once through the pinned client, then served from cache."""

    settings.ANGEE_DATA_DIR = tmp_path
    calls: list[str] = []

    def fake_get(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        calls.append(url)
        return httpx.Response(200, content=_PAYLOAD)

    _patch_get(monkeypatch, fake_get)

    first = _entry("https://example.test/data.csv").materialize()
    second = _entry("https://example.test/data.csv").materialize()

    assert first == second
    assert first.suffix == ".csv"
    assert first.read_bytes() == _PAYLOAD
    assert calls == ["https://example.test/data.csv"]  # second materialize hit the cache


def test_url_source_maps_ssrf_rejection_to_resource_load_error(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SSRF gate's ValidationError surfaces as a ResourceLoadError."""

    settings.ANGEE_DATA_DIR = tmp_path

    def fake_get(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        raise ValidationError("URL host must resolve only to public IP addresses.")

    _patch_get(monkeypatch, fake_get)
    with pytest.raises(ResourceLoadError, match="public IP"):
        _entry("https://internal.test/data.csv").materialize()


def test_url_source_maps_transport_failure_to_resource_load_error(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transport OSError surfaces as a ResourceLoadError."""

    settings.ANGEE_DATA_DIR = tmp_path

    def fake_get(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        raise ConnectionRefusedError("down")

    _patch_get(monkeypatch, fake_get)
    with pytest.raises(ResourceLoadError, match="fetch failed"):
        _entry("https://example.test/data.csv").materialize()


def test_url_source_maps_httpx_transport_failure_to_resource_load_error(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Native httpx transport errors remain resource-boundary errors."""

    settings.ANGEE_DATA_DIR = tmp_path

    def fake_get(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("down", request=httpx.Request("GET", url))

    _patch_get(monkeypatch, fake_get)
    with pytest.raises(ResourceLoadError, match="fetch failed"):
        _entry("https://example.test/data.csv").materialize()


def test_url_source_maps_non_2xx_to_resource_load_error(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-2xx response surfaces as a ResourceLoadError without caching."""

    settings.ANGEE_DATA_DIR = tmp_path

    def fake_get(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(404, content=b"not found")

    _patch_get(monkeypatch, fake_get)
    with pytest.raises(ResourceLoadError, match="HTTP 404"):
        _entry("https://example.test/missing.csv").materialize()
