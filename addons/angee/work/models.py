"""Operational queues, user-named stages, and same-row task work mechanics.

``Queue`` is a materialized child of :class:`spaces.Group`: its inherited group
row remains the one roster and hierarchy owner.  ``TaskWork`` is a table-less
donor onto ``projects.Task``; queue, stage, numbering, estimate, and triage
columns therefore exist only when this addon is composed, on the task table
itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, ClassVar, cast

from angee.base.actors import actor_user_id
from angee.base.fields import StateField
from angee.base.mixins import AuditMixin
from angee.base.models import AngeeDataModel, AngeeModel
from angee.base.stages import Stage as StagePrimitive
from angee.base.stages import StagedModelMixin
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from rebac import current_actor, system_context
from rebac.actors import is_sudo as ambient_is_sudo

from angee.parties.mixins import LinkSource
from angee.spaces.managers import GroupManager


class QueueManager(GroupManager):
    """Own personal-queue identity and idempotent provisioning."""

    PERSONAL_SLUG_PREFIX = "personal-"
    PERSONAL_KEY_PREFIX = "P"
    PERSONAL_KEY_MAX_PK_DIGITS = 11

    def _personal_pk(self, user: models.Model) -> str:
        """Return the saved numeric user PK used as private-queue identity."""

        if user.pk is None:
            raise ValidationError("A user must be saved before provisioning a personal queue.")
        user_pk = str(user.pk)
        if not user_pk.isdecimal():
            raise ValidationError("Personal queues require a numeric user primary key.")
        return user_pk

    def personal_slug(self, user: models.Model) -> str:
        """Return the unique stable slug of ``user``'s personal queue."""

        return f"{self.PERSONAL_SLUG_PREFIX}{self._personal_pk(user)}"

    def personal_key(self, user: models.Model) -> str:
        """Return ``P<pk>``, rejecting PKs above 11 digits rather than truncating."""

        user_pk = self._personal_pk(user)
        if len(user_pk) > self.PERSONAL_KEY_MAX_PK_DIGITS:
            raise ValidationError("Personal queue keys support user PKs up to 11 decimal digits.")
        return f"{self.PERSONAL_KEY_PREFIX}{user_pk}"

    def personal_for(self, user: models.Model, *, provision: bool = False) -> Any | None:
        """Return ``user``'s personal queue, optionally provisioning it."""

        slug = self.personal_slug(user)
        queue = self.sudo(reason="work.queue.personal.lookup").filter(slug=slug).first()
        if queue is not None or not provision:
            return queue
        return self.provision_personal(user)

    def provision_personal(self, user: models.Model) -> Any:
        """Idempotently create one private owner-rostered queue for ``user``."""

        slug = self.personal_slug(user)
        key = self.personal_key(user)
        with system_context(reason="work.queue.personal.provision"), transaction.atomic():
            queue = self.sudo(reason="work.queue.personal.provision.lookup").filter(slug=slug).first()
            if queue is None:
                label = str(user.get_full_name() or user.username)
                queue = self.model(
                    name=f"{label}'s work",
                    slug=slug,
                    description="Personal operational queue.",
                    visibility="private",
                    key=key,
                    triage_enabled=False,
                    cycles_enabled=False,
                    created_by_id=user.pk,
                    updated_by_id=user.pk,
                )
                queue.sudo(reason="work.queue.personal.provision.create")
                try:
                    with transaction.atomic():
                        queue.save()
                except IntegrityError:
                    queue = self.sudo(
                        reason="work.queue.personal.provision.concurrent_lookup"
                    ).get(slug=slug)
            self._ensure_personal_membership(queue, user)
            return queue

    def _ensure_personal_membership(self, queue: models.Model, user: models.Model) -> None:
        """Ensure the personal queue's inherited Group roster names its owner."""

        party_model = apps.get_model("parties", "Party")
        membership_model = apps.get_model("spaces", "Membership")
        person = party_model.objects.for_user(user)
        membership = membership_model._base_manager.filter(
            group_id=queue.pk,
            party_id=person.pk,
        ).first()
        if membership is None:
            membership = membership_model(
                group_id=queue.pk,
                party_id=person.pk,
                role=membership_model.MembershipRole.OWNER,
                confidence=1.0,
                source=LinkSource.MANUAL,
                is_confirmed=True,
                is_dismissed=False,
                created_by_id=user.pk,
                updated_by_id=user.pk,
            )
            membership.save()
            return
        changed = False
        desired = {
            "role": membership_model.MembershipRole.OWNER,
            "confidence": 1.0,
            "source": LinkSource.MANUAL,
            "is_confirmed": True,
            "is_dismissed": False,
        }
        for name, value in desired.items():
            if getattr(membership, name) != value:
                setattr(membership, name, value)
                changed = True
        if changed:
            membership.save(update_fields=(*desired, "updated_at"))


class Queue(AngeeModel):
    """An ongoing operational context, materialized as a ``spaces.Group`` child."""

    runtime = True
    extends = "spaces.Group"
    child_overrides_parent = True

    class EstimateScale(models.TextChoices):
        """Estimation vocabulary configured per queue."""

        NONE = "none", "None"
        LINEAR = "linear", "Linear"
        FIBONACCI = "fibonacci", "Fibonacci"
        EXPONENTIAL = "exponential", "Exponential"
        TSHIRT = "tshirt", "T-shirt"

    class CycleStartDay(models.IntegerChoices):
        """ISO weekday on which generated cycles start."""

        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    PROVISIONED_STAGES: ClassVar[tuple[tuple[str, str, int, str], ...]] = (
        ("Triage", "warning", 10, "triage"),
        ("Backlog", "neutral", 20, "backlog"),
        ("Todo", "brand", 30, "unstarted"),
        ("In progress", "info", 40, "started"),
        ("Done", "success", 50, "completed"),
        ("Canceled", "neutral", 60, "canceled"),
        ("Duplicate", "neutral", 70, "duplicate"),
    )

    key = models.CharField(
        max_length=12,
        unique=True,
        validators=(RegexValidator(r"^[A-Z0-9_]+$", "Use uppercase letters, numbers, or underscores."),),
    )
    triage_enabled = models.BooleanField(default=False)
    cycles_enabled = models.BooleanField(default=False)
    cycle_weeks = models.PositiveSmallIntegerField(
        default=2,
        validators=(MinValueValidator(1),),
    )
    cycle_cooldown_weeks = models.PositiveSmallIntegerField(default=0)
    cycle_start_day = models.PositiveSmallIntegerField(
        choices=CycleStartDay.choices,
        default=CycleStartDay.MONDAY,
        validators=(MinValueValidator(0), MaxValueValidator(6)),
    )
    upcoming_cycle_count = models.PositiveSmallIntegerField(default=2)
    estimate_scale = StateField(choices_enum=EstimateScale, default=EstimateScale.NONE)
    estimate_allow_zero = models.BooleanField(default=True)
    default_estimate = models.FloatField(
        null=True,
        blank=True,
        validators=(MinValueValidator(0.0),),
    )
    default_stage = models.ForeignKey(
        "work.Stage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="default_for_queues",
    )
    auto_archive_months = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=(MinValueValidator(1),),
    )
    auto_close_months = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=(MinValueValidator(1),),
    )

    objects = QueueManager()

    class Meta:
        """Django model options for the materialized queue child."""

        abstract = True
        ordering = ("key",)
        rebac_resource_type = "work/queue"
        rebac_id_attr = "sqid"

    def clean(self) -> None:
        """Normalize the queue key and validate its explicit default stage."""

        self.key = self.key.strip().upper()
        super().clean()
        self._validate_default_stage()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the queue and atomically provision its stage/numbering substrate."""

        adding = self._state.adding
        self.key = self.key.strip().upper()
        self._validate_default_stage()
        with transaction.atomic():
            super().save(*args, **kwargs)
            if adding:
                self._provision_workflow()

    def task_sequence_key(self) -> str:
        """Return the stable sequence lookup key for this queue's task numbers."""

        return f"work.task/{self.sqid}"

    def ensure_task_sequence(self) -> models.Model:
        """Return this queue's task-number sequence, creating it if absent."""

        sequence_model = apps.get_model("sequence", "Sequence")
        with system_context(reason="work.queue.ensure_task_sequence"):
            sequence, _created = sequence_model.objects.get_or_create(
                key=self.task_sequence_key(),
                defaults={
                    "name": f"{self.key} task numbers",
                    "template": "{number}",
                    "prefix": "",
                    "period_reset": "none",
                    "preview_enabled": True,
                },
            )
        return cast(models.Model, sequence)

    def next_task_number(self) -> int:
        """Draw the next gapless task number inside the caller's transaction."""

        self.ensure_task_sequence()
        sequence_model = apps.get_model("sequence", "Sequence")
        return int(sequence_model.objects.next_value(self.task_sequence_key()))

    def _provision_workflow(self) -> None:
        """Provision the seven category stages and configure the default once."""

        stage_model = apps.get_model("work", "Stage")
        stages: dict[str, models.Model] = {}
        with system_context(reason="work.queue.provision_workflow"):
            for name, tone, position, category in self.PROVISIONED_STAGES:
                stage, _created = stage_model.objects.get_or_create(
                    queue=self,
                    category=category,
                    defaults={"name": name, "tone": tone, "position": position},
                )
                stages[category] = stage
            self.ensure_task_sequence()
            if self.default_stage_id is None:
                self.default_stage = stages[stage_model.StageCategory.UNSTARTED]
                super().save(update_fields=("default_stage", "updated_at"))

    def _validate_default_stage(self) -> None:
        """Reject an explicit default stage owned by another queue."""

        if self.default_stage_id is None:
            return
        default_queue_id = getattr(self.default_stage, "queue_id", None)
        if self.pk is None or default_queue_id != self.pk:
            raise ValidationError({"default_stage": "Default stage must belong to this queue."})


class Stage(StagePrimitive, AuditMixin, AngeeDataModel):
    """One ordered, user-named pipeline stage owned by a queue."""

    runtime = True
    sqid_prefix = "stg_"
    container_field_name = "queue"
    category_field_name = "category"

    class StageCategory(models.TextChoices):
        """Closed semantic categories projected onto coarse task status."""

        TRIAGE = "triage", "Triage"
        BACKLOG = "backlog", "Backlog"
        UNSTARTED = "unstarted", "Unstarted"
        STARTED = "started", "Started"
        COMPLETED = "completed", "Completed"
        CANCELED = "canceled", "Canceled"
        DUPLICATE = "duplicate", "Duplicate"

    SYSTEM_CATEGORIES = frozenset((StageCategory.TRIAGE, StageCategory.DUPLICATE))

    queue = models.ForeignKey(
        "work.Queue",
        on_delete=models.CASCADE,
        related_name="stages",
    )
    category = StateField(choices_enum=StageCategory, default=StageCategory.UNSTARTED)

    class Meta(StagePrimitive.Meta):
        """Django model options for queue stages."""

        abstract = True
        ordering = ("queue", "position", "sqid")
        rebac_resource_type = "work/stage"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("queue", "name"),
                name="uq_work_stage_queue_name",
            ),
            models.UniqueConstraint(
                fields=("queue", "category"),
                condition=models.Q(category__in=("triage", "duplicate")),
                name="uq_work_stage_queue_system_category",
            ),
        )

    @classmethod
    def from_db(cls, db: Any, field_names: Sequence[str], values: Sequence[Any]) -> Stage:
        """Load a row and remember facts that identify a system stage."""

        instance = super().from_db(db, field_names, values)
        instance._loaded_name = instance.name if "name" in field_names else None
        instance._loaded_category = instance.category if "category" in field_names else None
        return cast(Stage, instance)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Prevent user creation or renaming of triage/duplicate system stages."""

        if not ambient_is_sudo():
            loaded_category = getattr(self, "_loaded_category", None)
            if self.category in self.SYSTEM_CATEGORIES and (
                self._state.adding or loaded_category != self.category
            ):
                raise ValidationError(
                    {"category": "Triage and duplicate stages are system-provisioned."}
                )
            if loaded_category in self.SYSTEM_CATEGORIES and (
                self.name != getattr(self, "_loaded_name", self.name)
                or self.category != loaded_category
            ):
                raise ValidationError(
                    {"name": "System-provisioned stages cannot be renamed or recategorized."}
                )
        super().save(*args, **kwargs)
        self._loaded_name = self.name
        self._loaded_category = self.category

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Prevent users from deleting the two system-provisioned stages."""

        if not ambient_is_sudo() and self.category in self.SYSTEM_CATEGORIES:
            raise ValidationError({"category": "System-provisioned stages cannot be deleted."})
        return super().delete(*args, **kwargs)


class TaskWork(StagedModelMixin, AngeeModel):
    """Same-row work contribution folded into ``projects.Task``."""

    extends = "projects.Task"
    runtime = False
    stage_container_field_name = "queue"

    work_stage_projection = True
    hasura_readable_fields = (
        "queue",
        "stage",
        "number",
        "estimate",
        "snoozed_until",
        "snoozed_by",
        "started_triage_at",
        "triaged_at",
    )
    hasura_filterable_fields = hasura_readable_fields
    hasura_sortable_fields = (
        "queue",
        "stage",
        "number",
        "estimate",
        "snoozed_until",
        "started_triage_at",
        "triaged_at",
    )
    hasura_aggregatable_fields = ("number", "estimate")
    hasura_groupable_fields = ("queue", "stage")
    hasura_insertable_fields = ("queue", "stage", "estimate")
    hasura_updatable_fields = hasura_insertable_fields
    hasura_forbidden_insertable_fields = ("status",)

    queue = models.ForeignKey(
        "work.Queue",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
    )
    stage = models.ForeignKey(
        "work.Stage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
    )
    number = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    estimate = models.FloatField(
        null=True,
        blank=True,
        validators=(MinValueValidator(0.0),),
    )
    snoozed_until = models.DateTimeField(null=True, blank=True, db_index=True)
    snoozed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    started_triage_at = models.DateTimeField(null=True, blank=True, db_index=True)
    triaged_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        """Abstract donor options folded into the concrete task table."""

        abstract = True
        constraints = (
            models.UniqueConstraint(
                fields=("queue", "number"),
                name="uq_projects_task_queue_number",
            ),
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Track caller-authored status separately from Django row loading."""

        explicit_status = "status" in kwargs
        object.__setattr__(self, "_work_track_status", False)
        object.__setattr__(self, "_work_internal_status", False)
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "_work_track_status", True)
        object.__setattr__(self, "_work_status_assigned", explicit_status)
        object.__setattr__(self, "_work_loaded_queue_id", getattr(self, "queue_id", None))
        object.__setattr__(self, "_work_loaded_stage_id", getattr(self, "stage_id", None))

    # Django QuerySet.update() bypasses save-path projection/rejection by nature;
    # the API routes through instance saves, and internal bulk writers are on their honor.
    def __setattr__(self, name: str, value: Any) -> None:
        """Remember direct status assignment while allowing the projection writer."""

        if (
            name == "status"
            and getattr(self, "_work_track_status", False)
            and not getattr(self, "_work_internal_status", False)
        ):
            object.__setattr__(self, "_work_status_assigned", True)
        super().__setattr__(name, value)

    def refresh_from_db(self, *args: Any, **kwargs: Any) -> None:
        """Refresh without misclassifying Django's field hydration as a direct write."""

        object.__setattr__(self, "_work_track_status", False)
        try:
            super().refresh_from_db(*args, **kwargs)
        finally:
            object.__setattr__(self, "_work_track_status", True)
            object.__setattr__(self, "_work_status_assigned", False)
            object.__setattr__(self, "_work_loaded_queue_id", self.queue_id)
            object.__setattr__(self, "_work_loaded_stage_id", self.stage_id)

    def apply_create_defaults(self) -> Mapping[str, Sequence[Any]]:
        """Default queue/stage from the actor and return their create relations."""

        relationships: dict[str, Sequence[Any]] = {}
        parent = getattr(super(), "apply_create_defaults", None)
        if callable(parent):
            relationships.update(parent())
        self._apply_queue_and_stage_defaults(provision=True)
        if self.queue_id is not None:
            relationships["queue"] = (self.queue,)
        return relationships

    def clean(self) -> None:
        """Project a stage before base lifecycle validation and enforce scope."""

        self._apply_queue_and_stage_defaults(provision=False)
        self._reject_direct_status_write()
        self._project_stage_lifecycle()
        super().clean()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist stage projection and queue numbering in one transaction."""

        self._reject_direct_status_write()
        update_fields = set(kwargs["update_fields"]) if kwargs.get("update_fields") is not None else None
        with transaction.atomic():
            defaulted = self._apply_queue_and_stage_defaults(provision=True)
            self.validate_stage_scope()
            projected = self._project_stage_lifecycle()
            allocated = False
            if self._state.adding and self.queue_id is not None and self.number is None:
                self.number = self.queue.next_task_number()
                allocated = True
            if update_fields is not None:
                update_fields.update(projected)
                update_fields.update(defaulted)
                if allocated:
                    update_fields.add("number")
                kwargs["update_fields"] = update_fields
            super().save(*args, **kwargs)
        object.__setattr__(self, "_work_status_assigned", False)
        object.__setattr__(self, "_work_loaded_queue_id", self.queue_id)
        object.__setattr__(self, "_work_loaded_stage_id", self.stage_id)

    def complete(self) -> Any:
        """Move to the first completed stage, or use the base verb without a queue."""

        if self.queue_id is None:
            return self._base_verb("complete")
        self.stage = self._stage_for_category("completed")
        self.save(update_fields=("stage", "updated_at"))
        return self

    def drop(self, reason: Any) -> Any:
        """Move to duplicate/canceled according to the base dropped reason."""

        try:
            reason_member = self.TaskDroppedReason(getattr(reason, "value", reason))
        except ValueError as error:
            raise ValidationError({"reason": "Choose duplicate, declined, or obsolete."}) from error
        if self.queue_id is None:
            return self._base_verb("drop", reason_member)
        category = "duplicate" if reason_member == self.TaskDroppedReason.DUPLICATE else "canceled"
        self.stage = self._stage_for_category(category)
        self.dropped_reason = reason_member
        self.save(update_fields=("stage", "dropped_reason", "updated_at"))
        return self

    def reopen(self) -> Any:
        """Move to the queue-owned default stage, or use the base verb without a queue."""

        if self.queue_id is None:
            return self._base_verb("reopen")
        stage = self.resolve_default_stage()
        if stage is None:
            raise ValidationError({"stage": "Queue has no default stage."})
        self.stage = stage
        self.save(update_fields=("stage", "updated_at"))
        return self

    @property
    def work_key(self) -> str | None:
        """Return the queue-keyed task number (for example ``ENG-42``)."""

        if self.queue_id is None or self.number is None:
            return None
        return f"{self.queue.key}-{self.number}"

    def _reject_direct_status_write(self) -> None:
        """Reject every caller-authored status assignment while work is composed."""

        if getattr(self, "_work_status_assigned", False) and not getattr(
            self,
            "_work_internal_status",
            False,
        ):
            raise ValidationError(
                {"status": "Set stage instead; status is projected by the work addon."}
            )

    def _apply_queue_and_stage_defaults(self, *, provision: bool) -> set[str]:
        """Infer queue from stage, then personal queue, then its default stage."""

        changed: set[str] = set()
        queue_cleared = (
            getattr(self, "_work_loaded_queue_id", None) is not None and self.queue_id is None
        )
        stage_cleared = (
            getattr(self, "_work_loaded_stage_id", None) is not None and self.stage_id is None
        )
        if queue_cleared != stage_cleared:
            raise ValidationError(
                "a stage implies its queue — clear both queue and stage to remove the task "
                "from its queue"
            )
        if self.queue_id is None and self.stage_id is not None:
            self.queue_id = self.stage.queue_id
            changed.add("queue")
        if self.queue_id is None and self._state.adding:
            user_id = actor_user_id(current_actor()) or getattr(self, "created_by_id", None)
            if user_id is not None:
                user_model = apps.get_model(settings.AUTH_USER_MODEL)
                with system_context(reason="work.task.personal_queue_user"):
                    user = user_model._base_manager.filter(pk=user_id).first()
                if user is not None:
                    queue_model = apps.get_model("work", "Queue")
                    queue = queue_model.objects.personal_for(user, provision=provision)
                    if queue is not None:
                        self.queue = queue
                        changed.add("queue")
        if self.stage_id is None and self.queue_id is not None:
            stage = self.resolve_default_stage()
            if stage is not None:
                self.stage = stage
                changed.add("stage")
        return changed

    def _project_stage_lifecycle(self) -> set[str]:
        """Project all seven stage categories onto coarse task lifecycle fields."""

        if self.stage_id is None:
            return set()
        category = str(self.stage.get_category())
        now = timezone.now()
        loaded_stage_id = getattr(self, "_work_loaded_stage_id", None)
        stage_changed = self._state.adding or loaded_stage_id != self.stage_id
        old_category = self._stage_category(loaded_stage_id) if stage_changed else category

        object.__setattr__(self, "_work_internal_status", True)
        try:
            if category in {"triage", "backlog", "unstarted", "started"}:
                self.status = self.TaskStatus.OPEN
                self.done_at = None
                self.dropped_reason = None
                self.dropped_at = None
            elif category == "completed":
                self.status = self.TaskStatus.DONE
                self.done_at = self.done_at or now
                self.dropped_reason = None
                self.dropped_at = None
            elif category == "canceled":
                self.status = self.TaskStatus.DROPPED
                if self.dropped_reason not in {
                    self.TaskDroppedReason.DECLINED,
                    self.TaskDroppedReason.OBSOLETE,
                }:
                    self.dropped_reason = self.TaskDroppedReason.OBSOLETE
                self.dropped_at = self.dropped_at or now
                self.done_at = None
            elif category == "duplicate":
                self.status = self.TaskStatus.DROPPED
                self.dropped_reason = self.TaskDroppedReason.DUPLICATE
                self.dropped_at = self.dropped_at or now
                self.done_at = None
            else:
                raise ValidationError({"stage": f"Unknown work stage category {category!r}."})
        finally:
            object.__setattr__(self, "_work_internal_status", False)

        fields = {"status", "done_at", "dropped_reason", "dropped_at"}
        if category == "triage" and stage_changed:
            self.started_triage_at = now
            self.triaged_at = None
            fields.update(("started_triage_at", "triaged_at"))
        elif category != "triage" and old_category == "triage":
            self.triaged_at = self.triaged_at or now
            fields.add("triaged_at")
        return fields

    def _stage_category(self, stage_id: Any | None) -> str | None:
        """Return the category of a previously loaded stage id."""

        if stage_id is None:
            return None
        stage_model = self.stage_model()
        queryset = stage_model._base_manager.all()
        sudo = getattr(queryset, "sudo", None)
        if callable(sudo):
            queryset = sudo(reason="work.task.loaded_stage_category")
        return cast(str | None, queryset.filter(pk=stage_id).values_list("category", flat=True).first())

    def _stage_for_category(self, category: str) -> Stage:
        """Return the first ordered stage in this task's queue for ``category``."""

        stage_model = self.stage_model()
        stage = stage_model.for_container(self.queue).filter(category=category).first()
        if stage is None:
            raise ValidationError({"stage": f"Queue has no {category} stage."})
        return cast(Stage, stage)

    def _base_verb(self, name: str, *args: Any) -> Any:
        """Run a projects-only lifecycle verb for a legacy queue-less task."""

        object.__setattr__(self, "_work_internal_status", True)
        try:
            return getattr(super(), name)(*args)
        finally:
            object.__setattr__(self, "_work_internal_status", False)


class UserWork(AngeeModel):
    """Provision personal queues after IAM user resources load."""

    extends = "iam.User"
    runtime = False

    class Meta:
        """Abstract same-row provisioning donor for IAM users."""

        abstract = True

    @classmethod
    def after_resource_load(
        cls,
        instances: Iterable[Any],
        *,
        tier: str,
        source: str,
        publish: bool = False,
    ) -> None:
        """Ensure every loaded user has exactly one personal queue."""

        del cls, tier, source, publish
        queue_model = apps.get_model("work", "Queue")
        for user in sorted(instances, key=lambda instance: instance.pk or 0):
            queue_model.objects.provision_personal(user)
