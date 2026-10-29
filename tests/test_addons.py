"""Installed catalogue metadata and marketplace projections preserve native ownership."""

from __future__ import annotations

import json
import sys
from importlib import metadata
from importlib.metadata import EntryPoint
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.apps import AppConfig, apps
from django.db import DatabaseError, connection, connections
from django.test.utils import CaptureQueriesContext, isolate_apps
from django_compose.addons import enabled_apps
from rebac import system_context

import tests.test_platform_install  # noqa: F401 -- register fixture models before database setup
from angee.platform import models as platform_models
from angee.platform.discovery import available_addons, resolve_catalogue_roots
from angee.resources.models import Resource as AbstractResource
from tests.conftest import catalogue_package, make_addon


@pytest.mark.parametrize("editable", [False, True])
def test_catalogue_reads_package_data_without_importing_disabled_apps(tmp_path, monkeypatch, editable) -> None:
    """Wheel RECORD and editable PEP 610 routes never execute application code."""

    name = "catalogue_probe.disabled"
    package = tmp_path / "catalogue_probe" / "disabled"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text('raise AssertionError("parent imported")\n')
    (package / "__init__.py").write_text('raise AssertionError("disabled app imported")\n')
    (package / "pyproject.toml").write_text(
        '[project]\nname="catalogue-probe-disabled"\nversion="1.0"\n'
        'description="Installed disabled addon"\n'
        '[project.entry-points."angee.addons"]\n"catalogue_probe.disabled"="catalogue_probe.disabled"\n'
        '[tool.angee.platform]\ncategory="Foundation"\n'
    )
    dist_info = tmp_path / "catalogue_probe_disabled-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        'Metadata-Version: 2.4\nName: catalogue-probe-disabled\nVersion: 1.0\n'
        'Summary: Installed disabled addon\n'
    )
    (dist_info / "entry_points.txt").write_text(f"[angee.addons]\n{name} = {name}\n")
    if editable:
        (dist_info / "direct_url.json").write_text(json.dumps({
            "url": package.as_uri(), "dir_info": {"editable": True},
        }))
    else:
        (dist_info / "RECORD").write_text("catalogue_probe/disabled/pyproject.toml,,\n")
    dist = metadata.PathDistribution(dist_info)
    installed = tuple(metadata.distributions())
    monkeypatch.setattr(metadata, "distributions", lambda **params: (*installed, dist))
    monkeypatch.syspath_prepend(str(tmp_path))

    available = available_addons()

    facts, entry_point = available[name]
    assert facts["name"] == name
    assert facts["description"] == "Installed disabled addon"
    assert facts["category"] == "Foundation"
    assert entry_point.dist.metadata["Name"] == "catalogue-probe-disabled"
    assert "catalogue_probe" not in sys.modules
    assert name not in sys.modules


def test_available_addons_uses_installed_entry_points() -> None:
    available = available_addons()
    assert "angee" not in available
    for name in ("angee.base", "angee.jobs", "angee.web", "angee.graphql", "angee.iam"):
        facts, origin = available[name]
        assert facts["name"] == name
        assert isinstance(origin, EntryPoint)


def test_package_root_resolution_uses_exact_app_config_aliases() -> None:
    dependency = catalogue_package("example.dependency")
    root = catalogue_package("example.root", depends_on=("example.dependency",))
    resolved = resolve_catalogue_roots(
        ("example.root.apps.RootConfig", "django.contrib.auth"), (root, dependency),
        aliases={"example.root.apps.RootConfig": "example.root"},
    )
    assert [package["name"] for package in resolved] == ["example.dependency", "example.root"]


def test_reconciliation_projects_native_facts_and_preserves_catalogue_history(
    composed_tables, tmp_path, monkeypatch
) -> None:
    """Direct dependencies survive every state; live counts and admission follow Django."""

    with monkeypatch.context() as patch:
        del composed_tables
        addon = apps.get_model("platform", "Addon")
        line = apps.get_model("linesdemo", "DocumentLine")
        loaded = make_addon(name="fakeaddon.loaded", path=tmp_path / "loaded", depends_on=("django.contrib.auth",))
        loaded.apps = apps
        loaded.models = {"documentline": line}
        installed_manifest = catalogue_package("fakeaddon.base", depends_on=(loaded.name,))
        local_manifest = catalogue_package(name="example.unavailable", depends_on=("fakeaddon.base",))
        available = {
            loaded.name: (catalogue_package(loaded.name), Path(loaded.path)),
            "fakeaddon.base": (
                installed_manifest,
                EntryPoint(name="fakeaddon.base", value="fakeaddon.base", group="angee.addons"),
            ),
            local_manifest["name"]: (local_manifest, tmp_path / "unavailable"),
        }
        def resource_counts():
            return {loaded.name: 7, "fakeaddon.base": 99}

        patch.setattr(platform_models, "available_addons", lambda: available)
        patch.setattr(platform_models.composed, "addons", lambda: [loaded])
        patch.setattr(platform_models.composed, "root_app_aliases", lambda: {loaded.name: loaded.name})
        patch.setattr(platform_models.composed, "resource_counts", resource_counts)
        with system_context(reason="test.platform.reconcile-native-facts"):
            addon.objects.create(
                name="fakeaddon.base",
                state=addon.AddonState.ENABLED,
                forced=True,
                pending=True,
                model_count=9,
                field_count=9,
                resource_count=9,
                model_labels=["stale.Model"],
            )
            historical = addon.objects.create(
                name="example.gone",
                source=addon.AddonSource.LOCAL,
                state=addon.AddonState.ENABLED,
                depends_on=["example.historical_dependency"],
                forced=True,
                pending=True,
                model_count=9,
            )
            remote = addon.objects.create(
                name="example.remote",
                source=addon.AddonSource.REMOTE,
                depends_on=["example.remote_dependency"],
                vcs_path="addons/remote",
            )
            materialised = addon.objects.create(
                name=local_manifest["name"],
                source=addon.AddonSource.REMOTE,
                vcs_path="addons/unavailable",
            )

            addon.objects.reconcile_from_registry(desired=frozenset({loaded.name}))

            enabled = addon.objects.get(name=loaded.name)
            disabled = addon.objects.get(name="fakeaddon.base")
            historical.refresh_from_db()
            remote.refresh_from_db()
            materialised.refresh_from_db()
        assert (enabled.state, enabled.source, enabled.kind) == (
            addon.AddonState.ENABLED,
            addon.AddonSource.INSTALLED,
            addon.AddonKind.CONSUMER,
        )
        assert enabled.distribution == "fakeaddon-loaded"
        assert enabled.requirements == []
        assert enabled.depends_on == []
        assert enabled.depended_by == ["fakeaddon.base"]
        assert enabled.forced is False
        assert enabled.pending is False
        assert (enabled.model_count, enabled.resource_count) == (1, 7)
        assert enabled.field_count == len(line._meta.fields) + len(line._meta.many_to_many)
        assert enabled.model_labels == [line._meta.label_lower]
        assert (disabled.state, disabled.source, disabled.label) == (
            addon.AddonState.DISABLED, addon.AddonSource.INSTALLED, ""
        )
        assert disabled.depends_on == [loaded.name]
        assert disabled.depended_by == [local_manifest["name"]]
        assert (disabled.model_count, disabled.field_count, disabled.resource_count) == (0, 0, 0)
        assert disabled.model_labels == []
        assert (disabled.forced, disabled.pending) == (False, False)
        assert historical.state == addon.AddonState.REMOVED
        assert historical.depends_on == ["example.historical_dependency"]
        assert (historical.forced, historical.pending, historical.model_count) == (False, False, 0)
        assert (remote.source, remote.state, remote.depends_on, remote.vcs_path) == (
            addon.AddonSource.REMOTE,
            addon.AddonState.DISABLED,
            ["example.remote_dependency"],
            "addons/remote",
        )
        assert (materialised.source, materialised.state, materialised.label) == (
            addon.AddonSource.INSTALLED,
            addon.AddonState.DISABLED,
            "",
        )
        assert materialised.depends_on == ["fakeaddon.base"]
        assert materialised.vcs_path == "addons/unavailable"
        assert str(materialised) == local_manifest["name"]


def test_enable_preview_uses_canonical_name_when_django_label_is_unknown(tmp_path, settings, monkeypatch) -> None:
    """An unresolved addon's display label falls back to its canonical name."""

    manifest = catalogue_package(name="unavailable_package.addon", depends_on=("unavailable_package.dependency",))
    monkeypatch.setattr(platform_models, "available_addons", lambda: {manifest["name"]: (manifest, tmp_path)})
    monkeypatch.setattr(platform_models.composed, "addons", lambda: [])
    monkeypatch.setattr(platform_models.composed, "root_app_aliases", lambda: {})
    settings.BASE_DIR = tmp_path
    settings.ANGEE_PROJECT_YAML_SETTINGS = frozenset({"INSTALLED_APPS"})
    (tmp_path / "settings.yaml").write_text("INSTALLED_APPS: []\n")

    preview = apps.get_model("platform", "Addon").objects.change_preview(manifest["name"], "enable")

    assert preview.can_apply is True
    assert preview.roots_after == (manifest["name"],)
    assert len(preview.addons_to_enable) == 1
    impact = preview.addons_to_enable[0]
    assert (impact.name, impact.label, impact.depends_on) == (
        manifest["name"], manifest["name"], manifest["depends_on"],
    )


@pytest.fixture
def resource_model(monkeypatch):
    """Compose the source ledger in an isolated native registry for projection tests."""

    with isolate_apps("angee.resources") as registry:

        class Resource(AbstractResource):
            class Meta(AbstractResource.Meta):
                app_label = "resources"
                abstract = False

        monkeypatch.setattr(platform_models.composed, "apps", registry)
        yield Resource


def test_resource_counts_tolerate_uncreated_ledger(monkeypatch, resource_model) -> None:
    resource = resource_model
    queryset_type = type(resource.objects.all())
    queried = []

    def unavailable(queryset):
        queried.append(queryset.db)
        raise AssertionError("a missing ledger must not be queried")

    monkeypatch.setattr(queryset_type, "counts_by_addon", unavailable)
    monkeypatch.setattr(
        connections[resource.objects.db].introspection,
        "table_names",
        lambda: [],
    )

    assert platform_models.composed.resource_counts() == {}
    assert queried == []


def test_resource_counts_propagate_database_failures(monkeypatch, resource_model) -> None:
    """An existing ledger's database failure aborts reconciliation instead of reporting zero."""

    resource = resource_model

    def unavailable(queryset):
        raise DatabaseError("ledger unavailable")

    monkeypatch.setattr(type(resource.objects.all()), "counts_by_addon", unavailable)
    monkeypatch.setattr(
        connections[resource.objects.db].introspection,
        "table_names",
        lambda: [resource._meta.db_table],
    )

    with pytest.raises(DatabaseError, match="ledger unavailable"):
        platform_models.composed.resource_counts()


@pytest.mark.parametrize("addon_count", [1, 3])
def test_unknown_desired_reads_pending_flags_once(composed_tables, tmp_path, monkeypatch, addon_count) -> None:
    with monkeypatch.context() as patch:
        del composed_tables
        addon = apps.get_model("platform", "Addon")
        pending = {f"pending_fixture.addon_{index}": bool(index % 2) for index in range(addon_count)}
        available = {name: (catalogue_package(name=name), tmp_path / name) for name in pending}
        patch.setattr(platform_models, "available_addons", lambda: available)
        patch.setattr(platform_models.composed, "addons", lambda: [])
        patch.setattr(platform_models.composed, "root_app_aliases", lambda: {})
        patch.setattr(platform_models.composed, "resource_counts", lambda **kwargs: {})

        with system_context(reason="test.platform.pending-query-count"):
            addon.objects.all().delete()
            for name, value in pending.items():
                addon.objects.create(name=name, pending=value)
            pending_sql = str(addon.objects.values_list("name", "pending").query)

            with CaptureQueriesContext(connection) as queries:
                addon.objects.reconcile_from_registry(desired=None)

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
    composed_tables, tmp_path, settings, monkeypatch
) -> None:
    """Only roots queue a disable; stale persisted flags cannot override the live graph."""

    with monkeypatch.context() as patch:
        del composed_tables
        addon = apps.get_model("platform", "Addon")
        root = make_addon(name="example.root", path=tmp_path / "root", depends_on=("example.dep",))
        dependency = make_addon(name="example.dep", path=tmp_path / "dep")
        closure = enabled_apps((root.name,), group="angee.addons")
        configs = tuple(AppConfig.create(entry) for entry in closure.entries)
        for config in configs:
            config.apps = apps
            config.models = {}
        patch.setattr(platform_models, "available_addons", lambda: {
            config.name: (catalogue_package(config.name, depends_on=closure.dependencies[config.name]), None)
            for config in configs
        })
        patch.setattr(platform_models.composed, "addons", lambda: list(configs))
        patch.setattr(platform_models.composed, "root_app_aliases", lambda: {root.name: root.name})
        patch.setattr(platform_models.composed, "resource_counts", lambda **kwargs: {})
        settings.BASE_DIR = tmp_path
        settings.ANGEE_PROJECT_YAML_SETTINGS = frozenset({"INSTALLED_APPS"})
        (tmp_path / "settings.yaml").write_text(f"INSTALLED_APPS:\n  - {root.name}\n")

        with system_context(reason="test.platform.loaded-root-pending"):
            addon.objects.reconcile_from_registry(desired=frozenset())
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

            addon.objects.reconcile_from_registry(desired=frozenset({root.name}))
            root_row.refresh_from_db()
            assert root_row.pending is False


def test_composed_membership_reuses_native_selected_closure(settings, monkeypatch) -> None:
    """A retained closure supplies membership without rediscovering installed packages."""

    from angee.platform import composed

    settings.COMPOSE_CLOSURE = SimpleNamespace(addons=frozenset({"angee.base"}))
    monkeypatch.setattr(
        composed, "installed_addons",
        lambda *args: (_ for _ in ()).throw(AssertionError("installed metadata rediscovered")),
    )
    assert [config.name for config in composed.addons()] == ["angee.base"]
