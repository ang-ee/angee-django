"""Tests for resource declarations, ordering, fetching, and loading."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, models
from rebac import system_context

from angee.base.models import AngeeModel
from angee.resources.entries import ResourceEntry
from angee.resources.exceptions import ResourceLoadError
from angee.resources.fetch import fetch_url
from angee.resources.models import Resource
from angee.resources.ordering import order_entries


@dataclass(slots=True)
class Addon:
    """Small addon stand-in exposing normalized resource declarations."""

    name: str
    """Full dotted addon name."""

    label: str
    """Django app label."""

    path: str
    """Filesystem root for local resource files."""

    resource_manifest: Mapping[str, tuple[Mapping[str, Any], ...]]
    """Normalized resource manifest keyed by tier."""


def addon(
    tmp_path: Path,
    *,
    name: str = "tests.resource_addon",
    label: str = "resource_addon",
    manifest: Mapping[str, tuple[Mapping[str, Any], ...]] | None = None,
) -> Addon:
    """Return a resource addon rooted at ``tmp_path``."""

    return Addon(
        name=name,
        label=label,
        path=str(tmp_path),
        resource_manifest=manifest
        or {"master": (), "install": (), "demo": ()},
    )


def entry(
    tmp_path: Path,
    declaration: dict[str, Any],
    *,
    tier: str = "master",
    owner: Addon | None = None,
) -> ResourceEntry:
    """Return a resource entry for one declaration mapping."""

    return ResourceEntry.from_declaration(
        owner or addon(tmp_path),
        tier,
        declaration,
    )


def test_resource_entry_reads_structured_rows_and_fields(
    tmp_path: Path,
) -> None:
    """YAML resources can declare a model and field envelope."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "notes.yaml").write_text(
        "_meta:\n"
        "  model: base.ImportNote\n"
        "rows:\n"
        "  - _xref: n1\n"
        "    fields:\n"
        "      title: First\n",
        encoding="utf-8",
    )

    rows = entry(
        tmp_path,
        {"path": "resources/notes.yaml"},
    ).read_resource_rows()

    assert len(rows) == 1
    assert rows[0].model_label == "base.ImportNote"
    assert rows[0].xref == "n1"
    assert rows[0].dataset_row == {"_xref": "n1", "title": "First"}


def test_resource_entry_rejects_reserved_keys_in_structured_fields(
    tmp_path: Path,
) -> None:
    """Structured field envelopes cannot override loader-owned keys."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "notes.yaml").write_text(
        "_meta:\n"
        "  model: base.ImportNote\n"
        "rows:\n"
        "  - _xref: n1\n"
        "    fields:\n"
        "      _xref: n2\n"
        "      title: First\n",
        encoding="utf-8",
    )

    with pytest.raises(ImproperlyConfigured, match="reserved keys: _xref"):
        entry(
            tmp_path,
            {"path": "resources/notes.yaml"},
        ).read_resource_rows()


def test_resource_entry_rejects_model_conflicts(tmp_path: Path) -> None:
    """File metadata cannot disagree with the entry model."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "data.yaml").write_text(
        "_meta:\n  model: base.Other\nrows:\n  - _xref: a\n",
        encoding="utf-8",
    )

    with pytest.raises(ResourceLoadError, match="model conflict"):
        entry(
            tmp_path,
            {"path": "resources/data.yaml", "model": "base.ImportNote"},
        ).read_resource_rows()


def test_resource_entry_rejects_unsupported_formats(tmp_path: Path) -> None:
    """Only text formats owned by the resource loader are accepted."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "data.xlsx").write_bytes(b"binary")

    with pytest.raises(ImproperlyConfigured, match="unsupported format"):
        entry(tmp_path, {"path": "resources/data.xlsx"}).read_resource_rows()


def test_order_entries_respects_same_and_cross_addon_dependencies(
    tmp_path: Path,
) -> None:
    """Dependency edges order resources across selected addons."""

    upstream_addon = addon(tmp_path, name="tests.upstream", label="upstream")
    downstream_addon = addon(
        tmp_path,
        name="tests.downstream",
        label="downstream",
    )
    first = ResourceEntry.from_declaration(
        upstream_addon,
        "master",
        {"path": "a.csv"},
    )
    second = ResourceEntry.from_declaration(
        downstream_addon,
        "master",
        {"path": "b.csv", "depends_on": ("upstream:a.csv",)},
    )
    third = ResourceEntry.from_declaration(
        downstream_addon,
        "master",
        {"path": "c.csv", "depends_on": ("b.csv",)},
    )

    ordered = order_entries([third, second, first])

    assert [item.source for item in ordered] == ["a.csv", "b.csv", "c.csv"]


def test_resource_entry_treats_string_depends_on_as_one_dependency(
    tmp_path: Path,
) -> None:
    """A bare dependency string is one resource key."""

    resource_entry = ResourceEntry.from_declaration(
        addon(tmp_path),
        "master",
        {"path": "b.csv", "depends_on": "a.csv"},
    )

    assert resource_entry.depends_on == ("a.csv",)


def test_order_entries_detects_cycles(tmp_path: Path) -> None:
    """Dependency cycles fail before any rows load."""

    owner = addon(tmp_path)
    first = ResourceEntry.from_declaration(
        owner,
        "master",
        {"path": "a.csv", "depends_on": ("b.csv",)},
    )
    second = ResourceEntry.from_declaration(
        owner,
        "master",
        {"path": "b.csv", "depends_on": ("a.csv",)},
    )

    with pytest.raises(ResourceLoadError, match="cycle"):
        order_entries([first, second])


def test_fetch_url_rejects_non_http_urls() -> None:
    """Remote resources are limited to http and https URLs."""

    with pytest.raises(ResourceLoadError, match="http/https"):
        fetch_url("file:///private/resource.csv")


def test_fetch_url_caches_by_full_url(
    tmp_path: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fetched URL is read from cache on the second request."""

    settings.ANGEE_DATA_DIR = tmp_path
    calls: list[str] = []

    class Response:
        """Tiny context manager matching urllib response use."""

        def read(self) -> bytes:
            """Return CSV payload bytes."""

            return b"_xref,username\nu1,alice\n"

        def __enter__(self) -> Response:
            """Return the response object for context-manager use."""

            return self

        def __exit__(self, *exc: object) -> None:
            """Close the fake response."""

            return None

    def urlopen(request: Any) -> Response:
        """Record the requested URL and return a fake response."""

        calls.append(request.full_url)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    first = fetch_url("https://example.test/data.csv")
    second = fetch_url("https://example.test/data.csv")

    assert first == second
    assert first.suffix == ".csv"
    assert first.read_bytes() == b"_xref,username\nu1,alice\n"
    assert calls == ["https://example.test/data.csv"]


@pytest.mark.django_db(transaction=True)
def test_resource_manager_loads_rows_and_resolves_xrefs(
    tmp_path: Path,
) -> None:
    """Resource rows load through import-export with FK xrefs and JSON."""

    class ImportUser(AngeeModel):
        """User-like model loaded from resources."""

        username = models.CharField(max_length=40, unique=True)
        first_name = models.CharField(max_length=40, blank=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ImportNote(AngeeModel):
        """Note-like model referencing a resource-loaded user."""

        title = models.CharField(max_length=80)
        tags = models.JSONField(default=list, blank=True)
        created_by = models.ForeignKey(ImportUser, on_delete=models.CASCADE)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ResourceLedger(Resource):
        """Concrete resource ledger for the test database."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    owner = _write_resource_files(tmp_path)
    models_to_create = (ImportUser, ImportNote, ResourceLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        result = ResourceLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.MASTER],
        )

        with system_context(reason="resource load assertions"):
            alice = ImportUser.objects.get(username="alice")
            note = ImportNote.objects.get(title="Framework map")
        ledger_xrefs = set(
            ResourceLedger.objects.values_list("xref", flat=True)
        )

        assert result.created == 2
        assert result.loaded == 2
        assert alice.first_name == "Alice"
        assert note.created_by == alice
        assert note.tags == ["composition", "resources"]
        assert ledger_xrefs == {"user_alice", "note_framework_map"}

        second = ResourceLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.MASTER],
        )
        assert second.created == 0
        assert second.updated == 0
        assert second.skipped == 2
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_adoption_is_opt_in(tmp_path: Path) -> None:
    """Rows without ledgers adopt existing targets only when requested."""

    class AdoptUser(AngeeModel):
        """Model with a unique natural key suitable for adoption."""

        username = models.CharField(max_length=40, unique=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class AdoptLedger(Resource):
        """Concrete resource ledger for adoption tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.adoptuser.csv").write_text(
        "_xref,username\nexisting,alice\n",
        encoding="utf-8",
    )
    manifest: Mapping[str, tuple[Mapping[str, Any], ...]] = {
        "master": (),
        "install": (
            {
                "path": "resources/010_base.adoptuser.csv",
                "adopt": True,
            },
        ),
        "demo": (),
    }
    owner = addon(tmp_path, manifest=manifest)
    no_adopt = addon(
        tmp_path,
        manifest={
            **manifest,
            "install": ({"path": "resources/010_base.adoptuser.csv"},),
        },
    )

    models_to_create = (AdoptUser, AdoptLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        AdoptUser.objects.create(username="alice")
        with pytest.raises((IntegrityError, ResourceLoadError)):
            AdoptLedger.objects.load_addons(
                (no_adopt,),
                tiers=[Resource.Tier.INSTALL],
            )

        result = AdoptLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.INSTALL],
        )

        assert result.created == 0
        assert result.updated == 0
        assert result.skipped == 1
        with system_context(reason="resource adoption assertions"):
            assert AdoptUser.objects.count() == 1
        assert AdoptLedger.objects.get(xref="existing").target_id
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


def _write_resource_files(tmp_path: Path) -> Addon:
    """Write a small resource set and return its declaring addon."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.importuser.csv").write_text(
        "_xref,username,first_name\nuser_alice,alice,Alice\n",
        encoding="utf-8",
    )
    (resource_dir / "020_base.importnote.yaml").write_text(
        "- _xref: note_framework_map\n"
        "  title: Framework map\n"
        "  created_by: resource_addon.user_alice\n"
        "  tags:\n"
        "    - composition\n"
        "    - resources\n",
        encoding="utf-8",
    )
    return addon(
        tmp_path,
        manifest={
            "master": (
                {"path": "resources/010_base.importuser.csv"},
                {"path": "resources/020_base.importnote.yaml"},
            ),
            "install": (),
            "demo": (),
        },
    )
