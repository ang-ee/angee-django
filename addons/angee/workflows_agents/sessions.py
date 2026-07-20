"""Session lifecycle service binding persisted agent chat to workflows."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import system_context

from angee.workflows import engine

SESSION_WORKFLOW_NAME = "Agent session"
"""Name of the install-tier workflow that owns persisted agent sessions."""


def start_session(agent: Any, *, owner: Any, context: dict[str, Any]) -> Any:
    """Create a persisted session and start its seeded workflow run."""

    if not agent.runtime_backend.runs_in_process:
        raise ValidationError({"agent": "This agent runtime uses container ACP sessions."})
    if agent.owner_id != owner.pk:
        raise ValidationError({"agent": "Only the agent owner can start a session."})
    if str(agent.runtime_status) != "running":
        raise ValidationError({"agent": "Provision this agent before starting a session."})

    session_model = apps.get_model("agents", "AgentSession")
    workflow_model = apps.get_model("workflows", "Workflow")
    with system_context(reason="workflows_agents.session.start"), transaction.atomic():
        workflow = (
            workflow_model.objects.filter(name=SESSION_WORKFLOW_NAME, published_from__isnull=True)
            .order_by("pk")
            .first()
        )
        if workflow is None:
            raise ValidationError(
                {"workflow": "The Agent session install resource has not been loaded and published."}
            )
        session = session_model.objects.create(
            agent=agent,
            owner=owner,
            context=dict(context),
            created_by_id=owner.pk,
            updated_by_id=owner.pk,
        )
        engine.start(workflow, subject=session, actor=owner)
    return session


def post_message(session: Any, text: str) -> Any:
    """Append one pending turn and deliver it to the session workflow."""

    prompt = text.strip()
    if not prompt:
        raise ValidationError({"text": "A message is required."})

    session_model = apps.get_model("agents", "AgentSession")
    turn_model = apps.get_model("agents", "AgentTurn")
    with system_context(reason="workflows_agents.session.post"), transaction.atomic():
        locked = session_model.objects.lock_if_supported().get(pk=session.pk)
        if str(locked.status) == "closed":
            raise ValidationError({"session": "This agent session is closed."})
        run = run_for(locked)
        if run.step_runs.filter(decisions__verdict="pending").exists():
            raise ValidationError({"session": "Resolve the pending tool approval before sending another message."})
        next_index = int(locked.turns.aggregate(last=models.Max("index"))["last"] or 0) + 1
        turn = turn_model.objects.create(
            session=locked,
            index=next_index,
            prompt=prompt,
            created_by_id=locked.owner_id,
            updated_by_id=locked.owner_id,
        )
        if not locked.title:
            locked.title = prompt[:200]
            locked.save(update_fields=["title", "updated_at"])
        run_id = run.pk
    engine.deliver(run_id)
    return turn


def close_session(session: Any) -> Any:
    """Close a persisted session and wake its workflow step to finish."""

    session_model = apps.get_model("agents", "AgentSession")
    with system_context(reason="workflows_agents.session.close"), transaction.atomic():
        locked = session_model.objects.lock_if_supported().get(pk=session.pk)
        if str(locked.status) != "closed":
            locked.close()
        run_id = run_for(locked).pk
    engine.deliver(run_id)
    return locked


def run_for(session: Any) -> Any:
    """Return the one workflow run whose generic subject is ``session``."""

    run_model = apps.get_model("workflows", "WorkflowRun")
    content_type = ContentType.objects.get_for_model(session, for_concrete_model=False)
    try:
        return run_model.objects.get(
            subject_content_type=content_type,
            subject_object_id=session.pk,
        )
    except run_model.DoesNotExist as error:
        raise ValidationError({"session": "This agent session has no workflow run."}) from error
