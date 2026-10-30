"""Built artifacts reconcile through native metadata; source never executes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from pydantic import BaseModel
from rebac import system_context

import tests.test_marketplace_graphql  # noqa: F401 -- register the source model graph before database setup
from angee.integrate_vcs.backend import RepoDescriptor, VCSBackend, source_path, tree_entries
from angee.platform.discovery import facts_from_distribution, installed_entry_points
from angee.platform_integrate_vcs import catalog
from tests.conftest import Addon, Repository, Source, VcsBridge, catalogue_package, make_integration
from tests.wheel_fixtures import write_wheel


class CatalogueBackend(VCSBackend):
    """Binary remote transport over test-authored, already built artifacts."""

    key = "local"

    class Config(BaseModel):
        fixture_root: str

    config_model = Config

    def ls_repos(self, *, org=""):
        return [RepoDescriptor(name="catalogue", org="tests", remote="https://example.com/catalogue.git")]

    def rev_parse(self, repository, ref):
        return "a" * 40

    def ls_tree(self, repository, *, ref, path, recursive=False):
        assert ref == "a" * 40
        return tree_entries(Path(self.bridge.config["fixture_root"]), path=path, recursive=recursive)

    def cat_file(self, repository, *, ref, path):
        assert ref == "a" * 40
        assert not connection.in_atomic_block
        return source_path(Path(self.bridge.config["fixture_root"]), path).read_bytes()

    def materialize(self, *args, **kwargs):
        raise AssertionError("Catalogue may not materialize source")


@pytest.fixture
def catalog_source(composed_tables, tmp_path, settings):
    settings.ANGEE_VCS_BACKEND_CLASSES = {
        **settings.ANGEE_VCS_BACKEND_CLASSES, "local": "tests.test_platform_integrate_vcs.CatalogueBackend",
    }
    artifact = write_wheel(
        tmp_path / "addons/base/base.whl", requirements=("angee-iam", "catalog-remote-dependency"),
        description="Remote declaration", keywords=("catalogue",), category="Integration",
    )
    write_wheel(tmp_path / "addons/dependency/dependency.whl", "catalog_remote.dependency", "catalog-remote-dependency")
    bridge = make_integration(
        "addon-catalog", model=VcsBridge, backend_class="local", config={"fixture_root": str(tmp_path)},
    )
    bridge.discover_repositories()
    with system_context(reason="tests.catalogue.source"):
        source = Source.objects.create(repository=Repository.objects.get(name="catalogue"), kind="addon", path="addons")
    return source, artifact


def snapshot():
    with system_context(reason="tests.catalogue.snapshot"):
        return list(Addon.objects.order_by("pk").values())


def test_remote_catalogue_reconciles_native_metadata_and_retains_removed_artifacts(catalog_source, monkeypatch):
    source, artifact = catalog_source
    monkeypatch.setattr("angee.platform_integrate_vcs.models.available_addons", lambda: {})
    assert source.refresh() == 2
    with system_context(reason="tests.catalogue.read"):
        row = Addon.objects.get(name="catalog_remote.base")
        assert row.distribution == "catalog-remote-base" and row.namespace == "catalog_remote"
        assert row.description == "Remote declaration" and row.keywords == ["catalogue"]
        assert row.category == "Integration" and row.depends_on == ["angee.iam", "catalog_remote.dependency"]
        assert row.vcs_path == "addons/base/base.whl" and row.source == Addon.AddonSource.REMOTE
        assert row.state == Addon.AddonState.DISABLED
    artifact.unlink()
    assert source.refresh() == 1
    with system_context(reason="tests.catalogue.removed"):
        row.refresh_from_db()
        assert row.state == Addon.AddonState.REMOVED and row.description == "Remote declaration"


@pytest.mark.parametrize("origin", [Addon.AddonSource.LOCAL, Addon.AddonSource.INSTALLED])
def test_installed_metadata_precedes_artifacts_until_the_distribution_disappears(catalog_source, monkeypatch, origin):
    source, _artifact = catalog_source
    manifest = catalogue_package("catalog_remote.base", description="Installed metadata", depends_on=("local.dep",))
    with system_context(reason="tests.catalogue.installed_fixture"):
        row = Addon.objects.create(
            name=manifest["name"], distribution=manifest["distribution"], description="Installed metadata",
            source=origin, state=Addon.AddonState.ENABLED, forced=True, model_count=2,
        )
    monkeypatch.setattr("angee.platform_integrate_vcs.models.available_addons", lambda: {row.name: (manifest, None)})
    source.refresh()
    with system_context(reason="tests.catalogue.precedence"):
        row.refresh_from_db()
        assert (row.description, row.source, row.state) == ("Installed metadata", origin, Addon.AddonState.ENABLED)
        assert row.vcs_source_id == source.pk and row.model_count == 2
    monkeypatch.setattr("angee.platform_integrate_vcs.models.available_addons", lambda: {})
    source.refresh()
    with system_context(reason="tests.catalogue.became_remote"):
        row.refresh_from_db()
        assert row.description == "Remote declaration" and row.model_count == 0 and not row.forced
        assert row.source == Addon.AddonSource.REMOTE and row.state == Addon.AddonState.DISABLED


def test_built_dynamic_identity_and_packaged_capabilities_are_authoritative(catalog_source):
    source, artifact = catalog_source
    write_wheel(
        artifact.parent / "notes.whl", "catalogue.integrate_notes", "catalogue-integrate-notes",
        description="Native dynamic metadata", keywords=("native", "notes"), code=True,
        document='[project]\nname="not-the-distribution-identity"\ndynamic=["entry-points"]\n'
                 '[tool.angee.platform]\ncategory="Data"\n',
    )
    write_wheel(artifact.parent / "ordinary.whl", None, "ordinary-library")
    assert source.refresh() == 3
    with system_context(reason="tests.catalogue.dynamic"):
        row = Addon.objects.get(name="catalogue.integrate_notes")
        assert row.distribution == "catalogue-integrate-notes" and row.category == "Data"
        assert row.description == "Native dynamic metadata" and row.keywords == ["native", "notes"]
        assert not Addon.objects.filter(distribution="ordinary-library").exists()
    assert "catalogue.integrate_notes" not in sys.modules


@pytest.mark.parametrize("dependency_first", [False, True])
def test_retained_requirements_reproject_across_sources(catalog_source, dependency_first):
    source, artifact = catalog_source
    write_wheel(artifact, "example.root", "example-root", requirements=("example-foo-bar>=1",))
    (artifact.parents[1] / "dependency/dependency.whl").unlink()
    root = artifact.parents[2]
    dependency = write_wheel(root / "second/dependency.whl", "example.foo_bar", "example-foo-bar")
    with system_context(reason="tests.catalogue.second_source"):
        second = Source.objects.create(repository=source.repository, kind="addon", path="second")
    first, last = (second, source) if dependency_first else (source, second)
    assert first.refresh() == last.refresh() == 1
    with system_context(reason="tests.catalogue.dependency"):
        row = Addon.objects.get(name="example.root")
        assert row.depends_on == ["example.foo_bar"]
        assert Addon.objects.get(name="example.foo_bar").depended_by == ["example.root"]
    write_wheel(dependency, "example.foo_bar", "example-foo-bar", requirements=("unavailable-package",))
    assert second.refresh() == 1


@pytest.mark.parametrize("collision", ["distribution", "app"])
def test_native_identity_collisions_write_neither_catalogue_nor_timestamp(catalog_source, collision):
    source, artifact = catalog_source
    write_wheel(artifact, "example.one", "example-one")
    write_wheel(artifact.parents[1] / "dependency/dependency.whl",
                "example.two" if collision == "distribution" else "example.one",
                "example-one" if collision == "distribution" else "example-two")
    before = snapshot()
    with CaptureQueriesContext(connection) as queries, pytest.raises(ImproperlyConfigured):
        source.refresh()
    assert not [query for query in queries if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                and any(f'"{model._meta.db_table}"' in query["sql"] for model in (Addon, Source))]
    assert snapshot() == before


@pytest.mark.parametrize("same_identity", [False, True])
def test_another_source_cannot_take_live_catalogue_ownership(catalog_source, same_identity):
    source, artifact = catalog_source
    assert source.refresh() == 2
    with system_context(reason="tests.catalogue.competing_source"):
        second = Source.objects.create(repository=source.repository, kind="addon", path="other")
    write_wheel(artifact.parents[2] / "other/competing.whl", "catalog_remote.base",
                "catalog-remote-base" if same_identity else "different-provider")
    before = snapshot()
    with pytest.raises(ImproperlyConfigured, match="already owned|Conflicting catalogue identity"):
        second.refresh()
    assert snapshot() == before


def test_installed_and_artifact_identity_share_one_row(catalog_source):
    source, artifact = catalog_source
    facts = facts_from_distribution(installed_entry_points()["angee.iam"].dist)
    write_wheel(artifact, facts["name"], facts["distribution"])
    assert source.refresh() == 2
    with system_context(reason="tests.catalogue.native_installed"):
        row = Addon.objects.get(name="angee.iam")
        assert row.vcs_source_id == source.pk and row.distribution == facts["distribution"]
        assert Addon.objects.filter(name="angee.iam").count() == 1


@pytest.mark.parametrize("failure", ["invalid", "unavailable", "oversized", "source_only"])
def test_bad_or_unavailable_artifacts_preserve_rows_and_timestamp(catalog_source, monkeypatch, failure):
    source, artifact = catalog_source
    source.refresh()
    before, timestamp = snapshot(), source.last_synced_at
    if failure == "invalid":
        artifact.write_bytes(b"not a wheel")
    elif failure == "unavailable":
        def unavailable(*args, **kwargs):
            raise OSError("offline")
        monkeypatch.setattr(CatalogueBackend, "cat_file", unavailable)
    elif failure == "oversized":
        monkeypatch.setattr(catalog, "MAX_CATALOGUE_BYTES", 1)
    else:
        artifact.unlink()
        (artifact.parents[1] / "dependency/dependency.whl").unlink()
        (artifact.parent / "pyproject.toml").write_text('[build-system]\nrequires=[]\nbuild-backend="must-not-run"\n')
    with pytest.raises(ImproperlyConfigured):
        source.refresh()
    assert snapshot() == before
    with system_context(reason="tests.catalogue.failure_timestamp"):
        source.refresh_from_db()
        assert source.last_synced_at == timestamp


@pytest.mark.parametrize("field,value", [("ref", "changed"), ("path", "changed"), ("repository_id", None)])
def test_source_coordinate_change_during_metadata_read_cannot_publish(catalog_source, monkeypatch, field, value):
    source, artifact = catalog_source
    source.refresh()
    before, timestamp = snapshot(), source.last_synced_at
    if field == "repository_id":
        with system_context(reason="tests.catalogue.replace_repository"):
            value = Repository.objects.create(vcs_bridge=source.repository.vcs_bridge, name="other", org="tests",
                                               remote=source.repository.remote).pk
    original, changed = catalog.wheel_metadata, False
    def read(*args, **kwargs):
        nonlocal changed
        assert not connection.in_atomic_block
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            with system_context(reason="tests.catalogue.concurrent_edit"):
                Source.objects.filter(pk=source.pk).update(**{field: value})
        return result
    monkeypatch.setattr(catalog, "wheel_metadata", read)
    with pytest.raises(ImproperlyConfigured, match="Source changed"):
        source.refresh()
    assert snapshot() == before
    with system_context(reason="tests.catalogue.stale_timestamp"):
        source.refresh_from_db()
        assert source.last_synced_at == timestamp


def test_catalogue_never_imports_build_or_addon_code(catalog_source, monkeypatch):
    source, artifact = catalog_source
    write_wheel(artifact, code=True)
    monkeypatch.setitem(sys.modules, "build", None)
    assert source.refresh() == 2
    assert "catalog_remote.base" not in sys.modules


def test_catalogue_distribution_uniqueness_remains_database_enforced(composed_tables):
    with system_context(reason="tests.catalogue.uniqueness"):
        Addon.objects.create(name="example.foo_bar", distribution="example-foo-bar")
        with pytest.raises(IntegrityError), transaction.atomic():
            Addon.objects.create(name="example.foo.bar", distribution="example-foo-bar")
