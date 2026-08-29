"""Actor-to-user attribution resolvers contributed by the agents addon."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from rebac import system_context


def agent_user_id(subject_id: str) -> Any | None:
    """Return the service-user pk for ``agents/agent:<subject_id>``.

    Attribution is a system fact, not an actor read: resolving the user behind an
    agent must not REBAC-scope through the agent's own permissions, so this uses
    the base manager under ``system_context``.
    """

    agent_model = apps.get_model("agents", "Agent")
    with system_context(reason="agents.actor_user_resolver"):
        return agent_model._base_manager.filter(sqid=subject_id).values_list("user_id", flat=True).first()
