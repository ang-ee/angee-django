"""Same-row admission and terminal-effect donors for workflow-owned Bridge syncs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, transaction
from django.utils import timezone
from rebac import actor_context, system_context

from angee.integrate.errors import IntegrationError
from angee.integrate.models import Bridge, BridgeSyncOccurrence, SyncDispatchReceipt
from angee.workflows.attempts import JsonPresence
from angee.workflows.engine import start_pinned
from angee.workflows.states import RunOrigin, RunStatus, WorkflowPurpose
from angee.workflows_integrate.sync import (
    IntegrationSyncDefinition,
    IntegrationSyncOccurrence,
    IntegrationSyncTerminalOutcome,
    _LaunchCapability,
    current_integration_sync_launch_capability,
    integration_sync_launch_capability,
    sync_cycle_dedup_key,
    workflow_run_execution_ref,
    workflow_run_id_from_execution_ref,
)


class IntegrationSyncWorkflow(models.Model):
    """Protect definitions designated for Bridge-owned system launch."""

    extends = "workflows.Workflow"

    class Meta:
        abstract = True

    def validate_run_launch(self, **facts: Any) -> None:
        """Admit integration sync only through its exact Bridge capability."""

        if self.purpose != WorkflowPurpose.INTEGRATION_SYNC:
            super().validate_run_launch(**facts)
            return
        capability = current_integration_sync_launch_capability()
        input_value = facts.get("input")
        if (
            capability is None
            or not facts.get("exact_version")
            or facts.get("origin") != RunOrigin.INTEGRATION_SYNC
            or facts.get("trigger") is not None
            or facts.get("parent_step_run") is not None
            or self.error_workflow_id is not None
            or self.pk != capability.definition.version_id
            or self.key != capability.definition.key
            or facts.get("dedup_key") != capability.dedup_key
            or facts.get("occurrence_id") != capability.occurrence_id
            or not isinstance(input_value, JsonPresence)
            or not input_value.present
            or input_value.value != capability.envelope
        ):
            raise ValidationError("Integration sync workflows require their exact Bridge launch capability.")
        super().validate_run_launch(**facts)
        subject = facts.get("subject")
        if (
            not isinstance(subject, Bridge)
            or subject.pk != capability.bridge_id
            or subject._meta.label_lower != capability.bridge_model._meta.label_lower
        ):
            raise ValidationError("Integration sync workflows require their bound Bridge subject.")
        subject._admit_integration_sync_workflow(  # noqa: SLF001 - cooperative same-row owner seam.
            capability,
            workflow=self,
            retained_run=facts.get("retained_run"),
            actor=facts.get("actor"),
        )


class IntegrationSyncWorkflowRun(models.Model):
    """Deliver Bridge terminal telemetry from the retained ADVANCE owner."""

    extends = "workflows.WorkflowRun"

    class Meta:
        abstract = True

    def deliver_terminal_effect(self, *, at: Any) -> None:
        """Settle an integration Bridge by exact execution-reference CAS."""

        super().deliver_terminal_effect(at=at)
        if self.workflow.purpose != WorkflowPurpose.INTEGRATION_SYNC:
            return
        subject = self.subject
        if not isinstance(subject, Bridge) or self.origin != RunOrigin.INTEGRATION_SYNC:
            raise ValidationError("Integration sync terminal evidence has an invalid subject or origin.")
        subject.deliver_integration_sync_terminal(self, at=at)


class IntegrationWorkflowBridge(models.Model):
    """Source-neutral Bridge workflow admission folded into Integration rows."""

    extends = "integrate.Integration"

    class Meta:
        abstract = True

    def integration_sync_definition(self) -> IntegrationSyncDefinition:
        """Return a connector-owned exact shipped definition binding."""

        raise ImproperlyConfigured(f"{self._meta.label} does not declare an integration sync workflow.")

    def validate_integration_sync_scope(self, *, envelope: dict[str, Any]) -> None:
        """Let the concrete connector reauthorize its selected source scope."""

        del envelope
        raise ImproperlyConfigured(f"{self._meta.label} does not authorize workflow sync scope.")

    def validate_integration_sync_binding(self, *, definition: IntegrationSyncDefinition) -> None:
        """Recheck connector-owned local binding fields without querying Workflow."""

        del definition
        raise ImproperlyConfigured(f"{self._meta.label} does not validate an integration sync binding.")

    def integration_sync_terminal_outcome(self, run: Any) -> IntegrationSyncTerminalOutcome:
        """Project terminal workflow evidence into generic Bridge telemetry."""

        if run.status == RunStatus.CANCELED:
            return IntegrationSyncTerminalOutcome(error=IntegrationError("Integration sync was canceled."))
        if run.status == RunStatus.FAILED:
            return IntegrationSyncTerminalOutcome(error=IntegrationError("Integration sync failed."))
        raise ImproperlyConfigured(f"{self._meta.label} does not project successful workflow sync results.")

    def dispatch_workflow_sync(
        self,
        *,
        now: Any,
        occurrence: IntegrationSyncOccurrence,
    ) -> SyncDispatchReceipt:
        """Atomically admit one exact workflow run and its Bridge execution marker."""

        if not isinstance(self, Bridge) or self.pk is None:
            raise ValidationError("Workflow sync dispatch requires a persisted Bridge.")
        alias = self._state.db or "default"
        if alias != "default":
            raise ValidationError("Workflow sync dispatch currently requires the default database.")
        with system_context(reason="workflows_integrate.sync.preflight"):
            preflight = type(self).objects.db_manager(alias).get(pk=self.pk)
            definition = preflight.integration_sync_definition()
            credential = preflight.credential
            if credential is None:
                raise ValidationError("Workflow sync requires a credential.")
            canonical_occurrence = occurrence.canonical()
            progress = preflight.sync_progress if isinstance(preflight.sync_progress, Mapping) else {}
            queue_lifecycle = progress.get(
                "queue_lifecycle",
                str(preflight.Lifecycle.CONNECTED),
            )
            dedup_key = sync_cycle_dedup_key(preflight, occurrence)
            envelope = {
                "schema_version": 1,
                "bridge": {"model": preflight._meta.label_lower, "id": int(preflight.pk)},
                "owner_id": int(preflight.owner_id),
                "configuration_generation": preflight.sync_admission_generation(),
                "credential": {
                    "id": int(credential.pk),
                    "material_revision": int(credential.material_revision),
                },
                "definition": {
                    "key": definition.key,
                    "version_id": int(definition.version_id),
                    "digest": definition.digest,
                },
                "occurrence": canonical_occurrence,
                "source_cutoff": canonical_occurrence["occurred_at"],
            }
            if queue_lifecycle != str(preflight.Lifecycle.CONNECTED):
                envelope["queue_lifecycle"] = queue_lifecycle
            workflow_model = apps.get_model("workflows", "Workflow")
            version = workflow_model.objects.get(pk=definition.version_id)
            owner = preflight.owner

        with system_context(reason="workflows_integrate.sync.launch"), transaction.atomic(using=alias):
            connection = transaction.get_connection(alias)
            capability = _LaunchCapability(
                alias=alias,
                connection_id=id(connection),
                atomic_id=id(connection.atomic_blocks[0]),
                bridge_model=type(self),
                bridge_id=int(self.pk),
                actor_id=int(preflight.owner_id),
                definition=definition,
                dedup_key=dedup_key,
                occurrence_id=occurrence.key.strip(),
                envelope=envelope,
            )
            with integration_sync_launch_capability(capability):
                run = start_pinned(
                    version,
                    preflight,
                    owner,
                    expected_definition_digest=definition.digest,
                    dedup_key=dedup_key,
                    occurrence_id=occurrence.key.strip(),
                    origin=cast(RunOrigin, RunOrigin.INTEGRATION_SYNC),
                    input=JsonPresence(True, envelope),
                )
            locked = capability.locked_bridge
            if locked is None:
                raise RuntimeError("Integration sync launch did not retain its canonical Bridge lock.")
            reference = workflow_run_execution_ref(run)
            if capability.disposition == "settled":
                raise ValidationError("This integration sync occurrence is already finished.")
            receipt = SyncDispatchReceipt.dispatched(reference)
            if capability.disposition == "new":
                locked.record_sync_dispatched(receipt, now=now)
            return receipt

    def _admit_integration_sync_workflow(
        self,
        capability: _LaunchCapability,
        *,
        workflow: Any,
        retained_run: Any,
        actor: Any,
    ) -> None:
        """Lock and recheck the canonical principal, credential, and Bridge."""

        alias = self._state.db or "default"
        if alias != "default":
            raise ValidationError("Workflow sync admission currently requires the default database.")
        capability.validate_context(alias=alias)
        envelope = capability.envelope
        credential_facts = envelope["credential"]
        credential_model = self._meta.get_field("credential").remote_field.model
        credential = (
            credential_model.objects.sudo(reason="workflows_integrate.sync.credential")
            .lock_if_supported()
            .get(pk=credential_facts["id"])
        )
        row = (
            capability.bridge_model.objects.sudo(reason="workflows_integrate.sync.bridge")
            .lock_if_supported()
            .get(pk=capability.bridge_id)
        )
        if (
            row.credential_id != credential.pk
            or credential.material_revision != credential_facts["material_revision"]
            or row.owner_id != envelope["owner_id"]
            or row.owner_id != capability.actor_id
            or getattr(actor, "pk", None) != row.owner_id
            or not row.owner.is_active
            or row.sync_admission_generation() != envelope["configuration_generation"]
            or workflow.pk != capability.definition.version_id
            or workflow.key != capability.definition.key
        ):
            raise ValidationError("Integration sync launch facts changed before admission.")
        row.validate_integration_sync_binding(definition=capability.definition)
        actor_bound_row = row.with_actor(row.owner)
        with actor_context(row.owner):
            actor_bound_row._require_record_access("write")  # noqa: SLF001 - model-owned permission law.
            actor_bound_row.validate_integration_sync_scope(envelope=envelope)

        occurrence_payload = envelope.get("occurrence")
        if occurrence_payload is None:
            occurrence = None
        elif isinstance(occurrence_payload, Mapping):
            try:
                occurrence = BridgeSyncOccurrence.from_payload(dict(occurrence_payload))
            except ValueError as error:
                raise ValidationError("Integration sync occurrence evidence is invalid.") from error
        else:
            raise ValidationError("Integration sync occurrence evidence is invalid.")
        queue_lifecycle = envelope.get(
            "queue_lifecycle",
            str(row.Lifecycle.CONNECTED),
        )
        allow_paused = row.validate_sync_occurrence_lifecycle(
            occurrence,
            expected_lifecycle=queue_lifecycle,
        )

        reference = "" if retained_run is None else workflow_run_execution_ref(retained_run)
        if retained_run is None:
            row.validate_sync_admission(allow_paused=allow_paused)
            capability.disposition = "new"
        elif retained_run.status in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING):
            row.validate_sync_eligibility(allow_paused=allow_paused)
            if row.sync_execution_ref != reference or row.sync_stage not in row.LIVE_SYNC_STAGES:
                raise ValidationError("Retained integration sync execution no longer owns this Bridge.")
            capability.disposition = "active"
        elif retained_run.status in RunStatus.TERMINAL:
            if row.sync_execution_ref == reference and row.sync_stage in row.LIVE_SYNC_STAGES:
                capability.disposition = "terminal_pending"
            elif not row.sync_execution_ref:
                capability.disposition = "settled"
            else:
                raise ValidationError("A newer integration sync execution owns this Bridge.")
        else:
            raise ValidationError("Retained integration sync status is invalid.")
        capability.locked_bridge = actor_bound_row

    def deliver_integration_sync_terminal(self, run: Any, *, at: Any) -> None:
        """Project and CAS one terminal run onto its exact Bridge execution."""

        if not isinstance(self, Bridge):
            raise ValidationError("Integration sync terminal delivery requires a Bridge.")
        if (self._state.db or "default") != "default":
            raise ValidationError("Workflow sync terminal delivery currently requires the default database.")
        outcome = self.integration_sync_terminal_outcome(run)
        reference = workflow_run_execution_ref(run)
        self.record_sync_terminal(
            reference,
            now=at or timezone.now(),
            result=outcome.items,
            error=outcome.error,
            retryable=outcome.retryable,
        )

    def workflow_sync_execution_is_active(self, execution_ref: str) -> bool:
        """Resolve an owned Run, including terminal work awaiting its ADVANCE effect."""

        run_id = workflow_run_id_from_execution_ref(execution_ref)
        if run_id is None or (self._state.db or "default") != "default":
            return False
        run_model = apps.get_model("workflows", "WorkflowRun")
        dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
        with system_context(reason="workflows_integrate.sync.liveness"):
            run = run_model.objects.select_related("workflow").filter(pk=run_id).first()
            if (
                run is None
                or run.subject_content_type_id is None
                or run.subject_object_id != self.pk
                or run.subject_content_type.model_class() is not type(self)
                or run.workflow.purpose != WorkflowPurpose.INTEGRATION_SYNC
                or run.origin != RunOrigin.INTEGRATION_SYNC
            ):
                return False
            if run.status in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING):
                return True
            if run.status not in RunStatus.TERMINAL:
                return False
            return dispatch_model.objects.filter(
                run=run,
                kind="advance",
                consumed_at__isnull=True,
            ).exists()
