"""Regression coverage for projects-owned container access inheritance."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import strawberry_django
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.db import connection, transaction
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
from rebac.testing import assert_decisions

from angee.graphql.capabilities import permissions_field
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging.testing.models import Channel, Message, Person, Thread, ThreadAttachment
from angee.projects.testing.models import (
    ActivityType,
    Link,
    Milestone,
    Participant,
    Project,
    ProjectBinding,
    Task,
    TaskRelation,
)
from angee.spaces.testing.models import Group, Membership
from angee.tags.testing.models import Tag, TagAssignment
from tests.conftest import (
    Backend,
    Drive,
    File,
    FileAttachment,
    Folder,
    Integration,
    Page,
    RecordBinding,
    SchemaAddon,
    Vault,
    Vendor,
    create_platform_admin,
    create_user,
)
from tests.decision_table import seat_cells
from tests.messaging_campaign import grant


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


def test_the_policy_decides_every_projects_permission(db: Any) -> None:
    """One world, every projects permission and extension arm with an allow and a deny, and its read scopes."""

    del db
    seats = {name: create_user(f"projects-policy-{name}") for name in (
        "owner", "editor", "reader", "team-member", "team-moderator", "task-owner", "assignee",
        "task-reader", "vault-owner", "outsider",
    )}
    seats.update(admin=create_platform_admin("projects-policy-admin"), anonymous=AnonymousUser())
    owner = seats["owner"]
    with system_context(reason="tests.projects.policy.world"):
        team = Group.objects.create(name="Project team", slug="projects-policy-team")
        for seat, role in (("team-member", "member"), ("team-moderator", "moderator")):
            Membership.objects.create(
                group=team, party=Person.objects.for_user(seats[seat]), role=role, is_confirmed=True,
            )
        backend_row = Backend.objects.create(slug="projects-policy", label="Policy", backend_class="local")
        drive = Drive.objects.create(backend=backend_row, slug="projects-policy", name="Bound", owner=owner)
        home = Folder.objects.create(drive=drive, name="Home", owner=owner)
        bound_folder = Folder.objects.create(drive=drive, name="Bound folder", owner=owner)
        project = Project.objects.create(title="Policy project", owner=owner, team=team, folder=home)
        milestone = Milestone.objects.create(project=project, name="Phase", sort_order=1024)
        task = Task.objects.create(project=project, title="Inherited", owner=seats["task-owner"],
                                   assignee=seats["assignee"])
        private = Task.objects.create(project=project, title="Restricted", owner=seats["task-owner"],
                                      visibility="restricted")
        trashed = Task.objects.create(project=project, title="Trashed", owner=seats["task-owner"])
        trashed.trash(reason="Withdrawn")
        project_task = Task.objects.create(subject=project, title="About the project")
        private_task = Task.objects.create(subject=private, title="About the restricted task")
        withheld_task = Task.objects.create(subject=trashed, title="About the trashed task")
        vault = Vault.objects.create(name="Bound vault", owner=seats["vault-owner"])
        vault_task = Task.objects.create(subject=vault, title="About the vault")
        promoted_from = Task.objects.create(title="Promoted", owner=seats["task-owner"])
        Project.objects.create(title="Promoted project", owner=owner, converted_from=promoted_from)
        activity = ActivityType.objects.create(key="projects-policy-call", name="Call")
        relation = TaskRelation.objects.create(task=task, related_task=private, kind="relates")
        participant = Participant.objects.create(project=project, party=Person.objects.for_user(seats["outsider"]))
        task_link = Link.objects.create(target=task, url="https://example.test/spec")
        project_link = Link.objects.create(target=project, url="https://example.test/home")
        vendor = Vendor.objects.create(slug="projects-policy", display_name="Policy channel")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
        bound_thread = Thread.objects.create()
        record_file = File.objects.create(
            drive=drive, filename="record.txt", content_hash="a" * 64, visibility="record", owner=None,
        )
        file_edge = FileAttachment.objects.attach(record_file, private)
        project_chatter = ThreadAttachment.objects.ensure_for_record(project)
        private_chatter = ThreadAttachment.objects.ensure_for_record(private)
        binding = None
        for target in (drive, bound_folder, channel, bound_thread, vault):
            bound = ProjectBinding.objects.bind(project=project, target=target)
            binding = binding or bound
        page = Page.objects.create(vault=Vault.objects.create(name="Notes", owner=owner), title="Spec")
        record_binding = RecordBinding.objects.upsert(page=page, target=task)
        tag = TagAssignment.objects.create(
            tag=Tag.objects.create(name="Policy"), content_type=ContentType.objects.get_for_model(Task),
            object_id=task.pk,
        )
    grant(project, "editor", seats["editor"])
    grant(project, "reader", seats["reader"])
    grant(private, "reader", seats["task-reader"])
    integration = Integration._base_manager.get(pk=channel.pk)

    project_readers = "owner editor reader team-member admin"
    task_writers = "editor team-moderator task-owner assignee"
    cells = seat_cells(
        seats.__getitem__,
        # projects/project
        (project, "create", "outsider", "anonymous"),
        (project, "read", project_readers, "outsider anonymous"),
        (project, "write", "owner editor team-moderator admin", "reader team-member"),
        (project, "share", "owner editor admin", "team-moderator reader"),
        (project, "delete", "owner admin", "editor"),
        (project, "transfer", "owner admin", "editor"),
        (project, "write__owner", "owner", "editor"),
        (project, "write__owns_items", "editor", "reader"),
        (project, "write__team", "editor", "team-moderator"),
        # projects/milestone
        (milestone, "read", "reader", "outsider"),
        *((milestone, action, "editor", "reader") for action in ("create", "write", "reach", "delete")),
        # projects/task on an inherited, a restricted and a trashed task
        (task, "free", "outsider", "anonymous"),
        (task, "create", "outsider", "anonymous"),
        (task, "read_granted", "reader", "outsider"),
        (task, "read", f"{project_readers} task-owner assignee", "outsider anonymous"),
        (private, "read", "task-reader task-owner admin", "reader"),
        (private, "read_as_subject", "task-reader", "reader"),
        (task, "write_granted", task_writers, "reader"),
        (task, "write", task_writers, "reader team-member"),
        (task, "write_as_subject", "assignee", "editor"),
        (task, "share_granted", "editor task-owner", "assignee"),
        (task, "share", "editor task-owner", "assignee"),
        (task, "narrow", "assignee", "reader"),
        (task, "widen", "owner task-owner", "assignee"),
        (task, "delete_granted", "editor task-owner", "assignee"),
        (task, "delete", "editor task-owner", "assignee"),
        (task, "transfer", "task-owner admin", "editor"),
        (task, "write__owner", "task-owner", "editor"),
        (task, "write__assignee", "task-owner", "assignee"),
        (task, "comment_granted", "assignee", "reader"),
        (task, "comment", "assignee", "reader"),
        (trashed, "conceal", "outsider", "anonymous"),
        (trashed, "concealed", "outsider", "anonymous"),
        (task, "concealed", "", "outsider"),
        (trashed, "concealed_keeper", "editor task-owner", "reader"),
        (trashed, "read", "editor task-owner admin", "reader"),
        (trashed, "restore", "editor", "reader"),
        (trashed, "withheld_as_subject", "outsider", "anonymous"),
        (trashed, "keeper_as_subject", "task-owner", "assignee"),
        (promoted_from, "read_promoted_phase", "owner", "task-owner"),
        # record tasks read and write through their subject
        (project_task, "subject_read", "reader", "outsider"),
        (project_task, "subject_write", "editor", "reader"),
        (project_task, "create", "editor", "reader"),
        (project_task, "read", "reader", "outsider"),
        (private_task, "subject_read", "task-reader", "reader"),
        (private_task, "subject_write", "task-owner", "task-reader"),
        (vault_task, "subject_read", "vault-owner", "owner"),
        (withheld_task, "subject_withheld", "outsider", "anonymous"),
        (withheld_task, "subject_keeper", "task-owner", "assignee"),
        (withheld_task, "read", "task-owner", "outsider"),
        # project-owned rows
        (activity, "read", "outsider", "anonymous"),
        *((activity, action, "admin", "owner") for action in ("create", "write", "delete")),
        (relation, "read", "task-owner", "reader"),
        *((relation, action, "assignee", "reader") for action in ("create", "write", "delete")),
        (participant, "read", "reader", "outsider"),
        *((participant, action, "editor", "reader") for action in ("create", "write", "delete")),
        (task_link, "read", "reader", "outsider"),
        (task_link, "write", "assignee", "reader"),
        (project_link, "read", "reader", "outsider"),
        *((project_link, action, "editor", "reader") for action in ("create", "write", "delete")),
        (binding, "read", "reader", "outsider"),
        (binding, "write", "", "owner admin"),
        (binding, "target_share", "owner editor", "reader"),
        (binding, "create", "owner editor", "reader"),
        (binding, "delete", "owner editor", "reader"),
        # extension arms: bound containers, record files, chatter and record edges
        (drive, "read", "reader", "outsider"),
        (drive, "write", "editor", "reader"),
        (drive, "share", "editor", "reader"),
        (bound_folder, "read", "reader", "outsider"),
        (bound_folder, "write", "editor", "reader"),
        (home, "read", "reader", "outsider"),
        (record_file, "read", "task-reader task-owner", "reader"),
        (record_file, "write", "task-owner", "task-reader"),
        (record_file, "share", "task-owner", "task-reader"),
        (integration, "read", "reader", "outsider"),
        (integration, "write", "editor", "reader"),
        (integration, "delete", "editor", "reader"),
        (bound_thread, "read_granted", "reader", "outsider"),
        (bound_thread, "write", "editor", "reader"),
        (project_chatter.thread, "chatter_read", "reader", "outsider"),
        (private_chatter.thread, "chatter_read", "task-reader", "reader"),
        (vault, "read_granted", "reader", "outsider"),
        (vault, "write_granted", "editor", "reader"),
        (vault, "share", "editor", "reader"),
        (file_edge, "target_write", "task-owner", "task-reader"),
        (project_chatter, "target_write", "editor", "reader"),
        (private_chatter, "target_write", "task-owner", "task-reader"),
        (record_binding, "target_read", "reader", "outsider"),
        (record_binding, "target_write", "assignee", "reader"),
        (tag, "target_read", "reader", "outsider"),
        (tag, "target_write", "assignee", "reader"),
    )
    assert_decisions(
        checks=cells,
        accessible=(
            (seats["reader"], "read", Task, (task, project_task)),
            (seats["task-reader"], "read", Task, (private, private_task)),
            (seats["reader"], "read", Project, (project,)),
        ),
    )


def test_task_permissions_are_typed_actor_scoped_and_batched(db: Any) -> None:
    """Three permission answers remain one list projection at 1, 10 and 50 rows."""

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
        # A hierarchy row derives its path on save, so the batch inserts one by one.
        for _ in range(50):
            Task.objects.create(project=project, owner=owner, assignee=assignee)
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


def test_project_binding_grants_and_revokes_thread_message_access(
    db: Any,
) -> None:
    """A project editor writes a bound thread's message and loses access after unbind."""

    del db
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
        binding = ProjectBinding.objects.bind(project=project, target=channel)
        assert ProjectBinding.objects.bind(project=project, target=channel).pk == binding.pk
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
        ProjectBinding.objects.bind(project=project, target=folder)
        project.folder = None
        project.save(update_fields=("folder", "updated_at"))
    assert folder.with_actor(editor).has_access("write")
    with actor_context(owner):
        ProjectBinding.objects.unbind(project=project, target=folder)
    assert not folder.with_actor(editor).has_access("write")
    assert channel.with_actor(editor).has_access("write")
    assert thread.with_actor(editor).has_access("write")
    assert message.with_actor(editor).has_access("read")
    assert message.with_actor(editor).has_access("write")
    with actor_context(owner):
        rolled_back = Thread.objects.create()
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                ProjectBinding.objects.bind(project=project, target=rolled_back)
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
    with transaction.atomic(), actor_context(owner):
        ProjectBinding.objects.unbind(project=project, target=channel)
        assert not channel.with_actor(editor).has_access("write")
        assert not thread.with_actor(editor).has_access("write")
        assert not message.with_actor(editor).has_access("read")
        assert not message.with_actor(editor).has_access("write")
    assert binding.pk is not None


def test_binding_edits_and_deletes_require_authority(db: Any) -> None:
    """Binding persistence cannot move or revoke access around either side's policy owner."""

    del db
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
        binding = ProjectBinding.objects.bind(project=project, target=first)
    with actor_context(outsider):
        assert ProjectBinding.objects.unbind(project=project, target=first) == 0
        assert ProjectBinding.objects.filter(pk=binding.pk).delete() == (0, {})
    with system_context(reason="test project binding scoped deletion"):
        stored = ProjectBinding.objects.get(pk=binding.pk)
        assert stored.project_id == project.pk
        assert stored.target == first
    with actor_context(outsider), pytest.raises(PermissionDenied):
        binding.with_actor(outsider).delete()
    with actor_context(outsider), pytest.raises(PermissionDenied):
        binding.target = second
        binding.with_actor(outsider).save()


def test_projects_app_ready_does_not_require_composed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Native deletion dispatch enumerates installed models without named lookups."""

    def refuse_model_lookup(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Project startup must not look up composed models.")

    monkeypatch.setattr(apps, "get_model", refuse_model_lookup)
    apps.get_app_config("projects").ready()
