"""VCS discovery reconciles declarations and provenance into the one Addon row."""

from __future__ import annotations

import sys
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from hatch_angee import AddonManifest
from rebac import system_context

from angee.platform_integrate_vcs.catalog import parse_addon_meta
from tests.conftest import Addon, Repository, Source, VcsBridge, make_integration
from tests.test_marketplace_graphql import marketplace_tables as marketplace_tables


@pytest.fixture()
def catalog_source(marketplace_tables: None, tmp_path: Path) -> tuple[Source, Path]:
    """Provide a real local VCS source with a remote-only addon declaration."""

    del marketplace_tables
    marker = tmp_path / "addons" / "base" / "addon.toml"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        '[addon]\nname = "catalog_remote.base"\ndescription = "Remote declaration"\n'
        'keywords = ["catalogue"]\ncategory = "Integration"\n'
        'depends_on = ["angee.iam", "catalog_remote.dependency"]\n'
    )
    bridge = make_integration(
        "addon-catalog",
        model=VcsBridge,
        backend_class="local",
        config={"local_root": str(tmp_path), "local_name": "catalogue"},
    )
    bridge.discover_repositories()
    with system_context(reason="test catalogue source"):
        repository = Repository.objects.get(name="catalogue")
        source = Source.objects.create(repository=repository, kind="addon", path="addons")
    return source, marker


def test_parse_addon_meta_reads_the_addon_block() -> None:
    """The manifest's ``[addon]`` block becomes the catalog descriptor."""

    blob = (
        b'[addon]\nname = "angee.demo"\ndescription = "A demo addon."\n'
        b'depends_on = ["angee.iam", "angee.platform"]\n'
    )

    meta = parse_addon_meta(blob)

    assert meta["name"] == "angee.demo"
    assert "label" not in meta
    assert meta["namespace"] == "angee"
    assert meta["description"] == "A demo addon."
    assert meta["depends_on"] == ["angee.iam", "angee.platform"]


def test_parse_addon_meta_coerces_a_bare_string_depends_on() -> None:
    """A scalar ``depends_on`` is normalised to a one-element list (manifest convention)."""

    assert parse_addon_meta(b'[addon]\nname = "x.y"\ndepends_on = "x.z"\n')["depends_on"] == ["x.z"]


def test_parse_addon_meta_tolerates_a_bare_manifest() -> None:
    """A minimal manifest has no invented Django identity or dependencies."""

    meta = parse_addon_meta(b'[addon]\nname = "solo"\n')

    assert meta["name"] == "solo"
    assert "label" not in meta
    assert meta["depends_on"] == []
    assert meta["description"] == ""


def test_remote_catalogue_reconciles_metadata_and_retains_removed_declarations(
    catalog_source: tuple[Source, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remote rows retain their declaration and provenance after disappearing."""

    source, marker = catalog_source
    monkeypatch.setattr("angee.platform_integrate_vcs.models.available_addons", lambda _dirs: {})

    assert source.refresh() == 1
    with system_context(reason="test catalogue read"):
        row = Addon.objects.get(name="catalog_remote.base")
    assert row.label == ""
    assert str(row) == "catalog_remote.base"
    assert row.namespace == "catalog_remote"
    assert row.source == Addon.Source.REMOTE
    assert row.state == Addon.State.DISABLED
    assert row.description == "Remote declaration"
    assert row.keywords == ["catalogue"]
    assert row.category == "Integration"
    assert row.depends_on == ["angee.iam", "catalog_remote.dependency"]
    assert row.vcs_source_id == source.pk
    assert row.vcs_path == "addons/base"
    assert source.last_synced_at is not None

    marker.write_text(marker.read_text().replace("Remote declaration", "Updated declaration"))
    assert source.refresh() == 1
    with system_context(reason="test catalogue read"):
        row.refresh_from_db()
        assert Addon.objects.filter(name=row.name).count() == 1
    assert row.description == "Updated declaration"

    marker.unlink()
    assert source.refresh() == 0
    with system_context(reason="test catalogue read"):
        row.refresh_from_db()
    assert row.state == Addon.State.REMOVED
    assert row.description == "Updated declaration"
    assert row.depends_on == ["angee.iam", "catalog_remote.dependency"]
    assert row.vcs_source_id == source.pk
    assert row.vcs_path == "addons/base"


@pytest.mark.parametrize("origin_kind", [Addon.Source.LOCAL, Addon.Source.INSTALLED])
def test_available_catalogue_updates_provenance_and_clears_runtime_facts_when_it_becomes_remote(
    catalog_source: tuple[Source, Path], monkeypatch: pytest.MonkeyPatch, origin_kind: str
) -> None:
    """Available metadata wins until materialization disappears; remote rows have no live counts."""

    source, marker = catalog_source
    manifest = AddonManifest(name="catalog_remote.base", depends_on=("local.dependency",))
    origin = (
        marker.parent
        if origin_kind == Addon.Source.LOCAL
        else EntryPoint(name=manifest.name, value=manifest.name, group="angee.addons")
    )
    available = {manifest.name: (manifest, origin)}
    monkeypatch.setattr("angee.platform_integrate_vcs.models.available_addons", lambda _dirs: available)
    with system_context(reason="test catalogue setup"):
        row = Addon.objects.create(
            name=manifest.name,
            label="native_label",
            source=origin_kind,
            state=Addon.State.ENABLED,
            description="Available declaration",
            depends_on=list(manifest.depends_on),
            forced=True,
            pending=True,
            model_count=2,
            field_count=7,
            resource_count=3,
            model_labels=["native_label.Record"],
            depended_by=["local.consumer"],
        )

    assert source.refresh() == 1
    with system_context(reason="test catalogue read"):
        row.refresh_from_db()
    assert row.source == origin_kind
    assert row.state == Addon.State.ENABLED
    assert row.label == "native_label"
    assert row.description == "Available declaration"
    assert row.depends_on == ["local.dependency"]
    assert row.model_count == 2
    assert row.forced is True
    assert row.vcs_source_id == source.pk
    assert row.vcs_path == "addons/base"

    available.clear()
    assert source.refresh() == 1
    with system_context(reason="test catalogue read"):
        row.refresh_from_db()
    assert row.source == Addon.Source.REMOTE
    assert row.state == Addon.State.DISABLED
    assert row.label == ""
    assert row.description == "Remote declaration"
    assert row.depends_on == ["angee.iam", "catalog_remote.dependency"]
    assert row.forced is False
    assert row.pending is False
    assert (row.model_count, row.field_count, row.resource_count) == (0, 0, 0)
    assert row.model_labels == []
    assert row.depended_by == []
    assert row.vcs_source_id == source.pk


def test_remote_catalogue_keeps_unknown_identity_without_importing_the_remote_name(
    catalog_source: tuple[Source, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remote declaration names never trigger local package or AppConfig imports."""

    source, marker = catalog_source
    marker.write_text('[addon]\nname = "catalog_remote_identity.base"\ndepends_on = ["catalog_remote.dependency"]\n')
    package = tmp_path / "python" / "catalog_remote_identity" / "base"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "apps.py").write_text(
        "from django.apps import AppConfig\n"
        "class RemoteConfig(AppConfig):\n"
        '    name = "catalog_remote_identity.base"\n'
        '    label = "remote_identity"\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path / "python"))
    monkeypatch.setattr("angee.platform_integrate_vcs.models.available_addons", lambda _dirs: {})
    assert "catalog_remote_identity" not in sys.modules

    assert source.refresh() == 1
    with system_context(reason="test catalogue read"):
        row = Addon.objects.get(name="catalog_remote_identity.base")
    assert row.label == ""
    assert row.state == Addon.State.DISABLED
    assert row.depends_on == ["catalog_remote.dependency"]
    assert not any(
        name == "catalog_remote_identity" or name.startswith("catalog_remote_identity.") for name in sys.modules
    )
