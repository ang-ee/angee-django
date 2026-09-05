"""Projects, milestones, tasks, involvement, relations, and external links."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import F, Q
from django.utils import timezone
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    current_actor,
    system_context,
    to_object_ref,
    write_relationships,
)

from angee.base.fields import FractionalRankField, StateField
from angee.base.mixins import AuditMixin, HistoryMixin, RevisionMixin
from angee.base.models import AngeeDataModel, AngeeManager
from angee.base.refs import RecordRefMixin, canonical_record_target
from angee.base.scoping import bind_actor
from angee.messaging.models import ThreadedModelMixin
from angee.scheduling.fields import RecurrenceField


class ProjectManager(AngeeManager):
    """Own the idempotent Task-to-Project maturation write."""

    def from_task(self, task: models.Model) -> models.Model:
        """Return the one project promoted from ``task``, creating it if needed."""

        if task.pk is None:
            raise ValidationError("A task must be saved before it can be promoted.")
        actor = current_actor()
        with transaction.atomic():
            with system_context(reason="projects.project.promote_from_task.lookup"):
                locked_task = type(task).objects.lock_if_supported().get(pk=task.pk)
                existing = self.filter(converted_from_id=task.pk).first()
            if existing is not None:
                bind_actor(existing, actor)
                return existing

            verified_actor = self.check_create()
            project = self.model(
                title=locked_task.title,
                body=locked_task.note,
                lead_id=locked_task.assignee_id,
                converted_from_id=locked_task.pk,
            )
            project.full_clean(validate_unique=False, validate_constraints=False)
            project.sudo(reason="projects.project.promote_from_task")
            try:
                with transaction.atomic():
                    project.save()
            except IntegrityError:
                with system_context(reason="projects.project.promote_from_task.concurrent_lookup"):
                    project = self.get(converted_from_id=task.pk)
            bind_actor(project, verified_actor)
            return project


class TaskManager(AngeeManager):
    """Own task creation policy and ThreadActivity maturation."""

    def check_create(
        self,
        relationships: Mapping[str, Sequence[Any]] | None = None,
    ) -> SubjectRef:
        """Require project write when a new task is attached to a project."""

        project_values = tuple((relationships or {}).get("project", ()))
        if any(not project.has_access("write") for project in project_values):
            raise PermissionDenied("Write access to the project is required to add a task.")
        return super().check_create(relationships)

    def from_activity(self, activity: models.Model) -> models.Model:
        """Return the one task promoted from ``activity``, creating it if needed."""

        if activity.pk is None:
            raise ValidationError("An activity must be saved before it can be promoted.")
        actor = current_actor()
        with transaction.atomic():
            with system_context(reason="projects.task.promote_from_activity.lookup"):
                locked_activity = type(activity).objects.lock_if_supported().get(pk=activity.pk)
                existing = self.filter(converted_from_activity_id=activity.pk).first()
            if existing is not None:
                bind_actor(existing, actor)
                return existing

            verified_actor = self.check_create()
            task = self.model(
                title=locked_activity.summary,
                note=locked_activity.note,
                assignee_id=locked_activity.user_id,
                due_date=locked_activity.due_date,
                converted_from_activity_id=locked_activity.pk,
                sort_order=self._append_rank("sort_order", project=None),
                sub_sort_order=self._append_rank("sub_sort_order", parent=None),
            )
            task.full_clean(validate_unique=False, validate_constraints=False)
            task.sudo(reason="projects.task.promote_from_activity")
            try:
                with transaction.atomic():
                    task.save()
            except IntegrityError:
                with system_context(reason="projects.task.promote_from_activity.concurrent_lookup"):
                    task = self.get(converted_from_activity_id=activity.pk)
            bind_actor(task, verified_actor)
            return task

    def _append_rank(self, field_name: str, **context: Any) -> float:
        """Return an append rank for one exact task ordering context."""

        field = cast(FractionalRankField, self.model._meta.get_field(field_name))
        with system_context(reason=f"projects.task.append_{field_name}"):
            previous = self.filter(**context).order_by(f"-{field_name}").values_list(field_name, flat=True).first()
        return field.get_append_rank(previous)


class LinkManager(AngeeManager):
    """Own URL-keyed upserts and target-derived ReBAC relations."""

    TARGET_RELATIONS = {
        "projects.project": "project",
        "projects.task": "task",
    }
    """Allowed target model labels mapped to their projects/link relation."""

    def create(self, **kwargs: Any) -> models.Model:
        """Create through the URL-keyed upsert contract."""

        try:
            target = kwargs.pop("target")
            url = kwargs.pop("url")
        except KeyError as error:
            raise TypeError("Link.objects.create() requires target and url.") from error
        return self.upsert(target=target, url=url, **kwargs)

    def upsert(
        self,
        *,
        target: models.Model,
        url: str,
        title: str = "",
        metadata: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> models.Model:
        """Create or update one link per canonical target and URL."""

        relation = self.target_relation(target)
        if not target.has_access("write"):
            raise PermissionDenied("Write access to the target is required to create a link.")
        actor = current_actor()
        canonical = canonical_record_target(target)
        values = {"title": title, "metadata": dict(metadata or {}), **fields}
        with transaction.atomic():
            with system_context(reason="projects.link.upsert.lookup"):
                link = self.filter(
                    content_type=canonical.content_type,
                    object_id=canonical.object_id,
                    url=url,
                ).first()
            if link is None:
                verified_actor = self.check_create({relation: (target,)})
                link = self.model(target=target, url=url, **values)
                link.full_clean(validate_unique=False, validate_constraints=False)
                link.sudo(reason="projects.link.upsert.create").save()
                bind_actor(link, verified_actor)
            else:
                for name, value in values.items():
                    setattr(link, name, value)
                link.sudo(reason="projects.link.upsert.update").save(update_fields=(*values, "updated_at"))
                bind_actor(link, actor)
            write_relationships(
                [
                    RelationshipTuple(
                        resource=to_object_ref(link),
                        relation=relation,
                        subject=SubjectRef(to_object_ref(target)),
                    )
                ]
            )
        return link

    @classmethod
    def target_relation(cls, target: models.Model) -> str:
        """Return the one projects/link relation for an allowed target."""

        try:
            return cls.TARGET_RELATIONS[target._meta.label_lower]
        except KeyError as error:
            raise ValidationError({"target": "Links may target only projects or tasks."}) from error

    @classmethod
    def target_model(cls, model_label: str) -> type[models.Model]:
        """Return the installed model for an allowed link target label."""

        normalized_label = str(model_label).strip().lower()
        if normalized_label not in cls.TARGET_RELATIONS:
            raise ValidationError({"target": "Links may target only projects or tasks."})
        return apps.get_model(normalized_label)


class Project(AuditMixin, ThreadedModelMixin, HistoryMixin, RevisionMixin, AngeeDataModel):
    """A bounded endeavor whose access is owned by direct ReBAC grants."""

    runtime = True
    sqid_prefix = "prj_"
    revisioned_fields = ("title", "body")
    rebac_grantable = {"reader": "share", "editor": "share"}

    class ProjectStatus(models.TextChoices):
        """Coarse project lifecycle states."""

        OPEN = "open", "Open"
        PAUSED = "paused", "Paused"
        DONE = "done", "Done"
        DROPPED = "dropped", "Dropped"

    class ProjectDateResolution(models.TextChoices):
        """Precision carried by a project planning date."""

        DAY = "day", "Day"
        MONTH = "month", "Month"
        QUARTER = "quarter", "Quarter"
        HALF_YEAR = "half_year", "Half year"
        YEAR = "year", "Year"

    title = models.CharField(max_length=240)
    body = models.TextField(blank=True, default="")
    status = StateField(choices_enum=ProjectStatus, default=ProjectStatus.OPEN)
    lead = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="led_projects",
    )
    start_date = models.DateField(null=True, blank=True, db_index=True)
    start_date_resolution = StateField(
        choices_enum=ProjectDateResolution,
        default=ProjectDateResolution.DAY,
    )
    target_date = models.DateField(null=True, blank=True, db_index=True)
    target_date_resolution = StateField(
        choices_enum=ProjectDateResolution,
        default=ProjectDateResolution.DAY,
    )
    folder = models.ForeignKey(
        "storage.Folder",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="projects",
    )
    converted_from = models.ForeignKey(
        "projects.Task",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="promoted_projects",
    )
    links = GenericRelation(
        "projects.Link",
        content_type_field="content_type",
        object_id_field="object_id",
    )

    objects = ProjectManager()

    class Meta:
        """Django model options for projects."""

        abstract = True
        ordering = ("status", "target_date", "title", "sqid")
        rebac_resource_type = "projects/project"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("converted_from",),
                name="uq_projects_project_converted_from",
            ),
        )

    def __str__(self) -> str:
        """Return the project title."""

        return self.title

    def pause(self) -> Project:
        """Pause this project, idempotently."""

        return self._set_status(self.ProjectStatus.PAUSED)

    def resume(self) -> Project:
        """Return this project to open work, idempotently."""

        return self._set_status(self.ProjectStatus.OPEN)

    def complete(self) -> Project:
        """Complete this project, idempotently."""

        return self._set_status(self.ProjectStatus.DONE)

    def drop(self) -> Project:
        """Drop this project, idempotently."""

        return self._set_status(self.ProjectStatus.DROPPED)

    def _set_status(self, status: ProjectStatus) -> Project:
        """Persist one lifecycle target while preserving exact replay no-ops."""

        if self.status == status:
            return self
        self.status = status
        self.save(update_fields=("status", "updated_at"))
        return self


class Milestone(AuditMixin, AngeeDataModel):
    """A named marker reached within one project."""

    runtime = True
    sqid_prefix = "mls_"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="milestones",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    target_date = models.DateField(null=True, blank=True, db_index=True)
    sort_order = FractionalRankField()

    class Meta:
        """Django model options for milestones."""

        abstract = True
        ordering = ("project", "sort_order", "sqid")
        rebac_resource_type = "projects/milestone"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("project", "sort_order"),
                name="uq_projects_milestone_project_rank",
            ),
        )

    def __str__(self) -> str:
        """Return the milestone name."""

        return self.name


class Task(AuditMixin, ThreadedModelMixin, HistoryMixin, AngeeDataModel):
    """The platform's one table for a discrete human action."""

    runtime = True
    sqid_prefix = "tsk_"
    thread_tracking_fields = ("status", "assignee", "due_date", "priority")

    class TaskStatus(models.TextChoices):
        """Coarse task lifecycle states."""

        OPEN = "open", "Open"
        DONE = "done", "Done"
        DROPPED = "dropped", "Dropped"

    class TaskDroppedReason(models.TextChoices):
        """Reasons an action leaves the plan without completion."""

        DUPLICATE = "duplicate", "Duplicate"
        DECLINED = "declined", "Declined"
        OBSOLETE = "obsolete", "Obsolete"

    class TaskPriority(models.TextChoices):
        """Human urgency carried by a task."""

        NONE = "none", "None"
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
    )
    milestone = models.ForeignKey(
        "projects.Milestone",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="subtasks",
    )
    title = models.CharField(max_length=240)
    note = models.TextField(blank=True, default="")
    status = StateField(choices_enum=TaskStatus, default=TaskStatus.OPEN)
    dropped_reason = StateField(
        choices_enum=TaskDroppedReason,
        null=True,
        blank=True,
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_project_tasks",
    )
    delegate = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="delegated_project_tasks",
    )
    priority = StateField(choices_enum=TaskPriority, default=TaskPriority.NONE)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    recurrence = RecurrenceField()
    sort_order = FractionalRankField()
    sub_sort_order = FractionalRankField()
    done_at = models.DateTimeField(null=True, blank=True, db_index=True)
    dropped_at = models.DateTimeField(null=True, blank=True, db_index=True)
    converted_from_activity = models.ForeignKey(
        "messaging.ThreadActivity",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="promoted_tasks",
    )
    links = GenericRelation(
        "projects.Link",
        content_type_field="content_type",
        object_id_field="object_id",
    )

    objects = TaskManager()

    class Meta:
        """Django model options for tasks."""

        abstract = True
        ordering = ("project", "sort_order", "sub_sort_order", "sqid")
        rebac_resource_type = "projects/task"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("project", "sort_order"),
                name="uq_projects_task_project_rank",
                nulls_distinct=False,
            ),
            models.UniqueConstraint(
                fields=("parent", "sub_sort_order"),
                name="uq_projects_task_parent_sub_rank",
                nulls_distinct=False,
            ),
            models.UniqueConstraint(
                fields=("converted_from_activity",),
                name="uq_projects_task_converted_activity",
            ),
        )

    def __str__(self) -> str:
        """Return the task title."""

        return self.title

    def clean(self) -> None:
        """Normalize insert lifecycle state and reject invalid task structure."""

        super().clean()
        self._normalize_insert_lifecycle()
        self._validate_structure(lock=False)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist after revalidating mutable project structure."""

        self._normalize_insert_lifecycle()
        update_fields = kwargs.get("update_fields")
        structure_fields = {"project", "project_id", "milestone", "milestone_id", "parent", "parent_id"}
        if self._state.adding or update_fields is None or structure_fields.intersection(update_fields):
            with transaction.atomic():
                self._validate_structure(lock=True)
                super().save(*args, **kwargs)
            return
        super().save(*args, **kwargs)

    def complete(self) -> Task:
        """Mark this task done, idempotently preserving its first completion time."""

        if self.status == self.TaskStatus.DONE and self.done_at is not None and self.dropped_at is None:
            return self
        self.status = self.TaskStatus.DONE
        self.done_at = self.done_at or timezone.now()
        self.dropped_reason = None
        self.dropped_at = None
        self.save(update_fields=("status", "done_at", "dropped_reason", "dropped_at", "updated_at"))
        return self

    def drop(self, reason: str | TaskDroppedReason) -> Task:
        """Drop this task for ``reason``, idempotently preserving the drop time."""

        try:
            reason_member = self.TaskDroppedReason(getattr(reason, "value", reason))
        except ValueError as error:
            raise ValidationError({"reason": "Choose duplicate, declined, or obsolete."}) from error
        if self.status == self.TaskStatus.DROPPED and self.dropped_reason == reason_member and self.dropped_at:
            return self
        self.status = self.TaskStatus.DROPPED
        self.dropped_reason = reason_member
        self.dropped_at = self.dropped_at or timezone.now()
        self.done_at = None
        self.save(update_fields=("status", "dropped_reason", "dropped_at", "done_at", "updated_at"))
        return self

    def reopen(self) -> Task:
        """Return a done or dropped task to the open state, idempotently."""

        if (
            self.status == self.TaskStatus.OPEN
            and self.done_at is None
            and self.dropped_reason is None
            and self.dropped_at is None
        ):
            return self
        self.status = self.TaskStatus.OPEN
        self.done_at = None
        self.dropped_reason = None
        self.dropped_at = None
        self.save(update_fields=("status", "done_at", "dropped_reason", "dropped_at", "updated_at"))
        return self

    def promote_to_project(self) -> models.Model:
        """Return the one project matured from this task."""

        project_model = apps.get_model("projects", "Project")
        return project_model.objects.from_task(self)

    def _normalize_insert_lifecycle(self) -> None:
        """Stamp coherent close state while rejecting contradictory inserts."""

        if not self._state.adding:
            return
        if self.status == self.TaskStatus.DONE:
            if self.dropped_at is not None or self.dropped_reason is not None:
                raise ValidationError({"status": "A done task cannot carry dropped state."})
            self.done_at = self.done_at or timezone.now()
            return
        if self.status == self.TaskStatus.DROPPED:
            if self.dropped_reason is None:
                raise ValidationError({"dropped_reason": "A dropped task requires a dropped reason."})
            if self.done_at is not None:
                raise ValidationError({"status": "A dropped task cannot carry a completion timestamp."})
            self.dropped_at = self.dropped_at or timezone.now()
            return
        if self.done_at is not None or self.dropped_at is not None or self.dropped_reason is not None:
            raise ValidationError({"status": "An open task cannot carry done or dropped state."})

    def _validate_structure(self, *, lock: bool) -> None:
        """Validate milestone scope and the complete parent chain."""

        with system_context(reason="projects.task.validate_structure"):
            if self.milestone_id is not None:
                milestone_model = self._meta.get_field("milestone").related_model
                milestone = milestone_model.objects.filter(pk=self.milestone_id).only("project_id").first()
                if milestone is not None and milestone.project_id != self.project_id:
                    raise ValidationError({"milestone": "Milestone must belong to the task's project."})

            ancestor_id = self.parent_id
            visited: set[Any] = set()
            while ancestor_id is not None:
                if ancestor_id == self.pk or ancestor_id in visited:
                    raise ValidationError({"parent": "Task parents cannot form a cycle."})
                visited.add(ancestor_id)
                ancestors = type(self).objects.filter(pk=ancestor_id)
                if lock:
                    ancestors = ancestors.lock_if_supported()
                ancestor_id = ancestors.values_list("parent_id", flat=True).first()


class TaskRelation(AuditMixin, AngeeDataModel):
    """One canonical directed relation between two tasks."""

    runtime = True
    sqid_prefix = "trl_"

    class TaskRelationKind(models.TextChoices):
        """Canonical task relation kinds."""

        BLOCKS = "blocks", "Blocks"
        DUPLICATE = "duplicate", "Duplicate"
        RELATES = "relates", "Relates"

    SYMMETRIC_KINDS = frozenset({TaskRelationKind.RELATES})
    """Kinds whose endpoints have no semantic direction.

    Duplicate edges deliberately keep ``task -> related_task`` direction: the
    first endpoint is the duplicate and the second is its canonical task.
    """

    task = models.ForeignKey(
        "projects.Task",
        on_delete=models.CASCADE,
        related_name="outbound_relations",
    )
    related_task = models.ForeignKey(
        "projects.Task",
        on_delete=models.CASCADE,
        related_name="inverse_relations",
    )
    kind = StateField(choices_enum=TaskRelationKind)

    class Meta:
        """Django model options for task relations."""

        abstract = True
        ordering = ("task", "related_task", "sqid")
        rebac_resource_type = "projects/task_relation"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("task", "related_task"),
                name="uq_projects_task_relation_pair",
            ),
            models.CheckConstraint(
                condition=~Q(task=F("related_task")),
                name="ck_projects_task_relation_distinct",
            ),
        )

    def clean(self) -> None:
        """Canonicalize symmetric endpoints before constraint validation."""

        super().clean()
        self._canonicalize_symmetric_pair()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist symmetric relations in deterministic endpoint order."""

        swapped = self._canonicalize_symmetric_pair()
        update_fields = kwargs.get("update_fields")
        if swapped and update_fields is not None:
            kwargs["update_fields"] = {*update_fields, "task", "related_task"}
        super().save(*args, **kwargs)

    def _canonicalize_symmetric_pair(self) -> bool:
        """Order symmetric relation endpoints by primary key."""

        if (
            self.kind not in self.SYMMETRIC_KINDS
            or self.task_id is None
            or self.related_task_id is None
            or self.task_id < self.related_task_id
        ):
            return False
        self.task_id, self.related_task_id = self.related_task_id, self.task_id
        return True

    @property
    def inverse_kind(self) -> str:
        """Return the relation vocabulary seen from ``related_task``."""

        if self.kind == self.TaskRelationKind.BLOCKS:
            return "blocked_by"
        if self.kind == self.TaskRelationKind.DUPLICATE:
            return "duplicated_by"
        return str(self.kind)

    def __str__(self) -> str:
        """Return a readable directed edge."""

        return f"{self.task_id} {self.kind} {self.related_task_id}"


class Participant(AuditMixin, AngeeDataModel):
    """A party's involvement in a project, never an access grant."""

    runtime = True
    sqid_prefix = "ppt_"

    class ParticipantKind(models.TextChoices):
        """Ways a party can be involved in a project."""

        MEMBER = "member", "Member"
        STAKEHOLDER = "stakeholder", "Stakeholder"
        COUNTERPARTY = "counterparty", "Counterparty"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="participants",
    )
    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="project_participations",
    )
    kind = StateField(choices_enum=ParticipantKind, default=ParticipantKind.MEMBER)

    class Meta:
        """Django model options for project participants."""

        abstract = True
        ordering = ("project", "kind", "sqid")
        rebac_resource_type = "projects/participant"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("project", "party"),
                name="uq_projects_participant_project_party",
            ),
        )

    def __str__(self) -> str:
        """Return a readable involvement label."""

        return f"{self.party_id} in {self.project_id}"


class Link(AuditMixin, RecordRefMixin, AngeeDataModel):
    """A URL-keyed external reference attached to a project or task."""

    runtime = True
    sqid_prefix = "plk_"

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    url = models.URLField(max_length=2048)
    title = models.CharField(max_length=240, blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)

    objects = LinkManager()

    class Meta:
        """Django model options for project and task links."""

        abstract = True
        ordering = ("-updated_at", "url", "sqid")
        rebac_resource_type = "projects/link"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("content_type", "object_id", "url"),
                name="uq_projects_link_target_url",
            ),
        )
        indexes = (models.Index(fields=("content_type", "object_id")),)

    def clean(self) -> None:
        """Reject targets outside the project/task record surface."""

        super().clean()
        if self.target is None:
            raise ValidationError({"target": "A project or task target is required."})
        LinkManager.target_relation(self.target)

    def __str__(self) -> str:
        """Return the link title or URL."""

        return self.title or self.url


class ThreadActivityProjects(models.Model):
    """Contribute Activity-to-Task maturation onto messaging.ThreadActivity."""

    extends = "messaging.ThreadActivity"
    runtime = False

    class Meta:
        """Abstract same-row behavior donor for messaging.ThreadActivity."""

        abstract = True

    def promote_to_task(self) -> models.Model:
        """Return the one task matured from this thread activity."""

        task_model = apps.get_model("projects", "Task")
        return task_model.objects.from_activity(self)
