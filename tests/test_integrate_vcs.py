"""Tests for the VCS inventory flow — discover/import/search/refresh/sync.

Uses the in-memory ``StubVCSBackend`` (registered as ``stub`` in the test
``ANGEE_VCS_BACKEND_CLASSES``); canned host data rides on the VCS bridge config.
REBAC-guarded reads run under ``system_context`` (writes already elevate
themselves).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone
from rebac import system_context

from angee.integrate.vcs.backend import LocalVCSBackend
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    VCS_TEST_MODELS,
    Repository,
    Source,
    Template,
    VcsBridge,
    _create_missing_tables,
    make_integration,
)

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


def _vcs_bridge(slug: str, *, config: dict[str, Any], backend_class: str = "stub") -> Any:
    """Create a VCS bridge child whose host/local data rides on config."""

    return make_integration(slug, backend_class=backend_class, model=VcsBridge, config=config)


def _repo_names() -> set[str]:
    """Return the inventoried repository names (read elevated)."""

    with system_context(reason="test read"):
        return set(Repository.objects.values_list("name", flat=True))


@pytest.mark.django_db(transaction=True)
def test_discover_repositories_reconciles_and_prunes(vcs_tables: None) -> None:
    """discoverRepositories inventories every repo and prunes ones that vanished."""

    del vcs_tables
    vcs = _vcs_bridge("disco", config={"stub_repos": REPOS})

    assert vcs.discover_repositories() == 2
    assert _repo_names() == {"acme/widgets", "acme/gadgets"}

    with system_context(reason="test"):
        vcs.config = {"stub_repos": REPOS[:1]}
        vcs.save(update_fields=["config", "updated_at"])
    vcs.discover_repositories()
    assert _repo_names() == {"acme/widgets"}


@pytest.mark.django_db(transaction=True)
def test_import_repository_adds_one_without_pruning(vcs_tables: None) -> None:
    """addRepository inventories the picked repo and leaves the others in place."""

    del vcs_tables
    vcs = _vcs_bridge("imp", config={"stub_repos": REPOS})
    vcs.discover_repositories()

    repository = vcs.import_repository("acme/widgets")
    assert repository.name == "acme/widgets"
    assert _repo_names() == {"acme/widgets", "acme/gadgets"}


@pytest.mark.django_db(transaction=True)
def test_search_repositories_is_the_typeahead(vcs_tables: None) -> None:
    """searchRepositories returns name-matching host candidates for the typeahead."""

    del vcs_tables
    vcs = _vcs_bridge("search", config={"stub_repos": REPOS})
    assert [candidate.name for candidate in vcs.search_repositories("widget")] == ["acme/widgets"]


@pytest.mark.django_db(transaction=True)
def test_source_refresh_materializes_templates(vcs_tables: None) -> None:
    """A template source refresh walks the tree and upserts Template rows."""

    del vcs_tables
    vcs = _vcs_bridge("tpl", config={"stub_repos": REPOS, "stub_tree": TREE, "stub_blobs": BLOBS})
    vcs.discover_repositories()
    with system_context(reason="test"):
        repository = Repository.objects.get(name="acme/widgets")
        source = Source.objects.create(repository=repository, kind="template", path="templates")

    assert source.refresh() == 1
    with system_context(reason="test read"):
        template = Template.objects.get(source=source)
    assert (template.kind, template.name, template.path) == ("workspace", "Dev", "templates/dev")


@pytest.mark.django_db(transaction=True)
def test_run_sync_refreshes_sources_and_records_lifecycle(vcs_tables: None) -> None:
    """The Bridge sync owner refreshes sources and records lifecycle telemetry."""

    del vcs_tables
    vcs = _vcs_bridge("sync", config={"stub_repos": REPOS, "stub_tree": TREE, "stub_blobs": BLOBS})
    vcs.discover_repositories()
    with system_context(reason="test"):
        repository = Repository.objects.get(name="acme/widgets")
        Source.objects.create(repository=repository, kind="template", path="templates")

    now = timezone.now()
    with system_context(reason="test sync"):  # the scheduler / GraphQL action wrap sync() likewise
        assert vcs.run_sync(now=now) == 1
        assert Template.objects.count() == 1
    vcs.refresh_from_db()
    assert vcs.last_sync_started_at == now
    assert vcs.last_sync_completed_at == now
    assert vcs.last_sync_status == "ok"
    assert vcs.last_sync_items == 1
    assert vcs.next_sync_at == now + timedelta(seconds=vcs.poll_interval)


@pytest.mark.django_db(transaction=True)
def test_local_backend_materializes_templates_through_the_source_flow(vcs_tables: None, tmp_path: Any) -> None:
    """A `local`-backed integration inventories a working tree into Template rows.

    Drives the same ``discover → Source.refresh`` path the resource-seeded console
    uses, proving the local backend wires through ``VcsBridge → Source →
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

    vcs = _vcs_bridge(
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
