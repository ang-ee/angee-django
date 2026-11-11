"""The work decision table, and project-team access derived from the live roster and guarded as sharing."""

from datetime import date

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rebac import PermissionDenied, actor_context, system_context
from rebac.testing import assert_decisions

from angee.base.testing.permissions import installed_field_owners
from angee.graphql.testing.documents import execute_schema
from angee.messaging.testing.models import Person
from angee.projects.testing.models import Cycle, Project, Task
from angee.spaces.testing.models import Group, Membership
from angee.work import schema as work_schema
from angee.work.models import ProjectWork
from tests.conftest import addon_schema, create_platform_admin
from tests.decision_table import seat_cells
from tests.test_productivity_write_behavior import Queue, Stage


@pytest.fixture
def team_case(composed_tables):
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


def test_project_roster_hides_seat_identity_without_membership_read(team_case):
    project, team, _, author, users = team_case
    with system_context(reason="test.work.private_roster"):
        team.visibility = "private"
        team.save(update_fields=["visibility"])
    query = """query($project: ID!) {
      project_manager_roster(project: $project) {
        people { subject label seat_id removable }
      }
    }"""
    schema = addon_schema(work_schema.schemas, "console")
    assert project.with_actor(author).has_access("read")
    assert not team.with_actor(author).has_access("read")
    hidden = execute_schema(schema, query, {"project": project.sqid}, user=author)
    assert hidden.errors is None
    people = hidden.data["project_manager_roster"]["people"]
    assert people and all(person["label"] for person in people)
    assert all(person["subject"] is None and person["seat_id"] is None and not person["removable"] for person in people)

    team_reader = users["owner"]
    assert team.with_actor(team_reader).has_access("read")
    visible = execute_schema(schema, query, {"project": project.sqid}, user=team_reader)
    assert visible.errors is None
    people = visible.data["project_manager_roster"]["people"]
    assert people and all(person["subject"] and person["seat_id"] for person in people)


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


def test_the_policy_decides_every_work_permission(composed_tables):
    """One queue world, every work permission and task extension arm with an allow and a deny."""

    seats = {role: get_user_model().objects.create_user(username=f"work-policy-{role}", password=None)
             for role in ("column-owner", "owner", "moderator", "member", "viewer", "outsider")}
    seats.update(admin=create_platform_admin("work-policy-admin"), anonymous=AnonymousUser())
    with system_context(reason="test.work.policy.world"):
        queue = Queue.objects.create(name="Policy", key="POLICY", slug="policy", owner=seats["column-owner"])
        for role in ("owner", "moderator", "member", "viewer"):
            Membership.objects.create(
                group=queue, party=Person.objects.for_user(seats[role]), role=role, is_confirmed=True,
            )
        stage = Stage._base_manager.get(pk=queue.default_stage_id)
        cycle = Cycle.objects.create(queue=queue, number=1, starts_on=date(2026, 1, 1), ends_on=date(2026, 1, 7))
        task = Task.objects.create(title="Queued", queue=queue)
        unstaged = Task.objects.create(title="Unstaged", queue=queue)
        Task._base_manager.filter(pk=unstaged.pk).update(stage=None)
        trashed = Task.objects.create(title="Trashed", queue=queue)
        trashed.trash(reason="Withdrawn")
    cells = seat_cells(
        seats.__getitem__,
        # work/queue reads and writes through its group's roster
        (queue, "create", "outsider", "anonymous"),
        (queue, "read", "owner moderator member viewer", "outsider"),
        (queue, "post", "member", "viewer"),
        (queue, "write", "moderator", "member"),
        (queue, "share", "owner", "moderator"),
        (queue, "write__owner", "column-owner", "owner"),
        (queue, "delete", "owner", "moderator"),
        *((row, "read", "viewer", "outsider") for row in (stage, cycle)),
        *((row, action, "moderator", "member") for row in (stage, cycle) for action in ("create", "write", "delete")),
        # projects/task arms through an inherited, staged queue
        (task, "read_granted", "member", "outsider"),
        (task, "read", "member", "outsider"),
        (unstaged, "read", "", "member"),
        (task, "write_granted", "moderator", "member"),
        (task, "share_granted", "moderator", "member"),
        (task, "comment_granted", "member", "viewer"),
        (task, "delete_granted", "moderator", "member"),
        (task, "read_as_subject", "member", "outsider"),
        (task, "write_as_subject", "moderator", "member"),
        # a trashed queue task stays with its removal authority
        (trashed, "read", "moderator admin", "member viewer"),
        (trashed, "write", "moderator", "member"),
    )
    assert_decisions(
        checks=cells,
        accessible=((seats["member"], "read", Task, (task,)),),
    )
