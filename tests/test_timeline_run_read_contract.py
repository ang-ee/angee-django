"""A record timeline never discloses runs outside the actor's run read scope."""

import pytest

from angee.workflows.testing.drivers import load_workflow
from angee.workflows.testing.models import WorkflowRun
from tests.conftest import create_user
from tests.test_timeline import read_timeline
from tests.workflow_steps import document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


def test_outsider_cannot_read_private_run_line_on_a_person_timeline(execution) -> None:
    actor, _sent = execution
    subject = create_user("probe-run-subject")
    outsider = create_user("probe-run-outsider")
    workflow = load_workflow(
        document("entry"), key="probe-private-run", actor=actor, subject_model=actor._meta.label,
        name="Private HR review",
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject)
    assert not WorkflowRun.objects.with_actor(outsider).filter(pk=run.pk).exists()
    data = read_timeline(subject, outsider, kinds=["workflows.run"])
    assert data["timeline"]["entries"] == [], data["timeline"]["entries"]
