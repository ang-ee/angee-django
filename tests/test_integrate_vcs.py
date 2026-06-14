"""Tests for the VCS inventory flow — discover/import/search/refresh/sync.

Uses the in-memory ``StubVCSBackend`` (registered as ``stub`` in the test
``ANGEE_VCS_BACKEND_CLASSES``); canned host data rides on the integration config.
REBAC-guarded reads run under ``system_context`` (writes already elevate
themselves).
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.integrate.models import Repository as AbstractRepository
from angee.integrate.models import Source as AbstractSource
from angee.integrate.models import Template as AbstractTemplate
from angee.integrate.models import VCSIntegration as AbstractVCSIntegration
from angee.integrate.vcs.backend import LocalVCSBackend
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    _create_missing_tables,
    make_integration,
)


class VCSIntegration(AbstractVCSIntegration):
    """Concrete VCS integration used by the inventory-flow tests."""

    class Meta(AbstractVCSIntegration.Meta):
        """Django model options for the canonical test VCS integration."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_vcs_integration"
        rebac_resource_type = "integrate/vcs_integration"
        rebac_id_attr = "sqid"


class Repository(AbstractRepository):
    """Concrete repository used by the inventory-flow tests."""

    class Meta(AbstractRepository.Meta):
        """Django model options for the canonical test repository."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_repository"
        rebac_resource_type = "integrate/repository"
        rebac_id_attr = "sqid"


class Source(AbstractSource):
    """Concrete source used by the inventory-flow tests."""

    class Meta(AbstractSource.Meta):
        """Django model options for the canonical test source."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_source"
        rebac_resource_type = "integrate/source"
        rebac_id_attr = "sqid"


class Template(AbstractTemplate):
    """Concrete template used by the inventory-flow tests."""

    source_kind = "template"

    class Meta(AbstractTemplate.Meta):
        """Django model options for the canonical test template."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_template"
        rebac_resource_type = "integrate/template"
        rebac_id_attr = "sqid"


VCS_TEST_MODELS = (VCSIntegration, Repository, Source, Template)

REPOS = [
    {
        "name": "acme/widgets",
        "org": "acme",
        "remote": "https://github.com/acme/widgets.git",
        "ssh_remote": "git@github.com:acme/widgets.git",
        "default_branch": "main",
        "visibility": "private",
    },
    {
        "name": "acme/gadgets",
        "org": "acme",
        "remote": "https://github.com/acme/gadgets.git",
        "default_branch": "main",
        "visibility": "public",
    },
]
TREE = [
    {"path": "templates/dev/copier.yml", "type": "blob", "oid": "a"},
    {"path": "templates/dev/README.md", "type": "blob", "oid": "b"},
]
BLOBS = {"templates/dev/copier.yml": "_angee:\n  kind: workspace\n  name: Dev\n"}


@pytest.fixture()
def vcs_tables(transactional_db: Any) -> Iterator[None]:
    """Create the iam/integrate/VCS test tables and sync the REBAC schema."""

    del transactional_db
    created = _create_missing_tables(IAM_CONNECTION_TEST_MODELS + INTEGRATE_TEST_MODELS + VCS_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


def _vcs_integration(slug: str, *, config: dict[str, Any], backend_class: str = "stub") -> Any:
    """Create a VCS integration whose host (or local) data rides on the config."""

    integration = make_integration(slug)
    with system_context(reason="test vcs setup"):
        integration.config = config
        integration.save(update_fields=["config", "updated_at"])
        return VCSIntegration.objects.create(integration=integration, backend_class=backend_class)


def _repo_names() -> set[str]:
    """Return the inventoried repository names (read elevated)."""

    with system_context(reason="test read"):
        return set(Repository.objects.values_list("name", flat=True))


@pytest.mark.django_db(transaction=True)
def test_discover_repositories_reconciles_and_prunes(vcs_tables: None) -> None:
    """discoverRepositories inventories every repo and prunes ones that vanished."""

    del vcs_tables
    vcs = _vcs_integration("disco", config={"stub_repos": REPOS})

    assert vcs.discover_repositories() == 2
    assert _repo_names() == {"acme/widgets", "acme/gadgets"}

    with system_context(reason="test"):
        vcs.integration.config = {"stub_repos": REPOS[:1]}
        vcs.integration.save(update_fields=["config", "updated_at"])
    vcs.discover_repositories()
    assert _repo_names() == {"acme/widgets"}


@pytest.mark.django_db(transaction=True)
def test_import_repository_adds_one_without_pruning(vcs_tables: None) -> None:
    """addRepository inventories the picked repo and leaves the others in place."""

    del vcs_tables
    vcs = _vcs_integration("imp", config={"stub_repos": REPOS})
    vcs.discover_repositories()

    repository = vcs.import_repository("acme/widgets")
    assert repository.name == "acme/widgets"
    assert _repo_names() == {"acme/widgets", "acme/gadgets"}


@pytest.mark.django_db(transaction=True)
def test_search_repositories_is_the_typeahead(vcs_tables: None) -> None:
    """searchRepositories returns name-matching host candidates for the typeahead."""

    del vcs_tables
    vcs = _vcs_integration("search", config={"stub_repos": REPOS})
    assert [candidate.name for candidate in vcs.search_repositories("widget")] == ["acme/widgets"]


@pytest.mark.django_db(transaction=True)
def test_source_refresh_materializes_templates(vcs_tables: None) -> None:
    """A template source refresh walks the tree and upserts Template rows."""

    del vcs_tables
    vcs = _vcs_integration("tpl", config={"stub_repos": REPOS, "stub_tree": TREE, "stub_blobs": BLOBS})
    vcs.discover_repositories()
    with system_context(reason="test"):
        repository = Repository.objects.get(name="acme/widgets")
        source = Source.objects.create(repository=repository, kind="template", path="templates")

    assert source.refresh() == 1
    with system_context(reason="test read"):
        template = Template.objects.get(source=source)
    assert (template.kind, template.name, template.path) == ("workspace", "Dev", "templates/dev")


@pytest.mark.django_db(transaction=True)
def test_sync_refreshes_every_source(vcs_tables: None) -> None:
    """The Bridge sync refreshes each repository's sources over the host."""

    del vcs_tables
    vcs = _vcs_integration("sync", config={"stub_repos": REPOS, "stub_tree": TREE, "stub_blobs": BLOBS})
    vcs.discover_repositories()
    with system_context(reason="test"):
        repository = Repository.objects.get(name="acme/widgets")
        Source.objects.create(repository=repository, kind="template", path="templates")

    with system_context(reason="test sync"):  # the scheduler / GraphQL action wrap sync() likewise
        assert vcs.sync() == 1
        assert Template.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_local_backend_materializes_templates_through_the_source_flow(vcs_tables: None, tmp_path: Any) -> None:
    """A `local`-backed integration inventories a working tree into Template rows.

    Drives the same ``discover → Source.refresh`` path the resource-seeded console
    uses, proving the local backend wires through ``VCSIntegration → Source →
    Template`` and that skip-dirs keep a stray ``copier.yml`` out of the inventory.
    """

    del vcs_tables
    template_dir = tmp_path / "templates" / "workspaces" / "dev"
    template_dir.mkdir(parents=True)
    (template_dir / "copier.yml").write_text("_angee:\n  kind: workspace\n  name: Dev\n")
    # A stray copier.yml inside a skip-dir under the source path must not be ingested.
    stray = tmp_path / "templates" / "node_modules" / "pkg"
    stray.mkdir(parents=True)
    (stray / "copier.yml").write_text("_angee:\n  kind: workspace\n  name: Stray\n")

    vcs = _vcs_integration(
        "local",
        config={"local_root": str(tmp_path), "local_name": "checkout"},
        backend_class="local",
    )

    assert vcs.discover_repositories() == 1
    assert _repo_names() == {"checkout"}
    with system_context(reason="test"):
        repository = Repository.objects.get(name="checkout")
        source = Source.objects.create(repository=repository, kind="template", path="templates")

    assert source.refresh() == 1
    with system_context(reason="test read"):
        template = Template.objects.get(source=source)
        names = set(Template.objects.values_list("name", flat=True))
    assert (template.kind, template.name, template.path) == ("workspace", "Dev", "templates/workspaces/dev")
    assert "Stray" not in names


def test_local_vcs_backend_walks_the_working_tree(tmp_path: Any) -> None:
    """The local backend inventories a working tree — paths relative to root, `.git` skipped."""

    template_dir = tmp_path / "templates" / "services" / "demo"
    template_dir.mkdir(parents=True)
    (template_dir / "copier.yml").write_text("_angee:\n  kind: service\n  name: demo\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("ignored")

    backend = LocalVCSBackend(SimpleNamespace(config={"local_root": str(tmp_path), "local_name": "repo"}))

    assert [repo.name for repo in backend.ls_repos()] == ["repo"]
    entries = backend.ls_tree(None, ref="main", path="templates", recursive=True)
    assert "templates/services/demo/copier.yml" in {e.path for e in entries if e.type == "blob"}
    assert all(".git" not in entry.path for entry in entries)
    assert backend.cat_file(None, ref="main", path="templates/services/demo/copier.yml").startswith(b"_angee")
    with pytest.raises(FileNotFoundError):
        backend.cat_file(None, ref="main", path="../escape")
