"""Composite IAM-group access across project-owned resource cascades."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.db import connection
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

from angee.projects.access import bind
from tests.conftest import Folder, Vendor, _clear_model_tables, _create_missing_tables
from tests.iam_models import Group
from tests.integrate_models import Integration
from tests.messaging_models import Channel, Fragment, Message, Thread
from tests.projects_models import PROJECT_TEST_MODELS, Project
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
    models = (
        Group,
        Vendor,
        Integration,
        Channel,
        Folder,
        *PROJECT_TEST_MODELS,
        Fragment,
        Thread,
        Message,
    )
    created = _create_missing_tables(models)
    try:
        owner = user_model.objects.create_user(username="composite-owner")
        person = user_model.objects.create_user(username="composite-person", kind="person")
        service = user_model.objects.create_user(username="composite-service", kind="service")
        with system_context(reason="test composite resources"):
            group = Group.objects.create(name="Composite editors")
            group.add_member(str(SubjectRef(to_object_ref(person))))
            group.add_member(str(SubjectRef(to_object_ref(service))))
            vendor = Vendor.objects.create(slug="composite", display_name="Composite")
            channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
            folder = Folder.objects.create(name="Composite files", owner=owner)
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
            with actor_context(member):
                assert project.has_access("write")
                assert folder.has_access("write")
                assert channel.has_access("write")
                assert thread.has_access("write")
                assert message.has_access("read")
                assert message.has_access("write")
            assert backend().check_access(
                subject=SubjectRef(to_object_ref(member)),
                action="member",
                resource=ObjectRef("knowledge/role", "vault_viewer"),
            ).allowed

        with system_context(reason="test composite revoke"):
            group.remove_member(str(SubjectRef(to_object_ref(person))))
            group.remove_member(str(SubjectRef(to_object_ref(service))))

        for former_member in (person, service):
            with actor_context(former_member):
                assert not project.has_access("write")
                assert not folder.has_access("write")
                assert not channel.has_access("write")
                assert not thread.has_access("write")
                assert not message.has_access("read")
                assert not message.has_access("write")
            assert not backend().check_access(
                subject=SubjectRef(to_object_ref(former_member)),
                action="member",
                resource=ObjectRef("knowledge/role", "vault_viewer"),
            ).allowed
    finally:
        _clear_model_tables(models)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)
