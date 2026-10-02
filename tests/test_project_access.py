"""Regression coverage for projects-owned container access inheritance."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import strawberry_django
from django.apps import apps
from django.core.management import call_command
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from graphql import GraphQLEnumType, GraphQLObjectType, get_named_type
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model

from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.fs import write_atomic
from angee.graphql.capabilities import permissions_field
from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from angee.projects.access import bind, unbind
from angee.testing.permissions import installed_field_owners
from tests.conftest import (
    Backend,
    Drive,
    File,
    Folder,
    SchemaAddon,
    Vault,
    Vendor,
    create_platform_admin,
    create_user,
    execute_schema,
    result_data,
)
from tests.messaging_models import Channel, Message, Person, Thread, ThreadAttachment
from tests.projects_models import Project, ProjectBinding, Task
from tests.spaces_models import Group, Membership


@strawberry_django.type(Task)
class TaskPermissionsType(AngeeNode):
    """Exercise the shared field on the real project/work permission graph."""

    permissions = permissions_field(("write", "share", "delete"))


_TASK_PERMISSIONS_RESOURCE = hasura_model_resource(
    TaskPermissionsType,
    model=Task,
    name="permission_tasks",
    filterable=["id"],
    sortable=["id"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
)


@pytest.mark.parametrize("storage", ("denormalized", "registry"))
def test_task_permissions_are_typed_actor_scoped_and_batched(project_access_schema: Any, storage: str) -> None:
    """Three permission answers remain one list projection at 1, 10 and 50 rows."""

    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
        call_command("rebac", "sync", verbosity=0)
        schema = GraphQLSchemas(
            [
                SchemaAddon({"public": {"query": (_TASK_PERMISSIONS_RESOURCE.query,)}}),
            ]
        ).build("public")
        graphql_type = schema._schema.get_type("TaskPermissionsType")
        assert isinstance(graphql_type, GraphQLObjectType)
        enum = get_named_type(graphql_type.fields["permissions"].type)
        assert isinstance(enum, GraphQLEnumType)
        assert set(enum.values) == {"write", "share", "delete"}
        owner = create_user("permission-owner")
        assignee = create_user("permission-assignee")
        reader = create_user("permission-reader")
        with system_context(reason="tests.projects.permissions.seed"):
            project = Project.objects.create(title="Permission project", owner=owner)
            Task.objects.bulk_create([Task(project=project, owner=owner, assignee=assignee) for _ in range(50)])
            write_relationships([RelationshipTuple(to_object_ref(project), "reader", to_subject_ref(reader))])
        document = """
            query Permissions($limit: Int!) {
              permission_tasks(order_by: [{id: asc}], limit: $limit) { id permissions }
            }
        """
        for actor, expected in (
            (owner, ["delete", "share", "write"]),
            (assignee, ["write"]),
            (reader, []),
        ):
            result_data(execute_schema(schema, document, {"limit": 1}, user=actor))
            counts = []
            for limit in (1, 10, 50):
                with (
                    patch(
                        "angee.graphql.capabilities.permission_annotations",
                        side_effect=AssertionError("Per-row permission fallback"),
                    ),
                    CaptureQueriesContext(connection) as queries,
                ):
                    rows = result_data(execute_schema(schema, document, {"limit": limit}, user=actor))[
                        "permission_tasks"
                    ]
                assert len(rows) == limit
                assert all(row["permissions"] == expected for row in rows)
                counts.append(len(queries))
            assert counts[0] == counts[1] == counts[2], counts


def test_project_and_messaging_schemas_declare_the_complete_cascade() -> None:
    """Project grants reach containers, threads, and their messages through native arrows."""

    projects = Path(apps.get_app_config("projects").path, "permissions.extends.zed").read_text()
    messaging = Path(apps.get_app_config("messaging").path, "permissions.zed").read_text()

    for definition in (
        "storage/drive",
        "storage/folder",
        "storage/file",
        "integrate/integration",
        "messaging/thread",
        "knowledge/vault",
        "knowledge/record_binding",
    ):
        assert f"definition {definition}" in projects
    assert "relation channel: messaging/channel // rebac:field=channel" in messaging
    assert "relation thread: messaging/thread // rebac:field=thread" in messaging


@pytest.mark.parametrize("storage", ("denormalized", "registry"))
def test_task_chatter_inherits_live_record_read(project_access_schema: Any, storage: str) -> None:
    """The task's read gate grants chatter read and revokes it when narrowed."""

    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
        call_command("rebac", "sync", verbosity=0)
        user = apps.get_model("iam", "User").objects.create_user(username="task-chatter-reader")
        with system_context(reason="tests.projects.task_chatter"):
            project = Project.objects.create(title="Thread project")
            task = Task.objects.create(project=project)
            attachment = ThreadAttachment.objects.ensure_for_record(task)
            write_relationships([RelationshipTuple(to_object_ref(project), "reader", to_subject_ref(user))])
        thread = attachment.thread
        assert thread.with_actor(user).has_access("read")
        assert Thread.objects.with_actor(user).with_action("read").scoped().filter(pk=thread.pk).exists()
        assert not thread.with_actor(user).has_access("write")
        with system_context(reason="tests.projects.task_narrow"):
            task.set_visibility("restricted")
        assert not thread.with_actor(user).has_access("read")
        assert not Thread.objects.with_actor(user).with_action("read").scoped().filter(pk=thread.pk).exists()


@pytest.fixture
def project_access_schema(tmp_path: Path, transactional_db: None) -> Any:
    """Load composed permission extensions and restore app schema paths afterward."""

    configs = list(apps.get_app_configs())
    originals = {config: getattr(config, "rebac_schema", None) for config in configs}
    sources = extension_source_map(configs, field_owners=installed_field_owners(configs))
    runtime = tmp_path / "project-access-runtime"
    for relative, source in sources.items():
        write_atomic(runtime / relative, source)
    apply_schema_paths(configs, runtime, sources=sources)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        for config, original in originals.items():
            if original is None:
                if hasattr(config, "rebac_schema"):
                    delattr(config, "rebac_schema")
            else:
                config.rebac_schema = original


@pytest.mark.parametrize("storage", ("denormalized", "registry"))
def test_bound_vault_requires_share_to_bind_another_project(project_access_schema: Any, storage: str) -> None:
    """A source project writer cannot expose its vault to another project's readers."""

    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
        call_command("rebac", "sync", verbosity=0)
        manager = create_user("vault-project-manager")
        owner = create_user("vault-owner")
        writer = create_user("vault-project-writer")
        reader = create_user("vault-destination-reader")
        admin = create_platform_admin("vault-administrator")
        with system_context(reason="tests.projects.vault_binding"):
            team = Group.objects.create(name="Project team", slug="vault-project-team")
            Membership.objects.create(
                group=team,
                party=Person.objects.for_user(writer),
                role="moderator",
                is_confirmed=True,
            )
            source = Project.objects.create(title="Source", owner=manager, team=team)
            destination = Project.objects.create(title="Destination", owner=writer)
            vault = Vault.objects.create(name="Bound vault", owner=owner)
            bind(project=source, target=vault)
            write_relationships([RelationshipTuple(to_object_ref(destination), "reader", to_subject_ref(reader))])
        assert source.with_actor(writer).has_access("write")
        assert not source.has_access("share")
        assert vault.with_actor(writer).has_access("write")
        assert not vault.has_access("share")
        with pytest.raises(PermissionDenied, match="Share access to the resource"):
            bind(project=destination.with_actor(writer), target=vault)
        assert not vault.with_actor(reader).has_access("read")
        assert not ProjectBinding._base_manager.filter(project=destination).exists()
        for sharer in (owner, manager, admin):
            assert vault.with_actor(sharer).has_access("share")
            assert Vault.objects.with_actor(sharer).with_action("share").filter(pk=vault.pk).exists()
        unbind(project=source.with_actor(manager), target=vault.with_actor(manager))
        assert not vault.with_actor(manager).has_access("share")
        assert vault.with_actor(owner).has_access("share")


@pytest.mark.django_db(transaction=True)
@override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
def test_project_binding_grants_and_revokes_thread_message_access(
    project_access_schema: Any,
) -> None:
    """A project editor writes a bound thread's message and loses access after unbind."""

    del project_access_schema
    user_model = apps.get_model("iam", "User")
    owner = user_model.objects.create_user(username="project-owner")
    editor = user_model.objects.create_user(username="project-editor", kind="service")
    with actor_context(owner):
        project = Project.objects.create(title="Cascade")
    with system_context(reason="tests.project_access.channel"):
        vendor = Vendor.objects.create(slug="project-channel", display_name="Project Channel")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
    with actor_context(owner):
        thread = Thread.objects.create(channel=channel)
        message = Message.objects.create(thread=thread)
        binding = bind(project=project, target=channel)
        assert bind(project=project, target=channel).pk == binding.pk
        assert ProjectBinding.objects.filter(pk=binding.pk, project=project).exists()
        assert (
            not active_relationship_model()
            .objects.filter(
                resource_type="integrate/integration",
                resource_id=str(channel.pk),
                relation="project",
                subject_type="projects/project",
                subject_id=str(project.pk),
            )
            .exists()
        )
        write_relationships([RelationshipTuple(to_object_ref(project), "editor", to_subject_ref(editor))])
    with system_context(reason="tests.project_access.folder"):
        storage_backend = Backend.objects.create(
            slug="project-folder",
            label="Project folder",
            backend_class="local",
        )
        drive = Drive.objects.create(
            backend=storage_backend,
            slug="project-folder",
            name="Project folder",
        )
        folder = Folder.objects.create(drive=drive, name="Project files", owner=owner)
    with actor_context(owner):
        project.folder = folder
        project.save(update_fields=("folder", "updated_at"))
        bind(project=project, target=folder)
        project.folder = None
        project.save(update_fields=("folder", "updated_at"))
    assert folder.with_actor(editor).has_access("write")
    unbind(project=project.with_actor(owner), target=folder.with_actor(owner))
    assert not folder.with_actor(editor).has_access("write")
    assert channel.with_actor(editor).has_access("write")
    assert thread.with_actor(editor).has_access("write")
    assert message.with_actor(editor).has_access("read")
    assert message.with_actor(editor).has_access("write")
    with actor_context(owner):
        rolled_back = Thread.objects.create()
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                bind(project=project, target=rolled_back)
                assert rolled_back.with_actor(editor).has_access("write")
                raise RuntimeError("rollback")
        assert not rolled_back.with_actor(editor).has_access("write")
        # Seed pre-upgrade evidence directly: native writes reject backed relations.
        leftover = active_relationship_model().objects.create(
            resource_type="messaging/thread",
            resource_id=str(rolled_back.pk),
            relation="project",
            subject_type="projects/project",
            subject_id=str(project.pk),
        )
    assert not rolled_back.with_actor(editor).has_access("write")
    assert active_relationship_model().objects.filter(pk=leftover.pk).exists()
    assert channel.with_actor(editor).has_access("write")
    with transaction.atomic():
        unbind(project=project.with_actor(owner), target=channel.with_actor(owner))
        assert not channel.with_actor(editor).has_access("write")
        assert not thread.with_actor(editor).has_access("write")
        assert not message.with_actor(editor).has_access("read")
        assert not message.with_actor(editor).has_access("write")
    assert binding.pk is not None


@pytest.mark.django_db(transaction=True)
@override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
def test_binding_edits_and_deletes_require_authority(project_access_schema: Any) -> None:
    """Binding persistence cannot move or revoke access around either side's policy owner."""

    del project_access_schema
    user_model = apps.get_model("iam", "User")
    owner = user_model.objects.create_user(username="binding-owner")
    outsider = user_model.objects.create_user(username="binding-outsider")
    with actor_context(owner):
        project = Project.objects.create(title="Protected")
    with system_context(reason="tests.project_access.targets"):
        storage_backend = Backend.objects.create(
            slug="binding-targets",
            label="Binding targets",
            backend_class="local",
        )
        drive = Drive.objects.create(
            backend=storage_backend,
            slug="binding-targets",
            name="Binding targets",
        )
        first = Folder.objects.create(drive=drive, name="First", owner=owner)
        second = Folder.objects.create(drive=drive, name="Second", owner=owner)
    with actor_context(owner):
        binding = bind(project=project, target=first)
    with actor_context(outsider), pytest.raises(PermissionDenied):
        unbind(project=project, target=first)
    with actor_context(outsider), pytest.raises(PermissionDenied):
        ProjectBinding.objects.filter(pk=binding.pk).delete()
    with actor_context(outsider), pytest.raises(PermissionDenied):
        binding.delete()
    with actor_context(outsider), pytest.raises(PermissionDenied):
        binding.target = second
        binding.save()


@pytest.mark.django_db(transaction=True)
@override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
def test_project_drive_access_reaches_folders_and_files(project_access_schema: Any) -> None:
    """A drive binding supplies the native access chain for every contained row."""

    del project_access_schema
    user_model = apps.get_model("iam", "User")
    owner = user_model.objects.create_user(username="drive-project-owner")
    editor = user_model.objects.create_user(username="drive-project-editor")
    with system_context(reason="tests.project_access.drive"):
        backend = Backend.objects.create(slug="project", label="Project", backend_class="local")
        drive = Drive.objects.create(backend=backend, slug="project", name="Project")
        folder = Folder.objects.create(drive=drive, name="Files")
        file = File.objects.create(
            drive=drive,
            folder=folder,
            filename="notes.txt",
            content_hash="0" * 64,
            storage_path="notes.txt",
        )
        write_relationships([RelationshipTuple(to_object_ref(drive), "editor", to_subject_ref(owner))])
    with actor_context(owner):
        project = Project.objects.create(title="Drive cascade")
        bind(project=project, target=drive)
        write_relationships([RelationshipTuple(to_object_ref(project), "editor", to_subject_ref(editor))])
    with actor_context(editor):
        assert drive.has_access("write")
        assert drive.has_access("share")
        assert folder.has_access("write")
        assert file.has_access("write")


def test_projects_app_ready_does_not_require_composed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Native deletion dispatch enumerates installed models without named lookups."""

    def refuse_model_lookup(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Project startup must not look up composed models.")

    monkeypatch.setattr(apps, "get_model", refuse_model_lookup)
    apps.get_app_config("projects").ready()
