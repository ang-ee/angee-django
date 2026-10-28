"""Projects, milestones, tasks, involvement, relations, and external links."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from enum import StrEnum
from typing import Any, ClassVar, Self, cast

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
    current_actor,
    generic_target,
    system_context,
    to_object_ref,
    to_subject_ref,
    user_subject_type,
)
from rebac.backends import backend
from rebac.field_backing import canonical_model

from angee.base.fields.ordering import FractionalRankField
from angee.base.fields.state import StateField
from angee.base.models import (
    AngeeDataModel,
    AuditMixin,
    HierarchyMixin,
    HierarchyQuerySet,
    ImmutableFieldsMixin,
    ItemOwnershipMixin,
    MergeableMixin,
    MergeableQuerySet,
    MergePolicy,
    OptimisticLockMixin,
    OwnerMixin,
    OwnerQuerySet,
    RecordRefMixin,
    TrashMixin,
    TrashQuerySet,
    candidate_relationships,
)
from angee.base.permissions import RecordAccessSubjectRefused, actor_user_id
from angee.messaging.models import AudienceMember, ThreadedModelMixin
from angee.projects.access import require_binding_access, require_target_binding_access
from angee.projects.events import (
    milestone_reached,
    project_phase_changed,
    project_status_changed,
    task_promoted,
)
from angee.projects.inputs import MilestoneTemplate
from angee.provenance.context import changeset, changeset_model, require_changeset
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager, AngeeHistoryMergeableUnscopedManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
from angee.scheduling.fields import RecurrenceField
from angee.serialization import strip_null_bytes
from angee.tags.models import TaggedModel


class ProjectSetupState(StrEnum):
    """Readiness of persisted setup acts, independent of the project's lifecycle."""

    NOT_SET_UP = "not_set_up"
    PARTIAL = "partial"
    COMPLETE = "complete"


class ProjectQuerySet[ProjectT: Project](OwnerQuerySet[ProjectT], AngeeHistoryQuerySet[ProjectT]):
    """Project rows with the shared ownership release contract."""

    def update(self, **kwargs: Any) -> int:
        """Keep home-folder access changes on the both-end authorization path."""

        if {"folder", "folder_id"}.intersection(kwargs):
            raise ValueError("Project.folder requires both-end authorization through save().")
        return super().update(**kwargs)

    def bulk_update(self, objs: Any, fields: Any, *args: Any, **kwargs: Any) -> int:
        """Refuse folder changes that bypass instance authorization."""

        if {"folder", "folder_id"}.intersection(fields):
            raise ValueError("Project.folder requires both-end authorization through save().")
        return super().bulk_update(objs, fields, *args, **kwargs)

    def bulk_create(self, objs: Iterable[ProjectT], *args: Any, **kwargs: Any) -> list[ProjectT]:
        """Require target authorization for every new home-folder attachment."""

        objs = tuple(objs)
        if any(project.folder_id is not None for project in objs):
            raise ValueError("Projects with folders require instance authorization through save().")
        return super().bulk_create(objs, *args, **kwargs)


_ProjectManagerBase = AngeeHistoryManager.from_queryset(ProjectQuerySet)


class ProjectManager[ProjectT: Project](_ProjectManagerBase[ProjectT]):
    """Own the idempotent Task-to-Project maturation write."""

    def setup_from_task(
        self,
        task: Any,
        *,
        configuration: Mapping[str, Any],
        idempotency_key: str,
        expected_revision: int | None = None,
    ) -> ProjectT:
        """Apply all setup acts atomically; replay never overwrites subsequent edits.

        The ambient changeset owns replay independently of ordinary Project
        inserts. Existing partial projects can be completed; failed invocations
        leave neither partial acts nor an idempotency claim.
        """

        actor, bypass = task.effective_actor(strict=True)
        actor_id = actor_user_id(actor)
        if actor_id is None:
            raise ValidationError({"idempotency_key": "Setup requires a user actor."})
        with changeset(fresh=True), transaction.atomic():
            locked = type(task).system_queryset(lock=("self",)).get(pk=task.pk)
            if actor is not None:
                locked.with_actor(actor)
            if not bypass and not locked.has_access("write"):
                raise PermissionDenied("Task write access is required for project setup.")
            project = self.model.system_queryset(lock=("self",)).filter(converted_from=locked).first()
            if project is not None:
                if actor is not None:
                    project.with_actor(actor)
                if not bypass and not project.has_access("write"):
                    raise PermissionDenied("Project write access is required for setup.")

            result = changeset_model().objects.claim_created(
                f"projects.setup:{actor_id}:{locked.pk}",
                idempotency_key,
                values=configuration,
                model=self.model,
                unavailable=ValidationError(
                    {"idempotency_key": "The claimed setup result is unavailable."},
                ),
            )
            if result is not None:
                if actor is not None:
                    result.with_actor(actor)
                if not bypass and not result.has_access("write"):
                    raise PermissionDenied("Project write access is required for setup replay.")
                return result

            if expected_revision is not None:
                locked.require_revision(expected_revision)
            result = project if project is not None else self.from_task(locked)
            result.apply_setup(**configuration)
            require_changeset().record_created(result)
            if actor is not None:
                result.with_actor(actor)
            return result

    def from_task(self, task: Task, *, expected_revision: int | None = None) -> ProjectT:
        """Return the one project promoted from ``task``, creating it if needed."""

        if task.pk is None:
            raise ValidationError("A task must be saved before it can be promoted.")
        actor, bypass = task.effective_actor(strict=True)
        with transaction.atomic():
            locked_task = type(task).system_queryset(lock=("self",)).get(pk=task.pk)
            if not bypass and not locked_task.with_actor(actor).has_access("write"):
                raise PermissionDenied("Write access to the task is required to promote it.")
            if expected_revision is not None:
                locked_task.require_revision(expected_revision)
            with system_context(reason="projects.project.promote_from_task.lookup"):
                existing = self.filter(converted_from_id=task.pk).first()
            if existing is not None:
                return existing.with_actor(actor) if actor is not None else existing

            project = self.model(
                title=locked_task.title,
                body=locked_task.note,
                lead_id=locked_task.assignee_id,
                converted_from_id=locked_task.pk,
            )
            project.full_clean(validate_unique=False, validate_constraints=False)
            try:
                with transaction.atomic():
                    if actor is None:
                        project.sudo(reason="projects.project.promote_from_task").save()
                    else:
                        project = self.with_actor(actor).insert(project)
            except IntegrityError:
                with system_context(reason="projects.project.promote_from_task.concurrent_lookup"):
                    project = self.get(converted_from_id=task.pk)
            else:
                project.unsudo()
                locked_task.sudo(reason="projects.task.promoted").save_without_historical_record(
                    update_fields=("revision",),
                )
                locked_task.unsudo()
                if actor is not None:
                    locked_task.with_actor(actor)
                task_promoted.send(sender=type(locked_task), task=locked_task, project=project)
            task.refresh_from_db()
            return project.with_actor(actor) if actor is not None else project


class TaskVisibilityQuerySet[TaskT: Task](models.QuerySet[TaskT]):
    """Keep visibility transitions on the authorized instance verb.

    A guard on ``update`` sits after ``HierarchyQuerySet`` in the task queryset so
    the hierarchy's own path rewrites pass through it (``angee.E021``).
    """

    def update(self, **kwargs: Any) -> int:
        """Keep visibility transitions on the authorized instance verb."""

        if "visibility" in kwargs:
            raise ValueError("Task.visibility requires set_visibility().")
        return super().update(**kwargs)

    def bulk_update(self, objs: Any, fields: Any, *args: Any, **kwargs: Any) -> int:
        """Refuse visibility changes that bypass transition validation."""

        if "visibility" in fields:
            raise ValueError("Task.visibility requires set_visibility().")
        return super().bulk_update(objs, fields, *args, **kwargs)


class TaskQuerySet[TaskT: Task](
    HierarchyQuerySet[TaskT],
    TaskVisibilityQuerySet[TaskT],
    MergeableQuerySet[TaskT],
    TrashQuerySet[TaskT],
    OwnerQuerySet[TaskT],
    AngeeHistoryQuerySet[TaskT],
):
    """Task rows with hierarchy, merge, trash, and ownership contracts."""

    def waiting_on(self, actor: Any) -> Self:
        """Open, untrashed tasks assigned to this actor, within the caller's scope."""

        identity = actor_user_id(to_subject_ref(actor)) if actor is not None else None
        return (
            self.none()
            if identity is None
            else self.untrashed().filter(
                status=self.model.TaskStatus.OPEN,
                assignee_id=identity,
            )
        )

    def for_record(self, record: Any, *, include_done: bool = True) -> Self:
        """Return ``record``'s tasks this queryset reads: open first, then by due date and title.

        The task schema answers who reads them through its declared subject.
        """

        self.model.validate_target(record)
        tasks = self.filter(**generic_target(record).lookups(self.model, "subject"))
        if not include_done:
            tasks = tasks.filter(status=self.model.TaskStatus.OPEN)
        closed = models.Case(
            models.When(status=self.model.TaskStatus.OPEN, then=models.Value(0)),
            default=models.Value(1),
            output_field=models.IntegerField(),
        )
        return tasks.order_by(closed, F("due_date").asc(nulls_last=True), "title", "sqid")

    def promoted_phase_expression(self) -> models.expressions.BaseExpression:
        """Project only the phase name admitted by the task's narrow permission."""

        tasks = self.with_action("read_promoted_phase")
        return (
            tasks.filter(pk=models.OuterRef("pk"))
            .order_by()
            .readable_scalar_subquery(
                "promoted_projects__current_milestone__name",
            )
        )


_TaskManagerBase = AngeeHistoryManager.from_queryset(TaskQuerySet)


class TaskManager[TaskT: Task](_TaskManagerBase[TaskT]):
    """Own record tasks: tasks whose subject is a declared record.

    permissions.zed derives a record task's access from its record, so these
    verbs write under the actor and the task schema refuses a record the actor
    cannot write.
    """

    def schedule(
        self,
        record: Any,
        *,
        title: str,
        assignee: Any = None,
        due_date: Any = None,
        note: str = "",
        activity_type: str | None = None,
    ) -> TaskT:
        """Create an open record task, assigned to ``assignee`` or the actor and due today by default."""

        self.model.validate_target(record)
        if isinstance(record, self.model) and record.subject_object_id is not None:
            # Its access is its record's: a task about it would answer to nobody's.
            raise ValidationError("A record task takes no record tasks of its own.")
        title = strip_null_bytes(title or "").strip()
        if not title:
            raise ValidationError({"title": "A record task needs a title."})
        assignee_id = getattr(assignee, "pk", assignee) or actor_user_id(current_actor())
        if assignee_id is None:
            raise ValidationError({"assignee": "A record task needs an assignee."})
        kinds = self.model.activity_type.field.related_model
        with transaction.atomic():
            task = self.model(
                title=title,
                note=strip_null_bytes(note or ""),
                assignee_id=assignee_id,
                due_date=due_date or timezone.localdate(),
                **generic_target(record).lookups(self.model, "subject"),
                activity_type_id=strip_null_bytes(activity_type or kinds.DEFAULT_KEY),
            )
            task.full_clean(validate_unique=False, validate_constraints=False)
            task.save()
        return task

    def log(self, record: Any, *, activity_type: str, occurred_on: Any, note: str) -> TaskT:
        """Record one exchange about ``record`` that already happened, as a done task of its type.

        The note's first line is the title; completing the task posts the rest on
        the record's chatter.
        """

        note = strip_null_bytes(note or "")
        first_line, _newline, rest = note.strip().partition("\n")
        title = first_line.strip()
        if not title:
            raise ValidationError({"note": "A logged exchange needs a nonempty first line."})
        if occurred_on is None:
            raise ValidationError({"due_date": "The day the exchange happened is required."})
        length = self.model.title.field.max_length
        assert length is not None
        if len(title) > length:
            title, rest = title[: length - 1] + "…", note
        with transaction.atomic():
            task = self.schedule(
                record,
                title=title,
                note=note,
                due_date=occurred_on,
                activity_type=activity_type,
            )
            task.complete(feedback=rest.strip())
        return task


class LinkManager[LinkT: Link](AngeeHistoryManager[LinkT]):
    """Own URL-keyed upserts on live project and task targets.

    permissions.zed backs the link's project and task relations with
    its GenericForeignKey, so a link is created, read, written and deleted
    with the matching permission on its target. Links are stored at
    :func:`rebac.generic_target` and written under the actor.
    """

    def create(self, **kwargs: Any) -> LinkT:
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
    ) -> LinkT:
        """Create or update one link per canonical target and URL.

        The link's create requires write on the target through the relation
        its type declares; a target type without one is refused under an actor,
        and ValueError names an unsaved or untyped target.
        """

        key = generic_target(target).lookups(self.model, "target")
        values = {"title": title, "metadata": dict(metadata or {}), **fields}
        with transaction.atomic():
            link = self.filter(url=url, **key).first()
            if link is None:
                link = self.model(url=url, **key, **values)
                link.full_clean(validate_unique=False, validate_constraints=False)
                return self.insert(link)
            for name, value in values.items():
                setattr(link, name, value)
            link.save(update_fields=(*values, "updated_at"))
        return link


class ProjectBindingManager[BindingT: ProjectBinding](AngeeHistoryManager[BindingT]):
    """Own project-container bindings; ``permissions.zed`` authorizes both ends."""

    def validate_target(self, target: models.Model) -> None:
        """Reject a resource outside the declared types; an integration binds only as a messaging channel."""

        self.model.validate_target(target)
        if canonical_model(type(target)) is apps.get_model("integrate", "Integration") and not (
            apps.get_model("messaging", "Channel")._base_manager.filter(pk=target.pk).exists()
        ):
            raise ValidationError({"target": "Project bindings reach an integration only through its channel."})

    def bind(self, *, project: models.Model, target: models.Model) -> BindingT:
        """Idempotently persist one canonical binding under the actor.

        The binding's ``create`` requires share on the project and the resource
        type's own grant authority through the relation declared for it.
        """

        if project.pk is None or target.pk is None:
            raise ValidationError("A project binding requires saved project and target rows.")
        self.validate_target(target)
        binding, _created = self.get_or_create(project=project, **generic_target(target).lookups(self.model, "target"))
        return binding

    def unbind(self, *, project: models.Model, target: models.Model) -> int:
        """Remove one explicit binding; its ``delete`` requires the same authority as ``bind``."""

        key = generic_target(target).lookups(self.model, "target")
        deleted, _by_model = self.filter(project=project, **key).delete()
        return deleted


class Project(
    TaggedModel,
    OwnerMixin,
    ItemOwnershipMixin,
    OptimisticLockMixin,
    ThreadedModelMixin,
    HistoryMixin,
    AngeeDataModel,
):
    """A bounded endeavor whose access is owned by direct ReBAC grants."""

    runtime = True
    sqid_prefix = "prj_"
    history_relations = ("milestones",)
    rebac_grantable = {"reader": "share", "editor": "share"}

    class ProjectStatus(models.TextChoices):
        """Coarse project lifecycle states."""

        OPEN = "open", "Open"
        PAUSED = "paused", "Paused"
        DONE = "done", "Completed"
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
    status_changed_at = models.DateTimeField(null=True, blank=True, editable=False)
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
    current_milestone = models.ForeignKey(
        "projects.Milestone",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.RESTRICT,
        related_name="current_for_projects",
    )
    links = GenericRelation(
        "projects.Link",
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="project",
    )

    objects = ProjectManager()

    class Meta:
        """Django model options for projects."""

        abstract = True
        ordering = ("status", "target_date", "title", "sqid")
        rebac_resource_type = "projects/project"
        constraints: ClassVar[list[models.BaseConstraint] | tuple[models.BaseConstraint, ...]] = (
            models.UniqueConstraint(
                fields=("converted_from",),
                name="uq_projects_project_converted_from",
            ),
        )

    # Promotion lineage stays on the original retained identity.
    merge_policies = {"converted_from": MergePolicy.KEEP}

    def __str__(self) -> str:
        """Return the project title."""

        return self.title

    @property
    def on_path(self) -> bool:
        """Whether this lifecycle state keeps the milestone path active."""

        return self.status == self.ProjectStatus.OPEN

    def apply_setup(
        self,
        *,
        milestones: list[dict[str, Any]] | None = None,
        vault_template: Any = None,
    ) -> None:
        """Terminal cooperative hook; contributors consume their own named inputs.

        Invoked only by ``setup_from_task`` inside its transaction. Existing
        milestones are adopted by unambiguous template name, filling only
        missing nullable choices and retaining their existing values.
        """

        if not isinstance(milestones, list) or not milestones:
            raise ValidationError({"milestones": "Declare at least one milestone."})
        milestone_model = apps.get_model("projects", "Milestone")
        values = [milestone_model.setup_template_values(item) for item in milestones]
        if not values or len({item["name"] for item in values}) != len(values):
            raise ValidationError({"milestones": "Declare at least one milestone, with unique names."})
        first = None
        for item in values:
            matches = list(milestone_model.objects.filter(project=self, name=item["name"])[:2])
            if len(matches) > 1:
                raise ValidationError({"milestones": "A template name matches multiple existing milestones."})
            milestone = matches[0] if matches else milestone_model.objects.create(project=self, **item)
            if matches:
                milestone.complete_setup_template(item)
            first = first or milestone
        assert first is not None  # The validated template contains at least one milestone.
        if self.current_milestone_id is None:
            self.set_current_milestone(first)
        binding_model = apps.get_model("projects", "ProjectBinding")
        vault_model = apps.get_model("knowledge", "Vault")
        content_type = ContentType.objects.get_for_model(vault_model)
        if not binding_model.objects.filter(project=self, content_type=content_type).exists():
            if vault_template is None:
                raise ValidationError({"vault_template": "Choose a vault template."})
            vault = vault_model.objects.create_from(
                vault_template,
                name=self.title,
                idempotency_key=f"project:{self.pk}",
            )
            binding_model.objects.bind(project=self, target=vault)

    @classmethod
    def setup_complete_condition(cls, actor: Any) -> Q:
        """Compose readable setup evidence; optional addons add their own acts."""

        milestones = apps.get_model("projects", "Milestone").objects.with_actor(actor).scoped()
        bindings = apps.get_model("projects", "ProjectBinding").objects.with_actor(actor).scoped()
        return Q(models.Exists(milestones.filter(pk=models.OuterRef("current_milestone_id")))) & Q(
            models.Exists(
                bindings.filter(
                    project_id=models.OuterRef("pk"),
                    content_type__app_label="knowledge",
                    content_type__model="vault",
                )
            ),
        )

    @classmethod
    def setup_state_expression(cls, actor: Any) -> models.expressions.BaseExpression:
        """Annotate persisted setup readiness without per-row queries."""

        if actor is None:
            return models.Value(ProjectSetupState.PARTIAL.value)
        return models.Case(
            models.When(cls.setup_complete_condition(actor), then=models.Value(ProjectSetupState.COMPLETE.value)),
            default=models.Value(ProjectSetupState.PARTIAL.value),
            output_field=models.CharField(),
        )

    @classmethod
    def overdue_milestone_count_expression(
        cls,
        actor: Any,
        *,
        milestone_name: str | None = None,
    ) -> models.expressions.BaseExpression:
        """Count the named unfinished phase, or the current phase when omitted."""

        milestones = (
            apps.get_model("projects", "Milestone")
            .objects.overdue()
            .filter(
                project_id=models.OuterRef("pk"),
            )
        )
        milestones = (
            milestones.filter(name=milestone_name)
            if milestone_name is not None
            else milestones.filter(
                pk=models.OuterRef("current_milestone_id"),
            )
        )
        return milestones.readable_count_subquery(actor=actor)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Require target authority before a folder edit can widen project access."""

        update_fields = kwargs.get("update_fields")
        folder_is_written = update_fields is None or bool({"folder", "folder_id"}.intersection(update_fields))
        previous: Any = None
        previous_folder_id = None
        if not self._state.adding and folder_is_written:
            previous = type(self)._base_manager.filter(pk=self.pk).only("folder").first()
            previous_folder_id = previous.folder_id if previous is not None else None
        folder_changed = folder_is_written and (self._state.adding or previous_folder_id != self.folder_id)
        if folder_changed:
            if self._state.adding and self.folder_id is not None:
                require_target_binding_access(self.folder)
            elif not self._state.adding:
                if previous_folder_id is not None:
                    previous_folder: Any = previous.folder
                    require_binding_access(project=self, target=previous_folder)
                if self.folder_id is not None:
                    require_binding_access(project=self, target=self.folder)
        super().save(*args, **kwargs)

    def pause(self, *, expected_revision: int | None = None) -> Project:
        """Pause this project, idempotently."""

        return self._set_status(self.ProjectStatus.PAUSED, expected_revision=expected_revision)

    def resume(self, *, expected_revision: int | None = None) -> Project:
        """Return this project to open work, idempotently."""

        return self._set_status(self.ProjectStatus.OPEN, expected_revision=expected_revision)

    def complete(self, *, expected_revision: int | None = None) -> Project:
        """Complete this project, idempotently."""

        return self._set_status(self.ProjectStatus.DONE, expected_revision=expected_revision)

    def drop(self, *, expected_revision: int | None = None) -> Project:
        """Drop this project, idempotently."""

        return self._set_status(self.ProjectStatus.DROPPED, expected_revision=expected_revision)

    def _set_status(self, status: ProjectStatus, *, expected_revision: int | None = None) -> Project:
        """Persist and announce one authorized lifecycle change in one transaction."""

        actor, bypass = self.effective_actor(strict=True)
        with transaction.atomic():
            locked = type(self).system_queryset(lock=("self",)).get(pk=self.pk)
            if not bypass and not locked.with_actor(actor).has_access("write"):
                raise PermissionDenied("Write access to the project is required to change its status.")
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if locked.status != status:
                previous_status = locked.status
                locked.status = status
                locked.status_changed_at = timezone.now()
                locked.sudo(reason="projects.project.set_status").save(
                    update_fields=("status", "status_changed_at", "updated_at"),
                )
                locked.unsudo()
                if actor is not None:
                    locked.with_actor(actor)
                project_status_changed.send(
                    sender=type(locked),
                    project=locked,
                    previous_status=previous_status,
                    status=status,
                )
            self.refresh_from_db()
        return self

    def selectable_milestones(self) -> MilestoneQuerySet[Milestone]:
        """Return this project's phase choices; contributors may narrow eligibility."""

        return self.milestones.all()

    def set_current_milestone(
        self,
        milestone: Milestone,
        *,
        expected_revision: int | None = None,
    ) -> Project:
        """Select an eligible phase without changing planned dates or historical receipts."""

        actor, bypass = self.effective_actor(strict=True)
        with transaction.atomic():
            locked = type(self).system_queryset(lock=("self",)).get(pk=self.pk)
            if not bypass and not locked.with_actor(actor).has_access("write"):
                raise PermissionDenied("Write access to the project is required to select its phase.")
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            eligible = locked.selectable_milestones().system_context(reason="projects.project.phase_eligibility")
            if milestone.pk is None or not eligible.filter(pk=milestone.pk, project_id=locked.pk).exists():
                raise ValidationError({"milestone": "Choose an eligible milestone of this project."})
            if locked.current_milestone_id != milestone.pk:
                previous = locked.current_milestone
                locked.current_milestone = milestone
                locked.sudo(reason="projects.project.set_current_milestone").save(
                    update_fields=("current_milestone", "updated_at"),
                )
                if locked.status == self.ProjectStatus.DONE:
                    if not bypass:
                        locked.with_actor(actor)
                    locked._set_status(self.ProjectStatus.OPEN)
                locked.unsudo()
                if actor is not None:
                    locked.with_actor(actor)
                if previous is not None:
                    previous.unsudo()
                    if actor is not None:
                        previous.with_actor(actor)
                milestone.unsudo()
                if actor is not None:
                    milestone.with_actor(actor)
                project_phase_changed.send(
                    sender=type(locked),
                    project=locked,
                    previous_milestone=previous,
                    milestone=milestone,
                )
            self.refresh_from_db()
        return self


class MilestoneQuerySet[MilestoneT: Milestone](AngeeHistoryQuerySet[MilestoneT]):
    """Milestone rows with project scheduling helpers."""

    def overdue(self) -> Self:
        """Unfinished milestones past their target on open projects."""

        return self.filter(target_date__lt=timezone.localdate(), reached_at__isnull=True, project__status="open")


_MilestoneManagerBase = AngeeHistoryManager.from_queryset(MilestoneQuerySet)


class _MilestoneManager[MilestoneT: Milestone](_MilestoneManagerBase[MilestoneT]):
    """Preserve the native milestone scheduling queryset on manager reads."""


class Milestone(HistoryMixin, OptimisticLockMixin, ImmutableFieldsMixin, AuditMixin, AngeeDataModel):
    """A named marker reached within one project."""

    runtime = True
    sqid_prefix = "mls_"
    immutable_fields = ("reached_at", "reached_by_id")

    objects = _MilestoneManager()

    @classmethod
    def setup_template_values(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        """Validate the milestone-owned portion of a setup template."""

        return MilestoneTemplate.values(values)

    def complete_setup_template(self, values: Mapping[str, Any]) -> None:
        """Fill missing nullable template values while preserving prior work."""

        changed = []
        for name, value in values.items():
            if value is not None and getattr(self, name) is None:
                setattr(self, name, value)
                changed.append(name)
        if changed:
            self.save(update_fields=(*changed, "updated_at"))

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="milestones",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    start_date = models.DateField(null=True, blank=True, db_index=True)
    reached_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)
    reached_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    target_date = models.DateField(null=True, blank=True, db_index=True)
    sort_order = FractionalRankField()

    class Meta:
        """Django model options for milestones."""

        abstract = True
        ordering = ("project", "sort_order", "sqid")
        rebac_resource_type = "projects/milestone"
        constraints: ClassVar[list[models.BaseConstraint] | tuple[models.BaseConstraint, ...]] = (
            models.CheckConstraint(
                condition=Q(start_date__isnull=True)
                | Q(target_date__isnull=True)
                | Q(start_date__lte=F("target_date")),
                name="ck_projects_milestone_dates",
            ),
            models.UniqueConstraint(
                fields=("project", "sort_order"),
                name="uq_projects_milestone_project_rank",
            ),
        )

    def __str__(self) -> str:
        """Return the milestone name."""

        return self.name

    def clean(self) -> None:
        """Reject a planned finish before the planned start."""

        super().clean()
        if self.start_date is not None and self.target_date is not None and self.start_date > self.target_date:
            raise ValidationError({"target_date": "The target date must be on or after the start date."})

    def mark_reached(self, *, expected_revision: int | None = None) -> Milestone:
        """Stamp and announce the first historical receipt, retaining it on replay."""

        actor, bypass = self.effective_actor(strict=True)
        with transaction.atomic():
            locked = type(self).system_queryset(lock=("self",)).get(pk=self.pk)
            if not bypass and not locked.with_actor(actor).has_access("reach"):
                raise PermissionDenied("Reach access to the milestone is required.")
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if locked.reached_at is None:
                locked.reached_at = timezone.now()
                locked.reached_by_id = actor_user_id(actor)
                locked.allow_immutable_save("reached_at", "reached_by_id")
                locked.sudo(reason="projects.milestone.mark_reached").save(
                    update_fields=("reached_at", "reached_by", "updated_at"),
                )
                locked.unsudo()
                if actor is not None:
                    locked.with_actor(actor)
                milestone_reached.send(sender=type(locked), milestone=locked)
            self.refresh_from_db()
        return self

    def validate_deletion(self, *, origin: models.Model | models.QuerySet[Any] | None) -> None:
        """Retain receipts while their project exists; allow its native cascade."""

        if isinstance(origin, Project) and origin.pk == self.project_id:
            return
        if isinstance(origin, models.QuerySet) and issubclass(origin.model, Project):
            return
        if self.reached_at is not None:
            raise ValidationError("A reached milestone cannot be deleted.")


class ActivityType(AuditMixin, AngeeDataModel):
    """Resource-declared kinds of record task; tasks keep the stable key."""

    DEFAULT_KEY: ClassVar[str] = "todo"
    """The plain to-do projects seeds: a record task's kind when none is named."""

    runtime = True
    sqid_prefix = "act_"
    key = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=160)
    glyph = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        abstract = True
        ordering = ("name", "key")
        rebac_resource_type = "projects/activity_type"

    def __str__(self) -> str:
        return self.name

    @classmethod
    def completion_line(cls, key: str | None, title: str) -> str:
        """Return the record chatter line announcing a done record task of kind ``key``."""

        if key in (None, cls.DEFAULT_KEY):
            return f"Done: {title}"
        kind = cls._base_manager.filter(key=key).only("name").first()
        return f"{kind.name if kind is not None else key} done: {title}"


class Task(
    HierarchyMixin,
    MergeableMixin,
    TrashMixin,
    RecordRefMixin,
    TaggedModel,
    ImmutableFieldsMixin,
    OwnerMixin,
    OptimisticLockMixin,
    ThreadedModelMixin,
    HistoryMixin,
    AngeeDataModel,
):
    """The platform's one table for a discrete human action."""

    _angee_base = AngeeHistoryMergeableUnscopedManager()

    runtime = True
    sqid_prefix = "tsk_"
    MERGED_PERMISSION = "write"
    retains_merged = True
    merge_policy = MergePolicy.MOVE
    on_target_delete = models.CASCADE
    thread_post_access = "comment"
    owner_container = "project"
    immutable_fields: ClassVar[tuple[str, ...]] = ("visibility",)
    rebac_grantable = {"reader": "share", "editor": "share"}

    class TaskVisibility(models.TextChoices):
        """Whether this task inherits its containers' access."""

        INHERITED = "inherited", "Inherited"
        RESTRICTED = "restricted", "Restricted"

    visibility = StateField(choices_enum=TaskVisibility, default=TaskVisibility.INHERITED)

    @classmethod
    def visibility_audience_fields(cls) -> tuple[str, ...]:
        """Fields the task's audience label reads from a projected row."""

        return ("visibility",)

    def visibility_audience_label(self) -> str:
        """Describe the task's declared read audience for its current visibility."""

        return (
            "Project readers and task participants"
            if self.visibility == self.TaskVisibility.INHERITED
            else "Task participants"
        )

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
    subject_content_type = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    subject_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey("subject_content_type", "subject_object_id")
    """The record this action is about; independent of its optional conversation."""
    activity_type = models.ForeignKey(
        "projects.ActivityType",
        to_field="key",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tasks",
    )
    links = GenericRelation(
        "projects.Link",
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="task",
    )
    file_attachments = GenericRelation("storage.FileAttachment", related_query_name="task")

    objects = TaskManager()

    class Meta:
        """Django model options for tasks."""

        abstract = True
        ordering = ("project", "sort_order", "sub_sort_order", "sqid")
        rebac_resource_type = "projects/task"
        constraints: ClassVar[list[models.BaseConstraint] | tuple[models.BaseConstraint, ...]] = (
            models.CheckConstraint(
                condition=(
                    Q(subject_content_type__isnull=True, subject_object_id__isnull=True)
                    | Q(subject_content_type__isnull=False, subject_object_id__isnull=False)
                ),
                name="ck_projects_task_subject_pair",
            ),
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
        )
        indexes = (
            *HierarchyMixin.Meta.indexes,
            models.Index(fields=("subject_content_type", "subject_object_id", "status", "due_date")),
            models.Index(fields=("assignee", "status", "due_date")),
        )

    def __str__(self) -> str:
        """Return the task title."""

        return self.title

    @classmethod
    def merge_contract(cls, reference: Any, rows: Any, *, survivor: Any) -> Any:
        """Append reparented children through the rank and hierarchy owners."""

        if reference.name != "parent":
            return NotImplemented
        list(cls.system_queryset(lock=("self",)).using(rows.db).filter(parent=survivor).order_by("pk"))
        for child in rows.order_by("sub_sort_order", "pk"):
            child.parent = survivor
            child.sub_sort_order = None
            child.save(using=rows.db, update_fields=("parent", "sub_sort_order", "updated_at"))

    @classmethod
    def setup_state_expression(cls, actor: Any) -> models.expressions.BaseExpression:
        """Read setup state only through a project the task's actor can read."""

        if actor is None:
            return models.Value(ProjectSetupState.NOT_SET_UP.value)
        project = apps.get_model("projects", "Project")
        rows = project.objects.with_actor(actor).scoped().filter(converted_from_id=models.OuterRef("pk"))
        return rows.annotate(_setup=project.setup_state_expression(actor)).readable_scalar_subquery(
            "_setup",
            actor=actor,
            default=ProjectSetupState.NOT_SET_UP.value,
            output_field=models.CharField(),
        )

    @classmethod
    def overdue_milestone_count_expression(
        cls,
        actor: Any,
        *,
        milestone_name: str | None = None,
    ) -> models.expressions.BaseExpression:
        """Count readable linked/promoted projects with an overdue named/current phase."""

        if actor is None:
            return models.Value(0)
        project = apps.get_model("projects", "Project")
        rows = (
            project.objects.with_actor(actor)
            .scoped()
            .filter(
                Q(converted_from_id=models.OuterRef("pk")) | Q(pk=models.OuterRef("project_id")),
            )
            .alias(
                _overdue=project.overdue_milestone_count_expression(actor, milestone_name=milestone_name),
            )
            .filter(_overdue__gt=0)
        )
        return rows.readable_count_subquery(actor=actor)

    @classmethod
    def priority_rank_expression(cls) -> models.Case:
        """Order urgency by the priority field's declared enum order."""

        priorities = cls.priority.field.choices_enum
        return models.Case(
            *(models.When(priority=priority, then=models.Value(rank)) for rank, priority in enumerate(priorities)),
            output_field=models.IntegerField(),
        )

    def priority_rank(self) -> int:
        """Use the SQL annotation or the priority field's declared enum order."""

        if hasattr(self, "_priority_rank"):
            return self._priority_rank
        return list(type(self).priority.field.choices_enum).index(self.priority)

    def promoted_phase(self) -> str | None:
        """Use the annotation or fetch the phase through the same scoped expression."""

        if hasattr(self, "_promoted_phase"):
            return self._promoted_phase
        actor = self.actor() or current_actor()
        if self.pk is None or actor is None:
            return None
        return (
            type(self)
            ._base_manager.filter(pk=self.pk)
            .annotate(_promoted_phase=type(self).objects.with_actor(actor).promoted_phase_expression())
            .values_list("_promoted_phase", flat=True)
            .first()
        )

    def clean(self) -> None:
        """Normalize insert lifecycle state and reject invalid task structure."""

        super().clean()
        self._normalize_insert_lifecycle()
        self._validate_structure()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Authorize project moves and assignment before revalidating task structure."""

        update_fields = kwargs.get("update_fields")
        deferred = self.get_deferred_fields()
        project_is_written = (update_fields is None and "project_id" not in deferred) or (
            update_fields is not None and bool({"project", "project_id"}.intersection(update_fields))
        )
        assignee_is_written = (update_fields is None and "assignee_id" not in deferred) or (
            update_fields is not None and bool({"assignee", "assignee_id"}.intersection(update_fields))
        )
        previous_project_id, previous_assignee_id = None, None
        if not self._state.adding and (project_is_written or assignee_is_written):
            previous_project_id, previous_assignee_id = (
                type(self).system_queryset().filter(pk=self.pk).values_list("project_id", "assignee_id").get()
            )
        project = self.project
        if project_is_written and project is not None:
            if self._state.adding or self.project_id != previous_project_id:
                actor, bypass = self.effective_actor(strict=True)
                if not bypass and not project.with_actor(actor).has_access("write"):
                    raise PermissionDenied("Write access to the project is required to add a task.")
        if assignee_is_written and self.assignee_id is not None:
            if self._state.adding or self.assignee_id != previous_assignee_id:
                self.validate_record_access_subject("assignee", self.assignee)
        assignment_actor = None
        if self._state.adding and self.assignee_id is not None:
            actor, bypass = self.effective_actor(strict=True)
            if not bypass:
                assignment_actor = actor

        self._normalize_insert_lifecycle()
        update_fields = kwargs.get("update_fields")
        structure_fields = {"project", "project_id", "milestone", "milestone_id", "parent", "parent_id"}
        if self._state.adding or update_fields is None or structure_fields.intersection(update_fields):
            with transaction.atomic():
                self._validate_structure()
                super().save(*args, **kwargs)
                # The persisted candidate lets REBAC evaluate every composed share arm.
                # A refusal rolls the insert and its transactional side effects back.
                if (
                    assignment_actor is not None
                    and (
                        self.container_owns_items()
                        or self.owner_id is None
                        or self.assignee_id != actor_user_id(assignment_actor)
                    )
                    and not self.has_access("share")
                ):
                    raise PermissionDenied("Share access to the task is required to assign it.")
            return
        super().save(*args, **kwargs)

    @classmethod
    def hand_actions(cls) -> tuple[str, ...]:
        """Hand verbs whose admission ``task_actions`` reports, in offer order; contributors extend."""

        return ("complete", "drop", "reopen")

    @classmethod
    def action_blockers(cls, action: str) -> tuple[tuple[models.Q, Any], ...]:
        """Row conditions refusing hand verb ``action``, each with its ``ValidationError`` message.

        The verbs and the batched ``task_actions`` projection share these
        conditions, so a control is offered exactly where its verb admits it.
        Contributors extend them through ``super()``; a verb checks them after
        its idempotent replay, so a blocker never refuses a no-op. A record task
        completes or cancels only while open: its record already carries the
        completion post.
        """

        if action in ("complete", "drop"):
            closed = Q(subject_object_id__isnull=False) & ~Q(status=cls.TaskStatus.OPEN)
            return ((closed, {"status": "Reopen this record task first."}),)
        return ()

    @classmethod
    def action_permission(cls, action: str) -> str:
        """The permission admitting a hand verb; contributors own stricter actions."""

        return "write"

    @classmethod
    def action_allowed_expression(cls, actor: Any, action: str) -> models.expressions.BaseExpression:
        """Batch the verb's permission and its blockers for one actor's rows."""

        if actor is None:
            return models.Value(False)
        rows = cls.objects.with_actor(actor).with_action(cls.action_permission(action)).scoped_for_aggregate()
        for condition, _error in cls.action_blockers(action):
            rows = rows.exclude(condition)
        return models.Exists(rows.filter(pk=models.OuterRef("pk")))

    def validate_action(self, action: str) -> None:
        """Refuse ``action`` with the first declared blocker the stored row matches."""

        for condition, error in self.action_blockers(action):
            if type(self).system_queryset().filter(condition, pk=self.pk).exists():
                raise ValidationError(error)

    def complete(self, *, feedback: str = "") -> Task:
        """Mark this task done, idempotently preserving its first completion time.

        A record task announces the completion, with ``feedback``, on its record.
        """

        with transaction.atomic():
            self._lock_lifecycle()
            if self.status == self.TaskStatus.DONE and self.done_at is not None and self.dropped_at is None:
                return self
            self.validate_action("complete")
            cast(Any, self).status = str(self.TaskStatus.DONE)
            self.done_at = self.done_at or timezone.now()
            cast(Any, self).dropped_reason = None
            self.dropped_at = None
            self.save(update_fields=("status", "done_at", "dropped_reason", "dropped_at", "updated_at"))
            self.post_completion(feedback)
        return self

    def _lock_lifecycle(self) -> None:
        """Hold this row's lock and load its stored lifecycle, so a concurrent verb replays."""

        if self.pk is None:
            return
        stored = (
            type(self)
            .system_queryset(lock=("self",))
            .values("status", "done_at", "dropped_at", "dropped_reason")
            .get(pk=self.pk)
        )
        for name, value in stored.items():
            setattr(self, name, value)

    def post_completion(self, feedback: str = "") -> None:
        """Announce this record task's completion, with ``feedback``, on its record's chatter.

        Every lifecycle that completes a task calls it once, after the transition;
        a task about no record posts nothing.
        """

        if self.subject_object_id is None:
            return
        # Structural: whoever completes a record task reads its record.
        with system_context(reason="projects.task.post_completion"):
            record = self.subject
        if not isinstance(record, ThreadedModelMixin):
            return
        kinds = type(self).activity_type.field.related_model
        body = kinds.completion_line(self.activity_type_id, self.title)
        feedback = strip_null_bytes(feedback or "").strip()
        record.message_system_post(
            body=f"{body}\n\n{feedback}" if feedback else body,
            message_type=apps.get_model("messaging", "Message").MessageKind.NOTIFICATION,
            subtype_key="activity_done",
        )

    def _message_after_create(self) -> None:
        """A record task keeps its conversation on the record, not a thread of its own."""

        if self.subject_object_id is None:
            super()._message_after_create()

    def drop(self, reason: str | TaskDroppedReason) -> Task:
        """Drop this task for ``reason``, idempotently preserving the drop time."""

        try:
            reason_member = self.TaskDroppedReason(getattr(reason, "value", reason))
        except ValueError as error:
            raise ValidationError({"reason": "Choose duplicate, declined, or obsolete."}) from error
        with transaction.atomic():
            self._lock_lifecycle()
            if self.status == self.TaskStatus.DROPPED and self.dropped_reason == reason_member and self.dropped_at:
                return self
            self.validate_action("drop")
            cast(Any, self).status = str(self.TaskStatus.DROPPED)
            cast(Any, self).dropped_reason = str(reason_member)
            self.dropped_at = self.dropped_at or timezone.now()
            self.done_at = None
            self.save(update_fields=("status", "dropped_reason", "dropped_at", "done_at", "updated_at"))
        return self

    def reopen(self) -> Task:
        """Return a done or dropped task to the open state, idempotently."""

        with transaction.atomic():
            self._lock_lifecycle()
            if (
                self.status == self.TaskStatus.OPEN
                and self.done_at is None
                and self.dropped_reason is None
                and self.dropped_at is None
            ):
                return self
            self.validate_action("reopen")
            cast(Any, self).status = str(self.TaskStatus.OPEN)
            self.done_at = None
            cast(Any, self).dropped_reason = None
            self.dropped_at = None
            self.save(update_fields=("status", "done_at", "dropped_reason", "dropped_at", "updated_at"))
        return self

    def promote_to_project(self, *, expected_revision: int | None = None) -> Project:
        """Return the one project matured from this task."""

        project_model = apps.get_model("projects", "Project")
        return project_model.objects.from_task(self, expected_revision=expected_revision)

    @classmethod
    def visibility_permission(cls, value: str) -> str:
        """The visibility verb's permission for one declared audience."""
        return "narrow" if value == cls.TaskVisibility.RESTRICTED else "widen"

    @classmethod
    def visibility_blockers(cls, value: str) -> tuple[tuple[models.Q, Callable[[], ValidationError]], ...]:
        """Domain constraints shared by the locked verb and choice projections."""
        return ()

    @classmethod
    def visibility_allowed_expression(cls, actor: Any, value: str) -> models.expressions.BaseExpression:
        """Batch permission and domain eligibility without exposing hidden facts."""
        if actor is None:
            return models.Value(False)
        rows = cls.objects.with_actor(actor).with_action(cls.visibility_permission(value)).scoped_for_aggregate()
        for condition, _error in cls.visibility_blockers(value):
            rows = rows.exclude(condition & ~models.Q(visibility=value))
        return models.Exists(rows.filter(pk=models.OuterRef("pk")))

    def set_visibility(self, value: str, *, expected_revision: int | None = None) -> Task:
        """Narrow or widen the task through its dedicated permission."""

        try:
            visibility = self.TaskVisibility(value)
        except ValueError as error:
            raise ValidationError({"visibility": "Choose inherited or restricted."}) from error
        actor, bypass = self.effective_actor(strict=True)
        permission = self.visibility_permission(visibility)
        with transaction.atomic():
            locked = type(self).system_queryset(lock=("self",)).get(pk=self.pk)
            if not bypass and (
                actor is None
                or not backend().check_access(subject=actor, action=permission, resource=to_object_ref(locked)).allowed
            ):
                raise PermissionDenied(f"{permission.title()} access to the task is required.")
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if locked.visibility != visibility:
                with system_context(reason="projects.task.validate_visibility"):
                    locked.validate_visibility(visibility)
                locked.visibility = visibility
                locked.allow_immutable_save("visibility")
                if actor is not None:
                    locked.with_actor(actor)
                locked.sudo(reason="projects.task.set_visibility").save(update_fields=("visibility", "updated_at"))
            self.refresh_from_db()
        return self

    def validate_visibility(self, value: str) -> None:
        """Check declared domain constraints on the locked row under system context.

        Contributors extend ``visibility_blockers`` so reads and writes agree.
        The task lock does not cover messages; message writers must also preserve
        their audience invariants under the same task lock.
        """
        for condition, error in self.visibility_blockers(value):
            if type(self).system_queryset().filter(condition, pk=self.pk).exists():
                raise error()

    def validate_record_access_subject(self, relation: str, subject: Any) -> None:
        """Refuse a record task's account holder who cannot read its record.

        The task schema hides a record task from anyone who cannot read the
        record, so such an assignee or grantee would hold a task they never see.
        Group holders are not resolved: members who cannot read the record simply
        do not see the task. An elevated write (a seed or a migration) assigns
        within grants it manages itself.
        """

        super().validate_record_access_subject(relation, subject)
        reference = to_subject_ref(subject)
        if (
            self.subject_object_id is None
            or reference.optional_relation
            or reference.subject_type != user_subject_type()
        ):
            return
        if self.effective_actor()[1]:
            return
        if reference.subject_id == "*":
            raise RecordAccessSubjectRefused()
        candidate = candidate_relationships(self, "subject_read")
        if candidate is None or not candidate.allows(reference, "subject_read"):
            raise RecordAccessSubjectRefused()

    def thread_audience_members(self) -> Iterable[AudienceMember]:
        """Notify the current assignee's existing party without creating a follower."""

        yield from super().thread_audience_members()
        if self.assignee_id is not None:
            person = apps.get_model("parties", "Person").system_queryset().filter(user_id=self.assignee_id).first()
            if person is not None:
                yield AudienceMember(party_id=person.pk)

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

    def _validate_structure(self) -> None:
        """Validate project-owned milestone scope; HierarchyMixin owns ancestry."""

        with system_context(reason="projects.task.validate_structure"):
            if self.milestone_id is not None:
                milestone_model = type(self).milestone.field.related_model
                milestone = milestone_model._base_manager.filter(pk=self.milestone_id).first()
                if milestone is not None and milestone.project_id != self.project_id:
                    raise ValidationError({"milestone": "Milestone must belong to the task's project."})


class TaskRelation(HistoryMixin, AuditMixin, AngeeDataModel):
    """One canonical directed relation between two tasks."""

    runtime = True
    sqid_prefix = "trl_"

    class TaskRelationKind(models.TextChoices):
        """Canonical task relation kinds."""

        BLOCKS = "blocks", "Blocks"
        RELATES = "relates", "Relates"

    SYMMETRIC_KINDS = frozenset({TaskRelationKind.RELATES})
    """Kinds whose endpoints have no semantic direction.

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
        constraints: ClassVar[list[models.BaseConstraint] | tuple[models.BaseConstraint, ...]] = (
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

        task_id = cast(Any, self).task_id
        related_task_id = cast(Any, self).related_task_id
        if (
            self.kind not in self.SYMMETRIC_KINDS
            or task_id is None
            or related_task_id is None
            or task_id < related_task_id
        ):
            return False
        cast(Any, self).task_id, cast(Any, self).related_task_id = related_task_id, task_id
        return True

    @property
    def inverse_kind(self) -> str:
        """Return the relation vocabulary seen from ``related_task``."""

        if self.kind == self.TaskRelationKind.BLOCKS:
            return "blocked_by"
        return str(self.kind)

    def __str__(self) -> str:
        """Return a readable directed edge."""

        return f"{self.task_id} {self.kind} {self.related_task_id}"


class Participant(HistoryMixin, AuditMixin, AngeeDataModel):
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
        constraints: ClassVar[list[models.BaseConstraint] | tuple[models.BaseConstraint, ...]] = (
            models.UniqueConstraint(
                fields=("project", "party"),
                name="uq_projects_participant_project_party",
            ),
        )

    def __str__(self) -> str:
        """Return a readable involvement label."""

        return f"{self.party_id} in {self.project_id}"


class ProjectBinding(HistoryMixin, AuditMixin, RecordRefMixin, AngeeDataModel):
    """One explicit projects-owned statement that a resource belongs to a project.

    The resource is stored at :func:`rebac.generic_target`. ``permissions.zed``
    backs one relation per bindable type with ``target`` and requires share on
    the project and the type's own grant authority to create or delete a
    binding, so bindings are written under the actor through
    :meth:`ProjectBindingManager.bind`; a type no relation names is refused. A
    binding is not moved: the library refuses a retargeting save under an actor.
    """

    history_revert_permission = "delete"
    merge_policy = MergePolicy.MOVE
    merge_identity = (("project", "content_type", "object_id"),)
    runtime = True
    sqid_prefix = "pbd_"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="resource_bindings",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    objects = ProjectBindingManager()

    class Meta:
        """Django model options for explicit project resource bindings."""

        abstract = True
        ordering = ("project", "content_type", "object_id", "sqid")
        rebac_resource_type = "projects/project_binding"
        constraints: ClassVar[list[models.BaseConstraint] | tuple[models.BaseConstraint, ...]] = (
            models.UniqueConstraint(
                fields=("project", "content_type", "object_id"),
                name="uq_projects_binding_project_target",
            ),
        )
        indexes = (models.Index(fields=("content_type", "object_id")),)

    def clean(self) -> None:
        """Require a live, declared resource."""

        super().clean()
        if self.target is None:
            raise ValidationError({"target": "A project binding target is required."})
        type(self).objects.validate_target(self.target)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Store the resource at its canonical identity (a channel as its integration)."""

        target = self.target
        if target is None:
            raise ValidationError({"target": "A project binding target is required."})
        type(self).objects.validate_target(target)
        canonical = generic_target(target)
        self.content_type = canonical.content_type
        self.object_id = canonical.object_id
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return a readable project-to-resource binding label."""

        return f"{self.project_id} -> {self.content_type_id}:{self.object_id}"


class Link(HistoryMixin, AuditMixin, RecordRefMixin, AngeeDataModel):
    """A URL-keyed external reference attached to a project or task."""

    merge_policy = MergePolicy.MOVE
    merge_identity = (("content_type", "object_id", "url"),)
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
        constraints: ClassVar[list[models.BaseConstraint] | tuple[models.BaseConstraint, ...]] = (
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
        self.validate_target(self.target)

    def __str__(self) -> str:
        """Return the link title or URL."""

        return self.title or self.url


class ProjectBindingsMixin(models.Model):
    """Projects-owned reverse collection for canonical generic bindings."""

    project_bindings = GenericRelation(
        "projects.ProjectBinding",
        content_type_field="content_type",
        object_id_field="object_id",
    )

    class Meta:
        abstract = True


class FolderProjects(ProjectBindingsMixin):
    """Compose project binding collection onto storage folders."""

    extends: ClassVar[str | None] = "storage.Folder"

    class Meta:
        abstract = True


class DriveProjects(ProjectBindingsMixin):
    """Projects-owned reverse collection for storage-drive bindings."""

    extends: ClassVar[str | None] = "storage.Drive"

    class Meta:
        abstract = True


class IntegrationProjects(ProjectBindingsMixin):
    """Projects-owned reverse collection for canonical messaging-channel bindings."""

    extends: ClassVar[str | None] = "integrate.Integration"

    class Meta:
        abstract = True


class ThreadProjects(ProjectBindingsMixin):
    """Projects-owned reverse collection for messaging-thread bindings."""

    extends: ClassVar[str | None] = "messaging.Thread"

    class Meta:
        abstract = True


class VaultProjects(ProjectBindingsMixin):
    """Projects-owned reverse collection for knowledge-vault bindings."""

    extends: ClassVar[str | None] = "knowledge.Vault"

    class Meta:
        abstract = True
