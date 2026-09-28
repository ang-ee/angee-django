"""Workflow publication and admission contracts."""

import pytest
from django.core.exceptions import ValidationError
from pydantic import BaseModel, Field

from angee.base.scoping import system_queryset
from angee.workflows.definition import DefinitionInvalid
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import load_workflow
from angee.workflows.testing.models import Workflow, WorkflowRun, WorkflowVersion
from tests.workflow_steps import STEP_CLASSES, Echo, Value, document

pytestmark = pytest.mark.django_db(transaction=True)


class RevisedInput(BaseModel):
    """A later publication adds a mandatory request field."""

    value: int
    label: str


class RevisedEcho(Step[RevisedInput, Value, None]):
    """A later graph contract on the same workflow identity."""

    key = "revised_echo"

    def run(self, ctx):
        """Complete only after the new input contract has been admitted."""
        return ctx.done({"value": ctx.input.value})


class ResultPayload(BaseModel):
    """An output envelope with an omittable leaf."""

    value: int
    optional: int = 0


class ResultSource(BaseModel):
    """Nested source paths exercise publication's presence guarantees."""

    required: ResultPayload
    optional: ResultPayload = Field(default_factory=lambda: ResultPayload(value=0))


class ResultEcho(Step[ResultSource, ResultSource, None]):
    """A typed source for whole-result publication checks."""

    key = "result_echo"


class VaultEcho(Echo):
    """The original graph requires a knowledge record as its subject."""

    key = "vault_echo"
    subject = "knowledge.vault"


class WorkflowEcho(Echo):
    """A graph requiring a workflow record needs a different identity."""

    key = "workflow_echo"
    subject = "workflows.workflow"


def test_request_key_replays_original_admission_after_publication(execution, settings):
    """A stronger current input contract never invalidates a keyed earlier start."""
    actor, _ = execution
    settings.ANGEE_WORKFLOW_STEP_CLASSES = {
        **STEP_CLASSES,
        "revised_echo": f"{__name__}.RevisedEcho",
    }
    workflow = load_workflow(document("entry"), key="request_publication", actor=actor)
    original = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 1}, request_key="test:original")
    saved = Workflow.objects.save_draft(
        workflow, draft=document("entry", step="revised_echo"),
        expected_revision=workflow.draft_revision, actor=actor,
    )
    assert saved.status == "saved" and not saved.issues
    version = Workflow.objects.publish(workflow, actor=actor)
    assert version.pk != original.version_id
    replay = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 1}, request_key="test:original")
    assert replay.pk == original.pk
    assert replay.version_id == original.version_id
    assert replay.input == {"value": 1}
    assert system_queryset(WorkflowRun).filter(request_key="test:original").count() == 1
    with pytest.raises(ValidationError):
        WorkflowRun.objects.start(workflow, actor=actor, input={"value": 1})


@pytest.mark.parametrize("outcome", ["done", "left", "right", "error"])
def test_publication_rejects_empty_target_lists(execution, outcome):
    """Every next entry declares an edge; ending a branch requires omission."""
    actor, _ = execution
    workflow = Workflow.objects.install_definition(
        key="empty_targets", name="Empty targets",
        draft={"nodes": {"entry": {"step": "route", "next": {outcome: []}}}},
        publish=False, actor=actor,
    )
    with pytest.raises(DefinitionInvalid) as caught:
        Workflow.objects.publish(workflow, actor=actor)
    assert any(
        issue.code == "target" and issue.path == ["nodes", "entry", "next", outcome]
        and "at least one target" in issue.message
        for issue in caught.value.issues
    )
    assert not system_queryset(WorkflowVersion).exists()


def test_result_projection_rejects_nonproducer_whole_object_at_publish(execution):
    """A result cannot bind its whole output to an independently skipped branch."""
    actor, _ = execution
    with pytest.raises(DefinitionInvalid) as caught:
        load_workflow(
            {
                "nodes": {
                    "choose": {
                        "step": "route", "config": {"outcome": "left"},
                        "next": {"left": "left", "right": "right"},
                    },
                    "left": {"step": "echo"},
                    "right": {"step": "echo"},
                },
                "results": [{"from": "left", "output": {"from": "right"}}],
            },
            key="missing_result", actor=actor,
        )
    assert caught.value.issues
    assert not system_queryset(WorkflowVersion).exists()


@pytest.mark.parametrize("source", ["entry", "input"])
@pytest.mark.parametrize(
    "when,path,reason",
    [(["error"], [], "error outcome"),
     (None, ["optional", "value"], "required at every level"),
     (None, ["required", "optional"], "required at every level")],
)
def test_whole_result_rejections_block_publication(execution, settings, source, when, path, reason):
    """Invalid whole bindings cannot produce a version through the production publisher."""
    actor, _ = execution
    settings.ANGEE_WORKFLOW_STEP_CLASSES = {**STEP_CLASSES, "result_echo": f"{__name__}.ResultEcho"}
    draft = document("entry", step="result_echo")
    draft["results"] = [{
        "from": "entry", "output": {"from": source, "path": path},
        **({"when": when} if when is not None else {}),
    }]
    with pytest.raises(DefinitionInvalid) as caught:
        load_workflow(draft, key="invalid_whole_result", actor=actor)
    assert any(issue.path == ["results", 0, "output"] and reason in issue.message for issue in caught.value.issues)
    assert not system_queryset(WorkflowVersion).exists()


@pytest.mark.parametrize("operation", ["install", "draft"])
def test_subject_identity_is_immutable_once_a_version_exists(execution, settings, operation):
    """Only the identity verb can change subjects, and it rejects changes after publication."""
    actor, _ = execution
    settings.ANGEE_WORKFLOW_STEP_CLASSES = {
        **STEP_CLASSES,
        "vault_echo": f"{__name__}.VaultEcho",
        "workflow_echo": f"{__name__}.WorkflowEcho",
    }
    workflow = Workflow.objects.install_definition(
        key="subject_contract", name="Subject contract", subject_model="knowledge.Vault",
        draft=document("entry", step="vault_echo"), actor=actor,
    )
    original = system_queryset(WorkflowVersion).get(pk=workflow.published_id)
    assert original.definition.issues(subject_model="knowledge.vault") == []
    if operation == "install":
        with pytest.raises(ValidationError, match="subject model cannot change"):
            Workflow.objects.install_definition(
                key=workflow.key, name=workflow.name, subject_model="workflows.Workflow",
                draft=document("entry", step="workflow_echo"), actor=actor,
            )
    else:
        workflow.subject_model = "workflows.workflow"
        saved = Workflow.objects.save_draft(
            workflow, draft=document("entry", step="workflow_echo"),
            expected_revision=workflow.draft_revision, actor=actor,
        )
        assert saved.status == "saved"
        with pytest.raises(DefinitionInvalid) as caught:
            Workflow.objects.publish(workflow, actor=actor)
        assert any(issue.code == "subject" for issue in caught.value.issues)
    retained = system_queryset(Workflow).get(pk=workflow.pk)
    assert retained.subject_model == "knowledge.vault" and retained.published_id == original.pk
    with pytest.raises(ValidationError, match="subject"):
        WorkflowRun.objects.start(retained, actor=actor, subject=retained, version=original)
