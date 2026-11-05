"""Agent sessions owners and their native model contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from datetime import datetime
from types import MappingProxyType
from typing import Any, ClassVar, Self

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, transaction
from rebac import actor_context, current_actor, database_context, system_context
from rebac.database import write_alias

from angee.agents.models.choices import (
    ACTIVE_TURN_DELETE_MESSAGE,
    ACTIVE_TURN_STATUSES,
    OPEN_TURN_STATUSES,
    SessionStatus,
    TurnStatus,
)
from angee.agents.runners import TurnOutcome
from angee.base.fields.state import StateField
from angee.base.fields.text import DiagnosticTextField
from angee.base.models.audit import AuditMixin
from angee.base.models.persistence import require_composed_type
from angee.base.models.transitions import StateTransitions, save_state, transition
from angee.base.refs import RecordRef
from angee.iam.managers import ProtectedManager
from angee.iam.models.append_only import ProtectedAppendOnlyModel, ProtectedAppendOnlyQuerySet
from angee.iam.models.base import ProtectedDataModel
from angee.iam.permissions import actor_user_id, instance_actor
from angee.jobs.enqueue import enqueue_task
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet


class AgentSessionQuerySet[SessionT: AgentSession](AngeeHistoryQuerySet[SessionT]):
    """Conversation selection and selected-set lifecycle writes."""

    def close_as_system(self, *, reason: str) -> None:
        """Close selected sessions with their open turns under the same database."""

        using = self.db
        with database_context(using), system_context(reason=reason), transaction.atomic(using=using):
            for session in (
                self.sudo(reason=reason).lock_if_supported().exclude(status=SessionStatus.CLOSED).order_by("pk")
            ):
                session._close_locked()


_AgentSessionManagerBase = AngeeHistoryManager.from_queryset(AgentSessionQuerySet)


class AgentSessionManager[SessionT: AgentSession](_AgentSessionManagerBase[SessionT]):
    """Create conversations under the caller's native REBAC create gate."""

    def current_for_agent(self, agent: Any) -> SessionT | None:
        """Return this actor's latest open session for an in-process agent."""

        user_id = actor_user_id(current_actor())
        if user_id is None or not agent.runs_in_process:
            return None
        with system_context(reason="agents.session.current_for_agent"):
            return (
                self.filter(agent=agent, user_id=user_id)
                .exclude(status=SessionStatus.CLOSED)
                .order_by("-updated_at", "-pk")
                .first()
            )

    def start(self, agent: Any, *, user: Any, context: Mapping[str, Any], actor: Any = None) -> Any:
        """Start an idle conversation with a callable, running in-process agent.

        The admission lock orders starts with teardown's session scan. Native
        ``no_key`` keeps this lifecycle barrier while allowing unrelated inserts
        referencing the agent, including tool/server selections.
        """

        actor = actor or instance_actor(agent)
        using = write_alias(self.model, using=self._db or agent._state.db, instance=agent)
        with database_context(using), transaction.atomic(using=using):
            # Serialize lifecycle updates while permitting unrelated FK inserts.
            locked = type(agent).system_queryset().using(using).lock_if_supported(no_key=True).get(pk=agent.pk)
            blocker = locked.chat_blocker()
            if blocker:
                raise ValidationError({"agent": blocker})
            return self.db_manager(using).with_actor(actor).create(agent=locked, user=user, context=dict(context))

    def close_for_agent_as_system(self, agent: Any) -> None:
        """Close an agent's sessions as part of its authorized in-process teardown."""

        self.db_manager(write_alias(self.model, using=self._db or agent._state.db, instance=agent)).sudo(
            reason="agents.session.deprovision"
        ).filter(agent=agent).close_as_system(
            reason="agents.session.deprovision",
        )


class AgentSession(AuditMixin, ProtectedDataModel, concrete=True):
    """Runtime-neutral conversation whose turns run once, without crash recovery.

    A pending turn retained across a rebuild runs first when the user next posts.
    A session left awaiting approval is released through Stop or Close. If an
    after-commit settlement signal is lost, the turn stays parked until Stop.
    Delete authority derives from the agent, independently of the session's user.
    """

    sqid_prefix = "ase_"
    record_display_paths: ClassVar[tuple[str, ...]] = ("title", "agent")
    agent = models.ForeignKey("agents.Agent", on_delete=models.CASCADE, related_name="sessions")
    # A session is its user's own conversation, not a transferable grant root: deleting
    # the user deletes it, and refuses while one of its turns still runs.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.CASCADE,
        related_name="+",
    )
    title = models.CharField(max_length=200, blank=True)
    context = models.JSONField(default=dict, blank=True)
    """The view from which this conversation was started, independent of its turns."""
    status = StateField(choices_enum=SessionStatus, default=SessionStatus.IDLE)
    replay_state = models.JSONField(default=list, blank=True)
    usage = models.JSONField(default=dict, blank=True)
    last_error = DiagnosticTextField(blank=True)

    status_transitions = StateTransitions(
        status,
        {
            SessionStatus.IDLE: [SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.CLOSED, SessionStatus.ERROR],
            SessionStatus.RUNNING: [
                SessionStatus.RUNNING,
                SessionStatus.IDLE,
                SessionStatus.AWAITING_APPROVAL,
                SessionStatus.CLOSED,
                SessionStatus.ERROR,
            ],
            SessionStatus.AWAITING_APPROVAL: [
                SessionStatus.IDLE,
                SessionStatus.RUNNING,
                SessionStatus.CLOSED,
                SessionStatus.ERROR,
            ],
            SessionStatus.ERROR: [
                SessionStatus.ERROR,
                SessionStatus.IDLE,
                SessionStatus.RUNNING,
                SessionStatus.CLOSED,
            ],
        },
    )

    objects = AgentSessionManager()

    @classmethod
    def turn_model(cls) -> type[AgentTurn]:
        """Return this session's configured turn model, enforcing the execution contract."""

        model = cls._meta.get_field("turns").related_model
        if model is None or not issubclass(model, AgentTurn):
            raise ImproperlyConfigured(f"{cls._meta.label}.turns must target an AgentTurn model.")
        return model

    class Meta:
        """Django model options for persisted agent sessions."""

        abstract = True
        ordering = ("-updated_at", "sqid")
        rebac_resource_type = "agents/session"

    def __str__(self) -> str:
        """Return the session title or its agent name."""

        return self.title or str(self.agent)

    def post(
        self,
        text: str,
        *,
        context: Mapping[str, Any] = MappingProxyType({}),
        actor: Any = None,
        dispatch_expires: datetime | None = None,
    ) -> Any:
        """Queue a prompt with this message's view context under the posting actor.

        Context must be an object; omission retains no view context and never
        inherits the session's starting view. Broker failure leaves the turn pending.
        ``dispatch_expires`` bounds delivery for callers recovering lost dispatch;
        the worker discards that delivery before a replacement can run.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        actor = actor or instance_actor(self)
        if not isinstance(context, Mapping):
            raise ValidationError({"context": "Message context must be an object."})
        prompt = text.strip()
        if not prompt:
            raise ValidationError({"text": "A message is required."})
        with database_context(using), transaction.atomic(using=using):
            locked = type(self).system_queryset(lock=("self",)).using(using).get(pk=self.pk)
            actor = locked.require_access("post", actor)
            if locked.status == SessionStatus.CLOSED:
                raise ValidationError({"session": "This agent session is closed."})
            blocker = locked.agent.chat_blocker()
            if blocker:
                raise ValidationError({"agent": blocker})
            if locked.status == SessionStatus.AWAITING_APPROVAL:
                raise ValidationError({"session": "Stop the pending tool approval before sending another message."})
            turns = locked.turns.with_actor(actor)
            next_index = int(turns.aggregate(last=models.Max("index"))["last"] or 0) + 1
            turn = turns.create(session=locked, index=next_index, prompt=prompt, context=dict(context))
            if not locked.title:
                locked.title = prompt[:200]
                locked.save(update_fields=["title", "updated_at"])
            self.enqueue_execution(expires=dispatch_expires)
            self.refresh_from_db(using=using)
        return turn

    def close(self, *, actor: Any = None) -> None:
        """Cancel every open turn and close the conversation atomically."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        actor = actor or instance_actor(self)
        with database_context(using), transaction.atomic(using=using):
            locked = type(self).system_queryset(lock=("self",)).using(using).get(pk=self.pk)
            actor = locked.require_access("write", actor)
            with actor_context(actor) if actor is not None else nullcontext():
                locked._close_locked()
            self.refresh_from_db(using=using)

    def _close_locked(self) -> None:
        """Cancel open turns after the caller locks and authorizes this session."""

        with system_context(reason="agents.session.close"):
            turn_model = type(self)._meta.apps.get_model("agents", "AgentTurn")
            turns = turn_model._default_manager.db_manager(self._state.db).filter(session_id=self.pk)
            for turn in turns.lock_if_supported().open().order_by("index"):
                turn.mark_canceled()
            if self.status != SessionStatus.CLOSED:
                self.mark_closed()

    def cancel_turn(self, turn: Any, *, actor: Any = None) -> None:
        """Stop one turn, release its execution slot and dispatch queued work.

        Stop is observed at the next batched stream flush. Already issued tools
        cannot be recalled, and silent tools cannot be reached until they return.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        actor = actor or instance_actor(self)
        with database_context(using), transaction.atomic(using=using):
            locked = type(self).system_queryset(lock=("self",)).using(using).get(pk=self.pk)
            actor = locked.require_access("write", actor)
            with (
                actor_context(actor) if actor is not None else nullcontext(),
                system_context(
                    reason="agents.session.cancel_turn",
                ),
            ):
                turn_model = type(locked)._meta.apps.get_model("agents", "AgentTurn")
                current = (
                    turn_model._default_manager.db_manager(using)
                    .filter(session_id=locked.pk)
                    .lock_if_supported()
                    .get(pk=turn.pk)
                )
                if current.status in OPEN_TURN_STATUSES:
                    active = current.status in ACTIVE_TURN_STATUSES
                    current.mark_canceled()
                    if active and locked.status != SessionStatus.CLOSED:
                        locked.mark_idle()
                    locked._enqueue_pending_turn()
                turn.refresh_from_db(using=using)
            self.refresh_from_db(using=using)

    def cancel_active_turn(self, *, actor: Any = None) -> None:
        """Select and stop the active turn while holding the session lock."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        actor = actor or instance_actor(self)
        with database_context(using), transaction.atomic(using=using):
            locked = type(self).system_queryset(lock=("self",)).using(using).get(pk=self.pk)
            actor = locked.require_access("write", actor)
            turns = require_composed_type(locked.turns.all(), AgentTurnQuerySet)
            turn = turns.with_actor(actor).active().order_by("index").first()
            if turn is not None:
                locked.cancel_turn(turn, actor=actor)
            self.refresh_from_db(using=using)

    def claim_turn(self) -> Any | None:
        """Claim the oldest pending turn under the session lock, never an active turn.

        A worker crash or hard time limit leaves a running turn for the user to
        stop. Duplicate deliveries neither reclaim it nor execute another turn.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        with (
            database_context(using),
            system_context(reason="agents.session.claim"),
            transaction.atomic(using=using),
        ):
            locked = type(self).system_queryset(lock=("self",)).using(using).filter(pk=self.pk).first()
            if locked is None or locked.status == SessionStatus.CLOSED:
                return None
            turns = require_composed_type(locked.turns.all(), AgentTurnQuerySet)
            if turns.active().exists():
                return None
            turn = locked.turns.lock_if_supported().filter(status=TurnStatus.PENDING).order_by("index").first()
            if turn is None:
                if locked.status in (SessionStatus.RUNNING, SessionStatus.AWAITING_APPROVAL):
                    locked.mark_idle()
            else:
                turn.mark_running()
                locked.mark_running()
                blocker = locked.agent.chat_blocker()
                if blocker:
                    locked.settle_turn(
                        turn,
                        TurnOutcome(
                            kind="failed",
                            error=blocker,
                        ),
                    )
                    turn = None
            self.refresh_from_db(using=using)
            return turn

    def settle_turn(self, turn: Any, outcome: TurnOutcome) -> None:
        """Store one still-running turn's outcome and dispatch its next pending turn."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        with (
            database_context(using),
            system_context(reason="agents.session.settle"),
            transaction.atomic(using=using),
        ):
            locked = type(self).system_queryset(lock=("self",)).using(using).filter(pk=self.pk).first()
            if locked is None:
                return
            current = locked.turns.lock_if_supported().filter(pk=turn.pk).order_by("pk").first()
            if current is None or current.status != TurnStatus.RUNNING:
                return
            usage = dict(locked.usage or {})
            for key, value in outcome.usage.items():
                usage[key] = usage.get(key, 0) + value
            locked.usage = usage
            fields = ["usage", "updated_at"]
            if outcome.kind == "completed":
                locked.replay_state = outcome.replay_state
                fields.append("replay_state")
                current.mark_completed(text=outcome.text, usage=outcome.usage)
                locked.mark_idle()
            else:
                error = (
                    "Tool approvals are not available yet."
                    if outcome.kind == "needs_approval"
                    else outcome.error or "Agent runtime failed."
                )
                current.mark_failed(error, usage=outcome.usage)
                locked.mark_error(current.error)
            locked.save(update_fields=fields)
            locked._enqueue_pending_turn()
            self.refresh_from_db(using=using)
            turn.refresh_from_db(using=using)

    def enqueue_execution(self, *, expires: datetime | None = None, robust: bool = False) -> None:
        """Carry this session's database into execution after its producer transaction commits."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        enqueue_task(
            "agents.run_session",
            kwargs={"session_id": self.pk, "using": using},
            expires=expires,
            robust=robust,
            using=using,
        )

    def _enqueue_pending_turn(self) -> None:
        """Send the next delivery on commit when this locked session has queued work."""

        turn_model = type(self)._meta.apps.get_model("agents", "AgentTurn")
        turns = turn_model._default_manager.db_manager(self._state.db).filter(session_id=self.pk)
        if self.status != SessionStatus.CLOSED and turns.filter(status=TurnStatus.PENDING).exists():
            self.enqueue_execution(robust=True)

    @transition(
        status,
        source=[SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.AWAITING_APPROVAL, SessionStatus.ERROR],
        target=SessionStatus.RUNNING,
        on_success=save_state,
    )
    def mark_running(self) -> None:
        """Project active turn execution onto the session."""

        self.last_error = ""
        self._transition_fields = {"last_error"}

    @transition(
        status,
        source=[SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.AWAITING_APPROVAL, SessionStatus.ERROR],
        target=SessionStatus.IDLE,
        on_success=save_state,
    )
    def mark_idle(self) -> None:
        """Project a parked session awaiting its next user turn."""

        self.last_error = ""
        self._transition_fields = {"last_error"}

    @transition(
        status,
        source=SessionStatus.RUNNING,
        target=SessionStatus.AWAITING_APPROVAL,
        on_success=save_state,
    )
    def mark_awaiting_approval(self) -> None:
        """Project a suspended tool approval onto the session."""

    @transition(
        status,
        source=[SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.AWAITING_APPROVAL, SessionStatus.ERROR],
        target=SessionStatus.CLOSED,
        on_success=save_state,
    )
    def mark_closed(self) -> None:
        """Mark the conversation closed after its open turns have been canceled."""

    @transition(
        status,
        source=[SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.AWAITING_APPROVAL, SessionStatus.ERROR],
        target=SessionStatus.ERROR,
        on_success=save_state,
    )
    def mark_error(self, message: str) -> None:
        """Project a session-level runtime error."""

        self.last_error = message
        self._transition_fields = {"last_error"}


class AgentTurnQuerySet[TurnT: AgentTurn](AngeeHistoryQuerySet[TurnT]):
    """Turn scopes shared by session execution and transport callers."""

    def open(self) -> Self:
        """Select unfinished turns, including pending work and claimed execution."""

        return self.filter(status__in=OPEN_TURN_STATUSES)

    def active(self) -> Self:
        """Select claimed work, including a turn parked on approval."""

        return self.filter(status__in=ACTIVE_TURN_STATUSES)


_AgentTurnManager = AngeeHistoryManager.from_queryset(AgentTurnQuerySet)


class AgentTurn(AuditMixin, ProtectedDataModel, concrete=True):
    """One prompt-to-response cycle with append-only ACP updates."""

    sqid_prefix = "atn_"
    session = models.ForeignKey("agents.AgentSession", on_delete=models.CASCADE, related_name="turns")
    index = models.PositiveIntegerField()
    prompt = models.TextField()
    context = models.JSONField(default=dict, blank=True, editable=False)
    """Server-retained view context supplied when this message was posted."""
    status = StateField(choices_enum=TurnStatus, default=TurnStatus.PENDING)
    update_count = models.PositiveIntegerField(default=0, editable=False)
    last_update_kind = models.CharField(max_length=64, blank=True, editable=False)
    segment_start = models.PositiveIntegerField(default=0, editable=False)
    text = models.TextField(blank=True)
    usage = models.JSONField(default=dict, blank=True)
    error = DiagnosticTextField(blank=True)

    status_transitions = StateTransitions(
        status,
        {
            TurnStatus.PENDING: [TurnStatus.RUNNING, TurnStatus.CANCELED],
            TurnStatus.RUNNING: [
                TurnStatus.AWAITING_APPROVAL,
                TurnStatus.COMPLETED,
                TurnStatus.FAILED,
                TurnStatus.CANCELED,
            ],
            TurnStatus.AWAITING_APPROVAL: [
                TurnStatus.COMPLETED,
                TurnStatus.FAILED,
                TurnStatus.CANCELED,
            ],
        },
    )

    objects = _AgentTurnManager()

    class Meta:
        """Django model options for agent turns."""

        abstract = True
        ordering = ("session", "index")
        rebac_resource_type = "agents/turn"
        constraints = (
            models.UniqueConstraint(fields=("session", "index"), name="uniq_agents_turn_session_index"),
            models.UniqueConstraint(
                fields=("session",),
                condition=models.Q(status__in=ACTIVE_TURN_STATUSES),
                name="uniq_agents_turn_active_session",
            ),
        )

    def append_updates(self, batch: Sequence[dict[str, Any]]) -> bool:
        """Save the worker's emitted batch, then report whether it may keep running.

        The claiming worker is the sole transcript writer. A canceled turn retains
        its final batch emitted before Stop was observed at this flush. A deleted
        turn is stopped and no longer has a transcript to retain.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        with database_context(using), transaction.atomic(using=using):
            locked = (
                type(self)
                .objects.using(using)
                .only("pk", "status", "updated_at", "update_count", "last_update_kind", "segment_start")
                .lock_if_supported()
                .filter(pk=self.pk)
                .order_by("pk")
                .first()
            )
            if locked is None:
                return False
            if batch and locked.status in (TurnStatus.RUNNING, TurnStatus.CANCELED):
                start = locked.update_count
                previous_kind = locked.last_update_kind
                previous_segment_start = locked.segment_start
                for offset, update in enumerate(batch):
                    kind = update["sessionUpdate"]
                    if kind != previous_kind:
                        locked.segment_start = start + offset
                    previous_kind = kind
                type(self).update_model().objects.db_manager(using).create(
                    turn=locked,
                    start_index=start,
                    end_index=start + len(batch),
                    previous_kind=locked.last_update_kind,
                    previous_segment_start=previous_segment_start,
                    updates=list(batch),
                )
                locked.update_count += len(batch)
                locked.last_update_kind = previous_kind
                locked.save(update_fields=["update_count", "last_update_kind", "segment_start", "updated_at"])
                self.update_count = locked.update_count
                self.last_update_kind = locked.last_update_kind
                self.segment_start = locked.segment_start
            return locked.status == TurnStatus.RUNNING

    @classmethod
    def update_model(cls) -> type[AgentTurnUpdate]:
        """Resolve the composed batch row through this turn's declared relation."""
        model = cls._meta.get_field("update_batches").related_model
        if model is None or not issubclass(model, AgentTurnUpdate):
            raise ImproperlyConfigured(f"{cls._meta.label}.update_batches must target AgentTurnUpdate.")
        return model

    @property
    def updates(self) -> list[dict[str, Any]]:
        """Return the retained transcript in wire order."""
        if "_prefetched_update_batches" in self.__dict__:
            batches = self.__dict__["_prefetched_update_batches"]
        else:
            batches = (
                self.update_model()
                ._base_manager.db_manager(self._state.db)
                .filter(turn_id=self.pk)
                .order_by("start_index")
            )
        return [update for batch in batches for update in batch.updates]

    def updates_since(self, index: int) -> tuple[list[dict[str, Any]], str, int]:
        """Read only batches overlapping an attachment's delivered position."""
        batches = (
            self.update_model()
            ._base_manager.db_manager(self._state.db)
            .filter(turn_id=self.pk, end_index__gt=index)
            .order_by("start_index")
            .values("start_index", "updates", "previous_kind", "previous_segment_start")
        )
        updates: list[dict[str, Any]] = []
        previous_kind = ""
        segment_start = 0
        for batch in batches:
            if not updates:
                previous_kind = batch["previous_kind"]
                segment_start = batch["previous_segment_start"]
            for offset, update in enumerate(batch["updates"]):
                position = batch["start_index"] + offset
                if position < index:
                    if update["sessionUpdate"] != previous_kind:
                        segment_start = position
                    previous_kind = update["sessionUpdate"]
                else:
                    updates.append(update)
        return updates, previous_kind, segment_start

    @property
    def delivery_updates(self) -> list[dict[str, Any]]:
        """Use the follower's bounded delta, or the full transcript for replay."""
        if "_delivery_updates" in self.__dict__:
            return self._delivery_updates
        return self.updates

    @property
    def delivery_start(self) -> int:
        return getattr(self, "_delivery_start", 0)

    @property
    def delivery_previous_kind(self) -> str:
        return getattr(self, "_delivery_previous_kind", "")

    @property
    def delivery_segment_start(self) -> int:
        return getattr(self, "_delivery_segment_start", 0)

    def set_delivery_delta(self, index: int) -> None:
        """Attach one read-only delivery slice to this fetched turn instance."""
        updates, previous_kind, segment_start = self.updates_since(index)
        self._delivery_updates = updates
        self._delivery_start = index
        self._delivery_previous_kind = previous_kind
        self._delivery_segment_start = segment_start

    def change_related_records(self) -> tuple[RecordRef, ...]:
        """Name the session without loading its guarded relation, including on delete."""

        model = type(self).session.field.related_model
        return (RecordRef.from_model(model, self.session_id),)

    def delete_blocker(self) -> str | None:
        """Refuse deletion of claimed work, including work parked on approval."""

        return ACTIVE_TURN_DELETE_MESSAGE if self.status in ACTIVE_TURN_STATUSES else None

    @transition(
        status,
        source=TurnStatus.PENDING,
        target=TurnStatus.RUNNING,
        on_success=save_state,
    )
    def mark_running(self) -> None:
        """Claim this pending turn for its single runtime execution."""

        self.error = ""
        self._transition_fields = {"error"}

    @transition(
        status,
        source=TurnStatus.RUNNING,
        target=TurnStatus.AWAITING_APPROVAL,
        on_success=save_state,
    )
    def mark_awaiting_approval(self) -> None:
        """Suspend this turn for deferred tool approval."""

    @transition(
        status,
        source=[TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL],
        target=TurnStatus.COMPLETED,
        on_success=save_state,
    )
    def mark_completed(self, *, text: str, usage: Mapping[str, int]) -> None:
        """Persist the final response projection for a completed turn."""

        self.text = text
        self.usage = dict(usage)
        self.error = ""
        self._transition_fields = {"text", "usage", "error"}

    @transition(
        status,
        source=[TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL],
        target=TurnStatus.FAILED,
        on_success=save_state,
    )
    def mark_failed(self, message: str, *, usage: Mapping[str, int]) -> None:
        """Persist a runtime-authored public failure message for this turn."""

        self.error = message
        self.usage = dict(usage)
        self._transition_fields = {"error", "usage"}

    @transition(
        status,
        source=[TurnStatus.PENDING, TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL],
        target=TurnStatus.CANCELED,
        on_success=save_state,
    )
    def mark_canceled(self) -> None:
        """Cancel this turn without deleting its audit trail."""


class AgentTurnUpdateQuerySet[UpdateT: AgentTurnUpdate](ProtectedAppendOnlyQuerySet[UpdateT]):
    """Keep persisted transcript batches immutable through the collection API."""


_AgentTurnUpdateManager = ProtectedManager.from_queryset(AgentTurnUpdateQuerySet)


class AgentTurnUpdate(ProtectedAppendOnlyModel, ProtectedDataModel, concrete=True):
    """One immutable flush batch ordered within its owning turn."""

    sqid_prefix = "atu_"
    turn = models.ForeignKey("agents.AgentTurn", on_delete=models.CASCADE, related_name="update_batches")
    start_index = models.PositiveIntegerField(editable=False)
    end_index = models.PositiveIntegerField(editable=False)
    previous_kind = models.CharField(max_length=64, blank=True, editable=False)
    previous_segment_start = models.PositiveIntegerField(default=0, editable=False)
    updates = models.JSONField(default=list, editable=False)

    objects = _AgentTurnUpdateManager()

    class Meta:
        abstract = True
        ordering = ("turn_id", "start_index")
        rebac_resource_type = "agents/turn_update"
        constraints = (models.UniqueConstraint(fields=("turn", "start_index"), name="uniq_agents_turn_update_start"),)
        indexes = (models.Index(fields=("turn", "end_index"), name="agents_turn_update_delta"),)
