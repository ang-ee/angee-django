"""Backfill the installed agent-session workflow's declared identity."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.identity import SqidPublicIdentity

_WORKFLOW_IDENTITY = SqidPublicIdentity(prefix="wfl_")


def applies(project_state: ProjectState) -> bool:
    """Apply once the generic workflow identity fields and AgentSession exist."""

    workflow = project_state.models.get(("workflows", "workflow"))
    run = project_state.models.get(("workflows", "workflowrun"))
    step_run = project_state.models.get(("workflows", "steprun"))
    if workflow is None and run is None and step_run is None:
        return False
    if workflow is None or run is None or step_run is None:
        raise ImproperlyConfigured("angee.workflows_agents:agent_session_identity found a partial workflow runtime")
    if ("agents", "agentsession") not in project_state.models:
        return False
    if ("resources", "resource") not in project_state.models:
        return False
    required = ((workflow, "purpose"), (run, "origin"), (step_run, "waiting_kind"))
    return all(field_name in model.fields for model, field_name in required)


def backfill_agent_session_identity(apps, schema_editor) -> None:
    """Classify only rows proven to belong to the declared installed session lineage."""

    workflow = apps.get_model("workflows", "Workflow")
    run = apps.get_model("workflows", "WorkflowRun")
    step_run = apps.get_model("workflows", "StepRun")
    content_type = apps.get_model("contenttypes", "ContentType")
    resource = apps.get_model("resources", "Resource")
    alias = schema_editor.connection.alias

    ledger = (
        resource._base_manager.using(alias)
        .filter(source_addon="angee.workflows_agents", xref="agent_session", target_model="workflows.Workflow")
        .order_by()
        .first()
    )
    if ledger is None or not ledger.target_id:
        return
    head_pk = _WORKFLOW_IDENTITY.public_id_to_pk(ledger.target_id)
    if head_pk is None:
        return
    head = (
        workflow._base_manager.using(alias)
        .filter(pk=head_pk, published_from_id__isnull=True)
        .order_by()
        .first()
    )
    if head is None:
        return
    lineage = workflow._base_manager.using(alias).filter(
        models.Q(pk=head.pk) | models.Q(published_from_id=head.pk)
    ).order_by()
    conflicting = lineage.exclude(key__in=("", "agent_session")).values_list("key", flat=True).first()
    if conflicting is not None:
        raise RuntimeError(
            "angee.workflows_agents agent_session ledger targets a workflow lineage "
            f"with conflicting stable key {conflicting!r}"
        )
    lineage.filter(key="").update(key="agent_session")
    lineage.update(purpose="agent_session")

    session_type = content_type.objects.using(alias).filter(app_label="agents", model="agentsession").first()
    if session_type is None:
        return
    session_runs = run._base_manager.using(alias).filter(
        workflow_id__in=models.Subquery(lineage.values("pk")),
        subject_content_type_id=session_type.pk,
    ).order_by()
    session_runs.filter(origin="unknown").update(origin="session")
    step_run._base_manager.using(alias).filter(
        run_id__in=models.Subquery(session_runs.values("pk")),
        step__step_class="agent_session",
        status="waiting",
        waiting_kind="",
    ).order_by().update(waiting_kind="external")


class Migration(migrations.Migration):
    """Backfill bridge-owned workflow, run, and wait classification."""

    # This backfill reads the resource ledger through Django's historical app
    # registry, so its model must exist before this operation is scheduled.
    dependencies = [("resources", "0001_initial")]
    operations = [migrations.RunPython(backfill_agent_session_identity, migrations.RunPython.noop)]
