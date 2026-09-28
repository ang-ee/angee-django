"""Proposal visibility through admitted shells, scoped grants, and global roles."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.base.mixins import CreationKeyConflict, StaleRevisionError
from tests.conftest import Backend, Drive, create_platform_admin
from tests.messaging_models import Person  # noqa: F401 -- resolve requester and roster person backings
from tests.projects_models import Project, Task
from tests.proposals_models import Answer, Proposal, Round, Topic
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

    write_relationships([RelationshipTuple(to_object_ref(resource), relation, to_subject_ref(subject))])


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
def test_proposals_follow_admission_and_scope_hierarchy(
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

    with actor_context(admin):
        alice_proposal = round.with_actor(admin).admit(alice)
        bob_proposal = round.with_actor(admin).admit(bob)
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

    assert set(Proposal.objects.as_user(alice).values_list("pk", flat=True)) == {alice_proposal.pk}
    assert set(Proposal.objects.as_user(bob).values_list("pk", flat=True)) == {bob_proposal.pk}
    assert set(Proposal.objects.as_user(admin).values_list("pk", flat=True)) == {
        alice_proposal.pk,
        bob_proposal.pk,
    }
    assert not Proposal.objects.as_user(diego).exists()
    assert not Proposal.objects.as_user(service).exists()
    assert set(Answer.objects.as_user(alice).values_list("pk", flat=True)) == {alice_answer.pk}
    assert set(Answer.objects.as_user(bob).values_list("pk", flat=True)) == {bob_answer.pk}
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
    assert set(Proposal.objects.as_user(carla).values_list("pk", flat=True)) == {alice_proposal.pk}
    assert set(Answer.objects.as_user(carla).values_list("pk", flat=True)) == {alice_answer.pk}
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
        responder = apps.get_model("iam", "User").objects.create_user(username="proposal-deferred-responder")
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
        proposal = Proposal.objects.create(round=round, responder=responder)
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
        updates = [query["sql"] for query in queries if query["sql"].startswith(f'UPDATE "{row._meta.db_table}"')]
        assert len(updates) == 1
        assert '"created_at" =' not in updates[0]
        stored = type(row)._base_manager.get(pk=row.pk)
        assert getattr(stored, field) == "Changed value"
        assert stored.created_at == original_created_at


@pytest.mark.django_db(transaction=True)
def test_task_proposal_donor_preserves_deferred_save() -> None:
    """The question guard preserves a loaded-fields-only update of unrelated facts."""

    with system_context(reason="tests.proposals.task_deferred_save"):
        row = Task.objects.create(clarification_creation_key="retained")
        deferred = Task.objects.only("pk", "note").get(pk=row.pk)
        deferred.note = "Changed note"
        with CaptureQueriesContext(connection) as queries:
            deferred.save()
        updates = [query["sql"] for query in queries if query["sql"].startswith("UPDATE ")]
        assert len(updates) == 1
        assert '"clarification_creation_key" =' not in updates[0]
        stored = Task.objects.get(pk=row.pk)
        assert stored.note == "Changed note"
        assert stored.clarification_creation_key == "retained"


def _review_round(admin, *, policy="drafts_and_tracks"):
    with actor_context(admin):
        project = Project.objects.create(title="Round target")
        now = timezone.now()
        round = Round(
            project=project,
            facilitator=admin,
            name="Review round",
            opening_policy=policy,
            last_call_at=now + timedelta(days=1),
            submission_deadline=now + timedelta(days=2),
        )
        round.sudo(reason="tests.proposals.review_round").save()
    return round


@pytest.mark.django_db(transaction=True)
def test_disclosure_excludes_preopening_withdrawal_and_hides_peer_identity(proposal_schema):
    admin = create_platform_admin("disclosure-manager")
    user_model = apps.get_model("iam", "User")
    alice = user_model.objects.create_user(username="disclosure-alice")
    bob = user_model.objects.create_user(username="disclosure-bob")
    carla = user_model.objects.create_user(username="disclosure-carla")
    round = _review_round(admin)
    with actor_context(admin):
        alice_proposal = round.with_actor(admin).admit(alice)
        bob_proposal = round.admit(bob)
        round.admit(carla)
    with actor_context(alice):
        alice_proposal.with_actor(alice).submit()
        alice_proposal.with_actor(alice).withdraw()
    with actor_context(admin):
        revision = round.revision
        assert round.with_actor(admin).can_admit()
        round.open(expected_revision=revision)
        with pytest.raises(StaleRevisionError):
            round.open(expected_revision=revision)
    with system_context(reason="tests.proposals.receipts"):
        alice_proposal.refresh_from_db()
        bob_proposal.refresh_from_db()
    assert alice_proposal.disclosed_at is None
    assert bob_proposal.disclosed_at is not None
    assert bob_proposal.with_actor(carla).has_access("read")
    assert not bob_proposal.with_actor(carla).has_access("read__responder")
    assert bob_proposal.with_actor(bob).has_access("read__responder")


@pytest.mark.django_db(transaction=True)
def test_retirement_cleans_peer_tracks_and_excludes_group_shared_answers(proposal_schema, settings):
    admin = create_platform_admin("retirement-manager")
    user_model = apps.get_model("iam", "User")
    alice = user_model.objects.create_user(username="retirement-alice")
    bob = user_model.objects.create_user(username="retirement-bob")
    round = _review_round(admin)
    with actor_context(admin), system_context(reason="tests.proposals.drive"):
        backend = Backend.objects.create(slug="proposal-backend", backend_class="local")
        Drive.objects.create(slug="proposal-default", name="Default", prefix="default", backend=backend)
        settings.ANGEE_STORAGE_DEFAULT_DRIVE = "proposal-default"
    with actor_context(admin):
        own = round.with_actor(admin).admit(alice, track=True)
        peer = round.admit(bob, track=True)
        assert own.track.owner_id is None
        assert peer.create_track().pk == peer.track_id
        assert peer.track.owns_items
    with actor_context(admin), system_context(reason="tests.proposals.retirement_shares"):
        peer_task = Task.objects.create(project=peer.track, title="Peer task", owner=alice, assignee=alice)
        drive = Drive.objects.get(project_bindings__project=peer.track)
        topic = Topic.objects.create(round=round, key="response", name="Response")
        answer = Answer.objects.create(proposal=peer, topic=topic)
        group = apps.get_model("iam", "Group").objects.create(name="Reviewers")
        _grant(group, "member", alice)
        group_subject = SubjectRef.of("auth/group", str(group.pk), "member")
        _grant(answer, "reader", group_subject)
        _grant(drive, "viewer", alice)
        _grant(peer.track, "editor", alice)
    assert answer.with_actor(alice).has_access("read")
    with actor_context(admin):
        report = round.with_actor(admin).remove_responder(alice, expected_revision=round.revision)
    assert report["removed"]
    assert report["reported"]
    assert not answer.with_actor(alice).has_access("read")
    assert not peer.track.with_actor(alice).has_access("read")
    assert not drive.with_actor(alice).has_access("read")
    with system_context(reason="tests.proposals.retirement_result"):
        peer_task.refresh_from_db()
    assert peer_task.owner_id is None
    assert peer_task.assignee_id is None
    with actor_context(admin), pytest.raises(ValidationError, match="not admitted"):
        round.with_actor(admin).remove_responder(admin)


@pytest.mark.django_db(transaction=True)
def test_question_surrender_replay_and_both_immutable_donors(proposal_schema):
    admin = create_platform_admin("question-manager")
    user_model = apps.get_model("iam", "User")
    asker = user_model.objects.create_user(username="question-asker")
    recipient = user_model.objects.create_user(username="question-recipient")
    round = _review_round(admin)
    with actor_context(admin):
        round.with_actor(admin).admit(asker)
        round.admit(recipient)
    with actor_context(asker):
        question = round.with_actor(asker).ask(
            "Question", "Body", audience="managers", client_creation_key="question-1"
        )
        assert round.ask("Question", "Body", audience="managers", client_creation_key="question-1").pk == question.pk
        with pytest.raises(CreationKeyConflict):
            round.ask("Changed", "Body", audience="managers", client_creation_key="question-1")
    with actor_context(admin):
        with pytest.raises(ValidationError, match="surrendered"):
            round.with_actor(admin).pass_clarification(question, recipient)
        passed = round.pass_clarification(question, recipient, audience="asker")
        assert passed.clarification_waiting() == [{"id": recipient.pk, "name": recipient.username}]
    assert passed.created_by_id is None
    assert passed.updated_by_id is None
    with system_context(reason="tests.proposals.immutable_donors"):
        row = Task.objects.get(pk=question.pk)
        row.visibility = "inherited"
        with pytest.raises(ValidationError, match="immutable"):
            row.save(update_fields=("visibility",))
        row.refresh_from_db()
        row.clarification_creation_key = "replacement"
        with pytest.raises(ValidationError, match="immutable"):
            row.save(update_fields=("clarification_creation_key",))


@pytest.mark.django_db(transaction=True)
def test_answer_insert_cannot_widen_responder_audience(proposal_schema):
    admin = create_platform_admin("answer-manager")
    responder = apps.get_model("iam", "User").objects.create_user(username="answer-responder")
    round = _review_round(admin)
    with actor_context(admin):
        proposal = round.with_actor(admin).admit(responder)
    with system_context(reason="tests.proposals.answer_topic"):
        topic = Topic.objects.create(round=round, key="answer", name="Answer")
    with actor_context(responder), pytest.raises(PermissionDenied, match="manager"):
        Answer(proposal=proposal, topic=topic, shared_with_responders=True).sudo(reason="tests.proposals.insert").save()
    with actor_context(admin):
        answer = Answer(proposal=proposal, topic=topic, shared_with_responders=True)
        answer.sudo(reason="tests.proposals.manager_insert").save()
    assert answer.shared_with_responders


@pytest.mark.django_db(transaction=True)
def test_facilitator_only_publication_does_not_admit_peer_to_track(proposal_schema, settings):
    admin = create_platform_admin("private-track-manager")
    user_model = apps.get_model("iam", "User")
    alice = user_model.objects.create_user(username="private-track-alice")
    bob = user_model.objects.create_user(username="private-track-bob")
    round = _review_round(admin, policy="facilitator_only")
    with actor_context(admin), system_context(reason="tests.proposals.private_drive"):
        backend = Backend.objects.create(slug="private-backend", backend_class="local")
        Drive.objects.create(slug="private-default", name="Default", prefix="default", backend=backend)
        settings.ANGEE_STORAGE_DEFAULT_DRIVE = "private-default"
    with actor_context(admin):
        alice_proposal = round.with_actor(admin).admit(alice, track=True)
        bob_proposal = round.admit(bob)
        bob_proposal.submit()
        round.open()
        alice_proposal.publish_track()
    assert not alice_proposal.with_actor(bob).has_access("read_track")
    assert alice_proposal.with_actor(alice).has_access("read_track")
    with actor_context(alice), pytest.raises(PermissionDenied, match="manager"):
        Task(project=alice_proposal.track, title="Shared task", shared_with_responders=True).sudo(
            reason="tests.proposals.shared_task_insert",
        ).save()
