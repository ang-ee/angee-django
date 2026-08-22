"""External-request evidence and its same-row capture contributors.

``Need`` is the durable evidence row. A need semantically targets exactly one
task or project. For a task target, ``project`` is also the indexed,
denormalized copy of ``task.project`` so project-level request counts need no
relation walk. ``targets_project`` records whether that project value is the
target or merely the task's denormalized context; the database constraint can
therefore enforce target exclusivity without rejecting task needs whose tasks
belong to projects.

``ChannelIntake`` folds capture configuration and trigger evaluation into the
existing ``messaging.Channel`` row. ``TaskIntake`` participates in the composed
task save chain and is the sole writer that re-denormalizes existing needs when
a task changes project. ``ThreadAttachmentIntake`` contributes the idempotent
binding verb for messaging's generic ``source`` attachment role.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from angee.base.fields import StateField
from angee.base.mixins import AuditMixin
from angee.base.models import AngeeDataModel, AngeeManager, AngeeModel, bind_actor
from angee.base.refs import canonical_record_target
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from rebac import current_actor, system_context


class NeedManager(AngeeManager):
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
            importance_value = self.model.Importance(getattr(importance, "value", importance))
        except ValueError as error:
            raise ValidationError({"importance": "Choose normal or important."}) from error

        target_field = self.target_field(target)
        target_filter = {f"{target_field}_id": target.pk}
        if target_field == "project":
            target_filter.update({"task__isnull": True, "targets_project": True})
        else:
            target_filter["targets_project"] = False
        actor = current_actor()
        with transaction.atomic():
            with system_context(reason="intake.need.capture.lookup"):
                locked_target = type(target).objects.sudo(reason="intake.need.capture.target").locked_get(
                    pk=target.pk
                )
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
            need.sudo(reason="intake.need.capture.create").save()
            bind_actor(need, verified_actor)
            return need

    def capture_from_message(self, message: models.Model, *, queue: models.Model) -> models.Model:
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

            party_id = self._resolved_sender_party_id(locked_message)
            title = self._message_title(locked_message)
            task = self._create_triage_task(
                queue=queue,
                title=title,
                note=str(locked_message.preview or ""),
                created_by_id=locked_message.created_by_id,
            )
            need = self.model(
                task=task,
                party_id=party_id,
                source_message=locked_message,
                importance=self.model.Importance.NORMAL,
                created_by_id=locked_message.created_by_id,
                updated_by_id=locked_message.updated_by_id,
            )
            need.full_clean(validate_unique=False, validate_constraints=False)
            need.sudo(reason="intake.need.capture_message.create").save()
            attachment_model = apps.get_model("messaging", "ThreadAttachment")
            attachment_model.bind_source_thread(
                task=task,
                thread=locked_message.thread,
                created_by_id=locked_message.created_by_id,
            )
            return need

    def _create_triage_task(
        self,
        *,
        queue: models.Model,
        title: str,
        note: str,
        project: models.Model | None = None,
        created_by_id: Any = None,
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
            sort_order=task_model.objects._append_rank("sort_order", project=project),
            sub_sort_order=task_model.objects._append_rank("sub_sort_order", parent=None),
            created_by_id=created_by_id,
            updated_by_id=created_by_id,
        )
        task.full_clean(validate_unique=False, validate_constraints=False)
        task.sudo(reason="intake.need.create_triage_task").save()
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

        sender = getattr(message, "sender", None)
        if sender is None:
            return None
        if sender.party_id is None:
            party_handle_model = apps.get_model("parties", "PartyHandle")
            party_handle_model.objects.suggest_for(sender)
            sender.refresh_from_db(fields=("party",))
        return sender.party_id


class Need(AuditMixin, AngeeDataModel):
    """One external or manually-authored request attached to one semantic target."""

    runtime = True
    sqid_prefix = "ned_"

    class Importance(models.TextChoices):
        """Binary requester signal, deliberately distinct from planning priority."""

        NORMAL = "normal", "Normal"
        IMPORTANT = "important", "Important"

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
    targets_project = models.BooleanField(default=False, editable=False)
    """Whether ``project`` is the direct target rather than task-project context."""

    importance = StateField(choices_enum=Importance, default=Importance.NORMAL)
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
        rebac_id_attr = "sqid"
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(targets_project=False, task__isnull=False)
                    | models.Q(
                        targets_project=True,
                        task__isnull=True,
                        project__isnull=False,
                    )
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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Track an explicitly authored project separately from denormalization."""

        supplied_project = kwargs.get("project") is not None or kwargs.get("project_id") is not None
        object.__setattr__(self, "_intake_track_targets", False)
        object.__setattr__(self, "_intake_internal_target", False)
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "_intake_track_targets", True)
        object.__setattr__(self, "_intake_project_assigned", supplied_project)

    def __setattr__(self, name: str, value: Any) -> None:
        """Remember caller-authored project assignment without counting internal projection."""

        if (
            name in {"project", "project_id"}
            and value is not None
            and getattr(self, "_intake_track_targets", False)
            and not getattr(self, "_intake_internal_target", False)
        ):
            object.__setattr__(self, "_intake_project_assigned", True)
        super().__setattr__(name, value)

    def full_clean(self, *args: Any, **kwargs: Any) -> None:
        """Keep Django field normalization from becoming authored target input."""

        tracking = getattr(self, "_intake_track_targets", True)
        object.__setattr__(self, "_intake_track_targets", False)
        try:
            super().full_clean(*args, **kwargs)
        finally:
            object.__setattr__(self, "_intake_track_targets", tracking)

    def refresh_from_db(self, *args: Any, **kwargs: Any) -> None:
        """Refresh target fields without marking the denormalized project as authored."""

        object.__setattr__(self, "_intake_track_targets", False)
        try:
            super().refresh_from_db(*args, **kwargs)
        finally:
            object.__setattr__(self, "_intake_track_targets", True)
            object.__setattr__(self, "_intake_project_assigned", False)

    def apply_create_defaults(self) -> Mapping[str, Sequence[Any]]:
        """Project the target before the generic create gate evaluates its relations."""

        self._normalize_target()
        if self.task_id is not None:
            return {"task": (self.task,)}
        return {"project": (self.project,)}

    def clean(self) -> None:
        """Normalize task-project context and reject missing or double-authored targets."""

        self._normalize_target()
        super().clean()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist with target semantics and task-project context kept coherent."""

        update_fields = kwargs.get("update_fields")
        target_fields = {"task", "task_id", "project", "project_id", "targets_project"}
        if self._state.adding or update_fields is None or target_fields.intersection(update_fields):
            self._normalize_target()
            if update_fields is not None:
                kwargs["update_fields"] = {
                    *update_fields,
                    "project",
                    "targets_project",
                }
        super().save(*args, **kwargs)
        object.__setattr__(self, "_intake_project_assigned", False)

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
                object.__setattr__(locked, "_intake_project_assigned", False)
                locked.save(update_fields=("task", "project", "targets_project", "updated_at"))
        bind_actor(task, actor)
        self.refresh_from_db()
        return task

    def _normalize_target(self) -> None:
        """Resolve the semantic target and project task context on this instance."""

        if self.task_id is None:
            if self.project_id is None:
                raise ValidationError("Exactly one of task or project must be targeted.")
            self.targets_project = True
            return
        if getattr(self, "_intake_project_assigned", False):
            raise ValidationError({"project": "Choose either a task or a project, not both."})
        task_project_id = self.task.project_id
        object.__setattr__(self, "_intake_internal_target", True)
        try:
            self.project_id = task_project_id
            self.targets_project = False
        finally:
            object.__setattr__(self, "_intake_internal_target", False)


class _ChannelIntakeContribution(models.Model):
    """Field-and-behavior base folded into the materialized Channel child.

    Channel is a multi-table child of Integration. Keeping this contribution on
    a narrow ``models.Model`` base avoids copying AngeeModel's timestamp columns
    ahead of that concrete parent while preserving the normal extension-donor
    discovery contract on :class:`ChannelIntake` below.
    """

    class IntakeTrigger(models.TextChoices):
        """Declared capture triggers; only all-messages is implemented in v1."""

        ALL_MESSAGES = "all_messages", "All messages"
        REACTION = "reaction", "Reaction"
        MENTION = "mention", "Mention"
        COMMAND = "command", "Command"

    hasura_readable_fields = ("intake_queue", "intake_trigger")
    hasura_filterable_fields = hasura_readable_fields
    hasura_sortable_fields = hasura_readable_fields
    hasura_aggregatable_fields: tuple[str, ...] = ()
    hasura_groupable_fields = hasura_readable_fields
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

    class Meta:
        """Abstract contribution folded into the concrete channel table."""

        abstract = True

    def should_capture_message(self, message: models.Model) -> bool:
        """Return whether this channel's configured v1 trigger accepts ``message``."""

        del message
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


class ChannelIntake(_ChannelIntakeContribution, AngeeModel):
    """Same-row intake configuration donor for ``messaging.Channel``."""

    extends = "messaging.Channel"

    class Meta:
        """Abstract donor discovered by the composer; runtime remains false."""

        abstract = True


class TaskIntake(AngeeModel):
    """Same-row task save participant that owns Need.project re-denormalization."""

    extends = "projects.Task"
    runtime = False

    class Meta:
        """Abstract donor options folded into the concrete task table."""

        abstract = True

    # Django QuerySet.update() bypasses save-path re-denormalization by nature;
    # the API routes through instance saves, and internal bulk writers are on their honor.
    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the task, then reconcile task-target Need project context once."""

        adding = self._state.adding
        update_fields = kwargs.get("update_fields")
        project_may_change = update_fields is None or bool({"project", "project_id"}.intersection(update_fields))
        with transaction.atomic():
            super().save(*args, **kwargs)
            if not adding and project_may_change:
                need_model = apps.get_model("intake", "Need")
                need_model._base_manager.filter(task_id=self.pk).exclude(project_id=self.project_id).update(
                    project_id=self.project_id, updated_at=timezone.now()
                )


class ThreadAttachmentIntake(AngeeModel):
    """Same-row source-thread binding contribution for messaging attachments."""

    extends = "messaging.ThreadAttachment"
    runtime = False
    SOURCE_ROLE = "source"

    class Meta:
        """Abstract donor options folded into the concrete attachment table."""

        abstract = True

    @classmethod
    def bind_source_thread(
        cls,
        *,
        task: models.Model,
        thread: models.Model | None,
        created_by_id: Any = None,
    ) -> models.Model:
        """Bind ``thread`` as ``task``'s source evidence, idempotently."""

        if task.pk is None or thread is None or thread.pk is None:
            raise ValidationError("A saved task and source thread are required.")
        content_type, object_id = canonical_record_target(task)
        attachment, created = cls._base_manager.get_or_create(
            content_type=content_type,
            object_id=object_id,
            role=cls.SOURCE_ROLE,
            defaults={
                "thread": thread,
                "label": str(task),
                "created_by_id": created_by_id,
                "updated_by_id": created_by_id,
            },
        )
        if not created and attachment.thread_id != thread.pk:
            raise ValidationError("Task already has a different source thread.")
        return attachment
