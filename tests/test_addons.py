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

    from angee.platform.models import Addon, AddonManager

    facts = AddonManager._registry_facts()
    row_keys = {
        "label", "namespace", "description", "keywords", "category",
        "kind", "source", "state", "forced", "pending",
        "model_count", "field_count", "resource_count",
        "depends_on", "depended_by", "model_labels",
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
        name="example.demo", label="demo", namespace="example", kind="consumer",
        forced=False, model_count=0, field_count=0, resource_count=0,
        depends_on=[], model_labels=[], description="", keywords=[], category="Example",
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
