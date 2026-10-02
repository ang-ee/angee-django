"""Project team access is derived from the real roster and guarded as sharing."""

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import override_settings
from rebac import PermissionDenied, actor_context, system_context
from rebac.backends import backend
from rebac.evaluator import evaluator_scope

from angee.messaging.testing.models import Person
from angee.projects.testing.models import Project
from angee.spaces.testing.models import Group, Membership
from angee.testing.permissions import installed_field_owners
from angee.work.models import ProjectWork
from tests.test_productivity_write_behavior import Queue, Stage
from tests.test_project_access import project_access_schema as project_access_schema


@pytest.fixture(params=["denormalized", "registry"])
def team_storage(request):
    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=request.param):
        yield


@pytest.fixture
def team_case(team_storage, project_access_schema):
    users = {}
    with system_context(reason="test.work.team.setup"):
        team = Group.objects.create(name="Roster", slug="roster", visibility="public")
        replacement = Group.objects.create(name="Replacement", slug="replacement")
        for role in ("owner", "moderator", "member", "viewer", "outsider", "replacement"):
            user = get_user_model().objects.create_user(username=f"team-{role}", password=None)
            users[role] = user
            if role != "outsider":
                person = Person.objects.for_user(user)
                Membership.objects.create(
                    group=replacement if role == "replacement" else team,
                    party=person, role="member" if role == "replacement" else role,
                    is_confirmed=True,
                )
        author = get_user_model().objects.create_user(username="project-author", password=None)
    with actor_context(author):
        project = Project.objects.create(title="Team project", team=team)
    return project, team, replacement, author, users


def test_project_roster_access_matrix_does_not_carry_share_or_delete(team_case):
    project, team, _, _, users = team_case
    assert issubclass(Project, ProjectWork)
    for role in ("owner", "moderator", "member", "viewer", "outsider"):
        user = users[role]
        project.with_actor(user)
        with actor_context(user):
            assert team.with_actor(user).has_access("read"), "Public team read must not become project read"
            expected_read = role in {"owner", "moderator", "member"}
            expected_write = role in {"owner", "moderator"}
            assert project.has_access("read") is expected_read
            assert project.has_access("write") is expected_write
            assert project.has_access("share") is False
            assert project.has_access("delete") is False
            assert project.has_access("write__team") is False
            assert Project.objects.filter(pk=project.pk).exists() is expected_read


@pytest.mark.parametrize("clear", [False, True])
def test_team_writer_cannot_rebind_or_clear_without_share(team_case, clear):
    project, team, replacement, _, users = team_case
    project.with_actor(users["moderator"])
    project.team = None if clear else replacement
    with actor_context(users["moderator"]), pytest.raises(PermissionDenied):
        project.save(update_fields=["team"])
    assert Project._base_manager.get(pk=project.pk).team_id == team.pk


def test_sharer_rebinding_and_clearing_changes_roster_access_immediately(team_case):
    project, _, replacement, author, users = team_case
    with actor_context(author):
        project.with_actor(author)
        assert project.has_access("share")
        assert project.has_access("write__team")
        project.team = replacement
        project.save(update_fields=["team"])
    with actor_context(users["member"]):
        project.with_actor(users["member"])
        assert not project.has_access("read")
        assert not Project.objects.filter(pk=project.pk).exists()
    with actor_context(users["replacement"]):
        project.with_actor(users["replacement"])
        assert project.has_access("read")
        assert Project.objects.filter(pk=project.pk).exists()
    with actor_context(author):
        project.with_actor(author)
        project.team = None
        project.save(update_fields=["team"])
    with actor_context(users["replacement"]):
        project.with_actor(users["replacement"])
        assert not project.has_access("read")
        assert not Project.objects.filter(pk=project.pk).exists()


def test_deleting_group_nulls_project_team_and_ends_access(team_case):
    project, team, _, _, users = team_case
    with actor_context(users["owner"]):
        team.with_actor(users["owner"]).delete()
    project.refresh_from_db()
    assert project.team_id is None
    with actor_context(users["member"]):
        project.with_actor(users["member"])
        assert not project.has_access("read")
        assert not Project.objects.filter(pk=project.pk).exists()


def test_team_donor_owns_field_gate_and_native_nullable_relation():
    owners = installed_field_owners(apps.get_app_configs())
    assert owners["projects/project"]["team"] == "angee.work"
    field = Project._meta.get_field("team")
    assert field.null
    assert field.related_model is Group
    for declaration in ("hasura_readable_fields", "hasura_filterable_fields",
                        "hasura_insertable_fields", "hasura_updatable_fields"):
        assert "team" in getattr(ProjectWork, declaration)


def test_project_queue_and_stage_scopes_compile_without_enumerating_rows(team_case, django_assert_num_queries):
    project, _, _, _, users = team_case
    with system_context(reason="test.work.scope.queue"):
        queue = Queue.objects.create(key="SCOPES", slug="scopes", name="Scopes")
        Membership.objects.create(group=queue, party=Person.objects.for_user(users["member"]), is_confirmed=True)
    stage = Stage._base_manager.get(pk=queue.default_stage_id)
    with actor_context(users["member"]), evaluator_scope():
        active = backend()
        active.schema()
        for row, action, allowed in ((project, "read", True), (project, "write", False),
                                     (project, "write__team", False), (queue, "read", True),
                                     (queue, "write", False), (stage, "read", True), (stage, "write", False)):
            rows = type(row).objects.with_actor(users["member"]).with_action(action).scoped().filter(pk=row.pk)
            assert list(rows.values_list("pk", flat=True)) == ([row.pk] if allowed else [])
