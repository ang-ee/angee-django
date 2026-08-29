"""Session lifecycle service binding persisted agent chat to workflows."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import system_context

from angee.agents.models import RuntimeStatus, SessionStatus, TurnStatus
from angee.workflows import engine
from angee.workflows.models import RunStatus


def start_session(agent: Any, *, owner: Any, context: dict[str, Any]) -> Any:
    """Create a persisted session and start its seeded workflow run."""

    if not agent.runtime_backend.runs_in_process:
        raise ValidationError({"agent": "This agent runtime uses container ACP sessions."})
    if agent.owner_id != owner.pk:
        raise ValidationError({"agent": "Only the agent owner can start a session."})
    if agent.runtime_status != RuntimeStatus.RUNNING:
        raise ValidationError({"agent": "Provision this agent before starting a session."})

    session_model = apps.get_model("agents", "AgentSession")
    workflow_model = apps.get_model("workflows", "Workflow")
    with system_context(reason="workflows_agents.session.start"), transaction.atomic():
        workflow = (
            workflow_model.objects.current_published()
            .filter(steps__is_entry=True, steps__step_class="agent_session")
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
        if locked.status == SessionStatus.CLOSED:
            raise ValidationError({"session": "This agent session is closed."})
        run = run_for(locked)
        if run.status in RunStatus.TERMINAL:
            # The engine can no longer execute turns for this run (canceled,
            # reaped, or failed before the exhaustion conversion existed).
            # Reconcile the projection so the UI stops reusing the session —
            # committed here, the refusal raises after the transaction.
            locked.close()
            run_id = None
        else:
            if run.awaiting_decision():
                raise ValidationError(
                    {"session": "Resolve the pending tool approval before sending another message."}
                )
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
    if run_id is None:
        raise ValidationError({"session": "This agent session has ended. Start a new session."})
    engine.deliver(run_id)
    return turn


def close_session(session: Any) -> Any:
    """Close a persisted session, expire approvals, and wake its workflow."""

    session_model = apps.get_model("agents", "AgentSession")
    with system_context(reason="workflows_agents.session.close"):
        run = run_for(session)
        engine.expire_pending_decisions(run, resolved_by="workflows_agents/session_close")
        with transaction.atomic():
            locked = session_model.objects.lock_if_supported().get(pk=session.pk)
            for turn in (
                locked.turns.lock_if_supported()
                .filter(status=TurnStatus.AWAITING_APPROVAL)
                .order_by("index")
            ):
                turn.cancel()
            if locked.status != SessionStatus.CLOSED:
                locked.close()
        engine.deliver(run.pk)
    return locked


def close_agent_sessions(agent: Any) -> None:
    """Close every open persisted session through the lifecycle service."""

    session_model = apps.get_model("agents", "AgentSession")
    with system_context(reason="workflows_agents.session.close_agent_sessions"):
        sessions = list(
            session_model.objects.filter(agent=agent).exclude(status=SessionStatus.CLOSED).order_by("pk")
        )
    for session in sessions:
        close_session(session)


def run_for(session: Any) -> Any:
    """Return the one workflow run whose generic subject is ``session``."""

    run_model = apps.get_model("workflows", "WorkflowRun")
    try:
        return run_model.objects.for_subject(session).get()
    except run_model.DoesNotExist as error:
        raise ValidationError({"session": "This agent session has no workflow run."}) from error
