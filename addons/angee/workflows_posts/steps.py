"""Thin workflow calls to comment eligibility and reply composition owners."""

from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict

from angee.agents.context import record_view_context
from angee.base.identity import public_id_of
from angee.posts.models import CommentAnswered
from angee.workflows.states import ERROR_OUTCOME
from angee.workflows.steps import Settlement, Step, StepMode
from angee.workflows_agents.steps import ConversationOutput, require_run_turn
from angee.workflows_posts.constants import AGENT_TURN_METADATA_KEY


class CheckReplied(Step[Any, None, None]):
    """Delegate already-answered and channel-authored eligibility to posts."""

    key = "check_replied"
    label = "Check for a reply"
    subject = "messaging.Message"
    mode = StepMode.DATABASE
    outcomes = {"replied": "Already answered", "open": "Reply needed"}

    def run(self, ctx: Any) -> Settlement:
        comment = ctx.subject_for_update()
        ctx.record(comment, "Comment")
        if comment.reply_state():
            ctx.note("The comment is already answered or authored by the channel; no reply is needed.")
            return ctx.done(outcome="replied")
        ctx.note("The comment is open for a reply.")
        return ctx.done(outcome="open")


class ReplyOutput(BaseModel):
    """The reply's native preparation or delivery state."""

    model_config = ConfigDict(extra="forbid")
    message_id: str
    status: str
    scheduled_at: datetime | None


class ScheduleReply(Step[ConversationOutput, ReplyOutput, None]):
    """Prepare a completed answer once per run through messaging's creation key."""

    key = "schedule_reply"
    label = "Schedule reply"
    subject = "messaging.Message"
    mode = StepMode.DATABASE
    outcomes = {"scheduled": "Reply prepared", "replied": "Already answered"}
    empty_outcomes = frozenset({ERROR_OUTCOME, "replied"})

    def run(self, ctx: Any) -> Settlement:
        turn = require_run_turn(ctx, ctx.input)
        comment = ctx.subject_for_update()
        if turn.context != record_view_context(comment) or ctx.input.text != turn.text:
            raise ValidationError("The answer must be this run's completed turn for the subject comment.")
        try:
            reply = comment.reply_to_comment(
                body=ctx.input.text, actor=ctx.actor,
                local={AGENT_TURN_METADATA_KEY: public_id_of(turn)},
                creation_key=f"workflow-reply:{public_id_of(ctx.run)}",
            )
        except CommentAnswered:
            ctx.note("The comment was answered while preparing the reply.")
            return ctx.done(outcome="replied")
        ctx.record(reply, "Reply", operation="created")
        if reply.status == reply.MessageStatus.DRAFT:
            ctx.note("Reply held for operator approval." if reply.scheduled_at is None else
                     f"Reply scheduled for {reply.scheduled_at.isoformat()}.")
        elif reply.status == reply.MessageStatus.QUEUED:
            ctx.note("Reply queued for delivery.")
        else:
            ctx.note(f"Reply {public_id_of(reply)} has status {reply.get_status_display()}.")
        return ctx.done(ReplyOutput(
            message_id=public_id_of(reply), status=str(reply.status), scheduled_at=reply.scheduled_at,
        ), outcome="scheduled")
