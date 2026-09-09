"""Owner-gated GraphQL mutations for persisted agent sessions."""

from __future__ import annotations

from typing import cast

import strawberry
from django.apps import apps
from django.core.exceptions import ValidationError
from strawberry.scalars import JSON

from angee.agents.schema import AgentSessionType, AgentTurnType
from angee.base.scoping import read_scoped_queryset
from angee.graphql.actions import authorized_action_target
from angee.graphql.ids import PublicID, to_public_id
from angee.iam.permissions import session_user
from angee.workflows_agents import sessions

Agent = apps.get_model("agents", "Agent")
AgentSession = apps.get_model("agents", "AgentSession")
WorkflowRun = apps.get_model("workflows", "WorkflowRun")


@strawberry.type
class AgentSessionWorkflowQuery:
    """Bounded bridge from one readable session to its workflow Run."""

    @strawberry.field
    def agent_session_workflow_run(self, info: strawberry.Info, session: PublicID) -> PublicID | None:
        """Return the session Run only when the caller may read both resources."""

        actor = session_user(info)
        target = authorized_action_target(info, AgentSession, session, "read")
        try:
            run = sessions.run_for(target)
        except ValidationError:
            return None
        scoped = read_scoped_queryset(WorkflowRun, actor, action="read")
        if scoped is None or not scoped.filter(pk=run.pk).exists():
            return None
        return to_public_id(WorkflowRun, run.pk)


@strawberry.type
class AgentSessionMutation:
    """Authenticated, row-authorized persisted chat mutations."""

    @strawberry.mutation
    def start_agent_session(
        self,
        info: strawberry.Info,
        agent: PublicID,
        context: JSON | None = None,
    ) -> AgentSessionType:
        """Start a new in-process session for an agent the caller owns."""

        owner = session_user(info)
        target = authorized_action_target(info, Agent, agent, "write")
        session = sessions.start_session(
            target,
            owner=owner,
            context=dict(context) if isinstance(context, dict) else {},
        )
        return cast(AgentSessionType, session)

    @strawberry.mutation
    def post_agent_message(
        self,
        info: strawberry.Info,
        session: PublicID,
        text: str,
    ) -> AgentTurnType:
        """Post a user turn to an owned persisted session."""

        target = authorized_action_target(info, AgentSession, session, "write")
        return cast(AgentTurnType, sessions.post_message(target, text))

    @strawberry.mutation
    def close_agent_session(
        self,
        info: strawberry.Info,
        session: PublicID,
    ) -> AgentSessionType:
        """Close an owned persisted session."""

        target = authorized_action_target(info, AgentSession, session, "write")
        return cast(AgentSessionType, sessions.close_session(target))


schemas = {"console": {"query": [AgentSessionWorkflowQuery], "mutation": [AgentSessionMutation]}}
"""GraphQL contributions installed by the workflows-agents addon."""
