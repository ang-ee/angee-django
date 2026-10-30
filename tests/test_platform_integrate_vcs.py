"""VCS discovery reconciles declarations and provenance into the one Addon row."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from rebac import system_context

import tests.test_marketplace_graphql  # noqa: F401 -- register the fixture model graph before database setup
from angee.integrate_vcs.backend import RepoDescriptor, VCSBackend
from angee.operator.daemon import OperatorDaemon, OperatorDaemonError
from angee.platform.discovery import facts_from_distribution, installed_entry_points
from angee.platform_integrate_vcs import catalog
from tests.conftest import Addon, Repository, Source, VcsBridge, catalogue_package, make_addon, make_integration

_BUILD_SYSTEM = """
[build-system]
requires=["hatchling>=1.27"]
build-backend="hatchling.build"
[tool.hatch.build.targets.wheel]
bypass-selection=true
"""


class CatalogueBackend(VCSBackend):
    """Remote test transport; only the isolated metadata producer sees fixture files."""

    key = "local"

    def ls_repos(self, *, org=""):
        return [RepoDescriptor(name="catalogue", org="tests", remote="https://example.com/catalogue.git")]

    def rev_parse(self, repository, ref):
        return "a" * 40


def fixture_metadata(root: Path, package_path: str) -> str:
    """Run the real trusted worker against test-authored projects offline.

    Minimal Hatchling fixtures explicitly package their capability document.
    Production never adds or interprets authored identity metadata this way.
    """

    image = os.environ.get("CATALOGUE_METADATA_TEST_IMAGE", "catalogue-metadata:prepared")
    worker = Path(catalog.__file__).with_name("metadata_worker.py")
    with tempfile.TemporaryDirectory(prefix="catalogue-fixture-", dir="/private/tmp") as temporary:
        snapshot = Path(temporary) / "repository"
        shutil.copytree(root, snapshot)
        snapshot.chmod(0o755)
        for marker in snapshot.rglob("pyproject.toml"):
            document = tomllib.loads(marker.read_text())
            points = document.get("project", {}).get("entry-points", {}).get("angee.addons", {})
            if document.get("build-system", {}).get("build-backend") == "hatchling.build" and len(points) == 1:
                target = next(iter(points)).replace(".", "/") + "/pyproject.toml"
                marker.write_text(marker.read_text() + '\n[tool.hatch.build.targets.wheel.force-include]\n'
                                  + json.dumps("pyproject.toml") + "=" + json.dumps(target) + "\n")
        result = subprocess.run([
            "docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", "65532:65532",
            "--tmpfs", "/work:rw,uid=65532,gid=65532,size=536870912",
            "--tmpfs", "/tmp:rw,uid=65532,gid=65532,size=33554432",
            "--mount", f"type=bind,source={snapshot},target=/work/repository,readonly",
            "--mount", f"type=bind,source={worker},target=/work/artifact,readonly",
            "--env", f"PACKAGE_PATH={package_path}", image,
        ], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise OperatorDaemonError(result.stderr[-8192:])
        return result.stdout


@pytest.fixture()
def catalog_source(composed_tables: None, tmp_path: Path, monkeypatch, settings) -> tuple[Source, Path]:
    """Provide a remote source whose native metadata job runs test-authored fixture builds."""

    del composed_tables
    marker = tmp_path / "addons" / "base" / "pyproject.toml"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        (
            '[project]\n'
            'name = "catalog-remote-base"\n'
            'version = "1.0.0"\n'
            'description = "Remote declaration"\n'
            'keywords = ["catalogue"]\n'
            'dependencies = ["angee-iam", "catalog-remote-dependency"]\n'
            '[project.entry-points."angee.addons"]\n'
            '"catalog_remote.base" = "catalog_remote.base"\n'
            '[tool.angee.platform]\n'
            'category = "Integration"\n'
            + _BUILD_SYSTEM
        )
    )
    sibling = marker.parent.parent / "dependency" / "pyproject.toml"
    sibling.parent.mkdir()
    sibling.write_text(_BUILD_SYSTEM + '[project]\nname="catalog-remote-dependency"\nversion="1.0"\n'
                       '[project.entry-points."angee.addons"]\n"catalog_remote.dependency"="catalog_remote.dependency"\n')
    settings.ANGEE_VCS_BACKEND_CLASSES = {
        **settings.ANGEE_VCS_BACKEND_CLASSES,
        "local": "tests.test_platform_integrate_vcs.CatalogueBackend",
    }
    def run_job(daemon, name, **values):
        assert values["repository_url"] == "https://example.com/catalogue.git"
        assert values["revision"] == "a" * 40
        assert values["credential"] == {"username": "git", "password": "x"}
        assert not connection.in_atomic_block
        return {"id": "metadata-operation", "status": "succeeded",
                "output": fixture_metadata(tmp_path, values["package_path"])}
    monkeypatch.setattr(OperatorDaemon, "run_source_build", run_job)
    bridge = make_integration("addon-catalog", model=VcsBridge, backend_class="local")
    bridge.discover_repositories()
    with system_context(reason="test catalogue source"):
        repository = Repository.objects.get(name="catalogue")
        source = Source.objects.create(repository=repository, kind="addon", path="addons")
    return source, marker


def test_remote_catalogue_reconciles_metadata_and_retains_removed_declarations(
    catalog_source: tuple[Source, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remote rows retain their declaration and provenance after disappearing."""

    source, marker = catalog_source
    monkeypatch.setattr("angee.platform_integrate_vcs.models.available_addons", lambda: {})

    assert source.refresh() == 2
    with system_context(reason="test catalogue read"):
        row = Addon.objects.get(name="catalog_remote.base")
    assert row.label == ""
    assert str(row) == "catalog_remote.base"
    assert row.namespace == "catalog_remote"
    assert row.source == Addon.AddonSource.REMOTE
    assert row.state == Addon.AddonState.DISABLED
    assert row.description == "Remote declaration"
    assert row.keywords == ["catalogue"]
    assert row.category == "Integration"
    assert row.depends_on == ["angee.iam", "catalog_remote.dependency"]
    assert row.vcs_source_id == source.pk
    assert row.vcs_path == "addons/base"
    assert source.last_synced_at is not None

    marker.write_text(marker.read_text().replace("Remote declaration", "Updated declaration"))
    assert source.refresh() == 2
    with system_context(reason="test catalogue read"):
        row.refresh_from_db()
        assert Addon.objects.filter(name=row.name).count() == 1
    assert row.description == "Updated declaration"

    marker.unlink()
    assert source.refresh() == 1
    with system_context(reason="test catalogue read"):
        row.refresh_from_db()
    assert row.state == Addon.AddonState.REMOVED
    assert row.description == "Updated declaration"
    assert row.depends_on == ["angee.iam", "catalog_remote.dependency"]
    assert row.vcs_source_id == source.pk
    assert row.vcs_path == "addons/base"


@pytest.mark.parametrize("origin_kind", [Addon.AddonSource.LOCAL, Addon.AddonSource.INSTALLED])
def test_available_catalogue_updates_provenance_and_clears_runtime_facts_when_it_becomes_remote(
    catalog_source: tuple[Source, Path], monkeypatch: pytest.MonkeyPatch, origin_kind: str
) -> None:
    """Available metadata wins until materialization disappears; remote rows have no live counts."""

    source, marker = catalog_source
    manifest = catalogue_package(name="catalog_remote.base", depends_on=("local.dependency",))
    origin = (
        marker.parent
        if origin_kind == Addon.AddonSource.LOCAL
        else EntryPoint(name=manifest["name"], value=manifest["name"], group="angee.addons")
    )
    available = {manifest["name"]: (manifest, origin)}
    monkeypatch.setattr("angee.platform_integrate_vcs.models.available_addons", lambda: available)
    with system_context(reason="test catalogue setup"):
        row = Addon.objects.create(
            name=manifest["name"],
            label="native_label",
            source=origin_kind,
            state=Addon.AddonState.ENABLED,
            description="Available declaration",
            depends_on=list(manifest["depends_on"]),
            forced=True,
            pending=True,
            model_count=2,
            field_count=7,
            resource_count=3,
            model_labels=["native_label.Record"],
            depended_by=["local.consumer"],
        )

    assert source.refresh() == 2
    with system_context(reason="test catalogue read"):
        row.refresh_from_db()
    assert row.source == origin_kind
    assert row.state == Addon.AddonState.ENABLED
    assert row.label == "native_label"
    assert row.description == "Available declaration"
    assert row.depends_on == ["local.dependency"]
    assert row.model_count == 2
    assert row.forced is True
    assert row.vcs_source_id == source.pk
    assert row.vcs_path == "addons/base"

    available.clear()
    assert source.refresh() == 2
    with system_context(reason="test catalogue read"):
        row.refresh_from_db()
    assert row.source == Addon.AddonSource.REMOTE
    assert row.state == Addon.AddonState.DISABLED
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
    marker.write_text(_BUILD_SYSTEM + (
                          '[project]\n'
                          'name = "catalog-remote-identity-base"\n'
                          'version = "1.0.0"\n'
                          'dependencies = ["catalog-remote-dependency"]\n'
                          '[project.entry-points."angee.addons"]\n'
                          '"catalog_remote_identity.base" = "catalog_remote_identity.base"\n'
                      ))
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
    monkeypatch.setattr("angee.platform_integrate_vcs.models.available_addons", lambda: {})
    assert "catalog_remote_identity" not in sys.modules

    assert source.refresh() == 2
    with system_context(reason="test catalogue read"):
        row = Addon.objects.get(name="catalog_remote_identity.base")
    assert row.label == ""
    assert row.state == Addon.AddonState.DISABLED
    assert row.depends_on == ["catalog_remote.dependency"]
    assert not any(
        name == "catalog_remote_identity" or name.startswith("catalog_remote_identity.") for name in sys.modules
    )


def test_catalogue_reverse_dependencies_include_live_remote_but_not_historical_declarers(composed_tables):
    with system_context(reason="test live catalogue graph"):
        target = Addon.objects.create(name="example.catalog_graph_target", state=Addon.AddonState.REMOVED)
        remote = Addon.objects.create(name="example.remote", source=Addon.AddonSource.REMOTE,
                                      state=Addon.AddonState.DISABLED,
                                      depends_on=["example.catalog_graph_target", "example.catalog_graph_target"])
        Addon.objects.create(name="example.removed", source=Addon.AddonSource.REMOTE,
                             state=Addon.AddonState.REMOVED, depends_on=["example.catalog_graph_target"])
        Addon.objects.reconcile_dependencies()
        target.refresh_from_db()
        assert target.depended_by == ["example.remote"]
        remote.state = Addon.AddonState.REMOVED
        remote.save(update_fields=["state"])
        Addon.objects.reconcile_dependencies()
        target.refresh_from_db()
        assert target.depended_by == []



def _catalogue_document(app: str, distribution: str, requirements: tuple[str, ...] = ()) -> str:
    """Native source metadata with explicit distribution and application identities."""

    return (
        f"[project]\nname={json.dumps(distribution)}\nversion=\"1.0\"\n"
        f"dependencies={json.dumps(requirements)}\n"
        f"[project.entry-points.\"angee.addons\"]\n{json.dumps(app)}={json.dumps(app)}\n"
        + _BUILD_SYSTEM
    )


@pytest.mark.parametrize("dependency_first", [False, True])
def test_catalogue_reprojects_retained_native_requirements_across_sources(
    catalog_source, monkeypatch, dependency_first,
):
    source, root_marker = catalog_source
    root_marker.write_text(_catalogue_document("example.root", "example-root", ("example-foo-bar>=1",)))
    (root_marker.parent.parent / "dependency" / "pyproject.toml").unlink()
    repository_root = root_marker.parents[2]
    dependency_marker = repository_root / "second-source" / "dependency" / "pyproject.toml"
    dependency_marker.parent.mkdir(parents=True)
    dependency_marker.write_text(_catalogue_document("example.foo_bar", "example-foo-bar"))
    monkeypatch.setattr("angee.platform_integrate_vcs.models.available_addons", lambda: {})
    with system_context(reason="test cross-source catalogue"):
        second = Source.objects.create(repository=source.repository, kind="addon", path="second-source")
    first, last = (second, source) if dependency_first else (source, second)
    assert first.refresh() == 1
    assert last.refresh() == 1
    with system_context(reason="test cross-source catalogue read"):
        root = Addon.objects.get(name="example.root")
        dependency = Addon.objects.get(name="example.foo_bar")
        assert root.distribution == "example-root"
        assert root.requirements == ["example-foo-bar>=1"]
        assert root.depends_on == ["example.foo_bar"]
        assert dependency.depended_by == ["example.root"]
    root_marker.write_text(_catalogue_document("example.root", "example-root"))
    assert source.refresh() == 1
    with system_context(reason="test cross-source catalogue reread"):
        root.refresh_from_db()
        dependency.refresh_from_db()
        assert root.requirements == []
        assert root.depends_on == []
        assert dependency.depended_by == []


@pytest.mark.parametrize("collision", ["canonical_distribution", "duplicate_app"])
def test_catalogue_rejects_ambiguous_source_documents_before_catalogue_writes(catalog_source, collision):
    source, marker = catalog_source
    marker.write_text(_catalogue_document("example.foo_bar", "example-foo-bar"))
    second = marker.parent.parent / "dependency" / "pyproject.toml"
    other = "example.foo.bar" if collision == "canonical_distribution" else "example.foo_bar"
    provider = "example-foo-bar" if collision == "canonical_distribution" else "different-provider"
    second.write_text(_catalogue_document(other, provider))
    with system_context(reason="test catalogue collision snapshot"):
        before = list(Addon.objects.order_by("pk").values())
    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(ImproperlyConfigured, match="Duplicate component distribution|Competing providers"):
            source.refresh()
    assert not [
        query["sql"] for query in queries
        if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        and any(f'"{model._meta.db_table}"' in query["sql"] for model in (Addon, Source))
    ]
    with system_context(reason="test catalogue collision unchanged"):
        assert list(Addon.objects.order_by("pk").values()) == before
        source.refresh_from_db()
        assert source.last_synced_at is None


@pytest.mark.parametrize("same_identity", [False, True])
def test_catalogue_rejects_another_sources_conflicting_identity_or_ownership(catalog_source, same_identity):
    source, marker = catalog_source
    marker.write_text(_catalogue_document("example.foo_bar", "example-foo-bar"))
    (marker.parent.parent / "dependency" / "pyproject.toml").unlink()
    assert source.refresh() == 1
    second_marker = marker.parents[2] / "second-source" / "pyproject.toml"
    second_marker.parent.mkdir()
    second_marker.write_text(_catalogue_document(
        "example.foo_bar" if same_identity else "example.foo.bar", "example-foo-bar",
    ))
    with system_context(reason="test competing source setup"):
        second = Source.objects.create(repository=source.repository, kind="addon", path="second-source")
        before = list(Addon.objects.order_by("pk").values())
    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(ImproperlyConfigured, match="Conflicting catalogue identity|already owned by source"):
            second.refresh()
    assert not [
        query["sql"] for query in queries
        if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        and any(f'"{model._meta.db_table}"' in query["sql"] for model in (Addon, Source))
    ]
    with system_context(reason="test competing source unchanged"):
        assert list(Addon.objects.order_by("pk").values()) == before
        second.refresh_from_db()
        assert second.last_synced_at is None


def test_installed_and_source_declarations_share_one_native_identity(catalog_source, monkeypatch):
    source, marker = catalog_source
    installed = make_addon(name="example.installed")
    facts = facts_from_distribution(installed_entry_points()[installed.name].dist)
    marker.write_text(_catalogue_document(installed.name, facts["distribution"]))
    (marker.parent.parent / "dependency" / "pyproject.toml").unlink()
    monkeypatch.setattr("angee.platform_integrate_vcs.models.available_addons", lambda: {installed.name: (facts, None)})
    with system_context(reason="test installed catalogue row"):
        row = Addon.objects.create(name=installed.name, distribution=facts["distribution"], requirements=[])
    assert source.refresh() == 1
    with system_context(reason="test installed catalogue identity"):
        row.refresh_from_db()
        assert Addon.objects.filter(name=installed.name).count() == 1
        assert row.vcs_source_id == source.pk
        assert row.distribution == facts["distribution"]



def test_catalogue_distribution_uniqueness_is_enforced_by_the_model(composed_tables):
    with system_context(reason="test catalogue native identity constraint"):
        Addon.objects.create(name="example.foo_bar", distribution="example-foo-bar")
        with pytest.raises(IntegrityError), transaction.atomic():
            Addon.objects.create(name="example.foo.bar", distribution="example-foo-bar")
        assert not Addon.objects.filter(name="example.foo.bar").exists()


def test_native_dynamic_metadata_and_ordinary_projects_preserve_catalogue_behavior(catalog_source, monkeypatch):
    source, marker = catalog_source
    package = marker.parents[1] / "catalogue" / "integrate_notes"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("raise AssertionError('catalogue imported the addon')\n")
    (package / "pyproject.toml").write_text(
        '[build-system]\nrequires=["hatch-angee>=1.1,<2"]\nbuild-backend="hatch_angee"\n'
        '[project]\nname="catalogue-integrate-notes"\nversion="1.0"\n'
        'dynamic=["entry-points"]\ndescription="Native dynamic metadata"\nkeywords=["native","notes"]\n'
        'dependencies=["catalog-remote-dependency>=1"]\n[tool.angee.platform]\ncategory="Data"\n'
    )
    ordinary = marker.parents[1] / "library"
    ordinary.mkdir()
    (ordinary / "pyproject.toml").write_text(_BUILD_SYSTEM + '[project]\nname="plain-library"\nversion="1.0"\n')
    monkeypatch.setattr("angee.platform_integrate_vcs.models.available_addons", lambda: {})
    assert source.refresh() == 3
    with system_context(reason="test native metadata row"):
        row = Addon.objects.get(name="catalogue.integrate_notes")
        assert not Addon.objects.filter(distribution="plain-library").exists()
    assert row.distribution == "catalogue-integrate-notes"
    assert row.description == "Native dynamic metadata"
    assert row.keywords == ["native", "notes"] and row.category == "Data"
    assert row.depends_on == ["catalog_remote.dependency"]
    assert row.vcs_path == "addons/catalogue/integrate_notes"
    assert "catalogue.integrate_notes" not in sys.modules


def test_build_failure_preserves_existing_catalogue_and_sync_time(catalog_source, monkeypatch):
    source, _marker = catalog_source
    assert source.refresh() == 2
    with system_context(reason="test catalogue before failure"):
        before = list(Addon.objects.order_by("pk").values())
    timestamp = source.last_synced_at

    def fail(*args, **kwargs):
        assert not connection.in_atomic_block
        raise RuntimeError("native metadata failed")

    monkeypatch.setattr(OperatorDaemon, "run_source_build", fail)
    with pytest.raises(RuntimeError, match="native metadata failed"):
        source.refresh()
    with system_context(reason="test catalogue after failure"):
        assert list(Addon.objects.order_by("pk").values()) == before
        source.refresh_from_db()
    assert source.last_synced_at == timestamp


@pytest.mark.parametrize("field,value", [("ref", "changed"), ("path", "changed"), ("repository_id", None)])
def test_source_coordinate_change_during_build_cannot_publish(catalog_source, monkeypatch, field, value):
    source, _marker = catalog_source
    assert source.refresh() == 2
    with system_context(reason="test stale source before"):
        before = list(Addon.objects.order_by("pk").values())
    timestamp = source.last_synced_at
    if field == "repository_id":
        with system_context(reason="test equal transport, different repository owner"):
            replacement = Repository.objects.create(
                vcs_bridge=source.repository.vcs_bridge, org="local", name="alternate",
                remote=source.repository.remote, ssh_remote=source.repository.ssh_remote,
                default_branch=source.repository.default_branch,
            )
        value = replacement.pk
    original = OperatorDaemon.run_source_build
    changed = False

    def prepare(*args, **kwargs):
        nonlocal changed
        assert not connection.in_atomic_block
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            with system_context(reason="test concurrent source edit"):
                Source.objects.filter(pk=source.pk).update(**{field: value})
        return result

    monkeypatch.setattr(OperatorDaemon, "run_source_build", prepare)
    with pytest.raises(ImproperlyConfigured, match="Source changed"):
        source.refresh()
    with system_context(reason="test stale source after"):
        assert list(Addon.objects.order_by("pk").values()) == before
        source.refresh_from_db()
    assert source.last_synced_at == timestamp


def test_malformed_non_addon_build_project_is_not_silently_ignored(catalog_source):
    source, marker = catalog_source
    broken = marker.parents[1] / "ordinary"
    broken.mkdir()
    (broken / "pyproject.toml").write_text(
        '[project]\nname="ordinary"\nversion="1.0"\n'
        '[build-system]\nrequires=[]\nbuild-backend="missing_native_backend"\n'
    )
    with pytest.raises(OperatorDaemonError, match="missing_native_backend"):
        source.refresh()
    with system_context(reason="test malformed catalogue remains empty"):
        assert not Addon.objects.filter(vcs_source=source).exists()
        source.refresh_from_db()
    assert source.last_synced_at is None


def test_serving_catalogue_never_imports_the_build_frontend(catalog_source, monkeypatch):
    source, _marker = catalog_source
    monkeypatch.setitem(sys.modules, "build", None)
    assert source.refresh() == 2


@pytest.mark.parametrize("failure", ("failed", "unavailable", "bad_json", "oversized"))
def test_job_failures_preserve_previous_catalogue_and_sync_time(catalog_source, monkeypatch, failure):
    source, _marker = catalog_source
    assert source.refresh() == 2
    with system_context(reason="tests.catalogue.job_failure_before"):
        before = list(Addon.objects.order_by("pk").values())
    timestamp = source.last_synced_at
    def fail(*args, **kwargs):
        if failure == "unavailable":
            raise OperatorDaemonError("Declare the trusted catalogue-metadata source-build job")
        if failure == "failed":
            return {"id": "failed-operation", "status": "failed", "error": "prepared backend unavailable"}
        return {"id": "bad-operation", "status": "succeeded",
                "output": "not-json" if failure == "bad_json" else "x" * 262145}
    monkeypatch.setattr(OperatorDaemon, "run_source_build", fail)
    with pytest.raises((OperatorDaemonError, ImproperlyConfigured)):
        source.refresh()
    with system_context(reason="tests.catalogue.job_failure_after"):
        assert list(Addon.objects.order_by("pk").values()) == before
        source.refresh_from_db()
        assert source.last_synced_at == timestamp
