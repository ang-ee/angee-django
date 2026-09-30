"""Canonical run references retain the workflow's declared concrete subject behavior."""

from contextlib import nullcontext

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from rebac import (
    RelationshipTuple,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.base.scoping import system_queryset
from angee.workflows.states import RunStatus
from angee.workflows.testing.drivers import run_until
from angee.workflows.testing.models import StepRun, Workflow, WorkflowRun
from tests.conftest import create_user
from tests.mtidemo.models import MtiChild, MtiParent
from tests.workflow_steps import Echo, document

pytestmark = pytest.mark.usefixtures("workflow_step_classes")


@pytest.mark.django_db(transaction=True)
def test_context_preserves_concrete_mti_subject(execution, register_step):
    """A child subject retains its fields through scoped reads and locked writes."""

    actor, _ = execution
    with system_context(reason="test.workflow_mti_subject"):
        subject = MtiChild.objects.create(title="Parent identity", detail="Child state")

    class SubjectWriter(Echo):
        subject = MtiChild._meta.label_lower

        def run(self, ctx):
            assert isinstance(ctx.subject, MtiChild)
            assert ctx.subject.detail == "Child state"
            locked = ctx.subject_for_update()
            assert isinstance(locked, MtiChild)
            locked.detail = "Updated child state"
            locked.save(update_fields=("detail",))
            return ctx.done(ctx.input)

    register_step(SubjectWriter)
    workflow = Workflow.objects.install_definition(
        key="concrete_subject",
        name="Concrete subject",
        subject_model=MtiChild._meta.label_lower,
        draft=document("entry"),
        actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject)
    assert WorkflowRun.objects.with_actor(actor).for_subject(subject).get().pk == run.pk
    step_run = system_queryset(StepRun).get(run=run)

    assert StepRun.objects.execute(step_run.pk)

    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained.status == RunStatus.SUCCEEDED
    assert retained.subject_content_type.model_class() is MtiParent
    assert system_queryset(MtiChild).get(pk=subject.pk).detail == "Updated child state"
    replacement = WorkflowRun.objects.reprocess(retained, actor=actor)
    assert replacement.reprocess_of_id == retained.pk
    assert replacement.subject_content_type_id == retained.subject_content_type_id
    assert replacement.subject_model_class is MtiChild
    assert replacement.subject_object_id == subject.pk


@pytest.mark.django_db(transaction=True)
def test_mti_subject_lookup_and_replay_share_canonical_identity(execution):
    """Parent and child lookup share stored identity without widening run visibility."""
    actor, _ = execution
    reader = create_user("subject-run-reader")
    outsider = create_user("subject-run-outsider")
    with system_context(reason="test.workflow_subject_identity"):
        child = MtiChild.objects.create(title="Shared identity", detail="Child")
        other = MtiChild.objects.create(title="Separate identity", detail="Other child")
        parent = MtiParent.objects.get(pk=child.pk)
    workflow = Workflow.objects.install_definition(
        key="subject_identity", name="Subject identity", subject_model=MtiChild._meta.label_lower,
        draft=document("entry"), actor=actor,
    )
    workflow.with_actor(actor).grant_record_access("starter", reader)
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=child, request_key="subject-replay")
    for subject in (child, parent):
        assert WorkflowRun.objects.with_actor(actor).for_subject(subject).get().pk == run.pk
        assert not WorkflowRun.objects.with_actor(reader).for_subject(subject).exists()
    run.with_actor(actor).grant_record_access("reader", reader)
    for subject in (child, parent):
        assert WorkflowRun.objects.with_actor(reader).for_subject(subject).get().pk == run.pk
        assert not WorkflowRun.objects.with_actor(outsider).for_subject(subject).exists()
    replay = WorkflowRun.objects.start(workflow, actor=actor, subject=child, request_key="subject-replay")
    assert replay.pk == run.pk
    with pytest.raises(ValidationError, match="different request"):
        WorkflowRun.objects.start(workflow, actor=actor, subject=other, request_key="subject-replay")
    with pytest.raises(ValidationError, match="wrong model"):
        WorkflowRun.objects.start(workflow, actor=actor, subject=parent, request_key="subject-replay")


@pytest.mark.django_db(transaction=True)
def test_undeclared_subject_uses_the_canonical_record_type(execution):
    """Without a concrete workflow contract the canonical record owns subject loading."""
    actor, _ = execution
    with system_context(reason="test.workflow_optional_subject"):
        child = MtiChild.objects.create(title="Optional subject", detail="Child")
    workflow = Workflow.objects.install_definition(
        key="optional_subject", name="Optional subject", draft=document("entry"), actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=child)
    assert WorkflowRun.objects.with_actor(actor).for_subject(child).get().pk == run.pk
    assert run.subject_model_class is MtiParent
    WorkflowRun.objects.cancel(run, actor=actor)
    with system_context(reason="test.workflow_subject_deleted"):
        MtiChild.objects.filter(pk=child.pk).delete()
    with pytest.raises(MtiParent.DoesNotExist):
        WorkflowRun.objects.reprocess(run, actor=actor)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("ambient_system", [False, True])
def test_subject_loading_requires_current_access_to_the_declared_child(execution, register_step, ambient_system):
    """Access to the canonical ancestor cannot substitute for the declared child's policy."""
    admin, _ = execution
    actor = create_user("child-subject-starter")
    with system_context(reason="test.workflow_subject_grants"):
        child = MtiChild.objects.create(title="Private child", detail="Child-only evidence")
        parent = MtiParent.objects.get(pk=child.pk)
        child_access = RelationshipTuple(to_object_ref(child), "reader", to_subject_ref(actor))
        write_relationships([child_access])

    class ChildReader(Echo):
        key = "read_mti_child"
        subject = MtiChild._meta.label_lower

        def run(self, ctx):
            assert ctx.subject.detail == "Child-only evidence"
            return ctx.done(ctx.input)

    register_step(ChildReader)
    workflow = Workflow.objects.install_definition(
        key="private_child", name="Private child", subject_model=MtiChild._meta.label_lower,
        draft=document("entry", step=ChildReader.key), actor=admin,
    )
    workflow.with_actor(admin).grant_record_access("starter", actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=child)
    with system_context(reason="test.workflow_subject_revocation"):
        delete_relationship(child_access)
        write_relationships([RelationshipTuple(to_object_ref(parent), "reader", to_subject_ref(actor))])
    assert parent.with_actor(actor).has_access("read")
    run_until(run)
    assert run.status == RunStatus.FAILED
    assert "absent or inaccessible" in system_queryset(StepRun).get(run=run).attempts.with_actor(actor).get().error
    with system_context(reason="test.workflow_subject_ambient") if ambient_system else nullcontext():
        with pytest.raises(PermissionDenied, match="subject"):
            WorkflowRun.objects.reprocess(run, actor=actor)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("ambient_system", [False, True])
def test_start_requires_read_access_to_subject(execution, ambient_system):
    """Workflow start access never grants access to an independently protected subject."""
    admin, _ = execution
    actor = create_user("subject_starter")
    subject = Workflow.objects.install_definition(
        key="protected_subject", name="Protected subject", draft=document("entry"), actor=admin,
    )
    workflow = Workflow.objects.install_definition(
        key="subject_admission", name="Subject admission", subject_model="workflows.workflow",
        draft=document("entry"), actor=admin,
    )
    workflow.with_actor(admin).grant_record_access("starter", actor)
    with (
        system_context(reason="test.explicit_subject_actor") if ambient_system else nullcontext(),
        pytest.raises(PermissionDenied, match="subject"),
    ):
        WorkflowRun.objects.start(workflow, actor=actor, subject=subject)
    assert not system_queryset(WorkflowRun).filter(version__workflow=workflow).exists()

    subject.with_actor(admin).grant_record_access("viewer", actor)
    with system_context(reason="test.explicit_subject_actor") if ambient_system else nullcontext():
        run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject)
    assert run.run_as_id == actor.pk and run.subject_object_id == subject.pk
