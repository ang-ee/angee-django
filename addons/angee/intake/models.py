"""Request evidence targeting exactly one task or project, and channel capture."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, Self, cast

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import DomainNameValidator, validate_email
from django.db import models, transaction
from django.db.models.functions import NullIf
from rebac import PermissionDenied, actor_context, current_actor, system_context

from angee.base.actors import instance_actor
from angee.base.errors import DomainError
from angee.base.fields import StateField
from angee.base.mixins import AuditMixin, OptimisticLockMixin
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet
from angee.base.scoping import bind_actor, system_queryset
from angee.decisions.contracts import DecisionRequest
from angee.decisions.forms import Action
from angee.decisions.states import Verdict

logger = logging.getLogger(__name__)


class NeedImportance(models.TextChoices):
    """Binary requester signal, deliberately distinct from planning priority."""

    NORMAL = "normal", "Normal"
    IMPORTANT = "important", "Important"


class NeedAccessAction(models.TextChoices):
    """Authored transitions for request access."""

    INTAKE_APPROVE = "intake.approve", "Approve"
    INTAKE_DENY = "intake.deny", "Deny"


class ApproveNeedAccess(
    Action, key=NeedAccessAction.INTAKE_APPROVE, label=NeedAccessAction.INTAKE_APPROVE.label, verdict=Verdict.COMPLETED,
):
    """Approve the request's account, with an optional explanation."""

    reason: str = ""


class DenyNeedAccess(
    Action, key=NeedAccessAction.INTAKE_DENY, label=NeedAccessAction.INTAKE_DENY.label, verdict=Verdict.REJECTED,
):
    """Decline access without changing the request's account."""

    reason: str = ""


class NeedQuerySet(AngeeQuerySet):
    """Request collections follow their stored target, including project tasks."""

    def for_project(self, project: models.Model) -> Self:
        """Include direct requests and requests on the project's tasks."""

        return self.filter(models.Q(project=project) | models.Q(task__project=project))


class NeedManager(AngeeManager.from_queryset(NeedQuerySet)):  # type: ignore[misc]
    """Own manual capture, message capture, and triage-task construction."""

    TARGET_FIELDS = {
        "projects.project": "project",
        "projects.task": "task",
    }

    def file_task(
        self, *, queue: Any, title: str, body: str, party: Any, client_creation_key: str,
        due_date: Any = None, estimate: float | None = None, importance: str = "normal",
    ) -> Any:
        """File an actor-owned task and its need atomically, replaying the whole request."""

        if not queue.has_access("read"):
            raise PermissionDenied("Queue read access is required to select a task container.")
        if not party.has_access("read"):
            raise PermissionDenied("Party read access is required to file a request.")
        task_model = apps.get_model("projects", "Task")
        actor = current_actor()
        scope = task_model.creation_key_actor_scope(actor, {})
        values = dict(queue_id=queue.pk, title=title, note=body, due_date=due_date, estimate=estimate)
        fingerprint = task_model.creation_fingerprint_for(
            {"task": values, "party": party, "importance": importance},
        )

        def insert() -> Any:
            task = task_model.objects.create(
                **values, created_by_id=scope, client_creation_key=client_creation_key,
                creation_fingerprint=fingerprint,
            )
            self.capture(target=task, party=party, body=body, importance=importance)
            return task

        with transaction.atomic():
            task, _created = task_model.objects.with_actor(actor).replay_or_insert(
                scope, client_creation_key, fingerprint, insert,
            )
            return task

    @classmethod
    def target_model(cls, model_label: str) -> type[models.Model]:
        """Return the installed model for one allowed need target label."""

        normalized = str(model_label).strip().lower()
        if normalized not in cls.TARGET_FIELDS:
            raise ValidationError({"target": "Needs may target only projects or tasks."})
        return apps.get_model(normalized)

    @classmethod
    def target_field(cls, target: models.Model) -> str:
        """Return the stored target field owned by ``target``'s model."""

        try:
            return cls.TARGET_FIELDS[target._meta.label_lower]
        except KeyError as error:
            raise ValidationError({"target": "Needs may target only projects or tasks."}) from error

    def capture(
        self,
        *,
        target: models.Model,
        body: str,
        party: models.Model | None = None,
        importance: str = "normal",
    ) -> models.Model:
        """Capture one exact manual request onto ``target`` under its write lock.

        The authored action has no separate idempotency token, so its replay
        identity is the normalized manual payload: target, body, party, and
        importance. A caller intentionally recording distinct identical evidence
        can edit the wording; ordinary action retries converge on the existing row.
        PostgreSQL makes that convergence exact with a target-row lock; SQLite
        provides the single-writer boundary through database-level serialization.
        """

        if target.pk is None:
            raise ValidationError({"target": "A saved project or task is required."})
        body = str(body or "").strip()
        if not body:
            raise ValidationError({"body": "A manual need requires a body."})
        try:
            importance_value = NeedImportance(getattr(importance, "value", importance))
        except ValueError as error:
            raise ValidationError({"importance": "Choose normal or important."}) from error

        target_field = self.target_field(target)
        target_filter = {f"{target_field}_id": target.pk}
        actor = current_actor()
        with transaction.atomic():
            with system_context(reason="intake.need.capture.lookup"):
                locked_target = type(target).objects.sudo(reason="intake.need.capture.target").locked_get(pk=target.pk)
                existing = (
                    self.model._base_manager.filter(
                        **target_filter,
                        party_id=None if party is None else party.pk,
                        body=body,
                        importance=importance_value,
                        source_message__isnull=True,
                    )
                    .order_by("pk")
                    .first()
                )
            if existing is not None:
                bind_actor(existing, actor)
                return existing

            verified_actor = self.check_create({target_field: (locked_target,)})
            need = self.model(
                **{target_field: locked_target},
                party=party,
                body=body,
                importance=importance_value,
            )
            need.full_clean(validate_unique=False, validate_constraints=False)
            if party is not None:
                need.validate_party_assignment()
            need.sudo(reason="intake.need.capture.create").save()
            bind_actor(need, verified_actor)
            return need

    def capture_from_message(
        self,
        message: models.Model,
        *,
        queue: models.Model,
        values: dict[str, Any] | None = None,
    ) -> models.Model:
        """Capture one message into triage; its clean replay no-op is SELECT-FOR-UPDATE-backed."""

        if message.pk is None or queue.pk is None:
            raise ValidationError("A saved message and intake queue are required.")
        message_model = type(message)
        with system_context(reason="intake.need.capture_message"), transaction.atomic():
            locked_message = (
                message_model.objects.sudo(reason="intake.need.capture_message.message")
                .lock_if_supported()
                .select_related("sender", "thread__title")
                .get(pk=message.pk)
            )
            existing = (
                self.model._base_manager.select_related("task").filter(source_message_id=locked_message.pk).first()
            )
            if existing is not None:
                return existing

            channel = locked_message.transport_channel(reason="intake.capture.configuration")
            mapped = dict(channel.mapped_task_values(locked_message) if values is None else values)
            claimed_name = mapped.pop("claimed_name", "")
            submission = locked_message.webform_submission()
            task = self._create_triage_task(
                queue=queue,
                title=mapped.pop("title", self._message_title(locked_message)),
                note=mapped.pop("note", str(locked_message.preview or "")),
                created_by_id=locked_message.created_by_id,
                values=mapped,
            )
            need = self.model(
                task=task,
                claimed_name=claimed_name,
                claimed_email=(submission.unverified_submitter_email or "") if submission else "",
                source_message=locked_message,
                importance=NeedImportance.NORMAL,
                created_by_id=locked_message.created_by_id,
                updated_by_id=locked_message.updated_by_id,
            )
            need.full_clean(validate_unique=False, validate_constraints=False)
            need.sudo(reason="intake.need.capture_message.create").save()
            attachment_model = apps.get_model("messaging", "ThreadAttachment")
            attachment_model.objects.bind_source_thread(task, locked_message.thread)
            try:
                # Identity refusals roll back linking, never the captured evidence.
                with transaction.atomic():
                    need.party_id = self._resolved_sender_party_id(locked_message)
                    if need.party_id is None and need.claimed_email and channel.intake_requester_domains:
                        need._link_requester(
                            allow_create=need.claimed_email.rsplit("@", 1)[-1].lower()
                            in channel.intake_requester_domains,
                            system_reason=f"intake.requester_domain:{channel.slug}",
                        )
                    if need.party_id is not None:
                        need.save(update_fields=("party",))
            except DomainError, ValidationError, PermissionDenied:
                logger.info("Requester linking was refused for need %s; capture is retained.", need.pk)
                need.refresh_from_db()
            return need

    def _create_triage_task(
        self,
        *,
        queue: models.Model,
        title: str,
        note: str,
        project: models.Model | None = None,
        created_by_id: Any = None,
        values: dict[str, Any] | None = None,
    ) -> models.Model:
        """Create one task in ``queue``'s system triage stage."""

        stage_model = apps.get_model("work", "Stage")
        triage = stage_model._base_manager.filter(queue_id=queue.pk, category="triage").first()
        if triage is None:
            raise ValidationError({"queue": "The intake queue has no triage stage."})
        task_model = apps.get_model("projects", "Task")
        title_limit = cast(int, task_model._meta.get_field("title").max_length)
        normalized_title = " ".join(str(title or "").split())[:title_limit]
        task = task_model(
            queue=queue,
            stage=triage,
            project=project,
            title=normalized_title or "Captured need",
            note=str(note or ""),
            created_by_id=created_by_id,
            updated_by_id=created_by_id,
            owner=None,
            **(values or {}),
        )
        task.allocate_ordering_ranks()
        with task._work_verb_write():
            task.full_clean(validate_unique=False, validate_constraints=False)
            task.sudo(reason="intake.need.create_triage_task").save(ownerless=True)
        return task

    @staticmethod
    def _message_title(message: models.Model) -> str:
        """Return a stable task title from the message preview or thread title."""

        preview = str(message.preview or "").strip()
        if preview:
            return preview
        thread = getattr(message, "thread", None)
        title = getattr(getattr(thread, "title", None), "text", "")
        return str(title or "Captured message")

    @staticmethod
    def _resolved_sender_party_id(message: models.Model) -> Any | None:
        """Resolve the sender through parties' matching owner and return its party id."""

        sender = message.sender
        if sender is None:
            return None
        if sender.party_id is None:
            party_handle_model = apps.get_model("parties", "PartyHandle")
            party_handle_model.objects.suggest_for(sender)
            sender.refresh_from_db(fields=("party", "party_link_confirmed"))
        return sender.party_id if sender.party_link_confirmed else None

    def reconcile_parties(self, *, apply: bool = False) -> Iterator[dict[str, Any]]:
        """Report legacy source matches; clear only untouched, unconfirmed copies."""

        candidates = (
            self.sudo(reason="intake.reconcile_parties.candidates")
            .filter(
                source_message__isnull=False,
                party__isnull=False,
            )
            .order_by("pk")
        )
        for pk in candidates.values_list("pk", flat=True).iterator():
            with system_context(reason="intake.reconcile_parties"), transaction.atomic():
                need = self.lock_if_supported().select_related("source_message__sender", "party").get(pk=pk)
                source = need.source_message
                sender = source.sender
                eligible = (
                    sender is not None
                    and not sender.party_link_confirmed
                    and need.party_id == sender.party_id
                    and need.updated_by_id == source.updated_by_id
                )
                user = apps.get_model("parties", "Party").objects.user_for(need.party)
                report = {
                    "need": need.sqid,
                    "account": user.sqid if user is not None else None,
                    "eligible": eligible,
                    "applied": apply and eligible,
                }
                if apply and eligible:
                    need.party = None
                    need.save(update_fields=("party",))
            yield report


class Need(OptimisticLockMixin, AuditMixin, AngeeDataModel):
    """One external or manually-authored request attached to one semantic target."""

    runtime = True
    sqid_prefix = "ned_"
    access_actions = (ApproveNeedAccess, DenyNeedAccess)
    # The requester's name is a sort axis through the linked party: an empty
    # name and an unreadable party both tie as NULL through the shared guard.
    hasura_aliases = {
        "filer_name": NullIf(models.F("party__display_name"), models.Value(""), output_field=models.TextField()),
    }

    party = models.ForeignKey(
        "parties.Party",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="needs",
    )
    task = models.ForeignKey(
        "projects.Task",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="needs",
    )
    project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="needs",
    )
    claimed_name = models.TextField(blank=True, default="", editable=False)
    claimed_email = models.TextField(blank=True, default="", editable=False)
    access_decision = models.ForeignKey(
        "decisions.Decision", null=True, blank=True, editable=False,
        on_delete=models.PROTECT, related_name="access_needs",
    )
    importance = StateField(choices_enum=NeedImportance, default=NeedImportance.NORMAL)
    body = models.TextField(blank=True, default="")
    source_message = models.ForeignKey(
        "messaging.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="captured_need",
    )
    original_task = models.ForeignKey(
        "projects.Task",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="former_needs",
    )

    objects = NeedManager()

    class Meta:
        """Django model options for request evidence."""

        abstract = True
        ordering = ("-importance", "-created_at", "sqid")
        rebac_resource_type = "intake/need"
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(task__isnull=False, project__isnull=True)
                    | models.Q(task__isnull=True, project__isnull=False)
                ),
                name="ck_intake_need_exactly_one_target",
            ),
            models.UniqueConstraint(
                fields=("source_message",),
                condition=models.Q(source_message__isnull=False),
                name="uq_intake_need_source_message",
            ),
        )
        indexes = (
            models.Index(fields=("project", "importance")),
            models.Index(fields=("task", "importance")),
            models.Index(fields=("party", "importance")),
        )

    @property
    def access_verdict(self) -> str | None:
        """Project the readable seat's outcome without storing a second verdict."""
        return self.access_decision.verdict if self.access_decision is not None else None

    @property
    def access_resolution(self) -> dict[str, Any] | None:
        """Project the readable seat's tagged answer."""
        return self.access_decision.resolution if self.access_decision is not None else None

    @property
    def access_resolved_by_id(self) -> Any:
        """Project the readable seat's resolver identity for server-side consumers."""
        return self.access_decision.resolved_by_id if self.access_decision is not None else None

    @property
    def access_resolved_by(self) -> Any:
        """Project the readable seat's resolver."""
        return self.access_decision.resolved_by if self.access_decision is not None else None

    @property
    def access_resolved_at(self) -> Any:
        """Project the readable seat's retained answer time."""
        return self.access_decision.resolved_at if self.access_decision is not None else None

    def _new_access_decision(self) -> Any:
        """Admit a system-requested seat whose live assignees are target sharers."""
        with system_context(reason="intake.need.access_question"):
            decisions = apps.get_model("decisions", "Decision").objects
            if self.access_decision_id is not None and self.access_decision.group.settled_at is not None:
                group = decisions.reask(
                    self.access_decision.group_id, actor=None, actions=self.access_actions, errors={},
                )
            else:
                group = decisions.admit_group((DecisionRequest(
                    kind="intake.access", subject=self, assignees=None,
                    actions=self.access_actions, requester=None, supersede=True,
                ),), actor=None, policy="first")
            return group.decisions.get(index=0)

    @property
    def target(self) -> Any:
        """Load the target for server-side validation, without exposing its projection."""

        field = self._meta.get_field("task" if self.task_id is not None else "project")
        return field.remote_field.model._base_manager.get(pk=getattr(self, field.attname))

    def clean(self) -> None:
        """Reject missing or double targets; a task's project is never copied."""

        super().clean()
        if (self.task_id is None) == (self.project_id is None):
            raise ValidationError("Exactly one of task or project must be targeted.")

    def validate_party_assignment(self) -> None:
        """Check a hand-authored share and every new holder before saving it."""

        actor, elevated = self.effective_actor()
        if not elevated and not self.target.with_actor(actor).has_access("share"):
            raise PermissionDenied("Setting a request's party requires target share permission.")
        if self.party_id is not None:
            user = self._account_for_party(self.party_id)
            if user is not None:
                self.validate_record_access_subject("party", user)

    def _account_for_party(self, party_id: Any) -> Any | None:
        """Resolve an assignment through the parties identity owner."""

        if party_id is None:
            return None
        party_model = self._meta.get_field("party").remote_field.model
        with system_context(reason="intake.need.party_subject"):
            return party_model.objects.user_for(party_model._base_manager.get(pk=party_id))

    def validate_record_access_subject(self, relation: str, subject: Any) -> None:
        """A request's holder must also satisfy the target's holder invariant."""

        super().validate_record_access_subject(relation, subject)
        self.target.validate_record_access_subject(relation, subject)

    def save(self, *, _access_decision: bool = False, **kwargs: Any) -> None:
        """Validate assignment, reset its old decision, and follow atomically.

        The access verbs supply ``_access_decision`` after admitting or answering
        their decision, so assignment persistence does not replace that seat.
        Mixed edits stamp audit and revision once through the actor before the
        assignment-only ``save_base`` persists the authorized relation change.
        """

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            kwargs["update_fields"] = update_fields
            if not update_fields or (
                not self._state.adding
                and not update_fields.intersection(
                    {
                        "task",
                        "task_id",
                        "project",
                        "project_id",
                        "party",
                        "party_id",
                    }
                )
            ):
                super().save(**kwargs)
                return
        with transaction.atomic():
            previous = (
                None
                if self._state.adding
                else (
                    type(self)
                    .objects.sudo(reason="intake.need.assignment_before")
                    .lock_if_supported()
                    .filter(pk=self.pk)
                    .values("task_id", "project_id", "party_id")
                    .first()
                )
            )
            values = {
                name: getattr(self, name)
                if previous is None or update_fields is None or {name, name.removesuffix("_id")} & update_fields
                else previous[name]
                for name in ("task_id", "project_id", "party_id")
            }
            candidate = type(self)(pk=self.pk, **values)
            candidate.clean()
            assignment_changed = values != previous and (
                values["party_id"] is not None or (previous is not None and previous["party_id"] is not None)
            )
            if assignment_changed:
                actor, elevated = self.effective_actor()
                if elevated:
                    candidate.sudo(reason="intake.need.assignment")
                else:
                    candidate.with_actor(actor)
                candidate.validate_party_assignment()
            reset_decision = False
            if previous is not None and values["party_id"] != previous["party_id"] and not _access_decision:
                old_user = self._account_for_party(previous["party_id"])
                new_user = self._account_for_party(values["party_id"])
                reset_decision = values["party_id"] is None or old_user != new_user
            if assignment_changed and not elevated:
                if previous is not None and not self.has_access("share"):
                    raise PermissionDenied("Changing a request's assignment requires need share permission.")
                assignment_fields = {
                    name for attname in values for name in (attname, attname.removesuffix("_id"))
                }
                separate_assignment = (
                    previous is None or update_fields is None or update_fields - assignment_fields - {"updated_at"}
                )
                if separate_assignment:
                    # Keep ordinary fields under their actor gates. Inserts first
                    # establish the target without assigning a party.
                    before = previous or {**values, "party_id": None}
                    for name, value in before.items():
                        setattr(self, name, value)
                    try:
                        super().save(**kwargs)
                    finally:
                        for name, value in values.items():
                            setattr(self, name, value)
                with actor_context(actor):
                    self.sudo(reason="intake.assign_party")
                    try:
                        if separate_assignment:
                            self.save_base(force_update=True, update_fields=set(values))
                        else:
                            super().save(**{**kwargs, "update_fields": set(values)})
                    finally:
                        self.with_actor(actor)
            else:
                super().save(**kwargs)
            if previous is None or reset_decision:
                # The row must exist before its decision's subject can reference it.
                # This owner-controlled FK write is part of the same save/revision.
                self.access_decision = self._new_access_decision()
                system_queryset(type(self)).filter(pk=self.pk).update(access_decision=self.access_decision)
            if reset_decision:
                apps.get_model("messaging", "ThreadFollower").objects.end_unreadable_for_record(self.target)
            if assignment_changed and values["task_id"] is not None and values["party_id"] is not None:
                with system_context(reason="intake.need.follow_requester"):
                    account = candidate._account_for_party(values["party_id"])
                    if account is None or account.kind != "person" or candidate.target.thread_reader_allowed(account):
                        candidate.target.message_subscribe(party=candidate.party)

    def _link_requester(self, *, allow_create: bool, system_reason: str | None = None) -> Any:
        """Resolve and assign the account; the caller persists it atomically.

        Ingress uses its channel's authorization and a named IAM system reason.
        The decision uses IAM's actor-gated factory. No credential or membership
        is written, and an existing person is never attached to a user.
        """

        users = get_user_model().objects
        existing = self._account_for_party(self.party_id)
        if existing is not None and not existing.is_active:
            raise ValidationError({"conflict": "The assigned account is inactive."})
        if existing is not None:
            return existing
        email = users.normalize_email(self.claimed_email)
        if not email:
            raise ValidationError({"conflict": "This request has no claimed email."})
        validate_email(email)
        user = users.person_for_email(email)
        if user is not None and not user.is_active:
            raise ValidationError({"conflict": "The matching account is inactive."})
        if user is None:
            if not allow_create:
                return None
            if system_reason is None:
                user = users.create_person(username=email, email=email)
            else:
                user = users.create_person_as_system(username=email, email=email, reason=system_reason)
        with system_context(reason="intake.need.link_requester"):
            party = apps.get_model("parties", "Party").objects.for_user(user)
            handle = apps.get_model("parties", "Handle").objects.upsert(platform="email", value=email)
            apps.get_model("parties", "PartyHandle").objects.link(party, handle, is_confirmed=False)
            self.party = party
        return user

    def decide_access(self, action: str, reason: str = "", expected_revision: int | None = None) -> Any:
        """Link the account and delegate the final answer to the decisions owner."""

        return self._decide_access(action, {"reason": reason}, expected_revision=expected_revision)

    @transaction.atomic
    def admit_requester(self, user: models.Model) -> Any:
        """Assign, approve, and follow one person through the existing decision owner."""

        if getattr(user, "kind", None) != "person":
            raise ValidationError({"user": "Choose a person account."})
        with system_context(reason="intake.need.admit_requester.party"):
            party = apps.get_model("parties", "Party").objects.for_user(user)
        self.party = party
        self.save(update_fields=("party", "updated_at"))
        approved = self.decide_access(NeedAccessAction.INTAKE_APPROVE)
        if approved is None or not self.target.thread_reader_allowed(user):
            raise PermissionDenied("The admitted requester must be able to read the record.")
        with system_context(reason="intake.need.admit_requester.follow"):
            self.target.message_subscribe(user=user)
        return approved

    @transaction.atomic
    def remove_requester(self) -> None:
        """Remove the requester seat and its decision-backed read together."""

        self.party = None
        self.save(update_fields=("party", "updated_at"))

    def reset_access(self, *, confirmed: bool, expected_revision: int) -> Any:
        """Retain requester identity and supersede its access answer atomically.

        A fresh pending decision revokes decision-backed requester access. Account
        credentials and the previous decision's audit history remain with their
        existing owners.
        """

        if not confirmed:
            raise ValidationError({"confirmed": "Confirm resetting requester access."})
        actor = instance_actor(self)
        with actor_context(actor), transaction.atomic():
            locked = system_queryset(type(self), lock=("self",)).get(pk=self.pk)
            if not locked.with_actor(actor).has_access("write") or not locked.target.with_actor(actor).has_access(
                "share",
            ):
                raise PermissionDenied("Resetting access requires need write and target share.")
            locked.require_revision(expected_revision)
            locked.access_decision = locked._new_access_decision()
            locked.save(
                _access_decision=True, expected_revision=expected_revision,
                update_fields=("access_decision", "updated_at"),
            )
            apps.get_model("messaging", "ThreadFollower").objects.end_unreadable_for_record(locked.target)
        self.refresh_from_db()
        return self

    def _decide_access(
        self, action: str, values: dict[str, Any], *,
        expected_revision: int | None = None, decision: Any = None, decision_revision: int | None = None,
    ) -> Any:
        """Orchestrate both request and inbox entrypoints under the same request lock."""
        action_model = next((model for model in self.access_actions if model.key == action), None)
        if action_model is None:
            raise ValidationError({"action": "Choose approve or deny."})
        actor = instance_actor(self)
        with actor_context(actor), transaction.atomic():
            locked = type(self).objects.sudo(reason="intake.need.decide_access.lock").locked_get(pk=self.pk)
            # Authorization uses the actor; the locked copy retains unredacted facts.
            if not self.with_actor(actor).has_access("write") or not locked.target.with_actor(actor).has_access(
                "share"
            ):
                raise PermissionDenied("Deciding request access requires need write and target share.")
            if action == NeedAccessAction.INTAKE_APPROVE and locked._account_for_party(locked.party_id) is None:
                get_user_model().objects.check_create()
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if decision is not None:
                if locked.access_decision_id != decision.pk:
                    raise ValidationError({"revision": "The access question has changed; reload it."})
                locked.access_decision.require_revision(decision_revision)
            verdict = action_model.verdict
            if locked.access_verdict == verdict:
                if action == NeedAccessAction.INTAKE_DENY:
                    return None
                return locked._account_for_party(locked.party_id)
            if locked.access_verdict == Verdict.COMPLETED:
                raise ValidationError({"action": "Approved access is final."})
            user = None
            if action == NeedAccessAction.INTAKE_APPROVE:
                try:
                    user = locked._link_requester(allow_create=True)
                except ValidationError:
                    raise
                except DomainError as error:
                    raise ValidationError({"conflict": error.code}) from error
            if not locked.access_decision.is_open:
                locked.access_decision = locked._new_access_decision()
            decision = locked.access_decision
            apps.get_model("decisions", "Decision").objects.decide(
                decision.pk, actor=actor, revision=decision.revision,
                action=action, values=values,
            )
            locked.save(
                _access_decision=True, expected_revision=expected_revision,
                update_fields=("party", "access_decision"),
            )
            if (
                action == NeedAccessAction.INTAKE_APPROVE
                and user is not None
                and getattr(user, "kind", None) == "person"
            ):
                with system_context(reason="intake.need.follow_approved_requester"):
                    if locked.target.thread_reader_allowed(user):
                        locked.target.message_subscribe(user=user)
        self.refresh_from_db()
        return user

    def convert_to_task(self, queue: models.Model) -> models.Model:
        """Return this need's task; its clean concurrent no-op is SELECT-FOR-UPDATE-backed."""

        if self.pk is None or queue.pk is None:
            raise ValidationError("A saved need and queue are required for conversion.")
        actor = current_actor()
        with system_context(reason="intake.need.convert_to_task"), transaction.atomic():
            locked = (
                type(self)
                .objects.sudo(reason="intake.need.convert_to_task.need")
                .lock_if_supported()
                .select_related("task", "project", "source_message")
                .get(pk=self.pk)
            )
            if locked.task_id is not None:
                task = locked.task
            else:
                title = locked.body or getattr(locked.source_message, "preview", "")
                task = type(self).objects._create_triage_task(
                    queue=queue,
                    project=locked.project,
                    title=title,
                    note=locked.body,
                    created_by_id=locked.created_by_id,
                )
                locked.task = task
                locked.project = None
                locked.save(update_fields=("task", "project", "updated_at"))
        bind_actor(task, actor)
        self.refresh_from_db()
        return task


class ProjectIntakeSetup(models.Model):
    """Link unassigned request parties before downstream setup admits people."""

    extends = "projects.Project"

    class Meta:
        abstract = True

    def apply_setup(self, *, party: Any | None = None, **options: Any) -> None:
        """Retain existing request identities; a new assignment uses its share gate."""

        need_model = apps.get_model("intake", "Need")
        needs = need_model.objects.filter(task_id=self.converted_from_id, party__isnull=True)
        for need in needs:
            if party is None:
                raise ValidationError({"party": "Choose a party for the unassigned request."})
            need.party = party
            need.save(update_fields=("party", "updated_at"))
        super().apply_setup(**options)


class ChannelIntake(models.Model):
    """Same-row intake fields and capture behavior for ``messaging.Channel``."""

    extends = "messaging.Channel"

    class IntakeTrigger(models.TextChoices):
        """Declared capture triggers; only all-messages is implemented in v1."""

        ALL_MESSAGES = "all_messages", "All messages"
        REACTION = "reaction", "Reaction"
        MENTION = "mention", "Mention"
        COMMAND = "command", "Command"

    hasura_readable_fields = ("intake_queue", "intake_trigger", "intake_field_map", "intake_requester_domains")
    hasura_filterable_fields = ("intake_queue", "intake_trigger")
    hasura_sortable_fields = hasura_filterable_fields
    hasura_aggregatable_fields: tuple[str, ...] = ()
    hasura_groupable_fields = hasura_filterable_fields
    hasura_updatable_fields = hasura_readable_fields

    intake_queue = models.ForeignKey(
        "work.Queue",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_channels",
    )
    intake_trigger = StateField(
        choices_enum=IntakeTrigger,
        default=IntakeTrigger.ALL_MESSAGES,
    )
    intake_field_map = models.JSONField(default=dict, blank=True)
    intake_requester_domains = models.JSONField(default=list, blank=True)

    intake_map_targets = frozenset({"title", "note", "due_date", "priority", "estimate", "claimed_name"})

    class Meta:
        """Abstract contribution folded into the concrete channel table."""

        abstract = True

    def clean(self) -> None:
        """Validate mappings through messaging's form spec and authorize domains."""

        super().clean()
        mapping = self.intake_field_map
        if not isinstance(mapping, dict):
            raise ValidationError({"intake_field_map": "Use an object mapping target fields to form properties."})
        if mapping:
            if str(self.backend_class) != "webform":
                raise ValidationError({"intake_field_map": "Field mapping requires the webform backend."})
            properties = {field.name: field for field in self.webform_spec().fields}
            used: set[str] = set()
            for target, name in mapping.items():
                field = properties.get(name) if isinstance(name, str) else None
                kinds = {"number", "integer"} if target == "estimate" else {"string"}
                if target not in self.intake_map_targets:
                    raise ValidationError({"intake_field_map": f"Unsupported target field: {target}."})
                if field is None or field.read_only or field.email:
                    raise ValidationError({"intake_field_map": f"{target} requires a writable, non-email property."})
                if field.kind not in kinds:
                    raise ValidationError({"intake_field_map": f"The form property has the wrong kind for {target}."})
                if name in used:
                    raise ValidationError({"intake_field_map": "Map each form property only once."})
                used.add(name)
        self._validate_requester_domains()

    def _validate_requester_domains(self) -> None:
        """A domain configuration is ingress authorization, checked before writing."""

        domains = self.intake_requester_domains
        if not isinstance(domains, list):
            raise ValidationError({"intake_requester_domains": "Use a list of lower-case domains."})
        if domains and str(self.backend_class) != "webform":
            raise ValidationError({"intake_requester_domains": "Requester domains require the webform backend."})
        for domain in domains:
            if not isinstance(domain, str) or domain != domain.strip().lower():
                raise ValidationError({"intake_requester_domains": "Use lower-case domains without whitespace."})
            try:
                DomainNameValidator()(domain)
            except ValidationError as error:
                raise ValidationError({"intake_requester_domains": "Enter valid domain names."}) from error
        previous = (
            []
            if self._state.adding
            else type(self)
            ._base_manager.filter(pk=self.pk)
            .values_list(
                "intake_requester_domains",
                flat=True,
            )
            .get()
        )
        if previous != domains:
            actor, elevated = self.effective_actor()
            if not elevated:
                if not self.has_access("write"):
                    raise PermissionDenied("Configuring requester domains requires channel write permission.")
                with actor_context(actor):
                    get_user_model().objects.check_create()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Protect the ingress setting even for writers that do not call full_clean."""

        update_fields = kwargs.get("update_fields")
        if self._state.adding or update_fields is None or "intake_requester_domains" in update_fields:
            self._validate_requester_domains()
        super().save(*args, **kwargs)

    def mapped_task_values(self, message: models.Model) -> dict[str, Any]:
        """Clean each mapped answer through its field; malformed answers fall back."""

        submission = message.webform_submission()
        if submission is None or not isinstance(self.intake_field_map, dict):
            return {}
        task_model = apps.get_model("projects", "Task")
        need_model = apps.get_model("intake", "Need")
        values: dict[str, Any] = {}
        for target, name in self.intake_field_map.items():
            if target not in self.intake_map_targets or not isinstance(name, str):
                continue
            value = submission.answers.get(name)
            if value is None or value == "":
                continue
            model = need_model if target == "claimed_name" else task_model
            try:
                values[target] = model._meta.get_field(target).clean(value, model())
            except ValidationError, TypeError, ValueError, OverflowError:
                logger.warning("Invalid intake value for %s on message %s; using its default.", target, message.pk)
        return values

    def should_capture_message(self, message: models.Model) -> bool:
        """Return whether this channel's configured v1 trigger accepts ``message``."""

        if self.intake_queue_id is None:
            return False
        match str(self.intake_trigger):
            case self.IntakeTrigger.ALL_MESSAGES:
                return True
            case self.IntakeTrigger.REACTION | self.IntakeTrigger.MENTION | self.IntakeTrigger.COMMAND:
                return False
            case _:
                raise ValidationError({"intake_trigger": "Unknown intake trigger."})

    def capture_ingested_message(self, message: models.Model) -> models.Model | None:
        """Dispatch an accepted ingested message to the Need write owner."""

        if not self.should_capture_message(message):
            return None
        need_model = apps.get_model("intake", "Need")
        return need_model.objects.capture_from_message(message, queue=self.intake_queue)


class DecisionIntake(models.Model):
    """Bind intake seats to their request for live, declared sharer authority."""

    extends = "decisions.Decision"
    hasura_filterable_fields = ("intake_need__task",)
    intake_need = models.ForeignKey(
        "intake.Need", null=True, blank=True, editable=False,
        on_delete=models.SET_NULL, related_name="access_decisions",
    )

    class Meta:
        abstract = True

    def decide(self, *, actor: Any, revision: int, action: str, values: dict[str, Any]) -> Any:
        """Keep inbox answers inside intake's authorized account-linking transaction."""
        if self.intake_need_id is None:
            return super().decide(actor=actor, revision=revision, action=action, values=values)
        need = self.intake_need.with_actor(actor)
        need._decide_access(action, values, decision=self, decision_revision=revision)
        return need.access_decision

    def save(self, **kwargs: Any) -> None:
        """Bind the domain's subject once, during the decision owner's admission."""
        if self._state.adding and self.kind == "intake.access":
            if self.subject_content_type.model_class() is not apps.get_model("intake", "Need"):
                raise ValidationError("An intake access decision must name a need.")
            self.intake_need_id = self.subject_object_id
        super().save(**kwargs)
