"""Tests for resource entry options, ordering, URL fetch, and selection."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from angee.base.apps import BaseAddonConfig
from angee.base.management.commands.angee_resources import Command
from angee.base.resources.entries import ResourceEntry
from angee.base.resources.exceptions import ResourceLoadError
from angee.base.resources.fetch import fetch_url
from angee.base.resources.models import Resource
from angee.base.resources.ordering import order_entries


def make_config(
    tmp_path: Path,
    name: str,
    label: str,
    resources: dict[Any, Any] | None = None,
) -> BaseAddonConfig:
    """Return a throwaway addon config rooted at ``tmp_path``."""

    config_cls = type(
        "ProbeConfig",
        (BaseAddonConfig,),
        {"name": name, "label": label, "resources": resources or {}},
    )
    module = ModuleType(name)
    module.__file__ = str(tmp_path / "__init__.py")
    return config_cls(name, module)


def declare(config: BaseAddonConfig, entry: dict[str, Any]) -> ResourceEntry:
    """Return a master-tier entry from one declaration mapping."""

    return ResourceEntry.from_declaration(config, "master", entry)


def test_entries_normalize_strings_and_mappings(tmp_path: Path) -> None:
    """Bare strings and option dicts normalize to canonical entry dicts."""

    config = make_config(
        tmp_path,
        "tests.norm_addon",
        "norm_addon",
        {
            "master": [
                "a.csv",
                {"path": "b.csv", "depends_on": ["a.csv"], "model": "x.Y"},
                {"url": "https://example.test/c.csv"},
            ]
        },
    )

    entries = config.resource_manifest["master"]

    assert entries[0] == {"path": "a.csv"}
    assert entries[1] == {
        "path": "b.csv",
        "depends_on": ("a.csv",),
        "model": "x.Y",
    }
    assert entries[2] == {"url": "https://example.test/c.csv"}


def test_entry_rejects_path_and_url_together(tmp_path: Path) -> None:
    """An entry must declare exactly one of path or url."""

    config = make_config(
        tmp_path,
        "tests.bad_addon",
        "bad_addon",
        {"master": [{"path": "a.csv", "url": "https://example.test/a.csv"}]},
    )

    with pytest.raises(ImproperlyConfigured, match="exactly one of"):
        config.resource_manifest


def test_order_entries_respects_depends_on(tmp_path: Path) -> None:
    """A dependency loads before the entry that declares it."""

    config = make_config(tmp_path, "tests.ord_addon", "ord_addon")
    first = declare(config, {"path": "a.csv"})
    second = declare(config, {"path": "b.csv", "depends_on": ["a.csv"]})

    ordered = order_entries([second, first])

    assert [entry.source for entry in ordered] == ["a.csv", "b.csv"]


def test_order_entries_detects_cycles(tmp_path: Path) -> None:
    """Mutually dependent entries raise a cycle error."""

    config = make_config(tmp_path, "tests.cycle_addon", "cycle_addon")
    first = declare(config, {"path": "a.csv", "depends_on": ["b.csv"]})
    second = declare(config, {"path": "b.csv", "depends_on": ["a.csv"]})

    with pytest.raises(ResourceLoadError, match="cycle"):
        order_entries([first, second])


def test_order_entries_rejects_unknown_target(tmp_path: Path) -> None:
    """A depends_on target that is not selected fails fast."""

    config = make_config(tmp_path, "tests.unk_addon", "unk_addon")
    entry = declare(config, {"path": "a.csv", "depends_on": ["missing.csv"]})

    with pytest.raises(ResourceLoadError, match="not selected"):
        order_entries([entry])


def test_order_entries_resolves_cross_addon(tmp_path: Path) -> None:
    """A ``<addon>:<source>`` dependency orders across addons."""

    producer = make_config(tmp_path, "tests.a_addon", "a_addon")
    consumer = make_config(tmp_path, "tests.b_addon", "b_addon")
    upstream = ResourceEntry.from_declaration(
        producer, "master", {"path": "a.csv"}
    )
    downstream = ResourceEntry.from_declaration(
        consumer, "master", {"path": "b.csv", "depends_on": ["a_addon:a.csv"]}
    )

    ordered = order_entries([downstream, upstream])

    assert [(entry.addon.label, entry.source) for entry in ordered] == [
        ("a_addon", "a.csv"),
        ("b_addon", "b.csv"),
    ]


def test_meta_model_conflict_raises(tmp_path: Path) -> None:
    """A file ``_meta.model`` that disagrees with the entry model fails."""

    resource_dir = tmp_path / "resources" / "master"
    resource_dir.mkdir(parents=True)
    (resource_dir / "data.yaml").write_text(
        "_meta:\n  model: notes.Other\nrows:\n  - _xref: a\n    name: x\n",
        encoding="utf-8",
    )
    config = make_config(tmp_path, "tests.meta_addon", "meta_addon")
    entry = declare(
        config,
        {"path": "resources/master/data.yaml", "model": "notes.Note"},
    )

    with pytest.raises(ResourceLoadError, match="model conflict"):
        entry.read_resource_rows()


def test_fetch_url_rejects_non_http_scheme() -> None:
    """Only http/https resource URLs are permitted."""

    with pytest.raises(ResourceLoadError, match="http/https"):
        fetch_url("file:///etc/passwd")


def test_fetch_url_caches_by_url(
    tmp_path: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A URL is downloaded once and reused from the cache afterwards."""

    settings.ANGEE_DATA_DIR = str(tmp_path)
    calls: list[str] = []

    class _FakeResponse:
        def read(self) -> bytes:
            return b"_xref,username\nu1,alice\n"

        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def fake_urlopen(request: Any) -> _FakeResponse:
        calls.append(request.full_url)
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    url = "https://example.test/seed.csv"
    first = fetch_url(url)
    second = fetch_url(url)

    assert first == second
    assert first.suffix == ".csv"
    assert first.read_bytes() == b"_xref,username\nu1,alice\n"
    assert calls == [url]


def test_command_selects_default_tier_set() -> None:
    """No tier means master+install; --include-demo adds demo."""

    command = Command()

    assert command._selected_tiers({}) == ("master", "install")
    assert command._selected_tiers({"tier": "demo"}) == ("demo",)
    assert command._selected_tiers({"include_demo": True}) == (
        "master",
        "install",
        "demo",
    )
    assert command._selected_tiers(
        {"tier": "master", "include_demo": True}
    ) == ("master", "demo")


def test_demo_load_requires_debug_or_flag(settings: Any) -> None:
    """Writing the demo tier is gated by DEBUG or --allow-non-dev."""

    settings.DEBUG = False
    with pytest.raises(ImproperlyConfigured, match="requires DEBUG"):
        Resource._default_manager.load_addons(tiers=["demo"])
