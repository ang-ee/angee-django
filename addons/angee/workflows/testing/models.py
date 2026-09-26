"""Concrete workflow models for source-addon tests.

Model labels and tables preserve source-addon references. Share declarations
must be explicit because ``AngeeModel.get_rebac_grantable`` reads the concrete
class declaration rather than inherited attributes.
"""

from __future__ import annotations

from angee.workflows import models as workflow_models
from angee.workflows.models import Edge as AbstractEdge
from angee.workflows.models import Step as AbstractStep
from angee.workflows.models import Trigger as AbstractTrigger
from angee.workflows.models import Workflow as AbstractWorkflow


class Workflow(AbstractWorkflow):
    """Concrete workflow model for source-addon tests."""

    rebac_grantable = AbstractWorkflow.rebac_grantable

    class Meta(AbstractWorkflow.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_workflow"
        rebac_resource_type = "workflows/workflow"


class Step(AbstractStep):
    """Concrete workflow step model for source-addon tests."""

    class Meta(AbstractStep.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step"
        rebac_resource_type = "workflows/step"


class Edge(AbstractEdge):
    """Concrete workflow edge model for source-addon tests."""

    class Meta(AbstractEdge.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_edge"
        rebac_resource_type = "workflows/edge"


class Trigger(AbstractTrigger):
    """Concrete workflow trigger model for source-addon tests."""

    class Meta(AbstractTrigger.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_trigger"
        rebac_resource_type = "workflows/trigger"


class WorkflowRun(workflow_models.WorkflowRun):
    """Concrete workflow run model for source-addon engine tests."""

    rebac_grantable = workflow_models.WorkflowRun.rebac_grantable

    class Meta(workflow_models.WorkflowRun.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_workflow_run"
        rebac_resource_type = "workflows/run"


class StepRun(workflow_models.StepRun):
    """Concrete workflow step-run journal model for source-addon engine tests."""

    class Meta(workflow_models.StepRun.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step_run"
        rebac_resource_type = "workflows/step_run"


class StepAttempt(workflow_models.StepAttempt):
    """Concrete retained attempt model for source-addon runtime tests."""

    class Meta(workflow_models.StepAttempt.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step_attempt"
        rebac_resource_type = "workflows/step_attempt"


class StepExternalSubscription(workflow_models.StepExternalSubscription):
    """Concrete attempt-owned external target for workflow engine tests."""

    class Meta(workflow_models.StepExternalSubscription.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step_external_subscription"


class StepArtifact(workflow_models.StepArtifact):
    """Concrete explicit result artifact model for source-addon runtime tests."""

    class Meta(workflow_models.StepArtifact.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step_artifact"


class WorkflowTestFixture(workflow_models.WorkflowTestFixture):
    """Concrete retained workflow test fixture model."""

    class Meta(workflow_models.WorkflowTestFixture.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_test_fixture"


class WorkflowRecoveryEvidence(workflow_models.WorkflowRecoveryEvidence):
    """Concrete retained recovery evidence model."""

    class Meta(workflow_models.WorkflowRecoveryEvidence.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_recovery_evidence"


class Decision(workflow_models.Decision):
    """Concrete decision model for source-addon runtime tests."""

    rebac_grantable = workflow_models.Decision.rebac_grantable

    class Meta(workflow_models.Decision.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_decision"
        rebac_resource_type = "workflows/decision"


class WorkflowDispatch(workflow_models.WorkflowDispatch):
    """Concrete durable dispatch model for source-addon runtime tests."""

    class Meta(workflow_models.WorkflowDispatch.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_dispatch"
