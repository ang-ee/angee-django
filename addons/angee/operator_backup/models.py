"""Manual capture plans and immutable completed backup evidence."""

from __future__ import annotations

import logging
from typing import Any, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import system_context

from angee.base.fields.state import StateField
from angee.base.models import (
    AngeeDataModel,
    AngeeManager,
    AngeeQuerySet,
    AppendOnlyModel,
    AppendOnlyQuerySet,
    OwnerMixin,
)
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.operator.daemon import OperatorDaemon, OperatorDaemonError
from angee.operator_backup.archives import archive_mount, validate_archive_destination
from angee.operator_backup.contracts import (
    BackupSelection,
    BackupTarget,
    CaptureInput,
    default_selection,
    validate_selection,
)
from angee.workflows.states import RunStatus
from angee.workflows.subjects import RunSubject

logger = logging.getLogger(__name__)

WORKFLOW_KEY = "operator_backup_capture"


class BackupPlan(RunSubject, OwnerMixin, HistoryMixin, AngeeDataModel):
    """Editable manual intent; native WorkflowRun retains every attempt."""

    runtime = True
    sqid_prefix = "bpl_"
    rebac_grantable = {"viewer": "share", "runner": "share"}

    name = models.CharField(max_length=200)
    target_stack = StateField(choices_enum=BackupTarget, default=BackupTarget.LOCAL)
    selection = models.JSONField(default=default_selection, validators=[validate_selection])
    destination = models.CharField(max_length=32, default="local", editable=False)

    class Meta:
        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "operator_backup/plan"

    def clean(self) -> None:
        """Store one complete canonical document, including typed group defaults."""
        super().clean()
        validate_selection(self.selection)
        self.selection = BackupSelection.model_validate(self.selection).model_dump(mode="json")

    def __str__(self) -> str:
        return self.name

    def snapshot(self, *, selection: object | None = None) -> dict[str, Any]:
        """Canonicalize stored or draft intent without changing the plan."""
        selected = self.selection if selection is None else selection
        validate_selection(selected)
        return CaptureInput.model_validate({"selection": selected, "target_stack": self.target_stack,
                                            "destination": self.destination}).model_dump(mode="json")

    def preview(self, *, actor: Any = None, selection: object | None = None) -> dict[str, Any]:
        """Discover an authorized draft without persisting it or dispatching capture."""
        self.require_access("create" if self._state.adding else "run", actor)
        snapshot = self.snapshot(selection=selection)
        mount = archive_mount()
        daemon = OperatorDaemon.from_settings()
        try:
            validate_archive_destination(mount, stack_root=daemon.stack_root())
            discovered = daemon.backup_preview(selection=snapshot["selection"])
        except OperatorDaemonError as error:
            logger.warning("Operator backup discovery did not complete (HTTP %s).", error.status_code)
            raise ValidationError(
                "Backup discovery could not finish communicating with the operator. Try discovery again. "
                "If it keeps failing, ask an administrator to check operator availability, access, and "
                "discovery diagnostics. No backup has started."
            ) from None
        return {**discovered, "selection": snapshot["selection"]}

    def start(
        self, actor: Any, *, request_key: str, expected_selection: object | None = None,
    ) -> Any:
        """Admit reviewed intent, preserving its original snapshot on replay."""
        self.require_access("run", actor)
        if not request_key or len(request_key) > 128:
            raise ValidationError({"request_key": "Use a nonempty request key of at most 128 characters."})
        archive_mount()
        with transaction.atomic():
            plan = cast(Any, type(self).objects).with_actor(actor).get(pk=self.pk)
            snapshot = plan.snapshot()
            with system_context(reason="operator_backup.start"):
                workflow = apps.get_model("workflows", "Workflow").objects.get(key=WORKFLOW_KEY)
                workflow.system_grant_record_access("starter", actor)
                runs = apps.get_model("workflows", "WorkflowRun").objects
                key = f"backup:{self.pk}:{request_key}"
                existing = runs.filter(request_key=key).first()
                admitted = existing.input if existing is not None else snapshot
                if expected_selection is not None and (
                    plan.snapshot(selection=expected_selection)["selection"] != admitted["selection"]
                ):
                    raise ValidationError({"expected_selection":
                                           "The plan selection changed. Discover and review it again before capture."})
                return runs.start(workflow, actor=actor, subject=plan, input=admitted, request_key=key)

    def admit_run(self, run: Any) -> None:
        """Permit only the declared capture workflow under the initiating actor."""
        self.require_access("run", run.run_as)
        if run.policy_version.workflow.key != WORKFLOW_KEY:
            raise ValidationError("Backup runs require the capture workflow.")
        CaptureInput.model_validate(run.input)
        if not run.step_runs.exists():
            expected = run.reprocess_of.input if run.reprocess_of_id is not None else self.snapshot()
            if run.input != expected:
                raise ValidationError("Backup runs must preserve the admitted plan snapshot.")
        active = type(run).objects.for_subject(self).filter(parent_step__isnull=True).exclude(
            status__in=RunStatus.terminal_values(),
        ).exclude(pk=run.pk)
        if active.exists():
            raise ValidationError("This plan already has an active backup.")

    def settle_run(self, run: Any, status: str) -> None:
        """Attempts remain native workflow evidence; no parallel run state."""


class BackupQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet[Any]):
    """Retain completed archive evidence through every collection write path."""

    def validate_insert(self) -> None:
        """Only the publication owner can admit retained completion evidence."""
        raise ValidationError("Use Backup.objects.publish() to retain a completed archive.")


_BackupManagerBase = AngeeManager.from_queryset(BackupQuerySet)


class BackupManager(_BackupManagerBase):
    """Publish one immutable record only after storage verified the archive."""

    def publish(self, *, plan: Any, execution: Any, file: Any, manifest: dict[str, Any]) -> Any:
        """Idempotently retain completed bytes and their frozen manifest."""
        if file.upload_state != "ready" or file.is_trashed:
            raise ValidationError("Only a ready archive can be published as a backup.")
        if not manifest or manifest.get("version") != 1:
            raise ValidationError("A version-one frozen archive manifest is required.")
        with system_context(reason="operator_backup.publish"), transaction.atomic():
            # The run is locked by the DATABASE step; standalone callers retain
            # that same serialization without changing the publication identity.
            type(execution).system_queryset(lock=("self",)).get(pk=execution.pk)
            row = cast(Any, self.filter(execution=execution).first())
            if row is not None:
                if row.plan_id != plan.pk or row.file_id != file.pk or row.manifest != manifest:
                    raise ValidationError("This execution already published different backup evidence.")
                return row
            row = cast(Any, self.model(plan=plan, execution=execution, file=file, manifest=manifest))
            row._owner_insert()
            return row


class Backup(AppendOnlyModel, AngeeDataModel):
    """Successful archive evidence; File alone owns bytes and download policy."""

    runtime = True
    sqid_prefix = "bkp_"
    plan = models.ForeignKey("operator_backup.BackupPlan", on_delete=models.PROTECT, related_name="backups")
    execution = models.OneToOneField("workflows.WorkflowRun", on_delete=models.PROTECT, related_name="backup")
    file = models.ForeignKey("storage.File", on_delete=models.PROTECT, related_name="backups")
    manifest = models.JSONField(editable=False)
    objects = BackupManager()

    class Meta(AppendOnlyModel.Meta):
        abstract = True
        ordering = ("-created_at", "sqid")
        rebac_resource_type = "operator_backup/backup"

    def __str__(self) -> str:
        return f"Backup {self.sqid}"
