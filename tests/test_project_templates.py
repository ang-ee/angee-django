"""Project templates: create-from-template copies, role mapping, anchored dates and hidden templates."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import graphql_request
from angee.projects.testing.models import Milestone, Project, Task, TaskRelation
from angee.provenance.contrib.angee.exceptions import CreationKeyConflict
from angee.scheduling.due import Align, DueRule, Offset, Term
from angee.tags.testing.models import Tag, TagAssignment
from tests.test_projects_statements import Source
from tests.test_projects_statements import system_of_record as system_of_record  # noqa: F401 -- autouse

User = get_user_model()
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("activity_catalog")]

BEFORE_FIRST_SLOT = DueRule(terms=[Term(anchor="first_slot", offset=Offset(-48, "hours"))])
WEEK_AFTER_CREATION = DueRule(terms=[Term(anchor="created", offset=Offset(7, "days"), align=[Align.to_local_day()])])
FIRST_SLOT_DAY = DueRule(terms=[Term(anchor="first_slot", align=[Align.to_local_day()])])
FIRST_SLOT = datetime(2026, 12, 10, 19, tzinfo=UTC)


def _user(name: str) -> Any:
    with system_context(reason="tests.projects.templates.user"):
        return User.objects.create_user(username=name, email=f"{name}@example.test")


def _grant(resource: Any, relation: str, user: Any) -> None:
    with system_context(reason="tests.projects.templates.grant"):
        write_relationships([RelationshipTuple(to_object_ref(resource), relation, to_subject_ref(user))])


def _tag(row: Any, name: str) -> Any:
    with system_context(reason="tests.projects.templates.tag"):
        tag, _created = Tag.objects.get_or_create(name=name)
        TagAssignment.objects.create(tag=tag, content_type=ContentType.objects.get_for_model(row), object_id=row.pk)
    return tag


def _tags(row: Any) -> set[str]:
    rows = TagAssignment._base_manager.filter(content_type=ContentType.objects.get_for_model(row), object_id=row.pk)
    return set(rows.values_list("tag__name", flat=True))


@pytest.fixture
def template() -> SimpleNamespace:
    """An invented production template: two phases, a parent task with a subtask, roles and rules."""

    owner = _user("template-owner")
    with actor_context(owner):
        project = Project.objects.create(title="Showcase template", body="How a showcase runs.", is_template=True)
        setup = Milestone.objects.create(project=project, name="Setup", target_date=date(2026, 1, 15))
        delivery = Milestone.objects.create(project=project, name="Delivery", due_rule=FIRST_SLOT_DAY)
        brief = Task.objects.create(
            title="Write the brief",
            project=project,
            milestone=setup,
            assignee_role="producer",
            due_rule=WEEK_AFTER_CREATION,
        )
        outline = Task.objects.create(title="Outline the brief", project=project, parent=brief, assignee=owner)
        reminder = Task.objects.create(
            title="Send the reminder",
            project=project,
            milestone=delivery,
            assignee_role="host",
            due_rule=BEFORE_FIRST_SLOT,
        )
        fixed = Task.objects.create(
            title="Book the room",
            project=project,
            due_date=date(2026, 1, 20),
            visibility="restricted",
        )
        closed = Task.objects.create(title="Old step", project=project)
        TaskRelation.objects.create(task=brief, related_task=reminder, kind="blocks")
    with actor_context(owner):
        Task.objects.get(pk=closed.pk).complete()
    _tag(project, "Showcase")
    _tag(brief, "Writing")
    return SimpleNamespace(
        owner=owner,
        project=project,
        setup=setup,
        delivery=delivery,
        brief=brief,
        outline=outline,
        reminder=reminder,
        fixed=fixed,
        closed=closed,
    )


def _create(template: Any, actor: Any, key: str = "first", **options: Any) -> Any:
    with actor_context(actor):
        source = Project.objects.get(pk=template.pk)
        return Project.objects.create_from_template(source, idempotency_key=key, **options)


def test_a_template_holds_rules_without_dates(template: SimpleNamespace) -> None:
    reminder = Task._base_manager.get(pk=template.reminder.pk)
    assert (reminder.due_date, reminder.due_at) == (None, None)
    assert reminder.rule_due().pending == ("first_slot",)
    assert Task._base_manager.get(pk=template.brief.pk).due_date is None


def test_creating_from_a_template_twice_with_one_key_returns_one_project(template: SimpleNamespace) -> None:
    first = _create(template.project, template.owner)
    again = _create(template.project, template.owner)
    assert again.pk == first.pk
    assert Project._base_manager.filter(created_from_template=template.project).count() == 1
    with pytest.raises(CreationKeyConflict):
        _create(template.project, template.owner, values={"title": "Another"})


def test_a_copy_carries_phases_open_tasks_relations_and_tags_but_no_dates(template: SimpleNamespace) -> None:
    project = _create(template.project, template.owner, values={"title": "Winter showcase"})
    project = Project._base_manager.get(pk=project.pk)
    assert (project.title, project.body, project.is_template) == ("Winter showcase", "How a showcase runs.", False)
    assert project.created_from_template_id == template.project.pk
    assert project.owner_id == template.owner.pk
    assert _tags(project) == {"Showcase"}

    phases = {row.name: row for row in Milestone._base_manager.filter(project=project).order_by("sort_order")}
    assert list(phases) == ["Setup", "Delivery"]
    assert phases["Setup"].target_date is None  # an absolute date never copies
    assert phases["Delivery"].due_rule == template.delivery.due_rule
    assert project.current_milestone_id == phases["Setup"].pk

    tasks = {row.title: row for row in Task._base_manager.filter(project=project)}
    assert set(tasks) == {"Write the brief", "Outline the brief", "Send the reminder", "Book the room"}
    assert tasks["Outline the brief"].parent_id == tasks["Write the brief"].pk
    assert tasks["Write the brief"].milestone_id == phases["Setup"].pk
    assert tasks["Book the room"].due_date is None
    assert (tasks["Book the room"].visibility, tasks["Write the brief"].visibility) == ("restricted", "inherited")
    assert all(row.status == "open" and not row.carries_statements() for row in tasks.values())
    assert _tags(tasks["Write the brief"]) == {"Writing"}
    relation = TaskRelation._base_manager.get(task__project=project)
    assert (relation.task_id, relation.related_task_id, relation.kind) == (
        tasks["Write the brief"].pk,
        tasks["Send the reminder"].pk,
        "blocks",
    )
    # The template is untouched.
    assert Task._base_manager.filter(project=template.project).count() == 5


def test_the_role_mapping_assigns_people_and_an_unmapped_role_stays_a_role(template: SimpleNamespace) -> None:
    producer = _user("template-producer")
    project = _create(template.project, template.owner, role_mapping={"producer": producer})
    tasks = {row.title: row for row in Task._base_manager.filter(project=project)}
    assert (tasks["Write the brief"].assignee_id, tasks["Write the brief"].assignee_role) == (producer.pk, "producer")
    assert (tasks["Send the reminder"].assignee_id, tasks["Send the reminder"].assignee_role) == (None, "host")
    # A task without a role keeps the person the template names.
    assert tasks["Outline the brief"].assignee_id == template.owner.pk


def test_anchored_dates_resolve_at_creation_and_later_through_set_due_anchor(template: SimpleNamespace) -> None:
    project = _create(template.project, template.owner, anchors={"first_slot": FIRST_SLOT})
    tasks = {row.title: row for row in Task._base_manager.filter(project=project)}
    reminder = tasks["Send the reminder"]
    assert reminder.due_at == FIRST_SLOT - timedelta(hours=48)
    created = timezone.localtime(Project._base_manager.get(pk=project.pk).created_at).date()
    assert tasks["Write the brief"].due_date == created + timedelta(days=7)
    assert Milestone._base_manager.get(project=project, name="Delivery").target_date == date(2026, 12, 10)

    later = _create(template.project, template.owner, key="later")
    assert Task._base_manager.get(project=later, title="Send the reminder").due_at is None
    with actor_context(template.owner):
        Project.objects.get(pk=later.pk).set_due_anchor("first_slot", FIRST_SLOT)
    assert Task._base_manager.get(project=later, title="Send the reminder").due_at == FIRST_SLOT - timedelta(hours=48)


def test_a_task_born_project_is_the_tasks_promoted_project() -> None:
    # Phases on a promoted project drive its task's stage through work's milestone mapping,
    # which the bare host does not compose; this template has tasks only.
    owner = _user("promoted-owner")
    with actor_context(owner):
        plan = Project.objects.create(title="Intake template", body="Template guidance.", is_template=True)
        Task.objects.create(title="Call the requester", project=plan)
        source = Task.objects.create(title="Spring showcase", note="Requested by the partner.")
        blank = Task.objects.create(title="Untitled request")
    project = _create(plan, owner, task=source)
    stored = Project._base_manager.get(pk=project.pk)
    assert (stored.converted_from_id, stored.title, stored.body) == (
        source.pk,
        "Spring showcase",
        "Requested by the partner.",
    )
    assert stored.created_from_template_id == plan.pk
    assert list(Task._base_manager.filter(project=project).values_list("title", flat=True)) == ["Call the requester"]
    # A blank promoted description takes the template's.
    assert Project._base_manager.get(pk=_create(plan, owner, key="blank", task=blank).pk).body == "Template guidance."
    with pytest.raises(ValidationError, match="already has a project"):
        _create(plan, owner, key="second", task=source)


def test_creating_needs_template_read_and_a_template(template: SimpleNamespace) -> None:
    outsider, reader = _user("template-outsider"), _user("template-reader")
    with actor_context(outsider), pytest.raises(PermissionDenied):
        Project.objects.create_from_template(template.project.with_actor(outsider), idempotency_key="denied")
    _grant(template.project, "reader", reader)
    copy = _create(template.project, reader)
    assert Project._base_manager.get(pk=copy.pk).owner_id == reader.pk
    with pytest.raises(ValidationError, match="template"):
        _create(copy, reader, key="from-a-copy")


def test_a_project_a_source_states_cannot_be_a_template(template: SimpleNamespace) -> None:
    with actor_context(template.owner):
        stated = Project.objects.create(title="Mirrored project")
    Source("tracker").state(stated, "identity", title="Mirrored project", body="")
    with actor_context(template.owner), pytest.raises(ValidationError, match="cannot be a template"):
        row = Project.objects.get(pk=stated.pk)
        row.is_template = True
        row.save(update_fields=("is_template", "updated_at"))


def test_templates_stay_out_of_attention_overdue_and_ordinary_lists(template: SimpleNamespace) -> None:
    with actor_context(template.owner):
        live = Project.objects.create(title="Live project")
        Task.objects.create(title="Live step", project=live, assignee=template.owner)
        Task.objects.create(title="Loose step", assignee=template.owner)  # about no project
        Milestone.objects.create(project=live, name="Late", target_date=date(2020, 1, 1))
    waiting = Task.objects.waiting_on(template.owner).system_context(reason="tests.projects.templates.waiting")
    assert set(waiting.values_list("title", flat=True)) == {"Live step", "Loose step"}
    assert list(Milestone.objects.overdue().system_context(reason="tests.overdue").values_list("name", flat=True)) == [
        "Late",
    ]

    schema = GraphQLSchemas.from_discovery().build("console")
    document = """query {
      projects(where: {is_template: {_eq: false}}) { id }
      templates: projects(where: {is_template: {_eq: true}}) { id }
      project_tasks(where: {in_template: {_eq: false}}) { title }
    }"""
    with actor_context(template.owner):
        result = schema.execute_sync(document, context_value=SimpleNamespace(request=graphql_request(template.owner)))
    assert result.errors is None, result.errors
    assert [row["id"] for row in result.data["projects"]] == [live.sqid]
    assert [row["id"] for row in result.data["templates"]] == [template.project.sqid]
    # A task about no project is never a template's: the filter keeps it.
    assert {row["title"] for row in result.data["project_tasks"]} == {"Live step", "Loose step"}


def test_graphql_creates_from_a_template_and_hands_a_date_back_to_its_rule(template: SimpleNamespace) -> None:
    producer = _user("graphql-producer")
    schema = GraphQLSchemas.from_discovery().build("console")

    def run(document: str, variables: dict[str, Any]) -> Any:
        with actor_context(template.owner):
            result = schema.execute_sync(
                document,
                variable_values=variables,
                context_value=SimpleNamespace(request=graphql_request(template.owner)),
            )
        assert result.errors is None, result.errors
        return result.data

    created = run(
        """mutation($id: ID!, $user: ID!) {
      create_project_from_template(id: $id, idempotency_key: "gql", title: "Gala",
        roles: [{role: "producer", user: $user}]) { ok id }
    }""",
        {"id": template.project.sqid, "user": producer.sqid},
    )["create_project_from_template"]
    assert created["ok"]
    project = Project._base_manager.get(sqid=created["id"])
    assert Task._base_manager.get(project=project, title="Write the brief").assignee_id == producer.pk

    anchored = run(
        """mutation($id: ID!) {
      set_project_due_anchor(id: $id, name: "first_slot", at: "2026-12-10T19:00:00+00:00") { ok }
    }""",
        {"id": project.sqid},
    )
    assert anchored["set_project_due_anchor"]["ok"]
    reminder = Task._base_manager.get(project=project, title="Send the reminder")
    with actor_context(template.owner):
        row = Task.objects.get(pk=reminder.pk)
        row.due_date = date(2026, 12, 1)
        row.save(update_fields=("due_date", "updated_at"))
    data = run(
        """query($id: String!) { project_tasks(where: {id: {_eq: $id}}) {
      due_date due_at due_status assignee_role due_basis
    } }""",
        {"id": reminder.sqid},
    )["project_tasks"][0]
    assert (data["due_date"], data["due_at"], data["assignee_role"]) == ("2026-12-01", None, "host")
    assert data["due_status"] in {"ok", "overdue"} and data["due_basis"]["at"] == "2026-12-08T19:00:00Z"

    reset = run("""mutation($id: ID!) { reset_task_due_to_rule(id: $id) { ok } }""", {"id": reminder.sqid})
    assert reset["reset_task_due_to_rule"]["ok"]
    assert Task._base_manager.get(pk=reminder.pk).due_date == date(2026, 12, 8)


def test_a_trusted_system_write_creates_from_a_template(template: SimpleNamespace) -> None:
    with system_context(reason="tests.projects.templates.system"):
        project = Project.objects.create_from_template(
            Project.objects.get(pk=template.project.pk),
            idempotency_key="seed",
            values={"title": "Seeded showcase"},
        )
    assert Project._base_manager.get(pk=project.pk).created_from_template_id == template.project.pk
    assert Task._base_manager.filter(project=project).count() == 4
    assert Milestone._base_manager.filter(project=project).count() == 2


def test_a_task_born_project_needs_write_on_the_task() -> None:
    owner, reader = _user("promoted-template-owner"), _user("promoted-reader")
    with actor_context(owner):
        plan = Project.objects.create(title="Intake template", is_template=True)
        source = Task.objects.create(title="Someone else's request")
    _grant(plan, "reader", reader)
    _grant(source, "reader", reader)
    with actor_context(reader), pytest.raises(PermissionDenied):
        Project.objects.create_from_template(
            Project.objects.get(pk=plan.pk),
            idempotency_key="denied",
            task=Task.objects.get(pk=source.pk),
        )
    assert not Project._base_manager.filter(converted_from=source).exists()


def test_the_template_flag_refuses_work_history_and_moves_rule_dates() -> None:
    owner = _user("template-flag")
    with actor_context(owner):
        worked = Project.objects.create(title="Worked project")
        done = Task.objects.create(title="Done step", project=worked)
        Task.objects.get(pk=done.pk).complete()
        live = Project.objects.create(title="Live project")
        follower = Task.objects.create(title="Follower", project=live, due_rule=WEEK_AFTER_CREATION)
    with actor_context(owner), pytest.raises(ValidationError, match="closed tasks"):
        row = Project.objects.get(pk=worked.pk)
        row.is_template = True
        row.save(update_fields=("is_template", "updated_at"))
    dated = Task._base_manager.get(pk=follower.pk).due_date
    assert dated is not None

    def flag(value: bool) -> None:
        with actor_context(owner):
            row = Project.objects.get(pk=live.pk)
            row.is_template = value
            row.save(update_fields=("is_template", "updated_at"))

    flag(True)  # a template supplies no anchors: its rules wait
    assert Task._base_manager.get(pk=follower.pk).due_date is None
    flag(False)
    assert Task._base_manager.get(pk=follower.pk).due_date == dated


def test_the_copy_resolves_every_row_against_one_anchor_map() -> None:
    owner = _user("template-cost")

    def anchor_reads(steps: int) -> int:
        with actor_context(owner):
            plan = Project.objects.create(title=f"Plan {steps}", is_template=True)
            for index in range(steps):
                Task.objects.create(title=f"Step {index}", project=plan, due_rule=WEEK_AFTER_CREATION)
        with CaptureQueriesContext(connection) as queries:
            project = _create(plan, owner, key=f"cost-{steps}")
        assert Task._base_manager.filter(project=project, due_date__isnull=False).count() == steps
        # A row reading its project's anchors on its own selects only them.
        return sum(
            1 for query in queries.captured_queries if '"due_anchors"' in query["sql"] and '"title"' not in query["sql"]
        )

    assert anchor_reads(3) == anchor_reads(9)
