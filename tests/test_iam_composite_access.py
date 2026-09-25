"""Composite IAM-group access across project-owned resource cascades."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.test import override_settings
from rebac import (
    ObjectRef,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    system_context,
    to_object_ref,
)
from rebac.backends import backend
from rebac.relationships import write_relationships

from angee.base.identity import public_subject_ref
from angee.iam.roles import principal_access
from angee.projects.access import bind
from tests.conftest import Backend, Drive, Folder, Vendor
from tests.iam_models import Group
from tests.messaging_models import Channel, Message, Thread
from tests.projects_models import Project
from tests.test_project_access import project_access_schema as project_access_schema


@pytest.fixture(params=("denormalized", "registry"))
def rebac_storage(request: pytest.FixtureRequest) -> Any:
    """Exercise the same public behavior through both local storage shapes."""

    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=request.param):
        yield request.param


@pytest.fixture
def composite_schema(rebac_storage: str, project_access_schema: Any) -> None:
    """Order storage selection before composed-schema synchronization."""

    del rebac_storage, project_access_schema


@pytest.mark.django_db(transaction=True)
def test_group_membership_reaches_and_revokes_project_resource_cascade(
    composite_schema: None,
) -> None:
    """Person and service members inherit one project grant and lose every arrow on removal."""

    del composite_schema
    user_model = apps.get_model("iam", "User")
    owner = user_model.objects.create_user(username="composite-owner")
    person = user_model.objects.create_user(username="composite-person", kind="person")
    service = user_model.objects.create_user(username="composite-service", kind="service")
    with system_context(reason="test composite resources"):
        group = Group.objects.create(name="Composite editors")
        group.add_member(str(SubjectRef(to_object_ref(person))))
        group.add_member(str(SubjectRef(to_object_ref(service))))
        vendor = Vendor.objects.create(slug="composite", display_name="Composite")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
        storage_backend = Backend.objects.create(
            slug="composite",
            label="Composite",
            backend_class="local",
        )
        drive = Drive.objects.create(
            backend=storage_backend,
            slug="composite",
            name="Composite",
        )
        folder = Folder.objects.create(drive=drive, name="Composite files", owner=owner)
    with actor_context(owner):
        project = Project.objects.create(title="Composite access")
        thread = Thread.objects.create(channel=channel)
        message = Message.objects.create(thread=thread)
        bind(project=project, target=folder)
        bind(project=project, target=channel)
        group_members = SubjectRef.of("auth/group", str(group.pk), "member")
        write_relationships(
            [
                RelationshipTuple(to_object_ref(project), "editor", group_members),
                RelationshipTuple(
                    ObjectRef("knowledge/role", "vault_viewer"),
                    "member",
                    group_members,
                ),
            ]
        )

    for member in (person, service):
        assert project.with_actor(member).has_access("write")
        assert folder.with_actor(member).has_access("write")
        assert channel.with_actor(member).has_access("write")
        assert thread.with_actor(member).has_access("write")
        assert message.with_actor(member).has_access("read")
        assert message.with_actor(member).has_access("write")
        assert (
            backend()
            .check_access(
                subject=SubjectRef(to_object_ref(member)),
                action="member",
                resource=ObjectRef("knowledge/role", "vault_viewer"),
            )
            .allowed
        )
        access = principal_access(SubjectRef(to_object_ref(member)))
        public_group = str(public_subject_ref(group_members))
        assert any(
            row.role == "knowledge/role:vault_viewer" and row.source == public_group and not row.direct
            for row in access.roles
        )
        assert any(
            row.resource_type == "projects/project"
            and row.relation == "editor"
            and row.source == public_group
            and not row.direct
            for row in access.grants
        )
        assert any(
            row.resource_type == "projects/project"
            and row.permission == "write"
            and row.source.endswith("#editor")
            and not row.direct
            for row in access.permissions
        )

    group_access = principal_access(group_members)
    assert any(row.role == "knowledge/role:vault_viewer" and row.direct for row in group_access.roles)
    assert any(
        row.resource_type == "projects/project" and row.relation == "editor" and row.direct
        for row in group_access.grants
    )

    with system_context(reason="test composite revoke"):
        assert group.remove_member(str(SubjectRef(to_object_ref(person))))
        assert group.remove_member(str(SubjectRef(to_object_ref(service))))

    for former_member in (person, service):
        assert (
            not backend()
            .check_access(
                subject=SubjectRef(to_object_ref(former_member)),
                action="member",
                resource=to_object_ref(group),
            )
            .allowed
        )
        access = principal_access(SubjectRef(to_object_ref(former_member)))
        assert all(row.role != "knowledge/role:vault_viewer" for row in access.roles)
        assert all(row.resource_type != "projects/project" for row in access.grants)
        assert not project.with_actor(former_member).has_access("write")
        assert not folder.with_actor(former_member).has_access("write")
        assert not channel.with_actor(former_member).has_access("write")
        assert not thread.with_actor(former_member).has_access("write")
        assert not message.with_actor(former_member).has_access("read")
        assert not message.with_actor(former_member).has_access("write")
        assert (
            not backend()
            .check_access(
                subject=SubjectRef(to_object_ref(former_member)),
                action="member",
                resource=ObjectRef("knowledge/role", "vault_viewer"),
            )
            .allowed
        )
