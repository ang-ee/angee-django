"""Addon discovery — the *available* set, sourced from installed bundles.

`available_addons()` is the installed-vs-enabled "available" tier (Django's
pip-installed packages vs `INSTALLED_APPS`): every addon a bundle advertises via
its `angee.addons` entry point, independent of which are enabled. The base addons
ship those entry points (generated from their `addon.toml` by the build hook), so a
fresh install of `django-angee` enumerates them with no `INSTALLED_APPS` list.
"""

from __future__ import annotations

from angee.addons import AvailableAddon, available_addons


def test_available_addons_enumerates_installed_base_addons() -> None:
    """The base addons are discovered from the env's `angee.addons` entry points."""

    available = available_addons()
    for name in ("angee.base", "angee.iam", "angee.storage", "angee.graphql"):
        assert name in available, f"{name!r} not advertised via angee.addons entry points"
        assert available[name].source == "installed"
    assert isinstance(available["angee.iam"], AvailableAddon)


def test_available_addons_includes_local_addon_dirs(tmp_path) -> None:
    """An `addon.toml` under a configured addon dir is discovered as a local addon."""

    addon = tmp_path / "example" / "demo"
    addon.mkdir(parents=True)
    (addon / "addon.toml").write_text('[addon]\nname = "example.demo"\n')

    available = available_addons([tmp_path])

    assert available["example.demo"].source == "local"
    assert available["example.demo"].anchor == str(addon)


def test_registry_facts_full_row_for_enabled_and_zeroed_for_available(db) -> None:
    """The reconcile's fact-gathering: a complete reflected row for every addon —
    full counts when enabled, a complete *zeroed* row when available-but-not-enabled
    (so a state flip never leaves stale counts), with reverse-deps as a list."""

    from angee.platform.models import AddonManager

    facts = AddonManager._registry_facts()
    row_keys = {
        "label", "namespace", "kind", "source", "enabled",
        "model_count", "field_count", "resource_count",
        "depends_on", "depended_by", "model_labels",
    }

    enabled = facts["angee.iam"]  # in the test INSTALLED_APPS
    assert enabled["enabled"] is True
    assert set(enabled) == row_keys  # complete row, no partial dict

    # an installed bundle that is *not* enabled in the test settings
    available = facts["angee.knowledge_graph_pgvector"]
    assert set(available) == row_keys  # complete row even when available-only
    assert available["enabled"] is False
    assert available["model_count"] == 0
    assert available["field_count"] == 0
    assert available["depends_on"] == []
    assert available["model_labels"] == []
    assert available["depended_by"] == []
