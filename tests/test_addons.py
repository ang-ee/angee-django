"""Addon discovery — the *available* set, sourced from installed bundles.

`available_addons()` is the installed-vs-enabled "available" tier (Django's
pip-installed packages vs `INSTALLED_APPS`): every wheel-owned addon advertised
through `angee.addons` entry points plus every folder addon discovered from the
configured addon roots, independent of which are enabled.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from hatch_angee import AddonManifest

from angee.addons import AvailableAddon, addon_manifest, available_addons
from angee.compose.appgraph import AppGraph
from angee.compose.dependencies import AddonDependencyGroup
from tests.conftest import make_addon


def test_manifest_binding_reuses_native_parse_and_refreshes_a_new_graph(tmp_path, monkeypatch) -> None:
    """Graph, capabilities and dependency projection share one unchanged parser result."""

    import angee.addons as addon_module

    config = make_addon(name="example.demo", path=tmp_path)
    original = addon_module.parse_manifest
    parsed = []

    def parse(marker):
        manifest = original(marker)
        parsed.append(manifest)
        return manifest

    monkeypatch.setattr(addon_module, "parse_manifest", parse)
    assert AppGraph().resolve((config,)) == (config,)
    manifest = addon_manifest(config)
    assert isinstance(manifest, AddonManifest)
    assert AddonDependencyGroup.from_app_configs((config,), project_dir=None).manifests == (manifest,)
    assert len(parsed) == 1
    assert manifest is parsed[0]
    (tmp_path / "addon.toml").write_text('[addon]\nname = "example.demo"\ndescription = "updated"\n')
    AppGraph().resolve((config,))
    assert addon_manifest(config).description == "updated"
    assert len(parsed) == 2
    assert addon_manifest(config) is parsed[1]


def test_manifest_binding_validates_native_identity(tmp_path) -> None:
    config = make_addon(name="example.demo", path=tmp_path)
    config.name = "example.other"
    with pytest.raises(ImproperlyConfigured, match="disagrees with AppConfig.name"):
        addon_manifest(config)


def test_manifest_does_not_predict_or_import_capability_exports(tmp_path) -> None:
    config = make_addon(name="example.demo", path=tmp_path)
    (tmp_path / "schema.py").write_text('raise AssertionError("early import")\n')
    (tmp_path / "mcp_tools.py").write_text('raise AssertionError("early import")\n')
    manifest = addon_manifest(config)
    assert manifest.schemas is None
    assert manifest.mcp == {}
    assert manifest.web == {}


def test_new_graph_observes_previously_missing_manifest(tmp_path) -> None:
    config = make_addon(name="example.demo", path=tmp_path)
    marker = tmp_path / "addon.toml"
    marker.unlink()
    assert addon_manifest(config) is None
    marker.write_text('[addon]\nname = "example.demo"\n')
    AppGraph().resolve((config,))
    assert addon_manifest(config).name == "example.demo"


def test_manifest_parser_retains_ordered_native_migration_entries(tmp_path) -> None:
    entries = (
        {"name": "rename_owner", "app_label": "demo", "module": "runtime_migrations.rename_owner"},
        {"name": "backfill_owner", "app_label": "demo", "module": "runtime_migrations.backfill_owner"},
    )
    config = make_addon(name="example.demo", path=tmp_path, migrations=entries)
    assert addon_manifest(config).migrations == entries


def test_available_addons_excludes_core_and_enumerates_folder_addons(settings) -> None:
    """The catalog contains capability addons, not the framework core apps."""

    available = available_addons(settings.ANGEE_ADDON_DIRS)
    for name in ("angee.base", "angee.compose", "angee.jobs"):
        assert name not in available
    for name in ("angee.graphql", "angee.iam", "angee.storage"):
        assert name in available, f"{name!r} not discovered from ANGEE_ADDON_DIRS"
        assert available[name].source == "local"
    assert isinstance(available["angee.iam"], AvailableAddon)


def test_available_addons_includes_local_addon_dirs(tmp_path) -> None:
    """An `addon.toml` under a configured addon dir is discovered as a local addon."""

    addon = tmp_path / "example" / "demo"
    addon.mkdir(parents=True)
    (addon / "addon.toml").write_text('[addon]\nname = "example.demo"\n')

    available = available_addons([tmp_path])

    assert available["example.demo"].source == "local"
    assert available["example.demo"].anchor == str(addon)


def test_available_addons_reads_local_manifest_through_upstream_discovery(tmp_path, monkeypatch) -> None:
    """Catalog discovery delegates source ordering and parsing to hatch-angee."""

    addon = tmp_path / "example" / "contract"
    seen = []

    def discover(roots):
        seen.append(tuple(roots))
        return [(addon, AddonManifest(name="example.contract"))]

    monkeypatch.setattr("angee.addons.discover", discover)
    available = available_addons([tmp_path])
    assert seen == [(tmp_path,)]
    assert available["example.contract"].source == "local"
    assert available["example.contract"].anchor == str(addon)


def test_registry_facts_full_row_for_enabled_and_zeroed_for_available(db) -> None:
    """The reconcile's fact-gathering: a complete reflected row for every addon —
    full counts when enabled, a complete *zeroed* row when available-but-not-enabled
    (so a state flip never leaves stale counts), with reverse-deps as a list."""

    from angee.platform.models import Addon, AddonManager

    facts = AddonManager._registry_facts()
    row_keys = {
        "label",
        "namespace",
        "description",
        "keywords",
        "category",
        "kind",
        "source",
        "state",
        "forced",
        "pending",
        "model_count",
        "field_count",
        "resource_count",
        "depends_on",
        "depended_by",
        "model_labels",
    }

    enabled = facts["angee.iam"]  # in the test INSTALLED_APPS
    assert enabled["state"] == Addon.State.ENABLED
    assert set(enabled) == row_keys  # complete row, no partial dict
    assert enabled["pending"] is False  # a composed addon is never pending
    assert enabled["category"] == "Foundation"  # mirrored from the addon.toml manifest

    # an installed bundle that is *not* enabled in the test settings
    available = facts["angee.knowledge_graph_pgvector"]
    assert set(available) == row_keys  # complete row even when available-only
    assert available["state"] == Addon.State.DISABLED
    assert available["forced"] is False
    assert available["pending"] is False  # not in the (empty) desired set
    assert available["category"] == ""  # metadata stays blank until composed
    assert available["model_count"] == 0
    assert available["field_count"] == 0
    assert available["depends_on"] == []
    assert available["model_labels"] == []
    assert available["depended_by"] == []


def test_registry_facts_pending_reflects_desired_settings_roots(db) -> None:
    """``pending`` flags an available-but-not-composed addon named in the desired roots.

    ``desired`` is the install owner's ``settings.yaml`` ``INSTALLED_APPS`` view; an
    available addon listed there but not yet composed is the board's "to install".
    """

    from angee.platform.models import AddonManager

    facts = AddonManager._registry_facts(desired=frozenset({"angee.knowledge_graph_pgvector"}))

    assert facts["angee.knowledge_graph_pgvector"]["pending"] is True
    # An addon composed in the test app graph stays non-pending even if named desired.
    assert facts["angee.iam"]["pending"] is False


def test_registry_facts_flags_a_queued_uninstall_for_a_composed_root(db, monkeypatch) -> None:
    """A composed *root* dropped from the desired roots is ``pending`` (a queued uninstall).

    The symmetric "to install" diff: a composed consumer root no longer named in
    ``settings.yaml`` leaves on the next boot, so the board shows it pending. A composed
    *dependency* (``required``) is never in the roots yet is not being uninstalled, so it
    is never flagged.
    """

    from dataclasses import replace

    from angee.platform import composed
    from angee.platform import models as platform_models
    from angee.platform.models import AddonManager

    root = composed.AddonRollup(
        name="example.demo",
        label="demo",
        namespace="example",
        kind="consumer",
        forced=False,
        model_count=0,
        field_count=0,
        resource_count=0,
        depends_on=[],
        model_labels=[],
        description="",
        keywords=[],
        category="Example",
    )
    monkeypatch.setattr(platform_models, "available_addons", lambda dirs=(): {})
    monkeypatch.setattr(platform_models.composed, "addon_rollups", lambda: [root])

    # Composed but dropped from the desired roots → queued uninstall.
    assert AddonManager._registry_facts(desired=frozenset())["example.demo"]["pending"] is True
    # Still a desired root → not pending.
    assert AddonManager._registry_facts(desired=frozenset({"example.demo"}))["example.demo"]["pending"] is False

    # A composed dependency (required) is never flagged, even absent from the roots.
    dependency = replace(root, name="example.dep", kind="required")
    monkeypatch.setattr(platform_models.composed, "addon_rollups", lambda: [dependency])
    assert AddonManager._registry_facts(desired=frozenset())["example.dep"]["pending"] is False


def test_failed_refresh_cannot_leave_a_previous_manifest_available(tmp_path) -> None:
    config = make_addon(name="example.demo", path=tmp_path)
    assert addon_manifest(config).name == "example.demo"
    (tmp_path / "addon.toml").write_text("invalid toml", encoding="utf-8")
    with pytest.raises(ImproperlyConfigured):
        AppGraph().resolve((config,))
    with pytest.raises(ImproperlyConfigured):
        addon_manifest(config)
