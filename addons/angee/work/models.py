"""Operational queues, cycles, triage, and same-row task work mechanics.

``Queue`` is a materialized child of :class:`spaces.Group`: its inherited group
row remains the one roster and hierarchy owner.  ``TaskWork`` is a table-less
donor onto ``projects.Task``; queue, stage, cycle, numbering, and triage
columns therefore exist only when this addon is composed, on the task table
itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, cast

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import IntegrityError, models, transaction
from django.db.models import F, Q
from django.utils import timezone
from django_choices_field import IntegerChoicesField
from rebac import PermissionDenied, current_actor, system_context
from rebac.actors import is_sudo
from rebac.mixins import RebacModelBase

from angee.base.actors import actor_user_id
from angee.base.fields import StateField
from angee.base.mixins import AuditMixin, ImmutableFieldsMixin, clean_trash_reason
from angee.base.models import AngeeDataModel, AngeeManager
from angee.base.refs import canonical_record_target
from angee.base.scoping import system_queryset
from angee.base.stages import Stage as StagePrimitive
from angee.base.stages import StagedModelMixin
from angee.parties.mixins import LinkSource
from angee.resources.mixins import ResourceLoadMixin
from angee.spaces.managers import GroupManager
from angee.work.merge import run_task_merge_contributors


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
                    queue = self.sudo(reason="work.queue.personal.provision.concurrent_lookup").get(slug=slug)
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


class Queue(ImmutableFieldsMixin, metaclass=RebacModelBase):
    """An ongoing operational context, materialized as a ``spaces.Group`` child."""

    runtime = True
    extends = "spaces.Group"
    immutable_fields = ("provision_stages",)

    class EstimateScale(models.TextChoices):
        """Estimation vocabulary configured per queue."""

        NONE = "none", "None"
        LINEAR = "linear", "Linear"
        FIBONACCI = "fibonacci", "Fibonacci"
        EXPONENTIAL = "exponential", "Exponential"
        TSHIRT = "tshirt", "T-shirt"
        HOURS = "hours", "Hours"
        DAYS = "days", "Days"
        WEEKS = "weeks", "Weeks"

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
    provision_stages = models.BooleanField(default=True)
    cycles_enabled = models.BooleanField(default=False)
    cycle_weeks = models.PositiveSmallIntegerField(
        default=2,
        validators=(MinValueValidator(1),),
    )
    cycle_cooldown_weeks = models.PositiveSmallIntegerField(default=0)
    cycle_start_day = IntegerChoicesField(
        choices_enum=CycleStartDay,
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

    def clean(self) -> None:
        """Normalize the queue key and validate its explicit default stage."""

        self.key = self.key.strip().upper()
        super().clean()
        self._validate_default_stage()
        self._validate_estimate_settings()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the queue and atomically provision its stage/numbering substrate."""

        adding = self._state.adding
        self.key = self.key.strip().upper()
        self._validate_default_stage()
        self._validate_estimate_settings()
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
        """Provision numbering and, when requested, the default stage set once."""

        self.ensure_task_sequence()
        if not self.provision_stages:
            return
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
            if self.default_stage_id is None:
                self.default_stage = stages[stage_model.StageCategory.UNSTARTED]
                super().save(update_fields=("default_stage", "updated_at"))

    def _validate_default_stage(self) -> None:
        """Require a same-queue default that can be entered without a verb or rule."""

        if self.default_stage_id is None:
            return
        stage_model = self._meta.get_field("default_stage").related_model
        stage = stage_model._base_manager.get(pk=self.default_stage_id)
        if self.pk is None or stage.queue_id != self.pk:
            raise ValidationError({"default_stage": "Default stage must belong to this queue."})
        if stage.entry_reserved or not stage.ordinary_entry_available:
            raise ValidationError({"default_stage": "Default stage must allow ordinary entry."})

    def _validate_estimate_settings(self) -> None:
        """Reject a default that conflicts with the queue's estimate policy."""

        if self.default_estimate == 0 and not self.estimate_allow_zero:
            raise ValidationError({"default_estimate": "Default estimate cannot be zero when zero is disallowed."})


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
    MANUAL_CATEGORY_FILTERS: ClassVar[dict[str, Any]] = {"rule_owned": False, "conceals": False}

    queue = models.ForeignKey(
        "work.Queue",
        on_delete=models.CASCADE,
        related_name="stages",
    )
    category = StateField(choices_enum=StageCategory, default=StageCategory.UNSTARTED)
    rule_owned = models.BooleanField(default=False)
    conceals = models.BooleanField(default=False)

    class Meta:
        """Django model options for queue stages."""

        abstract = True
        ordering = ("queue", "position", "sqid")
        rebac_resource_type = "work/stage"
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

    @property
    def ordinary_entry_available(self) -> bool:
        """Whether this stage allows entry by ordinary work verbs."""

        return not self.rule_owned and not self.conceals

    @property
    def on_path(self) -> bool:
        """Whether the stage belongs to the queue's ordinary progress path."""

        return not self.conceals and self.category not in (
            self.StageCategory.CANCELED,
            self.StageCategory.DUPLICATE,
        )

    @property
    def entry_reserved(self) -> bool:
        """Whether only the stage's owning verb or rule may enter it."""

        return self.rule_owned or self.category in self.SYSTEM_CATEGORIES

    @property
    def exit_reserved(self) -> bool:
        """Whether only a rule may leave this stage."""

        return self.rule_owned

    @classmethod
    def validate_transition(
        cls,
        previous: Stage | None,
        following: Stage | None,
        *,
        allow_system_entry: bool = False,
        adding: bool = False,
    ) -> None:
        """Validate a hand transition, independent of the caller's authorization context."""

        if previous is not None and following is not None and previous.pk == following.pk:
            return
        if previous is not None and previous.exit_reserved:
            raise ValidationError({"stage": "Only a rule can move a task out of a rule-owned stage."})
        if following is None or not following.entry_reserved:
            return
        if following.rule_owned:
            raise ValidationError({"stage": "Only a rule can move a task into a rule-owned stage."})
        if allow_system_entry:
            return
        verb = "mark_duplicate"
        if following.category == cls.StageCategory.TRIAGE:
            verb = "capture" if adding else "return_to_triage"
        raise ValidationError({"stage": f"Use {verb} to move a task into the system {following.category} stage."})

    @classmethod
    def for_manual_category(cls, container: models.Model, category: str) -> models.QuerySet[Any]:
        """Return ordered category candidates available to hand verbs."""

        return cls.for_container(container).filter(category=category, **cls.MANUAL_CATEGORY_FILTERS)

    @classmethod
    def resolve_default_fallback(cls, stages: models.QuerySet[Any]) -> Any | None:
        """Choose an ordinary unstarted stage when the queue has no explicit default."""

        return stages.filter(category=cls.StageCategory.UNSTARTED, **cls.MANUAL_CATEGORY_FILTERS).first()

    def validate_rule_stage(self, queue_id: Any | None) -> None:
        """Require an active-phase mapping to name a non-concealing rule stage in its queue."""

        if queue_id is not None and self.queue_id != queue_id:
            raise ValidationError({"active_stage": "Active stage must belong to the source task's queue."})
        if not self.rule_owned or self.conceals:
            raise ValidationError({"active_stage": "Active stage must be rule-owned and must not conceal tasks."})

    def clean(self) -> None:
        """Keep the queue's current default available for ordinary entry."""

        super().clean()
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        """Keep stage edits consistent with defaults, phase mappings and promoted tasks."""

        if self.conceals and self.rule_owned:
            raise ValidationError({"conceals": "A rule-owned stage cannot conceal tasks."})
        if self._state.adding:
            return
        if self.entry_reserved or not self.ordinary_entry_available:
            queue_model = self._meta.get_field("queue").related_model
            if queue_model._base_manager.filter(default_stage_id=self.pk).exists():
                field = "conceals" if self.conceals else "rule_owned" if self.rule_owned else "category"
                raise ValidationError({field: "The queue's default stage must allow ordinary entry."})
        # MilestoneWork owns this optional reverse relation in composed model graphs.
        try:
            milestones = self._meta.get_field("active_milestones").related_model
        except FieldDoesNotExist:
            pass
        else:
            mappings = milestones._base_manager.filter(active_stage_id=self.pk)
            if (not self.rule_owned or self.conceals) and mappings.exists():
                raise ValidationError({"rule_owned": "A mapped stage must remain rule-owned and non-concealing."})
            if mappings.filter(project__converted_from__queue__isnull=False).exclude(
                project__converted_from__queue_id=self.queue_id
            ).exists():
                raise ValidationError({"queue": "A mapped stage must remain in its source task's queue."})
        if self.conceals:
            project_model = apps.get_model("projects", "Project")
            if project_model._base_manager.filter(converted_from__stage_id=self.pk).exists():
                raise ValidationError({"conceals": "A stage holding a promoted source task cannot conceal tasks."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Protect system-stage identity and keep the queue's default enterable."""

        self._validate_configuration()
        if not is_sudo():
            persisted = None
            if not self._state.adding:
                # Compare persisted identity without loading unrelated deferred columns.
                persisted = type(self)._base_manager.filter(pk=self.pk).values_list("name", "category").first()
            previous_category = persisted[1] if persisted is not None else None
            if self.category in self.SYSTEM_CATEGORIES and previous_category != self.category:
                raise ValidationError({"category": "Triage and duplicate stages are system-provisioned."})
            if previous_category in self.SYSTEM_CATEGORIES and persisted != (self.name, self.category):
                raise ValidationError({"name": "System-provisioned stages cannot be renamed or recategorized."})
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Prevent users from deleting the two system-provisioned stages."""

        if not is_sudo() and self.category in self.SYSTEM_CATEGORIES:
            raise ValidationError({"category": "System-provisioned stages cannot be deleted."})
        return super().delete(*args, **kwargs)


class CycleManager(AngeeManager):
    """Own idempotent queue-cadence generation and due-cycle closure."""

    @staticmethod
    def _start_on_or_before(value: date, weekday: int) -> date:
        """Return the configured weekday on or immediately before ``value``."""

        return value - timedelta(days=(value.weekday() - weekday) % 7)

    @staticmethod
    def _ends_on(starts_on: date, cycle_weeks: int) -> date:
        """Return the inclusive end date of a cycle beginning on ``starts_on``."""

        return starts_on + timedelta(weeks=cycle_weeks) - timedelta(days=1)

    @staticmethod
    def _next_starts_on(cycle: Any, cooldown_weeks: int) -> date:
        """Return the first date after the cycle and its whole cooldown gap."""

        return cycle.ends_on + timedelta(days=1, weeks=cooldown_weeks)

    def generate_for_queue(
        self,
        queue: models.Model,
        *,
        as_of: date | None = None,
        window_end: date | None = None,
        completed_at: datetime | None = None,
    ) -> tuple[Any, ...]:
        """Generate and close one queue's cadence, returning its ordered cycles.

        Cycle windows are closed date intervals: both ``starts_on`` and
        ``ends_on`` belong to the cycle. The first empty-cadence window starts on
        the configured weekday at or before ``as_of`` (an exact start-day hit is
        included). Each next start is the day after the inclusive end plus exactly
        ``cycle_cooldown_weeks * 7`` calendar dates, which belong to no cycle.
        A cycle closes only when ``as_of > ends_on``; its end day remains active.

        Passing ``window_end`` generates every cadence start through that
        inclusive boundary. Without it, generation maintains the queue's
        configured number of future cycles. The queue row is locked, existing
        windows are reused, and numbers advance from the last row, making reruns
        over the same window exact no-ops.
        """

        as_of = as_of or timezone.localdate()
        if window_end is not None and window_end < as_of:
            raise ValidationError({"window_end": "Cycle window end cannot precede its start."})
        with system_context(reason="work.cycle.generate"), transaction.atomic():
            queue = type(queue).objects.sudo(reason="work.cycle.generate.queue").lock_if_supported().get(pk=queue.pk)
            if not queue.cycles_enabled:
                return ()
            cycles = list(
                self.sudo(reason="work.cycle.generate.rows").filter(queue=queue).order_by("starts_on", "number", "pk")
            )
            if not cycles:
                starts_on = self._start_on_or_before(as_of, int(queue.cycle_start_day))
                cycle = self.model(
                    queue=queue,
                    number=1,
                    starts_on=starts_on,
                    ends_on=self._ends_on(starts_on, int(queue.cycle_weeks)),
                )
                cycle.sudo(reason="work.cycle.generate.first").save()
                cycles.append(cycle)

            while True:
                next_starts_on = self._next_starts_on(
                    cycles[-1],
                    int(queue.cycle_cooldown_weeks),
                )
                if window_end is not None:
                    needs_next = next_starts_on <= window_end
                else:
                    future_count = sum(cycle.starts_on > as_of for cycle in cycles)
                    needs_next = future_count < int(queue.upcoming_cycle_count) or cycles[-1].ends_on < as_of
                if not needs_next:
                    break
                cycle = self.model(
                    queue=queue,
                    number=cycles[-1].number + 1,
                    starts_on=next_starts_on,
                    ends_on=self._ends_on(next_starts_on, int(queue.cycle_weeks)),
                )
                cycle.sudo(reason="work.cycle.generate.next").save()
                cycles.append(cycle)

            for index, cycle in enumerate(cycles[:-1]):
                if cycle.completed_at is None and cycle.ends_on < as_of:
                    cycle.close(
                        next_cycle=cycles[index + 1],
                        completed_at=completed_at,
                    )
            return tuple(
                self.sudo(reason="work.cycle.generate.result").filter(queue=queue).order_by("starts_on", "number", "pk")
            )


class Cycle(AuditMixin, AngeeDataModel):
    """One generated, queue-scoped planning window with immutable close evidence."""

    runtime = True
    sqid_prefix = "cyc_"

    queue = models.ForeignKey(
        "work.Queue",
        on_delete=models.CASCADE,
        related_name="cycles",
    )
    number = models.PositiveIntegerField()
    name = models.CharField(max_length=160, blank=True, default="")
    starts_on = models.DateField()
    ends_on = models.DateField()
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    uncompleted_upon_close = models.JSONField(blank=True, default=list, editable=False)

    objects = CycleManager()

    class Meta:
        """Django model options for generated queue cycles."""

        abstract = True
        ordering = ("queue", "number", "sqid")
        rebac_resource_type = "work/cycle"
        constraints = (
            models.UniqueConstraint(
                fields=("queue", "number"),
                name="uq_work_cycle_queue_number",
            ),
            models.CheckConstraint(
                condition=Q(ends_on__gte=F("starts_on")),
                name="ck_work_cycle_ordered_dates",
            ),
        )
        indexes = (models.Index(fields=("queue", "starts_on", "ends_on")),)

    @property
    def display_name(self) -> str:
        """Return the optional custom name or its stable ``Cycle N`` fallback."""

        return self.name or f"Cycle {self.number}"

    def __str__(self) -> str:
        """Return the custom or generated cycle label."""

        return self.display_name

    def clean(self) -> None:
        """Reject an inverted generated date window."""

        super().clean()
        if self.starts_on is not None and self.ends_on is not None and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "Cycle end must be on or after its start."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist while keeping close timestamp and snapshot immutable."""

        if self.starts_on is not None and self.ends_on is not None and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "Cycle end must be on or after its start."})
        if self.pk is not None and not self._state.adding:
            with system_context(reason="work.cycle.immutable_close"):
                persisted = (
                    type(self)
                    ._base_manager.filter(pk=self.pk)
                    .values(
                        "completed_at",
                        "uncompleted_upon_close",
                    )
                    .first()
                )
            if (
                persisted is not None
                and persisted["completed_at"] is not None
                and (
                    self.completed_at != persisted["completed_at"]
                    or self.uncompleted_upon_close != persisted["uncompleted_upon_close"]
                )
            ):
                raise ValidationError("A cycle's completed state and close snapshot are immutable.")
        super().save(*args, **kwargs)

    def close(
        self,
        *,
        next_cycle: Any | None = None,
        completed_at: datetime | None = None,
    ) -> Cycle:
        """Snapshot open tasks and roll them into the next cycle atomically.

        ``uncompleted_upon_close`` stores the sorted public task ids exactly as
        they stood immediately before rollover. Reinvoking a completed cycle is
        an exact no-op and cannot replace that evidence or move tasks again.
        """

        if self.pk is None:
            raise ValidationError("A cycle must be saved before it can close.")
        with system_context(reason="work.cycle.close"), transaction.atomic():
            cycles = type(self).objects.sudo(reason="work.cycle.close.rows").lock_if_supported()
            locked = cycles.get(pk=self.pk)
            if locked.completed_at is not None:
                self.completed_at = locked.completed_at
                self.uncompleted_upon_close = locked.uncompleted_upon_close
                return self
            if next_cycle is None:
                next_cycle = (
                    cycles.filter(queue_id=locked.queue_id, starts_on__gt=locked.starts_on)
                    .order_by("starts_on", "number", "pk")
                    .first()
                )
            else:
                next_cycle = cycles.get(pk=next_cycle.pk)
            if next_cycle is None:
                raise ValidationError({"next_cycle": "Generate the next cycle before closing."})
            if next_cycle.queue_id != locked.queue_id:
                raise ValidationError({"next_cycle": "Rollover cycle must belong to the same queue."})
            if next_cycle.starts_on <= locked.ends_on:
                raise ValidationError({"next_cycle": "Rollover cycle must start after this cycle ends."})

            task_model = apps.get_model("projects", "Task")
            tasks = list(
                task_model.objects.sudo(reason="work.cycle.close.tasks")
                .lock_if_supported()
                .filter(cycle_id=locked.pk, status=task_model.TaskStatus.OPEN)
                .order_by("pk")
            )
            snapshot = sorted(str(task.sqid) for task in tasks)
            closed_at = completed_at or timezone.now()
            if tasks:
                task_model._base_manager.filter(pk__in=[task.pk for task in tasks]).update(
                    cycle_id=next_cycle.pk,
                    updated_at=closed_at,
                )
            locked.completed_at = closed_at
            locked.uncompleted_upon_close = snapshot
            locked.sudo(reason="work.cycle.close.snapshot").save(
                update_fields=("completed_at", "uncompleted_upon_close", "updated_at")
            )
        # Return the committed state written through the separately locked row.
        self.refresh_from_db()
        return self


_WORK_UNLOADED = object()
"""Snapshot marker for a queue/stage id whose column was deferred at load.

Snapshots must read ``__dict__``, never the field descriptor: touching a
deferred ``queue_id``/``stage_id`` during ``__init__`` or ``refresh_from_db``
fires a nested partial refresh whose reload re-enters ``__init__`` with the
*other* id deferred — an unbounded mutual recursion of one query per level.
"""


class ProjectWork(models.Model):
    """Bind a project's access to the roster of an optional team."""

    extends = "projects.Project"

    hasura_readable_fields = ("team",)
    hasura_filterable_fields = hasura_readable_fields
    hasura_insertable_fields = hasura_readable_fields
    hasura_updatable_fields = hasura_readable_fields

    team = models.ForeignKey(
        "spaces.Group",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="projects",
    )

    @transaction.atomic
    def admit_manager(self, user: models.Model) -> models.Model:
        """Add a team moderator and follow this project in the same transaction."""

        if not self.has_access("share") or self.team_id is None:
            raise PermissionDenied("Project share and a team are required to add a manager.")
        if getattr(user, "kind", None) != "person":
            raise ValidationError({"user": "Choose a person account."})
        with system_context(reason="work.project.manager.party"):
            party = apps.get_model("parties", "Party").objects.for_user(user)
        membership_model = apps.get_model("spaces", "Membership")
        membership = membership_model.objects.add_confirmed(
            group=self.team, party=party, role=membership_model.MembershipRole.MODERATOR,
        )
        with system_context(reason="work.project.manager.follow"):
            self.message_subscribe(user=user)
        return membership

    @transaction.atomic
    def remove_manager(self, membership: models.Model) -> None:
        """Dismiss one team moderator through the roster's removal owner."""

        if not self.has_access("share") or self.team_id is None:
            raise PermissionDenied("Project share and a team are required to remove a manager.")
        if membership.group_id != self.team_id or membership.role != membership.MembershipRole.MODERATOR:
            raise ValidationError({"membership": "Choose a manager of this project's team."})
        if not membership.has_access("write"):
            raise PermissionDenied("Team roster management is required.")
        membership.dismiss()

    class Meta:
        """Abstract contribution folded into the concrete project table."""

        abstract = True

    def apply_setup(self, *, team: Any | None = None, **options: Any) -> None:
        """Set the declared team through the ordinary field-sharing gate."""

        if team is None:
            raise ValidationError({"team": "Choose a project team."})
        if self.team_id != team.pk:
            self.team = team
            self.save(update_fields=("team", "updated_at"))
        super().apply_setup(**options)

    @classmethod
    def setup_complete_condition(cls, actor: Any) -> models.Q:
        """A work-enabled project also needs its team configured."""

        return super().setup_complete_condition(actor) & models.Q(team__isnull=False)

    @transaction.atomic
    def sync_source_task_stage(self) -> None:
        """Apply the current project rule inside its lifecycle event transaction."""

        if self.converted_from_id is None:
            return
        task_model = self._meta.get_field("converted_from").related_model
        with system_context(reason="work.project.source_task_stage"):
            task = task_model.objects.lock_if_supported().get(pk=self.converted_from_id)
            stage = task.rule_stage(self)
            if stage is None or task.stage_id == stage.pk:
                return
            task.stage = stage
            with task._work_verb_write():
                task.save(update_fields=("stage", "updated_at"))


class MilestoneWork(models.Model):
    """Map a project's current phase to its source task's rule-owned stage."""

    extends = "projects.Milestone"

    hasura_readable_fields = ("active_stage",)
    hasura_filterable_fields = hasura_readable_fields
    hasura_insertable_fields = hasura_readable_fields
    hasura_updatable_fields = hasura_readable_fields

    active_stage = models.ForeignKey(
        "work.Stage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="active_milestones",
    )

    class Meta:
        """Abstract contribution folded into the concrete milestone table."""

        abstract = True

    @classmethod
    def setup_template_values(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve an optional stage choice while the milestone owns validation."""

        if not isinstance(values, Mapping):
            raise ValidationError({"milestones": "Each milestone template must be an object."})
        values = dict(values)
        stage = values.pop("active_stage", None)
        return {
            **super().setup_template_values(values),
            "active_stage": stage,
        }

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Validate phase mappings once at persistence, including generated writes."""

        update_fields = kwargs.get("update_fields")
        if update_fields is None or {"active_stage", "active_stage_id", "project", "project_id"}.intersection(
            update_fields
        ):
            self._validate_active_stage()
        super().save(*args, **kwargs)

    def _validate_active_stage(self) -> None:
        """Ask the stage owner to validate the source queue and rule reservation."""

        if self.active_stage_id is None:
            return
        project_model = self._meta.get_field("project").related_model
        queue_id = (
            project_model._base_manager.filter(pk=self.project_id)
            .values_list("converted_from__queue_id", flat=True)
            .first()
        )
        stage_model = self._meta.get_field("active_stage").related_model
        stage_model._base_manager.get(pk=self.active_stage_id).validate_rule_stage(queue_id)


class TaskWork(StagedModelMixin):
    """Same-row work contribution folded into ``projects.Task``."""

    extends = "projects.Task"
    stage_container_field_name = "queue"
    thread_team_field = "queue"

    if TYPE_CHECKING:
        queue_id: Any | None
        stage_id: Any | None
        cycle_id: Any | None
        snoozed_by_id: Any | None
        dropped_at: datetime | None

    work_stage_projection = True
    hasura_readable_fields = (
        "queue",
        "stage",
        "cycle",
        "number",
        "estimate",
        "snoozed_until",
        "snoozed_by",
        "started_triage_at",
        "triaged_at",
    )
    hasura_filterable_fields = hasura_readable_fields
    # A stable scope key filters readable tasks without revealing the queue row.
    hasura_container_scope_fields = ("queue__slug",)
    hasura_sortable_fields = (
        "queue",
        "stage",
        "stage__position",
        "cycle",
        "number",
        "estimate",
        "snoozed_until",
        "started_triage_at",
        "triaged_at",
    )
    hasura_aggregatable_fields = ("number", "estimate")
    hasura_groupable_fields = ("queue", "stage", "cycle")
    # Projected as objects by the work type extension; the node class cannot
    # see extension fields, so selection layers read this declaration.
    hasura_insertable_fields = ("queue", "stage", "cycle", "estimate")
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
    cycle = models.ForeignKey(
        "work.Cycle",
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
        self._work_snapshot_loaded_values()

    # Django QuerySet.update() bypasses the A1 authored-status and verb-only
    # system-stage boundaries by nature; the API routes through instance saves,
    # and internal bulk writers are on their honor.
    def __setattr__(self, name: str, value: Any) -> None:
        """Remember direct status assignment while allowing the projection writer."""

        if (
            name == "status"
            and getattr(self, "_work_track_status", False)
            and not getattr(self, "_work_internal_status", False)
        ):
            object.__setattr__(self, "_work_status_assigned", True)
        super().__setattr__(name, value)

    @contextmanager
    def _work_verb_write(self) -> Iterator[None]:
        """Let one owning verb perform lifecycle projection or enter a system stage."""

        internal = getattr(self, "_work_internal_status", False)
        object.__setattr__(self, "_work_internal_status", True)
        try:
            yield
        finally:
            object.__setattr__(self, "_work_internal_status", internal)

    def refresh_from_db(self, *args: Any, **kwargs: Any) -> None:
        """Refresh without misclassifying Django's field hydration as a direct write."""

        fields = kwargs.get("fields", args[1] if len(args) > 1 else None)
        status_assigned = getattr(self, "_work_status_assigned", False)
        object.__setattr__(self, "_work_track_status", False)
        try:
            super().refresh_from_db(*args, **kwargs)
        finally:
            object.__setattr__(self, "_work_track_status", True)
            if fields is None or "status" in fields:
                status_assigned = False
            object.__setattr__(self, "_work_status_assigned", status_assigned)
            self._work_snapshot_loaded_values(fields=fields)

    def _work_snapshot_loaded_values(self, *, fields: Iterable[str] | None = None) -> None:
        """Snapshot loaded work values without touching deferred columns."""

        for attname in ("queue_id", "stage_id", "estimate"):
            if fields is not None and not {attname, attname.removesuffix("_id")}.intersection(fields):
                continue
            value = self.__dict__.get(attname, _WORK_UNLOADED)
            object.__setattr__(self, f"_work_loaded_{attname}", value)

    def _work_loaded_value(self, attname: str) -> Any:
        """Return an as-loaded work value, resolving a deferred column only on demand."""

        snapshot = getattr(self, f"_work_loaded_{attname}", _WORK_UNLOADED)
        if snapshot is not _WORK_UNLOADED:
            return snapshot
        if attname in self.__dict__:
            # Assigned after being loaded deferred: the loaded value is gone
            # from the instance, so ask the row itself.
            value = type(self)._base_manager.filter(pk=self.pk).values_list(attname, flat=True).first()
        else:
            # Still deferred means never assigned: the lazy load below IS the
            # loaded value (and no longer recurses, per _work_snapshot_loaded_values).
            value = getattr(self, attname)
        object.__setattr__(self, f"_work_loaded_{attname}", value)
        return value

    def full_clean(self, *args: Any, **kwargs: Any) -> None:
        """Let Django normalize fields without inventing a direct status write.

        ``Model.clean_fields()`` assigns every cleaned value back through the
        field descriptor. Preserve a status assignment already made by the
        caller, but do not treat that framework-owned normalization as a new
        authored assignment. This keeps resource loading compatible with the
        direct-status rejection enforced by :meth:`clean` and :meth:`save`.
        """

        tracking = getattr(self, "_work_track_status", True)
        object.__setattr__(self, "_work_track_status", False)
        try:
            super().full_clean(*args, **kwargs)
        finally:
            object.__setattr__(self, "_work_track_status", tracking)

    def clean(self) -> None:
        """Project a stage before base lifecycle validation and enforce scope."""

        self._apply_queue_and_stage_defaults(provision=False)
        self._validate_estimate()
        previous, following = self._reject_reserved_stage_transition()
        self._reject_direct_status_write()
        self._project_stage_lifecycle(previous, following)
        self.validate_cycle_scope()
        super().clean()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist stage projection and queue numbering in one transaction."""

        self._reject_direct_status_write()
        update_fields = set(kwargs["update_fields"]) if kwargs.get("update_fields") is not None else None
        stage_written = self._state.adding or update_fields is None or bool({"stage", "stage_id"} & update_fields)
        with transaction.atomic():
            defaulted = self._apply_queue_and_stage_defaults(provision=True)
            defaulted.update(self._validate_estimate(update_fields=update_fields))
            if not stage_written:
                defaulted.discard("stage")
            previous, following = self._reject_reserved_stage_transition(lock=True, stage_written=stage_written)
            self.validate_stage_scope()
            self.validate_cycle_scope()
            projected = self._project_stage_lifecycle(previous, following) if stage_written else set()
            allocated = False
            if self._state.adding and self.queue_id is not None and self.number is None:
                queue = self.queue
                assert queue is not None
                self.number = queue.next_task_number()
                allocated = True
            if update_fields is not None:
                update_fields.update(projected)
                update_fields.update(defaulted)
                if allocated:
                    update_fields.add("number")
                kwargs["update_fields"] = update_fields
            super().save(*args, **kwargs)
        object.__setattr__(self, "_work_status_assigned", False)
        self._work_snapshot_loaded_values(fields=update_fields)

    @transaction.atomic
    def start(self) -> Any:
        """Start queued work; accept triage or reopen closed work first."""

        if self.queue_id is None:
            return self
        previous, _following = self._reject_reserved_stage_transition(manual=True, lock=True)
        category = previous.category if previous is not None else None
        if category == "started":
            return self
        if category == "triage":
            raise ValidationError({"stage": "Accept a task in triage before starting it."})
        if category in {"completed", "canceled", "duplicate"}:
            raise ValidationError({"stage": "Reopen a closed task before starting it."})
        stage = self._stage_for_category("started")
        Stage.validate_transition(previous, stage)
        self.stage = stage
        self.save(update_fields=("stage", "updated_at"))
        return self

    @transaction.atomic
    def complete(self) -> Any:
        """Move to the first completed stage, or use the base verb without a queue."""

        if self.queue_id is None:
            return self._base_verb("complete")
        stage = self._stage_for_category("completed")
        self._reject_reserved_stage_transition(target=stage, manual=True, lock=True)
        self.stage = stage
        self.save(update_fields=("stage", "updated_at"))
        return self

    @transaction.atomic
    def drop(self, reason: Any) -> Any:
        """Move to duplicate/canceled according to the base dropped reason."""

        try:
            reason_member = self.TaskDroppedReason(getattr(reason, "value", reason))
        except ValueError as error:
            raise ValidationError({"reason": "Choose duplicate, declined, or obsolete."}) from error
        if self.queue_id is None:
            return self._base_verb("drop", reason_member)
        category = "duplicate" if reason_member == self.TaskDroppedReason.DUPLICATE else "canceled"
        stage = self._stage_for_category(category)
        self._reject_reserved_stage_transition(
            target=stage, manual=True, allow_system_entry=category == "duplicate", lock=True
        )
        self.stage = stage
        self.dropped_reason = reason_member
        if category == "duplicate":
            self._reject_direct_status_write()
            with self._work_verb_write():
                self.save(update_fields=("stage", "dropped_reason", "updated_at"))
        else:
            self.save(update_fields=("stage", "dropped_reason", "updated_at"))
        return self

    @transaction.atomic
    def reopen(self) -> Any:
        """Move to the queue-owned default stage, or use the base verb without a queue."""

        if self.queue_id is None:
            return self._base_verb("reopen")
        stage = self.resolve_default_stage()
        if stage is None:
            raise ValidationError({"stage": "Queue has no default stage."})
        self._reject_reserved_stage_transition(target=cast(Stage, stage), manual=True, lock=True)
        self.stage = stage
        self.save(update_fields=("stage", "updated_at"))
        return self

    def _require_work_action(self, expected_revision: int | None) -> None:
        """Recheck the locked, fresh row before a hand verb, including no-op replay."""

        system_queryset(type(self), lock=("self",)).get(pk=self.pk)
        self.refresh_from_db()
        actor, elevated = self.effective_actor(strict=True)
        if not elevated and not self.with_actor(actor).has_access("write"):
            raise PermissionDenied("Task write access is required.")
        if expected_revision is not None:
            self.require_revision(expected_revision)

    @transaction.atomic
    def remove(self, *, reason: str = "", expected_revision: int | None = None) -> Any:
        """Conceal an unpromoted task in its queue; never physically delete it.

        The optional reason (at most 1000 characters) is the removal's history
        change reason, beside the stage it left for :meth:`restore`.
        """

        reason = clean_trash_reason(reason)
        self._require_work_action(expected_revision)
        if apps.get_model("projects", "Project")._base_manager.filter(converted_from_id=self.pk).exists():
            raise ValidationError({"stage": "A promoted task cannot be removed."})
        target = self.stage_model().for_container(self.queue).filter(conceals=True).first()
        if target is None:
            raise ValidationError({"stage": "The queue has no concealing stage."})
        previous, _following = self._reject_reserved_stage_transition(target=target, lock=True)
        Stage.validate_transition(previous, target)
        if self.stage_id != target.pk:
            self.stage = target
            self._change_reason = reason or None
            try:
                self.save(update_fields=("stage", "updated_at"))
            finally:
                del self._change_reason
        return self

    @transaction.atomic
    def restore(self, *, expected_revision: int | None = None) -> Any:
        """Return a removed task to the stage it held before removal.

        History owns that stage. When it no longer admits the task by hand
        (deleted, concealing or rule-owned), the queue's default stage serves.
        Restoration changes only the stage: today's permissions decide access.
        """

        self._require_work_action(expected_revision)
        if not self.stage_model()._base_manager.filter(pk=self.stage_id, conceals=True).exists():
            raise ValidationError({"stage": "Only a removed task can be restored."})
        target = self._stage_before_removal() or self.resolve_default_stage()
        if target is None:
            raise ValidationError({"stage": "Queue has no default stage."})
        system = target.category in Stage.SYSTEM_CATEGORIES
        self._reject_reserved_stage_transition(target=target, manual=True, allow_system_entry=system, lock=True)
        self.stage = target
        if not system:
            self.save(update_fields=("stage", "updated_at"))
            return self
        self._reject_direct_status_write()
        with self._work_verb_write():
            self.save(update_fields=("stage", "updated_at"))
        return self

    def _stage_before_removal(self) -> Stage | None:
        """Return the stage history shows before the latest concealment, if still enterable."""

        earlier = (
            self.history.exclude(stage_id=self.stage_id)
            .exclude(stage_id=None)
            .order_by("-history_date", "-history_id")
            .values_list("stage_id", flat=True)
            .first()
        )
        if earlier is None:
            return None
        return cast(
            "Stage | None",
            self.stage_model()._base_manager.filter(
                pk=earlier, queue_id=self.queue_id, conceals=False, rule_owned=False,
            ).first(),
        )

    @transaction.atomic
    def accept(self, stage: models.Model | None = None, *, expected_revision: int | None = None) -> Any:
        """Leave triage for a same-queue, non-system stage, idempotently."""

        self._require_work_action(expected_revision)
        if self.queue_id is None:
            raise ValidationError({"queue": "A queued task is required for triage."})
        target = stage or self.resolve_default_stage()
        if target is None:
            raise ValidationError({"stage": "Queue has no default stage."})
        if target.queue_id != self.queue_id:
            raise ValidationError({"stage": "Accepted stage must belong to the task's queue."})
        previous, _following = self._reject_reserved_stage_transition(
            target=cast(Stage, target), manual=True, lock=True
        )
        if self.stage_id == target.pk:
            if (
                self.status == self.TaskStatus.OPEN
                and self.done_at is None
                and self.dropped_reason is None
                and self.dropped_at is None
            ):
                return self
            self.save(update_fields=("stage", "updated_at"))
            return self
        if previous is None or previous.category != self.stage_model().StageCategory.TRIAGE:
            raise ValidationError({"stage": "Only a task in triage can be accepted."})
        self.stage = target
        self.save(update_fields=("stage", "updated_at"))
        return self

    @transaction.atomic
    def return_to_triage(self) -> Any:
        """Return work to its triage-enabled queue's triage stage, idempotently."""

        if self.queue_id is None or not self.queue.triage_enabled:
            raise ValidationError({"queue": "Returning to triage requires a triage-enabled queue."})
        stage = self._stage_for_category("triage")
        self._reject_reserved_stage_transition(target=stage, manual=True, allow_system_entry=True, lock=True)
        if self.stage_id == stage.pk:
            return self
        self._reject_direct_status_write()
        self.stage = stage
        with self._work_verb_write():
            self.save(update_fields=("stage", "updated_at"))
        return self

    @transaction.atomic
    def decline(self, reason: Any, *, expected_revision: int | None = None) -> Any:
        """Leave triage for the canceled stage with a closed dropped reason."""

        self._require_work_action(expected_revision)
        try:
            reason_member = self.TaskDroppedReason(getattr(reason, "value", reason))
        except ValueError as error:
            raise ValidationError({"reason": "Choose declined or obsolete."}) from error
        if reason_member not in {
            self.TaskDroppedReason.DECLINED,
            self.TaskDroppedReason.OBSOLETE,
        }:
            raise ValidationError({"reason": "Choose declined or obsolete."})
        if self.queue_id is None:
            raise ValidationError({"queue": "A queued task is required for triage."})
        target = self._stage_for_category("canceled")
        previous, _following = self._reject_reserved_stage_transition(target=target, manual=True, lock=True)
        if (
            self.stage_id == target.pk
            and self.status == self.TaskStatus.DROPPED
            and self.dropped_reason == reason_member
            and self.dropped_at is not None
            and self.done_at is None
        ):
            return self
        if previous is None or previous.category != self.stage_model().StageCategory.TRIAGE:
            raise ValidationError({"stage": "Only a task in triage can be declined."})
        self.stage = target
        self.dropped_reason = reason_member
        self.save(update_fields=("stage", "dropped_reason", "updated_at"))
        return self

    def snooze(self, until: datetime) -> Any:
        """Snooze a triage task until an inclusive instant or new chatter activity."""

        if timezone.is_naive(until):
            raise ValidationError({"until": "Snooze time must include a timezone."})
        if until <= timezone.now():
            raise ValidationError({"until": "Snooze time must be in the future."})
        user_id = actor_user_id(current_actor())
        if user_id is None:
            raise ValidationError({"snoozed_by": "Snoozing requires a user-backed actor."})
        category = self._stage_category(self.stage_id) if self.queue_id is not None else None
        if category != self.stage_model().StageCategory.TRIAGE:
            raise ValidationError({"stage": "Only a task in triage can be snoozed."})
        if self.snoozed_until == until and str(self.snoozed_by_id) == str(user_id):
            return self
        self.snoozed_until = until
        self.snoozed_by_id = user_id
        self.save(update_fields=("snoozed_until", "snoozed_by", "updated_at"))
        return self

    @classmethod
    def wake_due_snoozes(cls, *, now: datetime | None = None) -> int:
        """Clear snoozes whose inclusive wake instant is at or before ``now``."""

        now = now or timezone.now()
        return int(
            cls._base_manager.filter(
                snoozed_until__isnull=False,
                snoozed_until__lte=now,
            ).update(
                snoozed_until=None,
                snoozed_by=None,
                updated_at=now,
            )
        )

    @classmethod
    def wake_from_chatter_thread(cls, thread_id: Any) -> int:
        """Clear snooze state on the task whose chatter owns ``thread_id``."""

        if thread_id is None:
            return 0
        task_content_type = ContentType.objects.get_for_model(cls)
        attachment_model = apps.get_model("messaging", "ThreadAttachment")
        task_ids = attachment_model._base_manager.filter(
            content_type=task_content_type,
            thread_id=thread_id,
            role="chatter",
        ).values("object_id")
        snoozed_tasks = cls._base_manager.filter(
            pk__in=models.Subquery(task_ids),
            snoozed_until__isnull=False,
        )
        if not snoozed_tasks.exists():
            return 0
        return int(
            snoozed_tasks.update(
                snoozed_until=None,
                snoozed_by=None,
                updated_at=timezone.now(),
            )
        )

    def mark_duplicate(self, canonical: models.Model) -> Any:
        """Merge this duplicate into ``canonical`` in one row-locked transaction.

        The atomic postcondition is one directional duplicate relation, this
        task in its system duplicate stage (and therefore dropped/duplicate), all
        source links and followers moved with collision deduplication, followed
        by every deterministic ``ANGEE_WORK_MERGE_CONTRIBUTORS`` mover. Any
        failure rolls every one of those writes back.
        """

        if self.pk is None or canonical.pk is None:
            raise ValidationError("Both duplicate and canonical tasks must be saved.")
        if self.pk == canonical.pk:
            raise ValidationError({"canonical": "A task cannot duplicate itself."})
        with system_context(reason="work.task.mark_duplicate"), transaction.atomic():
            rows = list(
                type(self)
                .objects.sudo(reason="work.task.mark_duplicate.rows")
                .lock_if_supported()
                .filter(pk__in=(self.pk, canonical.pk))
                .order_by("pk")
            )
            by_pk = {row.pk: row for row in rows}
            if self.pk not in by_pk or canonical.pk not in by_pk:
                raise ValidationError({"canonical": "Duplicate or canonical task no longer exists."})
            source = by_pk[self.pk]
            canonical = by_pk[canonical.pk]
            if source.queue_id is None:
                raise ValidationError({"queue": "A queued task is required for duplicate triage."})
            duplicate_stage = source._stage_for_category("duplicate")
            source._reject_reserved_stage_transition(
                target=duplicate_stage, manual=True, allow_system_entry=True, lock=True
            )

            relation_model = apps.get_model("projects", "TaskRelation")
            duplicate_kind = relation_model.TaskRelationKind.DUPLICATE
            if (
                relation_model.objects.sudo(reason="work.task.mark_duplicate.reverse_relation_lookup")
                .filter(
                    task=canonical,
                    related_task=source,
                    kind=duplicate_kind,
                )
                .exists()
            ):
                raise ValidationError({"canonical": "A task and its canonical cannot be mutual duplicates."})
            canonical_stage = canonical.stage
            canonical_is_duplicate = (
                canonical_stage is not None
                and canonical_stage.category == canonical.stage_model().StageCategory.DUPLICATE
            ) or relation_model.objects.sudo(reason="work.task.mark_duplicate.canonical_relation_lookup").filter(
                task=canonical,
                kind=duplicate_kind,
            ).exists()
            if canonical_is_duplicate:
                raise ValidationError(
                    {
                        "canonical": (
                            "The canonical task is itself a duplicate; merge into the ultimate canonical instead."
                        )
                    }
                )
            existing = (
                relation_model.objects.sudo(reason="work.task.mark_duplicate.relation_lookup")
                .filter(task=source, kind=duplicate_kind)
                .first()
            )
            if existing is not None and existing.related_task_id != canonical.pk:
                raise ValidationError({"canonical": "Task already names a different canonical task."})
            relation, _created = relation_model.objects.get_or_create(
                task=source,
                related_task=canonical,
                defaults={"kind": duplicate_kind},
            )
            if relation.kind != duplicate_kind:
                raise ValidationError({"canonical": "Another relation already occupies this task pair."})

            already_projected = (
                source.stage_id == duplicate_stage.pk
                and source.status == source.TaskStatus.DROPPED
                and source.dropped_reason == source.TaskDroppedReason.DUPLICATE
                and source.dropped_at is not None
                and source.done_at is None
            )
            if not already_projected:
                source_stage = source.stage
                if source_stage is None or source_stage.category not in {
                    source.stage_model().StageCategory.TRIAGE,
                    source.stage_model().StageCategory.DUPLICATE,
                }:
                    raise ValidationError({"stage": "Only a task in triage can be marked duplicate."})
                source.stage = duplicate_stage
                source.dropped_reason = source.TaskDroppedReason.DUPLICATE
                with source._work_verb_write():
                    source.save(update_fields=("stage", "dropped_reason", "updated_at"))

            source._move_links_to(canonical)
            source._move_followers_to(canonical)
            run_task_merge_contributors(source, canonical)
        # Return the committed state written through the separately locked row.
        self.refresh_from_db()
        return self

    @property
    def work_key(self) -> str | None:
        """Return the queue-keyed task number (for example ``ENG-42``)."""

        if self.queue_id is None or self.number is None:
            return None
        return f"{self.queue.key}-{self.number}"

    def validate_cycle_scope(self) -> None:
        """Reject a cycle outside the task's queue."""

        if self.cycle_id is None:
            return
        cycle_model = self._meta.get_field("cycle").related_model
        cycle_queue_id = (
            cycle_model._base_manager.values_list("queue_id", flat=True).get(pk=self.cycle_id)
            if self.queue_id is not None
            else None
        )
        if cycle_queue_id is None or cycle_queue_id != self.queue_id:
            raise ValidationError({"cycle": "Cycle must belong to the task's queue."})

    def _reject_direct_status_write(self) -> None:
        """Reject every caller-authored status assignment while work is composed."""

        if getattr(self, "_work_status_assigned", False) and not getattr(
            self,
            "_work_internal_status",
            False,
        ):
            raise ValidationError({"status": "Set stage instead; status is projected by the work addon."})

    def _reject_reserved_stage_transition(
        self,
        *,
        target: Stage | None = None,
        manual: bool = False,
        allow_system_entry: bool = False,
        lock: bool = False,
        stage_written: bool = True,
    ) -> tuple[Stage | None, Stage | None]:
        """Read current stage facts and apply the Stage owner's reservation check.

        Saves and hand verbs hold the task row lock until their transaction ends.
        Only direct system writes and explicit rule writes bypass reservations;
        hand verbs always validate, including when called under sudo. Partial
        saves that omit stage keep the persisted stage as the projection target.
        """

        previous_id = None
        if not self._state.adding:
            rows = type(self).objects.sudo(reason="work.task.stage_transition")
            if lock:
                rows = rows.lock_if_supported()
            previous_id = rows.values_list("stage_id", flat=True).get(pk=self.pk)
        target_id = target.pk if target is not None else self.stage_id
        if not stage_written:
            target_id = previous_id
        stages = {stage.pk: stage for stage in self.stage_model()._base_manager.filter(pk__in=(previous_id, target_id))}
        previous = stages.get(previous_id)
        following = stages.get(target_id)
        changed = previous_id != target_id
        if changed and manual and following is not None and not following.ordinary_entry_available:
            raise ValidationError({"stage": "Hand verbs cannot enter a rule-owned or concealing stage."})
        if changed and following is not None and following.conceals and not self._state.adding:
            project_model = apps.get_model("projects", "Project")
            if project_model._base_manager.filter(converted_from_id=self.pk).exists():
                raise ValidationError({"stage": "A task with a promoted project cannot enter a concealing stage."})
        if manual or not (getattr(self, "_work_internal_status", False) or is_sudo()):
            Stage.validate_transition(
                previous, following, allow_system_entry=allow_system_entry, adding=self._state.adding
            )
        return previous, following

    def _apply_queue_and_stage_defaults(self, *, provision: bool) -> set[str]:
        """Infer queue from cycle/stage, then personal queue and default stage."""

        changed: set[str] = set()
        queue_cleared = self.queue_id is None and self._work_loaded_value("queue_id") is not None
        stage_cleared = self.stage_id is None and self._work_loaded_value("stage_id") is not None
        if queue_cleared != stage_cleared or (queue_cleared and self.cycle_id is not None):
            raise ValidationError("stage and cycle imply their queue — clear queue, stage, and cycle together")
        if self.queue_id is None and self.cycle_id is not None:
            cycle_model = self._meta.get_field("cycle").related_model
            self.queue_id = cycle_model._base_manager.values_list("queue_id", flat=True).get(pk=self.cycle_id)
            changed.add("queue")
        if self.queue_id is None and self.stage_id is not None:
            self.queue_id = self.stage_model()._base_manager.values_list("queue_id", flat=True).get(pk=self.stage_id)
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

    def _validate_estimate(self, *, update_fields: set[str] | None = None) -> set[str]:
        """Apply creation defaults and validate new zeros against current queue policy."""

        if not self._state.adding:
            written = update_fields
            if written is not None and not {"estimate", "queue", "queue_id"}.intersection(written):
                return set()
            estimate_changed = (written is None or "estimate" in written) and (
                self.estimate != self._work_loaded_value("estimate")
            )
            queue_changed = (written is None or bool({"queue", "queue_id"}.intersection(written))) and (
                self.queue_id != self._work_loaded_value("queue_id")
            )
            if not estimate_changed and not queue_changed:
                return set()
        needs_default = self._state.adding and self.estimate is None
        if self.queue_id is None or (not needs_default and self.estimate != 0):
            return set()
        queue = self.queue
        fields: tuple[str, ...] = ("estimate_allow_zero", "default_estimate")
        if needs_default:
            fields += ("estimate_scale",)
        with system_context(reason="work.task.estimate_policy"):
            queue.refresh_from_db(fields=fields)
        changed = set()
        if needs_default and queue.estimate_scale != Queue.EstimateScale.NONE:
            self.estimate = queue.default_estimate
            changed.add("estimate")
        if self.estimate == 0 and not queue.estimate_allow_zero:
            raise ValidationError({"estimate": "This queue does not allow a zero estimate."})
        return changed

    def _project_stage_lifecycle(self, previous: Stage | None, following: Stage | None) -> set[str]:
        """Project lifecycle fields from the stage facts read for this transition."""

        if following is None:
            return set()
        category = following.category
        now = timezone.now()
        stage_changed = previous is None or previous.pk != following.pk
        old_category = previous.category if previous is not None else None

        with self._work_verb_write():
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
        """Read the persisted category, independent of cached or edited stage objects."""

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
        stage = cast(type[Stage], stage_model).for_manual_category(self.queue, category).first()
        if stage is None:
            raise ValidationError({"stage": f"Queue has no {category} stage."})
        return cast(Stage, stage)

    def rule_stage(self, project: Any) -> Stage | None:
        """Resolve the source-task stage from cancellation or the current phase, never receipts."""

        if self.queue_id is None:
            return None
        if project.converted_from_id != self.pk:
            raise ValidationError({"project": "The project must have been promoted from this task."})
        category = "canceled" if project.status == project.ProjectStatus.DROPPED else "started"
        if category == "started" and project.current_milestone_id is not None:
            milestone_model = project._meta.get_field("current_milestone").related_model
            stage_id = (
                milestone_model._base_manager.filter(pk=project.current_milestone_id)
                .values_list("active_stage_id", flat=True)
                .get()
            )
            if stage_id is not None:
                stage = self.stage_model()._base_manager.get(pk=stage_id)
                stage.validate_rule_stage(self.queue_id)
                return cast(Stage, stage)
        return cast(
            Stage | None,
            self.stage_model()
            .for_container(self.queue)
            .filter(category=category, rule_owned=True, conceals=False)
            .first(),
        )

    def _move_links_to(self, canonical: models.Model) -> None:
        """Re-key source links to ``canonical``, deleting URL collisions."""

        link_model = apps.get_model("projects", "Link")
        source_target = canonical_record_target(self)
        canonical_target = canonical_record_target(canonical)
        source_links = list(
            link_model.objects.sudo(reason="work.task.mark_duplicate.links")
            .lock_if_supported()
            .filter(
                content_type=source_target.content_type,
                object_id=source_target.object_id,
            )
            .order_by("pk")
        )
        if not source_links:
            return
        canonical_urls = set(
            link_model._base_manager.filter(
                content_type=canonical_target.content_type,
                object_id=canonical_target.object_id,
            ).values_list("url", flat=True)
        )
        for link in source_links:
            if link.url in canonical_urls:
                link.delete()
                continue
            link.content_type = canonical_target.content_type
            link.object_id = canonical_target.object_id
            link.save(update_fields=("content_type", "object_id", "updated_at"))
            canonical_urls.add(link.url)

    def _move_followers_to(self, canonical: models.Model) -> None:
        """Move source chatter followers, preserving canonical collisions."""

        attachment_model = apps.get_model("messaging", "ThreadAttachment")
        follower_model = apps.get_model("messaging", "ThreadFollower")
        source_attachment = attachment_model.objects.for_record(self)
        if source_attachment is None:
            return
        source_followers = list(
            follower_model.objects.sudo(reason="work.task.mark_duplicate.followers")
            .lock_if_supported()
            .filter(attachment=source_attachment)
            .order_by("pk")
        )
        if not source_followers:
            return
        canonical_attachment = attachment_model.objects.ensure_for_record(
            canonical,
            title=canonical.message_thread_title(),
        )
        canonical_party_ids = set(
            follower_model._base_manager.filter(thread=canonical_attachment.thread).values_list(
                "party_id",
                flat=True,
            )
        )
        for follower in source_followers:
            if follower.party_id in canonical_party_ids:
                follower.delete()
                continue
            follower.thread_id = canonical_attachment.thread_id
            follower.attachment = canonical_attachment
            # A receipt is positional within its old thread and cannot be moved.
            follower.last_read_message = None
            follower.save(update_fields=("thread", "attachment", "last_read_message", "updated_at"))
            canonical_party_ids.add(follower.party_id)

    def _base_verb(self, name: str, *args: Any) -> Any:
        """Run a projects-only lifecycle verb for a legacy queue-less task."""

        with self._work_verb_write():
            return getattr(super(), name)(*args)


class UserWork(ResourceLoadMixin):
    """Provision personal queues after IAM user resources load."""

    extends = "iam.User"

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
    ) -> None:
        """Ensure every loaded user has exactly one personal queue."""

        queue_model = apps.get_model("work", "Queue")
        for user in sorted(instances, key=lambda instance: instance.pk or 0):
            queue_model.objects.provision_personal(user)

        super().after_resource_load(instances, tier=tier, source=source)
