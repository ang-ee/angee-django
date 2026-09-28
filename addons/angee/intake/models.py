"""Request evidence targeting exactly one task or project, and channel capture."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, Self, cast

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import DomainNameValidator, validate_email
from django.db import models, transaction
from django.db.models.functions import Coalesce, NullIf
from django.utils import timezone
from rebac import PermissionDenied, actor_context, current_actor, system_context
from strawberry_django_hasura import SortAlias

from angee.base.actors import actor_user_id, instance_actor
from angee.base.errors import DomainError
from angee.base.fields import StateField
from angee.base.mixins import AuditMixin, OptimisticLockMixin, audit_set_null
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet
from angee.base.scoping import bind_actor

logger = logging.getLogger(__name__)


class NeedImportance(models.TextChoices):
    """Binary requester signal, deliberately distinct from planning priority."""

    NORMAL = "normal", "Normal"
    IMPORTANT = "important", "Important"


class NeedAccessVerdict(models.TextChoices):
    """Decision-compatible stored outcomes, independent of workflow execution."""

    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Approved"
    REJECTED = "rejected", "Denied"


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
                party_id=self._resolved_sender_party_id(locked_message),
                claimed_name=claimed_name,
                claimed_email=(submission.claimed_email or "") if submission else "",
                source_message=locked_message,
                importance=NeedImportance.NORMAL,
                created_by_id=locked_message.created_by_id,
                updated_by_id=locked_message.updated_by_id,
            )
            need.full_clean(validate_unique=False, validate_constraints=False)
            need.sudo(reason="intake.need.capture_message.create").save()
            attachment_model = apps.get_model("messaging", "ThreadAttachment")
            attachment_model.objects.bind_source_thread(task, locked_message.thread)
            if need.party_id is None and need.claimed_email and channel.intake_requester_domains:
                try:
                    # A failed identity factory rolls back its own work, never the capture.
                    with transaction.atomic():
                        need._link_requester(
                            allow_create=need.claimed_email.rsplit("@", 1)[-1].lower()
                            in channel.intake_requester_domains,
                            system_reason=f"intake.requester_domain:{channel.slug}",
                        )
                except (DomainError, ValidationError):
                    logger.info("Requester linking was refused for need %s; capture is retained.", need.pk)
                    need.refresh_from_db()
                except Exception:
                    logger.exception("Requester linking failed for need %s; capture is retained.", need.pk)
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

        with system_context(reason="intake.reconcile_parties"):
            candidates = self.filter(source_message__isnull=False, party__isnull=False).order_by("pk")
            for pk in candidates.values_list("pk", flat=True).iterator():
                with transaction.atomic():
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
    access_verdict = StateField(
        choices_enum=NeedAccessVerdict, default=NeedAccessVerdict.PENDING, editable=False,
    )
    access_resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
        on_delete=audit_set_null, related_name="+",
    )
    access_resolved_at = models.DateTimeField(null=True, blank=True, editable=False)
    access_resolution = models.JSONField(default=dict, blank=True, editable=False)
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
            party_model = self._meta.get_field("party").remote_field.model
            with system_context(reason="intake.need.party_subject"):
                party = party_model._base_manager.get(pk=self.party_id)
                user = party_model.objects.user_for(party)
            if user is not None:
                self.validate_record_access_subject("party", user)

    def validate_record_access_subject(self, relation: str, subject: Any) -> None:
        """A request's holder must also satisfy the target's holder invariant."""

        super().validate_record_access_subject(relation, subject)
        self.target.validate_record_access_subject(relation, subject)

    def save(self, **kwargs: Any) -> None:
        """Validate the persisted assignment and follow it in the same transaction."""

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            kwargs["update_fields"] = update_fields
            if not update_fields:
                super().save(**kwargs)
                return
        with transaction.atomic():
            previous = None if self._state.adding else (
                type(self).objects.sudo(reason="intake.need.assignment_before")
                .lock_if_supported().filter(pk=self.pk)
                .values("task_id", "project_id", "party_id").first()
            )
            values = {
                name: getattr(self, name)
                if previous is None or update_fields is None or {name, name.removesuffix("_id")} & update_fields
                else previous[name]
                for name in ("task_id", "project_id", "party_id")
            }
            if (values["task_id"] is None) == (values["project_id"] is None):
                raise ValidationError("Exactly one of task or project must be targeted.")
            assignment_changed = values != previous and (
                values["party_id"] is not None or (previous is not None and previous["party_id"] is not None)
            )
            if assignment_changed:
                candidate = type(self)(pk=self.pk, **values)
                actor, elevated = self.effective_actor()
                if elevated:
                    candidate.sudo(reason="intake.need.assignment")
                else:
                    candidate.with_actor(actor)
                candidate.validate_party_assignment()
            super().save(**kwargs)
            if assignment_changed and values["task_id"] is not None and values["party_id"] is not None:
                with system_context(reason="intake.need.follow_requester"):
                    candidate.target.message_subscribe(party=candidate.party)

    def _link_requester(self, *, allow_create: bool, system_reason: str | None = None) -> Any:
        """Resolve the claimed account; capture or the decision authorizes this act.

        Ingress uses its channel's authorization and a named IAM system reason.
        The decision uses IAM's actor-gated factory. No credential or membership
        is written, and an existing person is never attached to a user.
        """

        users = get_user_model().objects
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
            self.sudo(reason="intake.need.link_requester").save(update_fields=("party",))
        return user

    def decide_access(self, action: str, reason: str = "", expected_revision: int | None = None) -> Any:
        """Approve or deny access, retaining a Decision-compatible resolution."""

        if action not in {"approve", "deny"}:
            raise ValidationError({"action": "Choose approve or deny."})
        actor = instance_actor(self)
        with actor_context(actor), transaction.atomic():
            locked = type(self).objects.sudo(reason="intake.need.decide_access.lock").locked_get(pk=self.pk)
            # Authorization uses the actor; the locked copy retains unredacted facts.
            if (
                not self.with_actor(actor).has_access("write")
                or not locked.target.with_actor(actor).has_access("share")
            ):
                raise PermissionDenied("Deciding request access requires need write and target share.")
            if action == "approve":
                get_user_model().objects.check_create()
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            verdict = NeedAccessVerdict.COMPLETED if action == "approve" else NeedAccessVerdict.REJECTED
            if locked.access_verdict == verdict:
                if action == "deny":
                    return None
                with system_context(reason="intake.need.decide_access.replay"):
                    parties = apps.get_model("parties", "Party").objects
                    return parties.user_for(locked.party) if locked.party_id else None
            if locked.access_verdict == NeedAccessVerdict.COMPLETED:
                raise ValidationError({"action": "Approved access is final."})
            user = None
            if action == "approve":
                try:
                    user = locked._link_requester(allow_create=True)
                except (DomainError, ValidationError) as error:
                    raise ValidationError({"conflict": str(error)}) from error
            locked.access_verdict = verdict
            locked.access_resolution = {"action": action, "reason": reason}
            locked.access_resolved_by_id = actor_user_id(actor)
            locked.access_resolved_at = timezone.now()
            locked.save(update_fields=(
                "access_verdict", "access_resolution", "access_resolved_by", "access_resolved_at",
            ))
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
        previous = [] if self._state.adding else type(self)._base_manager.filter(pk=self.pk).values_list(
            "intake_requester_domains", flat=True,
        ).get()
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
            except (ValidationError, TypeError, ValueError, OverflowError):
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


class TaskIntake(models.Model):
    """Task sort vocabulary backed by its first request's recorded identity."""

    extends = "projects.Task"
    runtime = False

    @staticmethod
    def filer_name_expression(info: Any, queryset: Any) -> models.Subquery:
        """Sort on the first need's party name, then its claimed name, else NULL."""

        needs = apps.get_model("intake", "Need").objects.with_actor(queryset.actor() or current_actor())
        first = (
            needs.scoped_for_aggregate().filter(task_id=models.OuterRef("pk"))
            .order_by("created_at", "pk")
            .annotate(_filer_name=Coalesce(
                NullIf("party__display_name", models.Value("")),
                NullIf("claimed_name", models.Value("")),
                output_field=models.TextField(),
            ))
        )
        return models.Subquery(first.values("_filer_name")[:1], output_field=models.TextField())

    hasura_sortable_aliases = {"filer_name": SortAlias("_filer_name", filer_name_expression)}

    class Meta:
        """Abstract donor options folded into the concrete task table."""

        abstract = True
