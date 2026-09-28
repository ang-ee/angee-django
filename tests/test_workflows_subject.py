"""Execution subjects preserve the declared concrete Django model identity."""

from contextlib import nullcontext

import pytest
from django.core.exceptions import PermissionDenied
from rebac import system_context

from angee.base.scoping import system_queryset
from angee.workflows.states import RunStatus
from angee.workflows.testing.models import StepRun, Workflow, WorkflowRun
from tests.conftest import create_user
from tests.mtidemo.models import MtiChild
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
    step_run = system_queryset(StepRun).get(run=run)

    assert StepRun.objects.execute(step_run.pk)

    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained.status == RunStatus.SUCCEEDED
    assert retained.subject_content_type.model_class() is MtiChild
    assert system_queryset(MtiChild).get(pk=subject.pk).detail == "Updated child state"


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
