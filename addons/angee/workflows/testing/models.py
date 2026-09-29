"""Concrete workflow and decision sources for native Django test database setup."""

from angee.decisions import models as decision_sources
from angee.workflows import models as sources


class Workflow(sources.Workflow):
    """Concrete workflow identity used by source-addon tests."""

    rebac_grantable = sources.Workflow.rebac_grantable

    class Meta(sources.Workflow.Meta):
        """Django options for the shared workflow test table."""

        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_workflow"
        rebac_resource_type = "workflows/workflow"


class WorkflowVersion(sources.WorkflowVersion):
    """Concrete immutable graph used by source-addon tests."""

    class Meta(sources.WorkflowVersion.Meta):
        """Django options for the shared version test table."""

        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_version"
        rebac_resource_type = "workflows/version"


class WorkflowRun(sources.WorkflowRun):
    """Concrete run used by source-addon tests."""

    rebac_grantable = sources.WorkflowRun.rebac_grantable

    class Meta(sources.WorkflowRun.Meta):
        """Django options for the shared run test table."""

        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_run"
        rebac_resource_type = "workflows/run"


class StepRun(sources.StepRun):
    """Concrete node execution used by source-addon tests."""

    class Meta(sources.StepRun.Meta):
        """Django options for the shared step-run test table."""

        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step_run"
        rebac_resource_type = "workflows/step_run"


class StepAttempt(sources.StepAttempt):
    """Concrete attempt used by source-addon tests."""

    class Meta(sources.StepAttempt.Meta):
        """Django options for the shared attempt test table."""

        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_attempt"
        rebac_resource_type = "workflows/step_attempt"


class StepArtifact(sources.StepArtifact):
    """Concrete artifact used by source-addon tests."""

    class Meta(sources.StepArtifact.Meta):
        """Django options for the shared artifact test table."""

        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_artifact"
        rebac_resource_type = "workflows/step_artifact"


class StepWatch(sources.StepWatch):
    """Concrete watch registered and captured inside native test transactions."""

    class Meta(sources.StepWatch.Meta):
        """Retain source observation constraints on the shared test table."""

        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_watch"
        rebac_resource_type = "workflows/step_watch"


class DecisionGroup(decision_sources.DecisionGroup):
    """Concrete group retained by its workflow step and later review rounds."""

    class Meta(decision_sources.DecisionGroup.Meta):
        """Keep native source options on the isolated group table."""

        abstract = False
        app_label = "decisions"
        db_table = "test_decisions_group"
        rebac_resource_type = "decisions/group"


class Decision(sources.DecisionWorkflow, decision_sources.Decision):
    """Concrete seat with the workflow resource axes composed onto its row."""

    class Meta(decision_sources.Decision.Meta):
        """Keep native source options on the isolated decision table."""

        abstract = False
        app_label = "decisions"
        db_table = "test_decisions_decision"
        rebac_resource_type = "decisions/decision"


class DecisionEvidence(decision_sources.DecisionEvidence):
    """Concrete evidence projection owned by decisions in the combined composition."""

    class Meta(decision_sources.DecisionEvidence.Meta):
        """Keep native source options on the isolated evidence table."""

        abstract = False
        app_label = "decisions"
        db_table = "test_decisions_evidence"
        rebac_resource_type = "decisions/evidence"


class Trigger(sources.Trigger):
    """Concrete trigger using only the base workflow addon contract."""

    class Meta(sources.Trigger.Meta):
        """Keep native source options on the isolated trigger table."""

        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_trigger"
        rebac_resource_type = "workflows/trigger"


class TriggerEvent(sources.TriggerEvent):
    """Concrete durable ledger used by native source tests."""

    class Meta(sources.TriggerEvent.Meta):
        """Keep native source options on the isolated event table."""

        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_trigger_event"
        rebac_resource_type = "workflows/trigger_event"
