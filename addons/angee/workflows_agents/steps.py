"""Queue ACP turns with scoped context, bounded waits and explicit closure."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from rebac import actor_context, generic_target, to_object_ref

from angee.agents.context import record_view_context
from angee.agents.models import TurnStatus
from angee.base.identity import public_id_of
from angee.jobs.enqueue import enqueue_task
from angee.jobs.timeouts import task_time_budget
from angee.workflows.steps import Fail, Settlement, Step
from angee.workflows.triggers import TriggerGrantTarget

QUEUEING_ALLOWANCE = timedelta(minutes=2)
MAX_PENDING_REDISPATCHES = 2


def conversation_timeout() -> timedelta:
    """Leave a queue allowance around the runtime's native worker time budget."""
    return task_time_budget() + QUEUEING_ALLOWANCE


class StartConversationConfig(BaseModel):
    """Select an agent and plain format text over subject identity and bound JSON."""

    model_config = ConfigDict(extra="forbid")
    agent: str = Field(min_length=1, json_schema_extra={"relation": {"resource": "agents.Agent"}})
    prompt_template: str = Field(min_length=1)
    timeout: timedelta = Field(default_factory=conversation_timeout, gt=timedelta())


class ConversationInput(BaseModel):
    """The engine binds data; an explicit session continues the same subject."""

    model_config = ConfigDict(extra="forbid")
    session: str | None = Field(
        default=None, min_length=1, json_schema_extra={"relation": {"resource": "agents.AgentSession"}},
    )
    data: JsonValue = Field(default_factory=dict)


class ConversationIdentity(BaseModel):
    """The exact conversation and turn retained by this run."""

    model_config = ConfigDict(extra="forbid")
    session: str = Field(min_length=1)
    turn: str = Field(min_length=1)


class ConversationOutput(ConversationIdentity):
    """The completed turn's final answer, independent of any tool side effect."""

    text: str


class ConversationState(ConversationIdentity):
    """Persist deadline and delivery recovery independently of observation wakes."""

    deadline: datetime
    redispatches: int = 0
    retry: int = 0


def render_prompt(template: str, *, context: dict[str, str], data: JsonValue) -> str:
    """Expose only JSON values; format traversal can never reach an ORM object."""
    values = {"subject": {"type": context["type"], "sqid": context["sqid"]}, "input": data}
    try:
        return template.format_map(values)
    except (KeyError, AttributeError, IndexError, ValueError, TypeError) as error:
        raise ValidationError(f"Invalid conversation prompt: {error}") from error


@dataclass(frozen=True)
class _CancelTurnAndFail(Fail):
    """Cancel at settlement after the failed DATABASE body has rolled back."""

    session: Any = None
    turn: Any = None
    actor: Any = None

    def transition(self, rows: Any, step_run: Any, attempt: Any) -> int:
        with actor_context(self.actor):
            self.session.cancel_turn(self.turn, actor=self.actor)
        return super().transition(rows, step_run, attempt)


class StartConversation(Step[ConversationInput, ConversationOutput, StartConversationConfig]):
    """Post atomically, then observe status or recover bounded task-delivery loss."""

    key = "start_conversation"
    label = "Start conversation"
    outcomes = {"done": "Answer prepared", "no_reply": "No answer"}

    def run(self, ctx: Any) -> Settlement:
        sessions = apps.get_model("agents", "AgentSession")
        turns = apps.get_model("agents", "AgentTurn")
        subject = ctx.subject
        if subject is None:
            raise ValidationError("A conversation requires a workflow subject.")
        context = record_view_context(subject)
        prompt = render_prompt(ctx.config.prompt_template, context=context, data=ctx.input.data)
        agent = ctx.load(apps.get_model("agents", "Agent"), ctx.config.agent, permission="call")
        publisher = ctx.run.version.published_by
        if publisher is None:
            raise ValidationError("The workflow version has no publisher to authorize this agent call.")
        TriggerGrantTarget(to_object_ref(agent), "caller", grant_permission="call").require_publisher_access(
            publisher, ctx.run.version,
        )
        retained = ConversationState.model_validate(ctx.state) if ctx.state else None
        if retained is not None:
            # Match the executor's session-before-turn lock order.
            session = ctx.load(sessions, retained.session, lock=True)
            turn = ctx.load(turns, retained.turn, lock=True)
            if turn.session_id != session.pk or session.agent_id != agent.pk or session.context != context:
                raise ValidationError("The retained turn belongs to a different conversation.")
            retry = (
                turn.status in (TurnStatus.FAILED, TurnStatus.CANCELED)
                and ctx.step_run.retries > retained.retry
            )
        else:
            retry = False
            if ctx.input.session is None:
                session = sessions.objects.start(agent, owner=ctx.run.run_as, context=context, actor=ctx.actor)
                ctx.record(session, "Conversation", operation="created")
            else:
                session = ctx.load(sessions, ctx.input.session, permission="post", lock=True)
                if session.agent_id != agent.pk or session.context != context:
                    raise ValidationError("The conversation belongs to a different agent or subject.")
                ctx.record(session, "Conversation")
        if retained is None or retry:
            turn = session.post(prompt, context=context, actor=ctx.actor)
            ctx.record(turn, "Agent turn", operation="created")
            retained = ConversationState(
                session=public_id_of(session), turn=public_id_of(turn),
                deadline=ctx.now + ctx.config.timeout, retry=ctx.step_run.retries,
            )
        if turn.status == TurnStatus.COMPLETED:
            ctx.record(turn, "Agent answer")
            outcome = "done" if turn.text.strip() else "no_reply"
            ctx.note("Agent prepared an answer." if outcome == "done" else "Agent returned no reply text.")
            return ctx.done(ConversationOutput(
                session=retained.session, turn=retained.turn, text=turn.text,
            ), outcome=outcome)
        if turn.status in (TurnStatus.FAILED, TurnStatus.CANCELED):
            return ctx.fail(turn.error or "The agent turn was canceled.")
        if ctx.now >= retained.deadline:
            if turn.status == TurnStatus.PENDING and retained.redispatches < MAX_PENDING_REDISPATCHES:
                enqueue_task("agents.run_session", kwargs={"session_id": session.pk}, robust=True)
                retained.redispatches += 1
                retained.deadline = ctx.now + ctx.config.timeout
            else:
                return _CancelTurnAndFail(
                    error="The agent turn exceeded its deadline; it was stopped. Retry to start another turn.",
                    session=session, turn=turn, actor=ctx.actor,
                )
        ctx.watch(turn)
        return ctx.wait(until=retained.deadline, state=retained.model_dump(mode="json"))


def require_run_turn(ctx: Any, identity: ConversationIdentity) -> Any:
    """Load a completed turn recorded as created by this run, under its read actor."""
    turn = ctx.load(apps.get_model("agents", "AgentTurn"), identity.turn)
    session = ctx.load(apps.get_model("agents", "AgentSession"), identity.session)
    records = apps.get_model("workflows", "StepRecord")
    target = generic_target(turn)
    if turn.session_id != session.pk or not records.objects.with_actor(ctx.actor).filter(
        run=ctx.run, operation="created", **target.lookups(records, "record"),
    ).exists():
        raise ValidationError("The agent turn was not created by this workflow run.")
    if turn.status != TurnStatus.COMPLETED:
        raise ValidationError("The agent turn has not completed successfully.")
    return turn


class CloseConversation(Step[ConversationOutput, ConversationOutput, None]):
    """Close a completed workflow-owned conversation through the ACP session owner."""

    key = "close_conversation"
    label = "Close conversation"

    def run(self, ctx: Any) -> Settlement:
        turn = require_run_turn(ctx, ctx.input)
        turn.session.close(actor=ctx.actor)
        ctx.record(turn.session, "Closed conversation", operation="changed")
        return ctx.done(ctx.input)
