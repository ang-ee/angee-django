"""Addon discovery — the *available* set, sourced from installed bundles.

`available_addons()` is the installed-vs-enabled "available" tier (Django's
pip-installed packages vs `INSTALLED_APPS`): every wheel-owned addon advertised
through `angee.addons` entry points plus every folder addon discovered from the
configured addon roots, independent of which are enabled.
"""

from __future__ import annotations

import sys
from importlib.machinery import ModuleSpec
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext
from hatch_angee import AddonManifest
from rebac import system_context

import angee.addons as addon_module
from angee.addons import addon_manifest, available_addons, resolve_app_config, resolve_manifest_roots
from angee.compose.appgraph import AppGraph
from angee.compose.dependencies import AddonDependencyGroup
from angee.platform import models as platform_models
from tests.conftest import make_addon
from tests.test_platform_install import platform_tables as platform_tables


@pytest.fixture
def disabled_app(tmp_path, monkeypatch):
    """A real disabled app whose label and default selection belong to Django."""

    for name in tuple(sys.modules):
        if name == "arp" or name.startswith("arp."):
            monkeypatch.delitem(sys.modules, name)
    package = tmp_path / "arp" / "base"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "addon.toml").write_text('[addon]\nname = "arp.base"\ndepends_on = ["example.loaded"]\n')
    (package / "models.py").write_text('raise AssertionError("disabled models imported")\n')
    (package / "apps.py").write_text(
        "from django.apps import AppConfig\n"
        "class BaseConfig(AppConfig):\n"
        "    name = 'arp.base'\n"
        "    label = 'arp'\n"
        "    default = True\n"
        "    def ready(self):\n"
        "        raise AssertionError('disabled ready called')\n"
        "class SelectedConfig(BaseConfig):\n"
        "    label = 'arp_selected'\n"
        "    default = False\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        yield package
    finally:
        for name in tuple(sys.modules):
            if name == "arp" or name.startswith("arp."):
                sys.modules.pop(name)


def test_manifest_binding_reuses_native_parse_and_refreshes_a_new_graph(tmp_path, monkeypatch) -> None:
    """Graph, capabilities and dependency projection share one unchanged parser result."""

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
        {"name": "rename_owner", "app_label": "demo", "module": "runtime_migrations.rename_owner",
         "compatible_source_sha256": ["a" * 64]},
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
        manifest, origin = available[name]
        assert manifest.name == name
        assert isinstance(origin, Path)


def test_available_addons_includes_local_addon_dirs_without_imports(tmp_path, monkeypatch) -> None:
    """Filesystem discovery reads declarations even when application imports fail."""

    addon = tmp_path / "example" / "demo"
    addon.mkdir(parents=True)
    (addon / "addon.toml").write_text('[addon]\nname = "example.demo"\n')
    (addon / "__init__.py").write_text('raise AssertionError("discovery imported the app")\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(addon_module.metadata, "entry_points", lambda **kwargs: [])

    available = available_addons([tmp_path])

    manifest, origin = available["example.demo"]
    assert manifest.name == "example.demo"
    assert origin == addon


def test_available_addons_reads_local_manifest_through_upstream_discovery(tmp_path, monkeypatch) -> None:
    """Catalog discovery delegates source ordering and parsing to hatch-angee."""

    addon = tmp_path / "example" / "contract"
    manifest = AddonManifest(name="example.contract")
    seen = []

    def discover(roots):
        seen.append(tuple(roots))
        return [(addon, manifest), (tmp_path / "shadowed", AddonManifest(name=manifest.name))]

    monkeypatch.setattr("angee.addons.discover", discover)
    available = available_addons([tmp_path])
    assert seen == [(tmp_path,)]
    assert available[manifest.name][0] is manifest
    assert available[manifest.name][1] is addon


@pytest.mark.parametrize("duplicate", [False, True])
def test_installed_discovery_keeps_native_origins_and_precedence(tmp_path, monkeypatch, duplicate) -> None:
    """Installed origins win over local roots, while duplicate installed names fail."""

    package = tmp_path / "editable_demo"
    package.mkdir()
    (package / "addon.toml").write_text('[addon]\nname = "example.editable"\n', encoding="utf-8")
    entry_point = EntryPoint(
        name="example.editable",
        value="editable_demo:apps.EditableConfig",
        group="angee.addons",
    )
    manifest = AddonManifest(name=entry_point.name, description="installed")
    competing = EntryPoint(name=entry_point.name, value="other_package:OtherConfig", group="angee.addons")
    spec = ModuleSpec("editable_demo", loader=None, is_package=True)
    spec.submodule_search_locations = [str(package)]
    monkeypatch.setattr(
        addon_module.metadata, "entry_points", lambda **kwargs: [entry_point, competing] if duplicate else [entry_point]
    )
    monkeypatch.setattr(addon_module.importlib.util, "find_spec", lambda name: spec)
    monkeypatch.setattr(addon_module, "parse_manifest", lambda marker: manifest)
    monkeypatch.setattr(
        addon_module, "discover", lambda roots: [(package, AddonManifest(name=entry_point.name, description="local"))]
    )

    if duplicate:
        with pytest.raises(ImproperlyConfigured, match="Duplicate installed addon entry point") as error:
            available_addons(())
        assert entry_point.value in str(error.value)
        assert competing.value in str(error.value)
        return

    available = available_addons(())

    assert available[entry_point.name][0] is manifest
    assert available[entry_point.name][1] is entry_point
    assert "editable_demo" not in sys.modules


@pytest.mark.parametrize(
    ("declaration", "label"),
    [
        ("arp.base", "arp"),
        ("arp.base.apps.BaseConfig", "arp"),
        ("arp.base.apps.SelectedConfig", "arp_selected"),
    ],
)
def test_disabled_app_identity_uses_native_config_without_population(disabled_app, declaration, label) -> None:
    before = tuple(apps.get_app_configs())

    config = resolve_app_config(declaration)

    assert config is not None
    assert (config.name, config.label, config.path) == ("arp.base", label, str(disabled_app))
    assert config.apps is None
    assert config.models is None
    assert "arp.base.models" not in sys.modules
    assert tuple(apps.get_app_configs()) == before


def test_app_identity_reuses_the_populated_config(monkeypatch) -> None:
    config = apps.get_app_config("platform")

    def unexpected_create(value):
        raise AssertionError(f"recreated populated app {value}")

    monkeypatch.setattr(addon_module.AppConfig, "create", unexpected_create)

    assert resolve_app_config(config.name) is config
    with pytest.raises(ImproperlyConfigured, match="disagrees with AppConfig.name"):
        resolve_app_config(config.name, expected_name="example.other")


def test_unresolvable_app_identity_is_unknown() -> None:
    assert resolve_app_config("example_missing_app.base") is None


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        ("raise RuntimeError('optional dependency failed')\n", "optional dependency failed"),
        (
            "from django.apps import AppConfig\n"
            "class FirstConfig(AppConfig):\n"
            "    name = 'arp.base'\n"
            "    default = True\n"
            "class SecondConfig(FirstConfig):\n"
            "    default = True\n",
            "more than one default AppConfig",
        ),
    ],
)
def test_disabled_config_failure_is_reported_without_breaking_discovery(
    disabled_app, caplog, source, diagnostic
) -> None:
    (disabled_app / "apps.py").write_text(source)
    before = tuple(apps.get_app_configs())

    assert resolve_app_config("arp.base") is None

    assert "arp.base" in caplog.text
    assert diagnostic in caplog.text
    assert tuple(apps.get_app_configs()) == before


def test_discovery_cannot_claim_another_apps_identity(disabled_app) -> None:
    del disabled_app
    with pytest.raises(ImproperlyConfigured, match="disagrees with AppConfig.name"):
        resolve_app_config("arp.base", expected_name="example.other")


def test_manifest_root_resolution_uses_exact_app_config_aliases() -> None:
    """The import-free graph owner resolves authored config paths without prefix guessing."""

    dependency = AddonManifest(name="example.dependency")
    root = AddonManifest(name="example.root", depends_on=("example.dependency",))

    resolved = resolve_manifest_roots(
        ("example.root.apps.RootConfig", "django.contrib.auth"),
        (root, dependency),
        aliases={"example.root.apps.RootConfig": "example.root"},
    )

    assert [manifest.name for manifest in resolved] == ["example.dependency", "example.root"]


def test_reconciliation_projects_native_facts_and_preserves_catalogue_history(
    platform_tables, disabled_app, tmp_path, monkeypatch
) -> None:
    """Direct dependencies survive every state; live counts and admission follow Django."""

    del platform_tables
    addon = apps.get_model("platform", "Addon")
    line = apps.get_model("linesdemo", "SaleLine")
    loaded = make_addon(name="example.loaded", path=tmp_path / "loaded", depends_on=("django.contrib.auth",))
    loaded.apps = apps
    loaded.models = {"saleline": line}
    loaded.angee_addon_root = True
    loaded.angee_forced = False
    installed_manifest = addon_module.parse_manifest(disabled_app / "addon.toml")
    local_manifest = AddonManifest(name="example.unavailable", depends_on=("arp.base",))
    available = {
        loaded.name: (addon_manifest(loaded), Path(loaded.path)),
        "arp.base": (installed_manifest, EntryPoint(name="arp.base", value="arp.base", group="angee.addons")),
        local_manifest.name: (local_manifest, tmp_path / "unavailable"),
    }
    count_aliases = []

    def resource_counts(*, using):
        count_aliases.append(using)
        return {loaded.name: 7, "arp.base": 99}

    monkeypatch.setattr(platform_models, "available_addons", lambda dirs: available)
    monkeypatch.setattr(platform_models.composed, "addons", lambda: [loaded])
    monkeypatch.setattr(platform_models.composed, "root_app_aliases", lambda: {})
    monkeypatch.setattr(platform_models.composed, "resource_counts", resource_counts)
    with system_context(reason="test.platform.reconcile-native-facts"):
        addon.objects.create(
            name="arp.base",
            state=addon.State.ENABLED,
            forced=True,
            pending=True,
            model_count=9,
            field_count=9,
            resource_count=9,
            model_labels=["stale.Model"],
        )
        historical = addon.objects.create(
            name="example.gone",
            source=addon.Source.LOCAL,
            state=addon.State.ENABLED,
            depends_on=["example.historical_dependency"],
            forced=True,
            pending=True,
            model_count=9,
        )
        remote = addon.objects.create(
            name="example.remote",
            source=addon.Source.REMOTE,
            depends_on=["example.remote_dependency"],
            vcs_path="addons/remote",
        )
        materialised = addon.objects.create(
            name=local_manifest.name,
            source=addon.Source.REMOTE,
            vcs_path="addons/unavailable",
        )

        addon.objects.reconcile_from_registry("default", desired=frozenset({loaded.name}))

        enabled = addon.objects.get(name=loaded.name)
        disabled = addon.objects.get(name="arp.base")
        historical.refresh_from_db()
        remote.refresh_from_db()
        materialised.refresh_from_db()
    assert count_aliases == ["default"]
    assert (enabled.state, enabled.source, enabled.kind) == (
        addon.State.ENABLED,
        addon.Source.LOCAL,
        addon.Kind.CONSUMER,
    )
    assert enabled.depends_on == ["django.contrib.auth"]
    assert enabled.depended_by == ["arp.base"]
    assert enabled.forced is False
    assert enabled.pending is False
    assert (enabled.model_count, enabled.resource_count) == (1, 7)
    assert enabled.field_count == len(line._meta.fields) + len(line._meta.many_to_many)
    assert enabled.model_labels == [line._meta.label_lower]
    assert (disabled.state, disabled.source, disabled.label) == (addon.State.DISABLED, addon.Source.INSTALLED, "arp")
    assert disabled.depends_on == [loaded.name]
    assert disabled.depended_by == [local_manifest.name]
    assert (disabled.model_count, disabled.field_count, disabled.resource_count) == (0, 0, 0)
    assert disabled.model_labels == []
    assert (disabled.forced, disabled.pending) == (False, False)
    assert historical.state == addon.State.REMOVED
    assert historical.depends_on == ["example.historical_dependency"]
    assert (historical.forced, historical.pending, historical.model_count) == (False, False, 0)
    assert (remote.source, remote.state, remote.depends_on, remote.vcs_path) == (
        addon.Source.REMOTE,
        addon.State.DISABLED,
        ["example.remote_dependency"],
        "addons/remote",
    )
    assert (materialised.source, materialised.state, materialised.label) == (
        addon.Source.LOCAL,
        addon.State.DISABLED,
        "",
    )
    assert materialised.depends_on == ["arp.base"]
    assert materialised.vcs_path == "addons/unavailable"
    assert str(materialised) == local_manifest.name


@pytest.mark.parametrize(
    ("declaration", "label"),
    [("arp.base", "arp"), ("arp.base.apps.SelectedConfig", "arp_selected")],
)
def test_disabled_config_selection_drives_catalogue_pending_and_install_preview(
    platform_tables, disabled_app, tmp_path, settings, monkeypatch, declaration, label
) -> None:
    """Exact desired roots preserve native config selection without enabling the app."""

    del platform_tables
    addon = apps.get_model("platform", "Addon")
    manifest = addon_module.parse_manifest(disabled_app / "addon.toml")
    monkeypatch.setattr(platform_models, "available_addons", lambda dirs: {manifest.name: (manifest, disabled_app)})
    monkeypatch.setattr(platform_models.composed, "addons", lambda: [])
    monkeypatch.setattr(platform_models.composed, "root_app_aliases", lambda: {})
    monkeypatch.setattr(platform_models.composed, "resource_counts", lambda **kwargs: {})
    settings.BASE_DIR = tmp_path
    settings.ANGEE_PROJECT_YAML_SETTINGS = frozenset({"INSTALLED_APPS"})
    (tmp_path / "settings.yaml").write_text(f"INSTALLED_APPS:\n  - {declaration}\n")
    before = tuple(apps.get_app_configs())

    with system_context(reason="test.platform.disabled-native-config"):
        addon.objects.reconcile_from_registry("default", desired=frozenset({declaration}))
        row = addon.objects.get(name=manifest.name)
        preview = addon.objects.change_preview(manifest.name, "install")

    assert (row.state, row.label, row.pending) == (addon.State.DISABLED, label, True)
    assert row.depends_on == list(manifest.depends_on)
    assert preview.can_apply is True
    assert preview.roots_after == preview.roots_before == (declaration,)
    assert [(impact.name, impact.label, impact.root) for impact in preview.addons_to_enable] == [
        (manifest.name, label, True)
    ]
    assert "arp.base.models" not in sys.modules
    assert tuple(apps.get_app_configs()) == before

    with system_context(reason="test.platform.unknown-desired-preserves-pending"):
        addon.objects.reconcile_from_registry("default", desired=None)
        row.refresh_from_db()
    assert row.pending is True


def test_install_preview_keeps_unresolvable_identity_unknown(tmp_path, settings, monkeypatch) -> None:
    manifest = AddonManifest(name="unavailable_package.addon", depends_on=("unavailable_package.dependency",))
    monkeypatch.setattr(platform_models, "available_addons", lambda dirs: {manifest.name: (manifest, tmp_path)})
    monkeypatch.setattr(platform_models.composed, "addons", lambda: [])
    monkeypatch.setattr(platform_models.composed, "root_app_aliases", lambda: {})
    settings.BASE_DIR = tmp_path
    settings.ANGEE_PROJECT_YAML_SETTINGS = frozenset({"INSTALLED_APPS"})
    (tmp_path / "settings.yaml").write_text("INSTALLED_APPS: []\n")

    preview = apps.get_model("platform", "Addon").objects.change_preview(manifest.name, "install")

    assert preview.can_apply is True
    assert preview.roots_after == (manifest.name,)
    assert len(preview.addons_to_enable) == 1
    impact = preview.addons_to_enable[0]
    assert (impact.name, impact.label, impact.depends_on) == (manifest.name, "", manifest.depends_on)


def test_resource_counts_forward_the_requested_database_alias(monkeypatch) -> None:
    resource = apps.get_model("resources", "Resource")
    queryset_type = type(resource.objects.all())
    aliases = []
    routing = []

    def counts_by_addon(queryset):
        aliases.append(queryset.db)
        return {"example.addon": 3}

    def allow_migrate_model(alias, model):
        routing.append((alias, model))
        return True

    monkeypatch.setattr(queryset_type, "counts_by_addon", counts_by_addon)
    monkeypatch.setattr(platform_models.composed.router, "allow_migrate_model", allow_migrate_model)

    assert platform_models.composed.resource_counts(using="catalogue") == {"example.addon": 3}
    assert aliases == ["catalogue"]
    assert routing == [("catalogue", resource)]


@pytest.mark.parametrize("routed_here", [False, True])
def test_resource_counts_tolerate_routed_away_or_uncreated_ledger(monkeypatch, routed_here) -> None:
    resource = apps.get_model("resources", "Resource")
    queryset_type = type(resource.objects.all())
    queried = []

    def unavailable(queryset):
        queried.append(queryset.db)
        raise DatabaseError("resource ledger does not exist yet")

    monkeypatch.setattr(queryset_type, "counts_by_addon", unavailable)
    monkeypatch.setattr(platform_models.composed.router, "allow_migrate_model", lambda alias, model: routed_here)

    assert platform_models.composed.resource_counts(using="catalogue") == {}
    assert queried == (["catalogue"] if routed_here else [])


@pytest.mark.parametrize("addon_count", [1, 3])
def test_unknown_desired_reads_pending_flags_once(platform_tables, tmp_path, monkeypatch, addon_count) -> None:
    del platform_tables
    addon = apps.get_model("platform", "Addon")
    pending = {f"pending_fixture.addon_{index}": bool(index % 2) for index in range(addon_count)}
    available = {name: (AddonManifest(name=name), tmp_path / name) for name in pending}
    monkeypatch.setattr(platform_models, "available_addons", lambda dirs: available)
    monkeypatch.setattr(platform_models.composed, "addons", lambda: [])
    monkeypatch.setattr(platform_models.composed, "root_app_aliases", lambda: {})
    monkeypatch.setattr(platform_models.composed, "resource_counts", lambda **kwargs: {})

    with system_context(reason="test.platform.pending-query-count"):
        for name, value in pending.items():
            addon.objects.create(name=name, pending=value)
        pending_sql = str(addon.objects.using("default").values_list("name", "pending").query)

        with CaptureQueriesContext(connection) as queries:
            addon.objects.reconcile_from_registry("default", desired=None)

        assert sum(query["sql"] == pending_sql for query in queries) == 1
        assert dict(addon.objects.values_list("name", "pending")) == pending


def test_pending_changes_is_unknown_when_project_yaml_is_not_effective(settings, monkeypatch) -> None:
    """An override is not falsely treated as either pending or settled."""

    settings.ANGEE_PROJECT_YAML_SETTINGS = frozenset()
    monkeypatch.setattr(platform_models.composed, "root_app_names", lambda: frozenset({"example.demo"}))
    monkeypatch.setattr(
        platform_models,
        "available_addons",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("catalogue read")),
    )

    assert apps.get_model("platform", "Addon").objects.pending_changes() is None


@pytest.mark.parametrize(
    ("desired", "loaded", "expected"),
    [
        (("example.demo",), frozenset({"example.demo"}), False),
        (("example.demo", "example.added"), frozenset({"example.demo"}), True),
        (("example.demo",), frozenset({"example.demo", "example.removed"}), True),
    ],
)
def test_pending_changes_compares_authored_roots_only(
    tmp_path, settings, monkeypatch, desired, loaded, expected
) -> None:
    """Injected runtime defaults cannot create drift; actual root edits still do."""

    roots = "\n".join(f"  - {name}" for name in desired)
    (tmp_path / "settings.yaml").write_text(f"INSTALLED_APPS:\n{roots}\n", encoding="utf-8")
    settings.BASE_DIR = tmp_path
    settings.ANGEE_PROJECT_YAML_SETTINGS = frozenset({"INSTALLED_APPS"})
    monkeypatch.setattr(platform_models.composed, "root_app_names", lambda: loaded)

    assert apps.get_model("platform", "Addon").objects.pending_changes() is expected


def test_loaded_root_pending_and_forced_admission_follow_the_composed_graph(
    platform_tables, tmp_path, settings, monkeypatch
) -> None:
    """Only roots queue a disable; stale persisted flags cannot override the live graph."""

    del platform_tables
    addon = apps.get_model("platform", "Addon")
    root = make_addon(name="example.root", path=tmp_path / "root", depends_on=("example.dep",))
    dependency = make_addon(name="example.dep", path=tmp_path / "dep")
    configs = AppGraph().resolve((root, dependency), declared_roots=(root,))
    for config in configs:
        config.apps = apps
        config.models = {}
    monkeypatch.setattr(platform_models, "available_addons", lambda dirs: {})
    monkeypatch.setattr(platform_models.composed, "addons", lambda: list(configs))
    monkeypatch.setattr(platform_models.composed, "root_app_aliases", lambda: {})
    monkeypatch.setattr(platform_models.composed, "resource_counts", lambda **kwargs: {})
    settings.BASE_DIR = tmp_path
    settings.ANGEE_PROJECT_YAML_SETTINGS = frozenset({"INSTALLED_APPS"})
    (tmp_path / "settings.yaml").write_text(f"INSTALLED_APPS:\n  - {root.name}\n")

    with system_context(reason="test.platform.loaded-root-pending"):
        addon.objects.reconcile_from_registry("default", desired=frozenset())
        root_row = addon.objects.get(name=root.name)
        dependency_row = addon.objects.get(name=dependency.name)
        assert root_row.pending is True
        assert dependency_row.pending is False
        assert dependency_row.forced is True
        addon.objects.filter(name=dependency.name).update(forced=False, depended_by=[])
        addon.objects.filter(name=root.name).update(forced=True, depended_by=[dependency.name])
        refused = addon.objects.change_preview(dependency.name, "disable")
        assert refused.can_apply is False
        assert "required" in refused.refusal
        assert addon.objects.change_preview(root.name, "disable").can_apply is True

        addon.objects.reconcile_from_registry("default", desired=frozenset({root.name}))
        root_row.refresh_from_db()
        assert root_row.pending is False


def test_failed_refresh_cannot_leave_a_previous_manifest_available(tmp_path) -> None:
    config = make_addon(name="example.demo", path=tmp_path)
    assert addon_manifest(config).name == "example.demo"
    (tmp_path / "addon.toml").write_text("invalid toml", encoding="utf-8")
    with pytest.raises(ImproperlyConfigured):
        AppGraph().resolve((config,))
    with pytest.raises(ImproperlyConfigured):
        addon_manifest(config)
