"""Request evidence targeting exactly one task or project, and channel capture."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from types import NotImplementedType
from typing import TYPE_CHECKING, Any, ClassVar, Final

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import DomainNameValidator, validate_email
from django.db import models, router, transaction
from django.db.models.expressions import BaseExpression
from django_compose.models import Composable
from rebac import (
    PermissionDenied,
    actor_context,
    check_new,
    current_actor,
    database_context,
    system_context,
    to_object_ref,
    user_subject_type,
)
from rebac.backends import backend
from rebac.resources import model_resource_type

from angee.base.exceptions import DomainError, RecordAccessSubjectRefused
from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.base.models.locks import OptimisticLockMixin
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.iam.models.base import ProtectedDataModel
from angee.iam.permissions import actor_user_id, instance_actor
from angee.intake.choices import NeedAccessAction, NeedImportance
from angee.intake.managers import NeedManager
from angee.intake.querysets import (
    _requested_by_viewer_filter,
    _requester_name_filter,
    filer_name_expression,
)
from angee.intake.resources import NeedResource
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.resources.mixins import ResourceLoadMixin

if TYPE_CHECKING:
    from angee.messaging.models import Message
    from angee.projects.models import Project, Task
    from angee.work.models import Queue


logger = logging.getLogger(__name__)


def _require_user_create() -> None:
    """Require the ambient actor to pass the native account-create gate."""

    actor = current_actor()
    resource_type = model_resource_type(get_user_model())
    if (
        actor is None
        or resource_type is None
        or not check_new(
            subject=actor,
            action="create",
            resource_type=resource_type,
        ).allowed
    ):
        raise PermissionDenied("Account create access is required.")


class Need(ResourceLoadMixin, HistoryMixin, OptimisticLockMixin, AuditMixin, ProtectedDataModel, concrete=True):
    """One external or manually-authored request attached to one semantic target."""

    resource_class = NeedResource
    sqid_prefix = "ned_"
    # The Need owner's projection supplies the same display name to filtering
    # and ordering, preserving the project-filed party gate.
    hasura_filter_expressions: ClassVar[Mapping[str, Callable[[models.QuerySet[Any]], BaseExpression]]] = {
        "filer_name": filer_name_expression,
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
        "decisions.Decision",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.PROTECT,
        related_name="access_needs",
    )
    admitted_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.PROTECT,
        related_name="+",
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
            # Each task's first request (task_requester_rows) is an ordered LIMIT 1 probe.
            models.Index(fields=("task", "created_at", "id")),
        )

    @classmethod
    def merge_contract(cls, reference: Any, rows: Any, *, survivor: Any) -> NotImplementedType | None:
        """Retarget requests through their assignment owner."""

        if reference.name != "task":
            return NotImplemented
        for need in rows.order_by("pk"):
            need.task = survivor
            try:
                need.save(update_fields=("task", "updated_at"))
            except RecordAccessSubjectRefused as error:
                raise ValidationError({"canonical": error.code}) from error
        return None

    def _lock_access_question(self, *, using: str | None = None) -> Any:
        """Lock the observed question before its Need; callers recheck the FK."""

        using = using or self._state.db
        decision_id = (
            type(self)
            .system_queryset()
            .using(using)
            .filter(pk=self.pk)
            .values_list(
                "access_decision_id",
                flat=True,
            )
            .get()
        )
        if decision_id is not None:
            apps.get_model("decisions", "Decision").objects.db_manager(using).sudo(
                reason="intake.need.assignment_question",
            ).locked_get(pk=decision_id)
        return decision_id

    @property
    def requester_access_granted(self) -> bool:
        """The request owner has applied approval to this account."""
        return self.admitted_user_id is not None

    @property
    def access_verdict(self) -> list[str] | None:
        return self.access_decision.verdict if self.access_decision is not None else None

    @property
    def access_answered_by(self) -> Any:
        """Project the readable decision's answerer."""
        return self.access_decision.answered_by if self.access_decision is not None else None

    @property
    def access_answered_at(self) -> Any:
        """Project the readable decision's retained answer time."""
        return self.access_decision.answered_at if self.access_decision is not None else None

    def _withdraw_access_decision(self, decision_id: Any, *, actor: Any) -> None:
        """Close a replaced unanswered question through its existing lifecycle owner."""

        if decision_id is not None:
            with system_context(reason="intake.need.replace_access_question"):
                (
                    apps.get_model("decisions", "Decision")
                    .objects.db_manager(self._state.db)
                    .get(pk=decision_id)
                    .withdraw(actor=actor)
                )

    def _new_access_decision(self) -> Any:
        """Ask one question assigned to the target's current sharers."""
        using = self._state.db or router.db_for_write(type(self), instance=self)
        with database_context(using), system_context(reason="intake.need.access_question"):
            readers = backend().lookup_subjects(
                resource=to_object_ref(self),
                action="share",
                subject_type=user_subject_type(),
            )
            assignees = tuple(
                get_user_model()
                .objects.db_manager(using)
                .filter(pk__in=[actor_user_id(ref) for ref in readers], is_active=True)
            )
            return (
                apps.get_model("decisions", "Decision")
                .objects.db_manager(using)
                .ask(
                    DecisionRequest(
                        kind="intake.access",
                        records=(self,),
                        assignees=assignees,
                        requester=None,
                        proposal=DecisionProposal.model_validate(
                            {
                                "alternatives": (
                                    {
                                        "key": NeedAccessAction.INTAKE_APPROVE,
                                        "label": "Approve access",
                                        "outcome": "approved",
                                    },
                                    {"key": NeedAccessAction.INTAKE_DENY, "label": "Deny access", "outcome": "denied"},
                                )
                            }
                        ),
                    ),
                    actor=None,
                )
            )

    @property
    def target(self) -> Task | Project:
        """Load the target for server-side validation, without exposing its projection."""

        if self.task_id is not None:
            return type(self).task.field.related_model._base_manager.using(self._state.db).get(pk=self.task_id)
        assert self.project_id is not None
        return type(self).project.field.related_model._base_manager.using(self._state.db).get(pk=self.project_id)

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

    def _account_for_party(self, party_id: Any, *, using: str | None = None) -> Any | None:
        """Resolve an assignment through the parties identity owner."""

        if party_id is None:
            return None
        party_model = type(self).party.field.related_model
        using = using or self._state.db
        with system_context(reason="intake.need.party_subject"):
            return party_model.objects.db_manager(using).user_for(
                party_model._base_manager.using(using).get(pk=party_id),
            )

    def validate_record_access_subject(self, relation: str, subject: Any) -> None:
        """A request's holder must also satisfy the target's holder invariant."""

        super().validate_record_access_subject(relation, subject)
        self.target.validate_record_access_subject(relation, subject)

    def save(self, *, _access_decision: bool = False, **kwargs: Any) -> None:
        """Validate assignment, reset its old decision, and follow atomically.

        Capture and access verbs supply ``_access_decision`` when they own question
        creation or answering, so assignment persistence does not replace it.
        Mixed edits stamp audit and revision once through the actor before the
        assignment-only ``save_base`` persists the authorized relation change.
        """

        using = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        kwargs["using"] = using
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
        with database_context(using), transaction.atomic(using=using):
            observed_decision = None
            if not self._state.adding:
                observed_decision = self._lock_access_question(using=using)
            previous: Mapping[str, object] | None = (
                None
                if self._state.adding
                else (
                    type(self)
                    .objects.db_manager(using)
                    .sudo(reason="intake.need.assignment_before")
                    .lock_if_supported()
                    .filter(pk=self.pk)
                    .values("task_id", "project_id", "party_id", "access_decision_id")
                    .first()
                )
            )
            if previous is not None and previous["access_decision_id"] != observed_decision:
                raise ValidationError({"revision": "The access question has changed; reload it."})
            assignment_names: Final = ("task_id", "project_id", "party_id")
            values = {
                name: getattr(self, name)
                if previous is None or update_fields is None or {name, name.removesuffix("_id")} & update_fields
                else previous[name]
                for name in assignment_names
            }
            candidate = type(self)(pk=self.pk, **values)
            candidate._state.db = using
            candidate.clean()
            assignment_changed = (
                previous is None or any(values[name] != previous[name] for name in assignment_names)
            ) and (values["party_id"] is not None or (previous is not None and previous["party_id"] is not None))
            if assignment_changed:
                actor, elevated = self.effective_actor()
                if elevated:
                    candidate.sudo(reason="intake.need.assignment")
                else:
                    candidate.with_actor(actor)
                candidate.validate_party_assignment()
            reset_decision = False
            if previous is not None and values["party_id"] != previous["party_id"] and not _access_decision:
                old_user = self._account_for_party(previous["party_id"], using=using)
                new_user = self._account_for_party(values["party_id"], using=using)
                reset_decision = values["party_id"] is None or old_user != new_user
            if assignment_changed and not elevated:
                if previous is not None and not self.has_access("share"):
                    raise PermissionDenied("Changing a request's assignment requires need share permission.")
                assignment_fields = {name for attname in values for name in (attname, attname.removesuffix("_id"))}
                separate_assignment = (
                    previous is None or update_fields is None or update_fields - assignment_fields - {"updated_at"}
                )
                if separate_assignment:
                    # Keep ordinary fields under their actor gates. Inserts first
                    # establish the target without assigning a party.
                    before: Mapping[str, object] = previous or {**values, "party_id": None}
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
                            self.save_base(using=using, force_update=True, update_fields=set(values))
                        else:
                            super().save(**{**kwargs, "update_fields": set(values)})
                    finally:
                        self.with_actor(actor)
            else:
                super().save(**kwargs)
            if (previous is None and not _access_decision) or reset_decision:
                # The row must exist before its decision can concern it.
                # This owner-controlled FK write is part of the same save/revision.
                self.access_decision = self._new_access_decision()
                self.admitted_user = None
                type(self).system_queryset().using(using).filter(pk=self.pk).update(
                    access_decision=self.access_decision,
                    admitted_user=None,
                )
            if reset_decision and previous is not None:
                self._withdraw_access_decision(previous["access_decision_id"], actor=instance_actor(self))
            if reset_decision:
                apps.get_model("messaging", "ThreadFollower").objects.db_manager(using).end_unreadable_for_record(
                    self.target
                )
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

        users = get_user_model().objects.db_manager(self._state.db)
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
            party = apps.get_model("parties", "Party").objects.db_manager(self._state.db).for_user(user)
            handle = (
                apps.get_model("parties", "Handle")
                .objects.db_manager(self._state.db)
                .upsert(platform="email", value=email)
            )
            # A claimed address is unverified: parties' claim owner records it for
            # review, with this request as its readable evidence, and it decides
            # nothing. Both callers' gates already authorized it, so it runs as their
            # system work, never as a decider's write on the requester's party.
            apps.get_model("parties", "Party").objects.db_manager(self._state.db).propose_claimed_handle_as_system(
                party, handle, evidence=self, reason="intake.need.link_requester"
            )
            self.party = party
        return user

    def decide_access(self, action: str, expected_revision: int | None = None) -> Any:
        """Link the account and delegate the final answer to the decisions owner."""

        if action not in NeedAccessAction.values:
            raise ValidationError({"action": "Choose approve or deny."})
        actor = instance_actor(self)
        if not self.with_actor(actor).has_access("write") or not self.target.with_actor(actor).has_access("share"):
            raise PermissionDenied("Deciding request access requires need write and target share.")
        # The question FK is redacted for actors without share permission.
        if self.access_decision_id is None:
            raise ValidationError({"action": "This need has no access question; create a new access request."})
        # Both entrypoints lock the decision first, then its asking owner.
        using = self._state.db or router.db_for_write(type(self), instance=self)
        with database_context(using), actor_context(actor), transaction.atomic(using=using):
            decision = (
                apps.get_model("decisions", "Decision")
                .objects.db_manager(using)
                .sudo(
                    reason="intake.answer.lock",
                )
                .locked_get(pk=self.access_decision_id)
            )
            locked = type(self).objects.db_manager(using).sudo(reason="intake.answer.need").locked_get(pk=self.pk)
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if locked.access_decision_id != decision.pk:
                raise ValidationError({"revision": "The access question has changed; reload it."})
            if not locked.with_actor(actor).has_access("write") or not locked.target.with_actor(actor).has_access(
                "share"
            ):
                raise PermissionDenied("Deciding request access requires need write and target share.")
            if not decision.is_open:
                if decision.verdict == [action]:
                    self.refresh_from_db()
                    return locked.admitted_user
                if decision.verdict == [NeedAccessAction.INTAKE_APPROVE]:
                    raise ValidationError({"action": "Approved access is final."})
                decision = locked._new_access_decision()
                type(self).objects.db_manager(using).filter(pk=locked.pk).update(access_decision=decision)
            decision = decision.decide(actor=actor, chosen=[action], revision=decision.revision)
        self.refresh_from_db()
        return self.admitted_user

    def apply_access_answer(self, decision: Any) -> Any:
        """Consume this question only while it remains this request's current one."""
        if not decision.verdict:
            return None
        return self._decide_access(
            decision.verdict[0],
            decision=decision,
            decision_revision=decision.revision,
        )

    def admit_requester(self, user: models.Model) -> Any:
        """Assign, approve, and follow one person through the existing decision owner."""

        using = self._state.db or router.db_for_write(type(self), instance=self)
        if user._state.db != using:
            raise ValueError("A request and its admitted user must use the same database.")
        with database_context(using), transaction.atomic(using=using):
            if getattr(user, "kind", None) != "person":
                raise ValidationError({"user": "Choose a person account."})
            with system_context(reason="intake.need.admit_requester.party"):
                party = apps.get_model("parties", "Party").objects.db_manager(self._state.db).for_user(user)
            self.party = party
            self.save(update_fields=("party", "updated_at"))
            approved = self.decide_access(str(NeedAccessAction.INTAKE_APPROVE))
            if approved is None or not self.target.thread_reader_allowed(user):
                raise PermissionDenied("The admitted requester must be able to read the record.")
            with system_context(reason="intake.need.admit_requester.follow"):
                self.target.message_subscribe(user=user)
            return approved

    def remove_requester(self) -> None:
        """Remove the requester seat and its decision-backed read together."""

        using = self._state.db or router.db_for_write(type(self), instance=self)
        with database_context(using), transaction.atomic(using=using):
            self.party = None
            self.save(update_fields=("party", "updated_at"))

    def reset_access(self, *, confirmed: bool, expected_revision: int) -> Any:
        """Retain requester identity and ask a fresh independent access question.

        A fresh pending decision revokes decision-backed requester access. Account
        credentials and the previous decision's audit history remain with their
        existing owners.
        """

        if not confirmed:
            raise ValidationError({"confirmed": "Confirm resetting requester access."})
        actor = instance_actor(self)
        using = self._state.db or router.db_for_write(type(self), instance=self)
        with database_context(using), actor_context(actor), transaction.atomic(using=using):
            observed_decision = self._lock_access_question()
            locked = type(self).system_queryset(lock=("self",)).using(using).get(pk=self.pk)
            if locked.access_decision_id != observed_decision:
                raise ValidationError({"revision": "The access question has changed; reload it."})
            if not locked.with_actor(actor).has_access("write") or not locked.target.with_actor(actor).has_access(
                "share",
            ):
                raise PermissionDenied("Resetting access requires need write and target share.")
            locked.require_revision(expected_revision)
            decision = locked._new_access_decision()
            locked.admitted_user = None
            locked.save(
                _access_decision=True,
                expected_revision=expected_revision,
                update_fields=("admitted_user", "updated_at"),
            )
            type(self).system_queryset().using(using).filter(pk=locked.pk).update(access_decision=decision)
            locked._withdraw_access_decision(observed_decision, actor=actor)
            apps.get_model("messaging", "ThreadFollower").objects.db_manager(using).end_unreadable_for_record(
                locked.target
            )
        self.refresh_from_db()
        return self

    def _decide_access(
        self,
        action: str,
        *,
        expected_revision: int | None = None,
        decision: Any = None,
        decision_revision: int | None = None,
    ) -> Any:
        """Orchestrate both request and inbox entrypoints under the same request lock."""
        if action not in NeedAccessAction.values:
            raise ValidationError({"action": "Choose approve or deny."})
        actor = instance_actor(self)
        using = self._state.db or router.db_for_write(type(self), instance=self)
        with database_context(using), actor_context(actor), transaction.atomic(using=using):
            locked = (
                type(self)
                .objects.db_manager(using)
                .sudo(reason="intake.need.decide_access.lock")
                .locked_get(pk=self.pk)
            )
            # Authorization uses the actor; the locked copy retains unredacted facts.
            if not self.with_actor(actor).has_access("write") or not locked.target.with_actor(actor).has_access(
                "share"
            ):
                raise PermissionDenied("Deciding request access requires need write and target share.")
            if action == NeedAccessAction.INTAKE_APPROVE and locked._account_for_party(locked.party_id) is None:
                _require_user_create()
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if decision is not None:
                if locked.access_decision_id != decision.pk:
                    raise ValidationError({"revision": "The access question has changed; reload it."})
                locked_decision = locked.access_decision
                assert locked_decision is not None  # Its stored key matched the provided decision.
                locked_decision.require_revision(decision_revision)
            if decision is None or locked.access_verdict != [action]:
                raise ValidationError({"action": "Apply the request's current answer."})
            user = None
            if action == NeedAccessAction.INTAKE_APPROVE:
                try:
                    user = locked._link_requester(allow_create=True)
                except ValidationError:
                    raise
                except DomainError as error:
                    raise ValidationError({"conflict": error.code}) from error
            locked.admitted_user = user
            locked.save(
                _access_decision=True,
                expected_revision=expected_revision,
                update_fields=("party", "access_decision", "admitted_user"),
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

    def convert_to_task(self, queue: Queue) -> Task:
        """Return this need's task; its clean concurrent no-op is SELECT-FOR-UPDATE-backed."""

        if self.pk is None or queue.pk is None:
            raise ValidationError("A saved need and queue are required for conversion.")
        actor = current_actor()
        using = self._state.db or router.db_for_write(type(self), instance=self)
        if queue._state.db != using:
            raise ValueError("A request and its task queue must use the same database.")
        with (
            database_context(using),
            system_context(reason="intake.need.convert_to_task"),
            transaction.atomic(using=using),
        ):
            observed_decision = self._lock_access_question()
            locked = (
                type(self)
                .objects.db_manager(using)
                .sudo(reason="intake.need.convert_to_task.need")
                .lock_if_supported()
                .select_related("task", "project", "source_message")
                .get(pk=self.pk)
            )
            if locked.access_decision_id != observed_decision:
                raise ValidationError({"revision": "The access question has changed; reload it."})
            task: Task
            if locked.task is not None:
                task = locked.task
            else:
                title = locked.body or getattr(locked.source_message, "preview", "")
                task = (
                    apps.get_model("projects", "Task")
                    .objects.db_manager(self._state.db)
                    .capture_triage(
                        queue=queue,
                        project=locked.project,
                        title=title,
                        note=locked.body,
                        created_by_id=locked.created_by_id,
                    )
                )
                locked.task = task
                locked.project = None
                locked.save(update_fields=("task", "project", "updated_at"))
        if actor is not None:
            task.with_actor(actor)
        self.refresh_from_db()
        return task


class TaskIntakeFilters(Composable, extends="projects.Task"):
    """Filter tasks by who filed them, the way the task shows it: intake's first request."""

    hasura_filter_expressions: ClassVar[Mapping[str, Callable[[models.QuerySet[Any]], BaseExpression]]] = {
        "requester_name": _requester_name_filter,
        "requested_by_viewer": _requested_by_viewer_filter,
    }

    class Meta:
        abstract = True


class ProjectIntakeSetup(Composable, extends="projects.Project"):
    """Link unassigned request parties before downstream setup admits people."""

    class Meta:
        abstract = True

    def apply_setup(self, *, party: Any | None = None, **options: Any) -> None:
        """Retain existing request identities; a new assignment uses its share gate."""

        need_model = apps.get_model("intake", "Need")
        needs = (
            need_model.objects.db_manager(self._state.db).none()
            if self.converted_from_id is None
            else need_model.objects.db_manager(self._state.db).filter(
                task_id=self.converted_from_id, party__isnull=True
            )
        )
        for need in needs:
            if party is None:
                raise ValidationError({"party": "Choose a party for the unassigned request."})
            need.party = party
            need.save(update_fields=("party", "updated_at"))
        super().apply_setup(**options)


class IntakeTriggerField(StateField[models.TextChoices]):
    """Read and write retired channel triggers as the supported capture mode."""

    _retired = frozenset({"reaction", "mention", "command"})

    def to_python(self, value: Any) -> Any:
        raw = getattr(value, "value", value)
        return super().to_python("all_messages" if isinstance(raw, str) and raw in self._retired else value)

    def deconstruct(self) -> tuple[str, str, Any, dict[str, Any]]:
        """Keep the field's database declaration identical to StateField."""
        name, _, args, kwargs = super().deconstruct()
        return name, "angee.base.fields.state.StateField", args, kwargs


class ChannelIntake(Composable, extends="messaging.Channel"):
    """Same-row intake fields and capture behavior for ``messaging.Channel``."""

    class IntakeTrigger(models.TextChoices):
        """Implemented channel capture trigger."""

        ALL_MESSAGES = "all_messages", "All messages"

    hasura_readable_fields: ClassVar[tuple[str, ...]] = (
        "intake_queue",
        "intake_trigger",
        "intake_field_map",
        "intake_requester_domains",
    )
    hasura_filterable_fields: ClassVar[tuple[str, ...]] = ("intake_queue", "intake_trigger")
    hasura_sortable_fields: ClassVar[tuple[str, ...]] = hasura_filterable_fields
    hasura_aggregatable_fields: ClassVar[tuple[str, ...]] = ()
    hasura_groupable_fields: ClassVar[tuple[str, ...]] = (*hasura_filterable_fields, "intake_queue__name")
    hasura_updatable_fields: ClassVar[tuple[str, ...]] = hasura_readable_fields

    intake_queue = models.ForeignKey(
        "work.Queue",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_channels",
    )
    intake_trigger = IntakeTriggerField(
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
                if field.schema["type"] not in kinds:
                    raise ValidationError({"intake_field_map": f"The form property has the wrong kind for {target}."})
                if name in used:
                    raise ValidationError({"intake_field_map": "Map each form property only once."})
                used.add(name)
        self._validate_requester_domains()

    def _validate_requester_domains(self, *, using: str | None = None) -> None:
        """A domain configuration is ingress authorization, checked before writing."""

        using = using or self._state.db or router.db_for_write(type(self), instance=self)
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
            ._base_manager.using(using)
            .filter(pk=self.pk)
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
                with database_context(using), actor_context(actor):
                    _require_user_create()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Protect the ingress setting even for writers that do not call full_clean."""

        update_fields = kwargs.get("update_fields")
        if self._state.adding or update_fields is None or "intake_requester_domains" in update_fields:
            self._validate_requester_domains(using=kwargs.get("using"))
        super().save(*args, **kwargs)

    def mapped_task_values(self, message: Message) -> dict[str, Any]:
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
                field = model._meta.get_field(target)
                assert isinstance(field, models.Field)
                values[target] = field.clean(value, model())
            except ValidationError, TypeError, ValueError, OverflowError:
                logger.warning("Invalid intake value for %s on message %s; using its default.", target, message.pk)
        return values

    def should_capture_message(self, message: Message) -> bool:
        """Return whether this channel accepts ``message``."""

        if self.intake_queue_id is None:
            return False
        trigger = type(self)._meta.get_field("intake_trigger").to_python(self.intake_trigger)
        match str(trigger):
            case self.IntakeTrigger.ALL_MESSAGES:
                return True
            case _:
                raise ValidationError({"intake_trigger": "Unknown intake trigger."})

    def capture_ingested_message(self, message: Message) -> Need | None:
        """Dispatch an accepted ingested message to the Need write owner."""

        if not self.should_capture_message(message):
            return None
        queue = self.intake_queue
        assert queue is not None  # should_capture_message required the configured queue.
        need_model = apps.get_model("intake", "Need")
        return need_model.objects.db_manager(self._state.db).capture_from_message(message, queue=queue)
