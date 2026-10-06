"""Links and resource bindings authorize their target through the projects schema."""

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import PermissionDenied, actor_context, generic_target, system_context

from angee.integrate.testing.integration import Integration
from angee.projects.testing.models import Link, Project, ProjectBinding, Task
from tests.conftest import Backend, Drive, Folder, Vendor, create_user, vault_for
from tests.test_knowledge import _grant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def task_project(composed_permissions: None) -> tuple[Any, Any, Any, Any]:
    """An owner's project and task, a project reader and an outsider."""

    del composed_permissions
    owner, reader, outsider = (create_user(name) for name in ("edge-owner", "edge-reader", "edge-outsider"))
    with actor_context(owner):
        project = Project.objects.create(title="Edges")
        task = Task.objects.create(project=project, title="Linked")
        project.grant_record_access("reader", reader)
    return owner, reader, outsider, task


def test_link_upsert_requires_write_on_its_target_and_lists_in_one_scoped_statement(task_project: Any) -> None:
    owner, reader, outsider, task = task_project
    with actor_context(owner):
        link = Link.objects.upsert(target=task, url="https://example.test/spec", title="Spec")
        assert link.actor() is not None
        assert Link.objects.upsert(target=task, url="https://example.test/spec", title="Spec v2").pk == link.pk
        assert Link.objects.create(target=task.project, url="https://example.test/home").url.endswith("/home")
    for actor, visible in ((reader, True), (outsider, False)):
        with actor_context(actor), CaptureQueriesContext(connection) as queries:
            rows = list(Link.objects.filter(**generic_target(task).lookups(Link, "target")))
        assert [row.pk for row in rows] == ([link.pk] if visible else [])
        assert sum(Link._meta.db_table in query["sql"] for query in queries.captured_queries) == 1
        with actor_context(actor), pytest.raises(PermissionDenied):
            Link.objects.upsert(target=task, url="https://example.test/denied")
    with actor_context(reader), pytest.raises(PermissionDenied):
        readable = Link.objects.get(pk=link.pk)
        readable.title = "Renamed by a reader"
        readable.save(update_fields=("title", "updated_at"))
    with actor_context(owner):
        assert Link.objects.filter(pk=link.pk).delete()[0] == 1


def test_link_refuses_undeclared_and_untyped_targets(task_project: Any) -> None:
    owner, _reader, _outsider, task = task_project
    vault = vault_for(owner, name="Not linkable")
    with actor_context(owner):
        with pytest.raises(ValidationError, match="may target only"):
            Link.objects.upsert(target=vault, url="https://example.test/vault")
        with pytest.raises(ValidationError, match="may target only"):
            Link.declared_target_model("knowledge.Vault")
        # The schema itself refuses a type no relation names, before any model validation.
        with pytest.raises(PermissionDenied):
            Link.objects.insert(Link(url="https://example.test/raw", **generic_target(vault).lookups(Link, "target")))
        with pytest.raises(ValueError, match="no primary key"):
            Link.objects.upsert(target=Task(project=task.project, title="Unsaved"), url="https://example.test/x")
    with system_context(reason="test.links.system"):
        assert Link.objects.upsert(target=task, url="https://example.test/system").pk
        assert Link.objects.count() == 1


@pytest.fixture
def bindable(composed_permissions: None) -> dict[str, Any]:
    """A project owner with a drive and a vault, plus a writer who cannot share."""

    del composed_permissions
    names = ("bind-owner", "bind-writer", "bind-reader", "bind-outsider")
    owner, writer, reader, outsider = (create_user(name) for name in names)
    with system_context(reason="test.bindings.seed"):
        backend = Backend.objects.create(slug="bindings", label="Bindings", backend_class="local")
        drive = Drive.objects.create(backend=backend, slug="bindings", name="Bindings", owner=owner)
        folder = Folder.objects.create(drive=drive, name="Files", owner=owner)
    vault = vault_for(owner, name="Bound")
    with actor_context(owner):
        project = Project.objects.create(title="Bindings")
        project.grant_record_access("editor", writer)
        project.grant_record_access("reader", reader)
        task = Task.objects.create(project=project, title="Not bindable")
    _grant(vault, "editor", writer)
    return {
        "owner": owner, "writer": writer, "reader": reader, "outsider": outsider, "project": project,
        "drive": drive, "folder": folder, "vault": vault, "task": task,
    }


def test_binding_create_and_delete_take_share_on_both_ends(bindable: dict[str, Any]) -> None:
    owner, writer, reader, outsider = (bindable[name] for name in ("owner", "writer", "reader", "outsider"))
    project, drive, vault = bindable["project"], bindable["drive"], bindable["vault"]
    with actor_context(owner):
        binding = ProjectBinding.objects.bind(project=project, target=drive)
        assert ProjectBinding.objects.bind(project=project, target=drive).pk == binding.pk
        assert binding.actor() is not None
    assert project.with_actor(writer).has_access("share")
    assert vault.with_actor(writer).has_access("write") and not vault.with_actor(writer).has_access("share")
    with actor_context(writer), pytest.raises(PermissionDenied):
        ProjectBinding.objects.bind(project=project, target=vault)
    # A project reader sees the binding but holds share on neither end.
    with actor_context(reader), pytest.raises(PermissionDenied):
        ProjectBinding.objects.unbind(project=project, target=drive)
    with actor_context(reader), pytest.raises(PermissionDenied):
        ProjectBinding.objects.get(pk=binding.pk).delete()
    assert ProjectBinding._base_manager.filter(pk=binding.pk).exists()
    for actor, visible in ((reader, True), (outsider, False)):
        with actor_context(actor), CaptureQueriesContext(connection) as queries:
            rows = list(ProjectBinding.objects.filter(**generic_target(drive).lookups(ProjectBinding, "target")))
        assert [row.pk for row in rows] == ([binding.pk] if visible else [])
        assert sum(ProjectBinding._meta.db_table in query["sql"] for query in queries.captured_queries) == 1
    with actor_context(owner):
        ProjectBinding.objects.bind(project=project, target=vault)
        ProjectBinding.objects.unbind(project=project, target=drive)
    with system_context(reason="test.bindings.remaining"):
        assert list(ProjectBinding.objects.values_list("object_id", flat=True)) == [vault.pk]


def test_binding_refuses_undeclared_targets_and_moves(bindable: dict[str, Any]) -> None:
    owner, project, task, folder = bindable["owner"], bindable["project"], bindable["task"], bindable["folder"]
    with system_context(reason="test.bindings.integration"):
        vendor = Vendor.objects.create(slug="plain-integration", display_name="Plain integration")
        integration = Integration.objects.create(vendor=vendor, owner=owner)
    with actor_context(owner):
        with pytest.raises(ValidationError, match="may target only"):
            ProjectBinding.objects.bind(project=project, target=task)
        # The schema reaches integrations (a channel stores as one); the manager admits only channels.
        with pytest.raises(ValidationError, match="only through its channel"):
            ProjectBinding.objects.bind(project=project, target=integration)
        binding = ProjectBinding.objects.bind(project=project, target=folder)
        binding.target = bindable["drive"]
        with pytest.raises(PermissionDenied):
            binding.save()
        with pytest.raises(PermissionDenied):
            ProjectBinding.objects.filter(pk=binding.pk).update(object_id=bindable["drive"].pk)
    with system_context(reason="test.bindings.system"):
        # System writes still enter declared edges, and `target` canonicalizes on save.
        row = ProjectBinding(project=project, target=bindable["vault"])
        row.save()
        assert (row.content_type, row.object_id) == (
            generic_target(bindable["vault"]).content_type, bindable["vault"].pk,
        )
        assert ProjectBinding.objects.filter(project=project).count() == 2
        assert bindable["vault"].with_actor(bindable["writer"]).has_access("share")
        task_key = generic_target(task).lookups(ProjectBinding, "target")
        assert not ProjectBinding._base_manager.filter(**task_key).exists()


def test_project_folder_keeps_its_own_binding_gate(bindable: dict[str, Any]) -> None:
    project, folder, writer = bindable["project"], bindable["folder"], bindable["writer"]
    with actor_context(writer):
        project.folder = folder
        with pytest.raises(PermissionDenied, match="Write access to the resource"):
            project.save(update_fields=("folder", "updated_at"))
    with actor_context(bindable["owner"]):
        project.folder = folder
        project.save(update_fields=("folder", "updated_at"))
    assert folder.with_actor(writer).has_access("write")
