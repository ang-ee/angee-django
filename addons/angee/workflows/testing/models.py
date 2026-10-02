"""Concrete workflow sources for native Django test database setup."""

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models

from angee.base.models import AngeeDataModel
from angee.workflows import models as sources
from angee.workflows.subjects import RunSubject


class RunSubjectRecord(RunSubject, AngeeDataModel):
    """Persist hook observations and refusals for generic lifecycle proofs."""

    admissions = models.PositiveIntegerField(default=0)
    settlements = models.JSONField(default=list)
    refuse_admission = models.BooleanField(default=False)
    refuse_settlement = models.BooleanField(default=False)

    def admit_run(self, run: Any) -> None:
        """Retain each admission unless the record refuses the run."""
        if self.refuse_admission:
            raise ValidationError("The subject refuses admission.")
        self.admissions += 1
        self.save(update_fields=["admissions"])

    def settle_run(self, run: Any, status: str) -> None:
        """Record terminal facts, optionally refusing after the write to prove rollback."""
        self.settlements.append({"run": run.pk, "status": status, "output": run.output})
        self.save(update_fields=["settlements"])
        if self.refuse_settlement:
            raise ValidationError("The subject refuses settlement.")

    class Meta(AngeeDataModel.Meta):
        """Keep test-only subject state in the optional testing app."""

        abstract = False
        app_label = "workflows_testing"
        db_table = "test_workflows_subject"
        rebac_resource_type = "workflows_testing/subject"


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


class WorkflowRunEvidence(sources.WorkflowRunEvidence):
    """Concrete admission evidence for native workflow source tests."""

    class Meta(sources.WorkflowRunEvidence.Meta):
        """Django options for the shared run evidence test table."""

        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_run_evidence"
        rebac_resource_type = "workflows/run_evidence"


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
