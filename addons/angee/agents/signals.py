"""Agent lifecycle rules on instance, queryset, and cascade deletion paths."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db.models.signals import post_delete, pre_delete

from angee.agents.models import ACTIVE_TURN_DELETE_MESSAGE, Agent, AgentSession, AgentTurn
from angee.base.signals import connect_for_models
from angee.iam.service_users import deactivate_service_user


def connect() -> None:
    """Wire lifecycle receivers to concrete agents, sessions, and turns."""

    connect_for_models(
        pre_delete, refuse_agent_delete, applies=lambda model: issubclass(model, Agent),
        dispatch_uid="angee.agents.agent.delete_guard",
    )
    connect_for_models(
        pre_delete, refuse_session_delete, applies=lambda model: issubclass(model, AgentSession),
        dispatch_uid="angee.agents.session.delete_guard",
    )
    connect_for_models(
        pre_delete, refuse_active_turn_delete, applies=lambda model: issubclass(model, AgentTurn),
        dispatch_uid="angee.agents.turn.delete_guard",
    )
    connect_for_models(
        post_delete, deactivate_agent_service_user, applies=lambda model: issubclass(model, Agent),
        dispatch_uid="angee.agents.agent.service_user.deactivate",
    )


def refuse_agent_delete(sender: Any, instance: Agent, *, origin: Any, **kwargs: Any) -> None:
    """Require teardown, rereading only a direct delete's potentially stale instance."""

    current = type(instance)._base_manager.filter(pk=instance.pk).first() if origin is instance else instance
    if current is None:
        return
    if blocker := current.delete_blocker():
        raise ValidationError(blocker)


def refuse_session_delete(sender: Any, instance: AgentSession, **kwargs: Any) -> None:
    """Serialize deletion with claims, checking hidden work under the session lock."""

    locked = type(instance).system_queryset(lock=("self",)).filter(pk=instance.pk).first()
    if locked is None:
        return
    turn_model = locked._meta.get_field("turns").related_model
    if turn_model.system_queryset().active().filter(session_id=locked.pk).exists():
        raise ValidationError(ACTIVE_TURN_DELETE_MESSAGE)


def refuse_active_turn_delete(sender: Any, instance: AgentTurn, *, origin: Any, **kwargs: Any) -> None:
    """Refuse claimed work; direct deletes reread, cascades use collector-loaded state."""

    current = type(instance)._base_manager.filter(pk=instance.pk).first() if origin is instance else instance
    if current is None:
        return
    if blocker := current.delete_blocker():
        raise ValidationError(blocker)


def deactivate_agent_service_user(sender: Any, instance: Agent, **kwargs: Any) -> None:
    """Deactivate the agent's service account after every supported delete path."""

    deactivate_service_user(instance)
