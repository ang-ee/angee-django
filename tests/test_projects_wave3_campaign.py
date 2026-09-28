"""Project projections and access-bearing edits compose their existing owners."""

from copy import deepcopy

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import PermissionDenied, actor_context, system_context
from rebac.backends import backend
from rebac.schema.parser import parse_zed

from angee.projects.access import bind, unbind
from angee.storage.exceptions import UploadDenied
from tests.conftest import Backend, Drive, File, FileAttachment, Page, RecordBinding, Vault
from tests.messaging_campaign import grant
from tests.messaging_models import Person
from tests.projects_models import Milestone, Project, Task
from tests.spaces_models import Group, Membership
from tests.t3_campaign import campaign_access as campaign_access
from tests.t3_campaign import campaign_user as campaign_user
from tests.t3_campaign import messaging_access_schema as messaging_access_schema
from tests.t3_campaign import relationship_snapshot


@pytest.fixture
def project_case(campaign_access, campaign_user):
    owner, reader, assignee = (campaign_user(role) for role in ("owner", "reader", "assignee"))
    with system_context(reason="tests.t3.project"):
        project = Project.objects.create(title="Project", owner=owner)
        task = Task.objects.create(title="Item", project=project, owner=owner, assignee=assignee)
    grant(project, "reader", reader)
    return owner, reader, assignee, project, task


@pytest.mark.parametrize("path", ("save", "partial", "queryset", "bulk_update"))
@pytest.mark.parametrize("elevated", (False, True))
def test_visibility_cannot_bypass_its_verb_even_under_system_context(project_case, path, elevated):
    owner, _, _, _, task = project_case
    before = Task._base_manager.filter(pk=task.pk).values().get()
    context = system_context(reason="tests.t3.visibility") if elevated else actor_context(owner)
    with context:
        candidate = Task.objects.get(pk=task.pk)
        candidate.visibility = "restricted"
        with pytest.raises((ValidationError, ValueError)):
            if path == "queryset":
                Task.objects.filter(pk=task.pk).update(visibility="restricted")
            elif path == "bulk_update":
                Task.objects.bulk_update([candidate], ["visibility"])
            else:
                candidate.save(**({"update_fields": ("visibility",)} if path == "partial" else {}))
    assert Task._base_manager.filter(pk=task.pk).values().get() == before


@pytest.mark.parametrize("initial,target", (("inherited", "restricted"), ("restricted", "inherited")))
def test_visibility_hook_sees_locked_old_value_and_refusal_is_atomic(project_case, monkeypatch, initial, target):
    owner, _, _, _, task = project_case
    with actor_context(owner):
        task.with_actor(owner).set_visibility(initial)
    before = Task._base_manager.filter(pk=task.pk).values().get()
    observed = []

    def refuse(row, value):
        observed.append((row.pk, row.visibility, value))
        raise ValidationError({"visibility": "This record cannot change audience."})

    monkeypatch.setattr(Task, "validate_visibility", refuse)
    with actor_context(owner), pytest.raises(ValidationError, match="cannot change audience"):
        task.with_actor(owner).set_visibility(target)
    assert observed == [(task.pk, initial, target)]
    assert Task._base_manager.filter(pk=task.pk).values().get() == before


def test_priority_rank_uses_enum_order_in_one_query_per_direction(project_case, django_assert_num_queries):
    owner, _, _, project, initial = project_case
    choices = tuple(Task._meta.get_field("priority").choices_enum)
    with system_context(reason="tests.t3.priorities"):
        initial.delete()
        rows = [
            Task.objects.create(title=str(choice), priority=choice, project=project, owner=owner)
            for choice in reversed(choices)
        ]
    ids = {row.priority: row.pk for row in rows}
    with actor_context(owner):
        query = Task._base_manager.annotate(rank=Task.objects.priority_rank_expression())
        # Warm schema metadata independently of the actual ordered SELECT budget.
        list(query.values_list("pk", flat=True))
        for descending in (False, True):
            with django_assert_num_queries(1):
                actual = list(query.order_by("-rank" if descending else "rank").values_list("pk", flat=True))
            assert actual == [ids[choice] for choice in (reversed(choices) if descending else choices)]


def test_phase_only_permission_discloses_name_without_project_access(project_case):
    manager, _, _, _, task = project_case
    # The source task's owner is the submitter; the promoted project has another owner.
    submitter = task.assignee
    with system_context(reason="tests.t3.promoted_phase"):
        Task._base_manager.filter(pk=task.pk).update(owner=submitter, project=None)
        promoted = Project.objects.create(title="Hidden project title", owner=manager, converted_from=task)
        phase = Milestone.objects.create(project=promoted, name="Current phase")
        promoted.set_current_milestone(phase)
    task = Task._base_manager.get(pk=task.pk)
    assert task.with_actor(submitter).promoted_phase() is None
    active = backend()
    original = active.schema()
    schema = deepcopy(original)
    fragment = parse_zed("definition projects/task { permission read_promoted_phase = owner }").definitions[0]
    schema.definitions = [
        definition.extend(permission_arms=fragment.permissions)
        if definition.resource_type == "projects/task"
        else definition
        for definition in schema.definitions
    ]
    active.set_schema(schema)
    try:
        assert task.with_actor(submitter).promoted_phase() == phase.name
        assert not Project.objects.with_actor(submitter).filter(pk=promoted.pk).exists()
        assert not Milestone.objects.with_actor(submitter).filter(pk=phase.pk).exists()
        with actor_context(submitter):
            assert not task.promoted_projects.exists()
            query = Task._base_manager.annotate(_promoted_phase=Task.objects.promoted_phase_expression())
            list(query.values_list("_promoted_phase", flat=True))
            with CaptureQueriesContext(connection) as queries:
                values = list(query.filter(pk=task.pk).values_list("_promoted_phase", flat=True))
            assert values == [phase.name]
            assert len(queries) == 1
    finally:
        active.set_schema(original)


def test_record_file_follows_task_and_download_rechecks_after_narrowing(project_case):
    owner, reader, assignee, _, task = project_case
    with system_context(reason="tests.t3.record_file"):
        backend_row = Backend.objects.create(slug="task-files", label="Files", backend_class="local")
        drive = Drive.objects.create(backend=backend_row, slug="task-files", name="Files", owner=owner)
        record_file = File.objects.create(
            drive=drive,
            filename="record.txt",
            content_hash="b" * 64,
            storage_path="record.txt",
            visibility="record",
            owner=None,
            upload_state="ready",
        )
        inherited = File.objects.create(drive=drive, filename="drive.txt", content_hash="c" * 64, owner=None)
        FileAttachment.objects.attach(record_file, task)
    grant(drive, "viewer", reader)
    before = relationship_snapshot()
    token = record_file.issue_download_token(actor=reader)
    assert File.objects.for_download_token(token).pk == record_file.pk
    with actor_context(owner):
        task.with_actor(owner).set_visibility("restricted")
    for actor, allowed in ((owner, True), (assignee, True), (reader, False)):
        assert record_file.with_actor(actor).has_access("read") is allowed
        assert File.objects.with_actor(actor).filter(pk=record_file.pk).exists() is allowed
    assert inherited.with_actor(reader).has_access("read")
    with pytest.raises(UploadDenied):
        File.objects.for_download_token(token)
    assert relationship_snapshot() == before


def test_project_vault_binding_needs_share_and_drive_inherits_share(project_case, campaign_user):
    owner, reader, _, project, _ = project_case
    writer = campaign_user("moderator")
    with system_context(reason="tests.t3.project_containers"):
        team = Group.objects.create(name="Project team", owner=owner)
        Membership.objects.create(
            group=team, party=Person.objects.for_user(writer), role="moderator", is_confirmed=True
        )
        project.team = team
        project.save(update_fields=("team",))
        destination = Project.objects.create(title="Other project", owner=writer)
        vault = Vault.objects.create(name="Bound vault", owner=owner)
        backend_row = Backend.objects.create(slug="bound-drive", label="Drive", backend_class="local")
        drive = Drive.objects.create(backend=backend_row, slug="bound-drive", name="Bound drive", owner=None)
        bind(project=project, target=vault)
        bind(project=project, target=drive)
    assert vault.with_actor(writer).has_access("write")
    assert not vault.with_actor(writer).has_access("share")
    before = relationship_snapshot()
    with actor_context(writer), pytest.raises(PermissionDenied):
        bind(project=destination.with_actor(writer), target=vault.with_actor(writer))
    for row in (vault, drive):
        assert row.with_actor(owner).has_access("share")
        assert row.with_actor(reader).has_access("read")
        assert not row.with_actor(reader).has_access("share")
        with actor_context(owner):
            unbind(project=project.with_actor(owner), target=row.with_actor(owner))
        assert not row.with_actor(reader).has_access("read")
    assert not drive.with_actor(owner).has_access("share")
    assert relationship_snapshot() == before


def test_knowledge_binding_requires_both_readable_ends(project_case):
    owner, reader, _, _, task = project_case
    with system_context(reason="tests.t3.knowledge_edges"):
        vault = Vault.objects.create(name="Private knowledge", owner=owner)
        page = Page.objects.create(vault=vault, title="Guide")
    with actor_context(owner):
        binding = RecordBinding.objects.upsert(page=page, target=task)
    with actor_context(reader):
        assert not RecordBinding.objects.for_record(task).exists()
    grant(vault, "viewer", reader)
    with actor_context(reader):
        assert list(RecordBinding.objects.for_record(task).values_list("pk", flat=True)) == [binding.pk]
    with actor_context(owner):
        task.with_actor(owner).set_visibility("restricted")
    with actor_context(reader):
        assert not RecordBinding.objects.for_record(task).exists()
