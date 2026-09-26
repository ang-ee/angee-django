"""Proposal visibility through invitations, scoped grants, and global roles."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.apps import apps
from django.db import connection, models
from django.test import override_settings
from django.test.utils import CaptureQueriesContext, isolate_apps
from django.utils import timezone
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.proposals.models import TaskProposalAccess
from tests.conftest import create_platform_admin
from tests.projects_models import Project, Task
from tests.proposals_models import Answer, Proposal, Round, Topic
from tests.tables import model_tables
from tests.test_project_access import project_access_schema as project_access_schema


@pytest.fixture(params=("denormalized", "registry"))
def rebac_storage(request: pytest.FixtureRequest) -> Any:
    """Exercise the same hierarchy through both local storage shapes."""

    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=request.param):
        yield request.param


@pytest.fixture
def proposal_schema(rebac_storage: str, project_access_schema: Any) -> None:
    """Select storage before synchronizing the composed proposal schema."""

    del rebac_storage, project_access_schema


def _grant(resource: Any, relation: str, subject: Any) -> None:
    """Write one direct relationship using the models' canonical identities."""

    write_relationships(
        [RelationshipTuple(to_object_ref(resource), relation, to_subject_ref(subject))]
    )


def _create_proposal(*, actor: Any, round: Round, responder: Any) -> Proposal:
    """Exercise the retained manual factory's explicit preflight and scoped insert."""

    proposal = Proposal(round=round, responder=responder)
    with actor_context(actor):
        Proposal.objects.check_create(
            {
                "round": (round,),
                "responder": (responder,),
            }
        )
        proposal.sudo(reason="tests.proposals.create").save()
    return proposal


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("target_kind", ("project", "task"))
def test_proposals_follow_invitation_and_scope_hierarchy(
    proposal_schema: None,
    target_kind: str,
) -> None:
    """Responders stay sealed; scoped and global viewers inherit exactly row access."""

    del proposal_schema
    user_model = apps.get_model("iam", "User")
    admin = create_platform_admin("proposal-admin")
    alice = user_model.objects.create_user(username="proposal-alice", kind="person")
    bob = user_model.objects.create_user(username="proposal-bob", kind="person")
    carla = user_model.objects.create_user(username="proposal-carla", kind="person")
    diego = user_model.objects.create_user(username="proposal-diego", kind="person")
    service = user_model.objects.create_user(username="proposal-agent", kind="service")
    project_editor = user_model.objects.create_user(
        username="proposal-project-editor",
        kind="person",
    )
    round_viewer = user_model.objects.create_user(
        username="proposal-round-viewer",
        kind="person",
    )
    task_viewer = user_model.objects.create_user(
        username="proposal-task-viewer",
        kind="person",
    )

    with actor_context(admin):
        project = Project.objects.create(title="Example project")
    with system_context(reason="tests.proposals.task"):
        task = Task(project=project, created_by=admin, updated_by=admin)
        Task._base_manager.bulk_create([task])
    now = timezone.now()
    with actor_context(admin):
        round = Round(
            facilitator=admin,
            name="Reviewer response",
            last_call_at=now,
            submission_deadline=now + timedelta(days=7),
            **{target_kind: task if target_kind == "task" else project},
        )
        round.sudo(reason="tests.proposals.round").save()
    with system_context(reason="tests.proposals.topic"):
        topic = Topic.objects.create(
            round=round,
            key="approach",
            name="Approach",
            sort_order=1024.0,
        )

    with system_context(reason="tests.proposals.invite"):
        _grant(round, "responder", alice)
        _grant(round, "responder", bob)

    alice_proposal = _create_proposal(actor=alice, round=round, responder=alice)
    bob_proposal = _create_proposal(actor=bob, round=round, responder=bob)
    with system_context(reason="tests.proposals.answers"):
        alice_answer = Answer.objects.create(
            proposal=alice_proposal,
            topic=topic,
            body="Alice's complete response",
        )
        bob_answer = Answer.objects.create(
            proposal=bob_proposal,
            topic=topic,
            body="Bob's complete response",
        )

    with pytest.raises(PermissionDenied):
        _create_proposal(actor=carla, round=round, responder=carla)
    with pytest.raises(PermissionDenied):
        _create_proposal(actor=bob, round=round, responder=alice)

    assert set(Proposal.objects.as_user(alice).values_list("pk", flat=True)) == {
        alice_proposal.pk
    }
    assert set(Proposal.objects.as_user(bob).values_list("pk", flat=True)) == {
        bob_proposal.pk
    }
    assert set(Proposal.objects.as_user(admin).values_list("pk", flat=True)) == {
        alice_proposal.pk,
        bob_proposal.pk,
    }
    assert not Proposal.objects.as_user(diego).exists()
    assert not Proposal.objects.as_user(service).exists()
    assert set(Answer.objects.as_user(alice).values_list("pk", flat=True)) == {
        alice_answer.pk
    }
    assert set(Answer.objects.as_user(bob).values_list("pk", flat=True)) == {
        bob_answer.pk
    }
    assert not Answer.objects.as_user(diego).exists()

    with system_context(reason="tests.proposals.project_viewer"):
        _grant(project, "editor", project_editor)
        _grant(round, "proposal_viewer", round_viewer)
        _grant(alice_proposal, "reader", carla)
        if target_kind == "task":
            _grant(task, "proposal_viewer", task_viewer)
    with actor_context(project_editor):
        project.with_actor(project_editor).grant_record_access("proposal_viewer", diego)
        project.with_actor(project_editor).grant_record_access("proposal_viewer", service)
    for viewer in (diego, service):
        assert set(Proposal.objects.as_user(viewer).values_list("pk", flat=True)) == {
            alice_proposal.pk,
            bob_proposal.pk,
        }
        assert set(Answer.objects.as_user(viewer).values_list("pk", flat=True)) == {
            alice_answer.pk,
            bob_answer.pk,
        }
        assert not alice_proposal.with_actor(viewer).has_access("read__cost")
    assert set(Proposal.objects.as_user(round_viewer).values_list("pk", flat=True)) == {
        alice_proposal.pk,
        bob_proposal.pk,
    }
    assert set(Proposal.objects.as_user(carla).values_list("pk", flat=True)) == {
        alice_proposal.pk
    }
    assert set(Answer.objects.as_user(carla).values_list("pk", flat=True)) == {
        alice_answer.pk
    }
    assert not alice_proposal.with_actor(carla).has_access("read__cost")
    if target_kind == "task":
        assert set(Proposal.objects.as_user(task_viewer).values_list("pk", flat=True)) == {
            alice_proposal.pk,
            bob_proposal.pk,
        }

    global_viewer = user_model.objects.create_user(
        username="proposal-global-viewer",
        kind="person",
    )
    with system_context(reason="tests.proposals.global_viewer"):
        write_relationships(
            [
                RelationshipTuple(
                    ObjectRef("proposals/role", "proposal_viewer"),
                    "member",
                    to_subject_ref(global_viewer),
                )
            ]
        )
    assert set(Proposal.objects.as_user(global_viewer).values_list("pk", flat=True)) == {
        alice_proposal.pk,
        bob_proposal.pk,
    }
    assert set(Answer.objects.as_user(global_viewer).values_list("pk", flat=True)) == {
        alice_answer.pk,
        bob_answer.pk,
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("model_name", ("round", "topic", "proposal"))
def test_proposal_save_leaves_unrelated_deferred_columns_unwritten(
    proposal_schema: None,
    model_name: str,
) -> None:
    """Invariant checks must preserve Django's loaded-fields-only UPDATE."""

    del proposal_schema
    with system_context(reason="tests.proposals.deferred_save"):
        user = apps.get_model("iam", "User").objects.create_user(username="proposal-deferred-owner")
        project = Project.objects.create(title="Deferred proposal target")
        now = timezone.now()
        round = Round.objects.create(
            project=project,
            facilitator=user,
            name="Original round",
            last_call_at=now,
            submission_deadline=now + timedelta(days=7),
        )
        topic = Topic.objects.create(round=round, key="scope", name="Original topic", sort_order=1024.0)
        proposal = Proposal.objects.create(round=round, responder=user)
        row, field = {
            "round": (round, "name"),
            "topic": (topic, "name"),
            "proposal": (proposal, "staffing"),
        }[model_name]
        deferred = type(row)._base_manager.defer("created_at").get(pk=row.pk)
        original_created_at = row.created_at
        assert "created_at" not in deferred.__dict__
        setattr(deferred, field, "Changed value")
        with CaptureQueriesContext(connection) as queries:
            deferred.save()
        updates = [
            query["sql"] for query in queries
            if query["sql"].startswith(f'UPDATE "{row._meta.db_table}"')
        ]
        assert len(updates) == 1
        assert '"created_at" =' not in updates[0]
        stored = type(row)._base_manager.get(pk=row.pk)
        assert getattr(stored, field) == "Changed value"
        assert stored.created_at == original_created_at


@pytest.mark.django_db(transaction=True)
@isolate_apps()
def test_task_proposal_donor_preserves_deferred_save() -> None:
    """The optional queue guard must not load other columns on an unrelated save."""

    class DeferredTask(TaskProposalAccess, models.Model):
        title = models.CharField(max_length=80)
        body = models.TextField()

        class Meta:
            app_label = "tests"

    with model_tables((DeferredTask,)):
        row = DeferredTask.objects.create(title="Original", body="Retained")
        deferred = DeferredTask.objects.only("pk", "title").get(pk=row.pk)
        deferred.title = "Changed"
        with CaptureQueriesContext(connection) as queries:
            deferred.save()
        updates = [query["sql"] for query in queries if query["sql"].startswith("UPDATE ")]
        assert len(updates) == 1
        assert '"body" =' not in updates[0]
        stored = DeferredTask.objects.get(pk=row.pk)
        assert stored.title == "Changed"
        assert stored.body == "Retained"
