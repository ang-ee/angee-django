"""Projects, milestones, tasks, involvement, relations, and external links."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
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
    current_actor,
    system_context,
    to_object_ref,
)
from rebac.backends import backend

from angee.base.actors import actor_user_id
from angee.base.fields import FractionalRankField, StateField
from angee.base.mixins import (
    AuditMixin,
    CreationKeyMixin,
    CreationKeyQuerySet,
    HistoryMixin,
    ImmutableFieldsMixin,
    ItemOwnershipMixin,
    OptimisticLockMixin,
    OwnerMixin,
    OwnerQuerySet,
    RevisionMixin,
)
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet
from angee.base.refs import RecordRefMixin, canonical_record_model, canonical_record_target
from angee.base.scoping import bind_actor, system_queryset
from angee.messaging.models import AudienceMember, ThreadedModelMixin
from angee.projects.access import bind, require_binding_access, require_target_binding_access
from angee.projects.events import (
    milestone_reached,
    project_phase_changed,
    project_status_changed,
    task_promoted,
)
from angee.projects.inputs import MilestoneTemplate
from angee.scheduling.fields import RecurrenceField


class ProjectSetupState(StrEnum):
    """Readiness of persisted setup acts, independent of the project's lifecycle."""

    NOT_SET_UP = "not_set_up"
    PARTIAL = "partial"
    COMPLETE = "complete"


class ProjectQuerySet(OwnerQuerySet[Any], AngeeQuerySet[Any]):
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

    def bulk_create(self, objs: Iterable[Any], *args: Any, **kwargs: Any) -> list[Any]:
        """Require target authorization for every new home-folder attachment."""

        objs = tuple(objs)
        if any(project.folder_id is not None for project in objs):
            raise ValueError("Projects with folders require instance authorization through save().")
        return super().bulk_create(objs, *args, **kwargs)


class ProjectManager(AngeeManager.from_queryset(ProjectQuerySet)):  # type: ignore[misc]
    """Own the idempotent Task-to-Project maturation write."""

    def setup_from_task(
        self, task: Any, *, configuration: Mapping[str, Any], client_creation_key: str,
        expected_revision: int | None = None,
    ) -> Any:
        """Apply all setup acts atomically; replay never overwrites subsequent edits.

        Receipts belong to the actor, task and client key, independently of
        ordinary Project inserts. Existing partial projects can be completed;
        failed invocations leave neither partial acts nor a receipt.
        """

        actor, bypass = task.effective_actor(strict=True)
        actor_id = actor_user_id(actor)
        if actor_id is None:
            raise ValidationError({"client_creation_key": "Setup requires a user actor."})
        receipt_model = apps.get_model("projects", "ProjectSetupReceipt")
        fingerprint = receipt_model.creation_fingerprint_for(configuration)
        with transaction.atomic():
            locked = system_queryset(type(task), lock=("self",)).get(pk=task.pk)
            bind_actor(locked, actor)
            if not bypass and not locked.has_access("write"):
                raise PermissionDenied("Task write access is required for project setup.")
            project = system_queryset(self.model, lock=("self",)).filter(converted_from=locked).first()
            if project is not None:
                bind_actor(project, actor)
                if not bypass and not project.has_access("write"):
                    raise PermissionDenied("Project write access is required for setup.")

            def insert() -> Any:
                if expected_revision is not None:
                    locked.require_revision(expected_revision)
                target = project if project is not None else self.from_task(locked)
                target.apply_setup(**configuration)
                return receipt_model.objects.create(
                    actor_id=actor_id, task=locked, project=target,
                    client_creation_key=client_creation_key, creation_fingerprint=fingerprint,
                )

            receipt, _created = receipt_model.objects.replay_or_insert(
                (actor_id, locked.pk), client_creation_key, fingerprint, insert,
            )
            result = receipt.project
            bind_actor(result, actor)
            if not bypass and not result.has_access("write"):
                raise PermissionDenied("Project write access is required for setup replay.")
            return result

    def from_task(self, task: Any, *, expected_revision: int | None = None) -> models.Model:
        """Return the one project promoted from ``task``, creating it if needed."""

        if task.pk is None:
            raise ValidationError("A task must be saved before it can be promoted.")
        actor, bypass = task.effective_actor(strict=True)
        with transaction.atomic():
            locked_task = system_queryset(type(task), lock=("self",)).get(pk=task.pk)
            if not bypass and not locked_task.with_actor(actor).has_access("write"):
                raise PermissionDenied("Write access to the task is required to promote it.")
            if expected_revision is not None:
                locked_task.require_revision(expected_revision)
            with system_context(reason="projects.project.promote_from_task.lookup"):
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
            else:
                locked_task.sudo(reason="projects.task.promoted").save_without_historical_record(
                    update_fields=("revision",),
                )
                bind_actor(locked_task.unsudo(), actor)
                bind_actor(project.unsudo(), verified_actor)
                task_promoted.send(sender=type(locked_task), task=locked_task, project=project)
            task.refresh_from_db()
            bind_actor(project, verified_actor)
            return project


class ProjectSetupReceipt(CreationKeyMixin, models.Model):
    """Internal receipt for one actor/task/key; only the setup owner writes it."""

    runtime = True
    creation_key_scope = ("actor", "task")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    task = models.ForeignKey("projects.Task", on_delete=models.CASCADE, related_name="+")
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="+")
    objects = models.Manager.from_queryset(CreationKeyQuerySet)()

    class Meta:
        abstract = True
        constraints = (CreationKeyMixin.creation_key_constraint(scope=("actor", "task")),)


class TaskQuerySet(CreationKeyQuerySet[Any], OwnerQuerySet[Any], AngeeQuerySet[Any]):
    """Task rows with shared creation replay and ownership release contracts."""

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

    def priority_rank_expression(self) -> models.Case:
        """Order urgency by the priority field's declared enum order."""

        priorities = self.model._meta.get_field("priority").choices_enum
        return models.Case(
            *(models.When(priority=priority, then=models.Value(rank)) for rank, priority in enumerate(priorities)),
            output_field=models.IntegerField(),
        )

    def promoted_phase_expression(self) -> models.Expression:
        """Project only the phase name admitted by the task's narrow permission."""

        tasks = cast(TaskQuerySet, self.with_action("read_promoted_phase"))
        return (
            tasks.filter(pk=models.OuterRef("pk"))
            .order_by()
            .readable_scalar_subquery(
                "promoted_projects__current_milestone__name",
            )
        )


class TaskManager(AngeeManager.from_queryset(TaskQuerySet)):  # type: ignore[misc]
    """Own idempotent task promotion from a ThreadActivity."""

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
            )
            task.allocate_ordering_ranks()
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


class LinkManager(AngeeManager):
    """Own URL-keyed upserts on live project and task targets."""

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


class ProjectBindingQuerySet(AngeeQuerySet[Any]):
    """Explicit bindings whose deletion requires authority over both ends."""

    def update(self, **kwargs: Any) -> int:
        """Keep binding identity edits on the both-end authorization path."""

        if self.model.binding_fields.intersection(kwargs):
            raise ValueError("Project binding identities require authorization through save().")
        return super().update(**kwargs)

    def bulk_update(self, objs: Any, fields: Any, *args: Any, **kwargs: Any) -> int:
        """Refuse identity edits that bypass instance authorization."""

        if self.model.binding_fields.intersection(fields):
            raise ValueError("Project binding identities require authorization through save().")
        return super().bulk_update(objs, fields, *args, **kwargs)

    def bulk_create(self, objs: Iterable[Any], *args: Any, **kwargs: Any) -> list[Any]:
        """Require instance authorization before creating access-bearing bindings."""

        raise ValueError("Project bindings require both-end authorization through bind() or save().")

    def delete(self) -> tuple[int, dict[str, int]]:
        """Require canonical unbind authority for every explicit bulk deletion."""

        for binding in self.select_related("content_type"):
            project = binding.project
            target = binding.target
            if target is None:
                raise ValidationError({"target": "A live project binding target is required for deletion."})
            require_binding_access(project=project, target=target)
        return super().delete()


class Project(
    OwnerMixin,
    ItemOwnershipMixin,
    OptimisticLockMixin,
    ThreadedModelMixin,
    HistoryMixin,
    RevisionMixin,
    AngeeDataModel,
):
    """A bounded endeavor whose access is owned by direct ReBAC grants."""

    runtime = True
    sqid_prefix = "prj_"
    revisioned_fields = ("title", "body")
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
    knowledge_bindings = GenericRelation("knowledge.RecordBinding", related_query_name="project")

    objects = ProjectManager()

    class Meta:
        """Django model options for projects."""

        abstract = True
        ordering = ("status", "target_date", "title", "sqid")
        rebac_resource_type = "projects/project"
        constraints = (
            models.UniqueConstraint(
                fields=("converted_from",),
                name="uq_projects_project_converted_from",
            ),
        )

    def __str__(self) -> str:
        """Return the project title."""

        return self.title

    @property
    def on_path(self) -> bool:
        """Whether this lifecycle state keeps the milestone path active."""

        return self.status == self.ProjectStatus.OPEN

    def apply_setup(
        self, *, milestones: list[dict[str, Any]] | None = None, vault_template: Any = None,
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
        if self.current_milestone_id is None:
            self.set_current_milestone(first)
        binding_model = apps.get_model("projects", "ProjectBinding")
        vault_model = apps.get_model("knowledge", "Vault")
        content_type = ContentType.objects.get_for_model(vault_model)
        if not binding_model.objects.filter(project=self, content_type=content_type).exists():
            if vault_template is None:
                raise ValidationError({"vault_template": "Choose a vault template."})
            vault = vault_model.objects.create_from(
                vault_template, name=self.title, client_creation_key=f"project:{self.pk}",
            )
            bind(project=self, target=vault)

    @classmethod
    def setup_complete_condition(cls, actor: Any) -> Q:
        """Compose readable setup evidence; optional addons add their own acts."""

        milestones = apps.get_model("projects", "Milestone").objects.with_actor(actor).scoped()
        bindings = apps.get_model("projects", "ProjectBinding").objects.with_actor(actor).scoped()
        return Q(models.Exists(milestones.filter(pk=models.OuterRef("current_milestone_id")))) & Q(
            models.Exists(bindings.filter(
                project_id=models.OuterRef("pk"), content_type__app_label="knowledge", content_type__model="vault",
            )),
        )

    @classmethod
    def setup_state_expression(cls, actor: Any) -> models.Expression:
        """Annotate persisted setup readiness without per-row queries."""

        if actor is None:
            return models.Value(ProjectSetupState.PARTIAL.value)
        return models.Case(
            models.When(cls.setup_complete_condition(actor), then=models.Value(ProjectSetupState.COMPLETE.value)),
            default=models.Value(ProjectSetupState.PARTIAL.value), output_field=models.CharField(),
        )

    @classmethod
    def overdue_milestone_count_expression(cls, actor: Any, *, milestone_name: str | None = None) -> models.Expression:
        """Count the named unfinished phase, or the current phase when omitted."""

        milestones = apps.get_model("projects", "Milestone").objects.overdue().filter(
            project_id=models.OuterRef("pk"),
        )
        milestones = milestones.filter(name=milestone_name) if milestone_name is not None else milestones.filter(
            pk=models.OuterRef("current_milestone_id"),
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

        return self._set_status(str(self.ProjectStatus.PAUSED), expected_revision=expected_revision)

    def resume(self, *, expected_revision: int | None = None) -> Project:
        """Return this project to open work, idempotently."""

        return self._set_status(str(self.ProjectStatus.OPEN), expected_revision=expected_revision)

    def complete(self, *, expected_revision: int | None = None) -> Project:
        """Complete this project, idempotently."""

        return self._set_status(str(self.ProjectStatus.DONE), expected_revision=expected_revision)

    def drop(self, *, expected_revision: int | None = None) -> Project:
        """Drop this project, idempotently."""

        return self._set_status(str(self.ProjectStatus.DROPPED), expected_revision=expected_revision)

    def _set_status(self, status: str, *, expected_revision: int | None = None) -> Project:
        """Persist and announce one authorized lifecycle change in one transaction."""

        actor, bypass = self.effective_actor(strict=True)
        with transaction.atomic():
            locked = system_queryset(type(self), lock=("self",)).get(pk=self.pk)
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
                bind_actor(locked.unsudo(), actor)
                project_status_changed.send(
                    sender=type(locked),
                    project=locked,
                    previous_status=previous_status,
                    status=status,
                )
            self.refresh_from_db()
        return self

    def selectable_milestones(self) -> models.QuerySet[Any]:
        """Return this project's phase choices; contributors may narrow eligibility."""

        return self.milestones.all()

    def set_current_milestone(
        self,
        milestone: models.Model,
        *,
        expected_revision: int | None = None,
    ) -> Project:
        """Select an eligible phase without changing planned dates or historical receipts."""

        actor, bypass = self.effective_actor(strict=True)
        with transaction.atomic():
            locked = system_queryset(type(self), lock=("self",)).get(pk=self.pk)
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
                        bind_actor(locked, actor)
                    locked._set_status(str(self.ProjectStatus.OPEN))
                bind_actor(locked.unsudo(), actor)
                if previous is not None:
                    bind_actor(previous.unsudo(), actor)
                bind_actor(milestone.unsudo(), actor)
                project_phase_changed.send(
                    sender=type(locked),
                    project=locked,
                    previous_milestone=previous,
                    milestone=milestone,
                )
            self.refresh_from_db()
        return self


class MilestoneQuerySet(CreationKeyQuerySet[Any], AngeeQuerySet[Any]):
    """Milestone rows with the shared creation replay contract."""

    def overdue(self) -> Any:
        """Unfinished milestones past their target on open projects."""

        return self.filter(target_date__lt=timezone.localdate(), reached_at__isnull=True, project__status="open")


class Milestone(CreationKeyMixin, OptimisticLockMixin, ImmutableFieldsMixin, AuditMixin, AngeeDataModel):
    """A named marker reached within one project."""

    runtime = True
    sqid_prefix = "mls_"
    immutable_fields = ("reached_at", "reached_by_id")
    creation_key_scope = "project"

    objects = AngeeManager.from_queryset(MilestoneQuerySet)()

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
        constraints = (
            CreationKeyMixin.creation_key_constraint(scope="project"),
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
            locked = system_queryset(type(self), lock=("self",)).get(pk=self.pk)
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
                bind_actor(locked.unsudo(), actor)
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


class Task(
    CreationKeyMixin,
    ImmutableFieldsMixin,
    OwnerMixin,
    OptimisticLockMixin,
    ThreadedModelMixin,
    HistoryMixin,
    AngeeDataModel,
):
    """The platform's one table for a discrete human action."""

    runtime = True
    sqid_prefix = "tsk_"
    thread_tracking_fields = ("status", "assignee", "due_date", "priority", "visibility")
    thread_post_access = "comment"
    owner_container = "project"
    immutable_fields = ("visibility",)
    rebac_grantable = {"reader": "share", "editor": "share"}

    class TaskVisibility(models.TextChoices):
        """Whether this task inherits its containers' access."""

        INHERITED = "inherited", "Inherited"
        RESTRICTED = "restricted", "Restricted"

    visibility = StateField(choices_enum=TaskVisibility, default=TaskVisibility.INHERITED)

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
        related_query_name="task",
    )
    file_attachments = GenericRelation("storage.FileAttachment", related_query_name="task")
    knowledge_bindings = GenericRelation("knowledge.RecordBinding", related_query_name="task")

    objects = TaskManager()

    class Meta:
        """Django model options for tasks."""

        abstract = True
        ordering = ("project", "sort_order", "sub_sort_order", "sqid")
        rebac_resource_type = "projects/task"
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
            CreationKeyMixin.creation_key_constraint(),
        )

    def __str__(self) -> str:
        """Return the task title."""

        return self.title

    @classmethod
    def setup_state_expression(cls, actor: Any) -> models.Expression:
        """Read setup state only through a project the task's actor can read."""

        if actor is None:
            return models.Value(ProjectSetupState.NOT_SET_UP.value)
        project = apps.get_model("projects", "Project")
        rows = project.objects.with_actor(actor).scoped().filter(converted_from_id=models.OuterRef("pk"))
        return rows.annotate(_setup=project.setup_state_expression(actor)).readable_scalar_subquery(
            "_setup", actor=actor, default=ProjectSetupState.NOT_SET_UP.value, output_field=models.CharField(),
        )

    @classmethod
    def overdue_milestone_count_expression(cls, actor: Any, *, milestone_name: str | None = None) -> models.Expression:
        """Count readable linked/promoted projects with an overdue named/current phase."""

        if actor is None:
            return models.Value(0)
        project = apps.get_model("projects", "Project")
        rows = project.objects.with_actor(actor).scoped().filter(
            Q(converted_from_id=models.OuterRef("pk")) | Q(pk=models.OuterRef("project_id")),
        ).alias(
            _overdue=project.overdue_milestone_count_expression(actor, milestone_name=milestone_name),
        ).filter(_overdue__gt=0)
        return rows.readable_count_subquery(actor=actor)

    def allocate_ordering_ranks(self) -> None:
        """Fill omitted project and parent ordering ranks through their fields."""

        for field_name in ("sort_order", "sub_sort_order"):
            field = cast(FractionalRankField, self._meta.get_field(field_name))
            field.pre_save(self, True)

    def priority_rank(self) -> int:
        """Use the SQL annotation or the priority field's declared enum order."""

        if hasattr(self, "_priority_rank"):
            return self._priority_rank
        return list(self._meta.get_field("priority").choices_enum).index(self.priority)

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
        self._validate_structure(lock=False)

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
                system_queryset(type(self)).filter(pk=self.pk).values_list("project_id", "assignee_id").get()
            )
        if project_is_written and self.project_id is not None:
            if self._state.adding or self.project_id != previous_project_id:
                actor, bypass = self.effective_actor(strict=True)
                if not bypass and not self.project.with_actor(actor).has_access("write"):
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
                self._validate_structure(lock=True)
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

    def complete(self) -> Task:
        """Mark this task done, idempotently preserving its first completion time."""

        if self.status == self.TaskStatus.DONE and self.done_at is not None and self.dropped_at is None:
            return self
        cast(Any, self).status = str(self.TaskStatus.DONE)
        self.done_at = self.done_at or timezone.now()
        cast(Any, self).dropped_reason = None
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
        cast(Any, self).status = str(self.TaskStatus.DROPPED)
        cast(Any, self).dropped_reason = str(reason_member)
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
        cast(Any, self).status = str(self.TaskStatus.OPEN)
        self.done_at = None
        cast(Any, self).dropped_reason = None
        self.dropped_at = None
        self.save(update_fields=("status", "done_at", "dropped_reason", "dropped_at", "updated_at"))
        return self

    def promote_to_project(self, *, expected_revision: int | None = None) -> models.Model:
        """Return the one project matured from this task."""

        project_model = apps.get_model("projects", "Project")
        return project_model.objects.from_task(self, expected_revision=expected_revision)

    @classmethod
    def visibility_permission(cls, value: str) -> str:
        """The visibility verb's permission for one declared audience."""
        return "narrow" if value == cls.TaskVisibility.RESTRICTED else "widen"

    @classmethod
    def visibility_blockers(cls, value: str) -> tuple[tuple[models.Q, type[ValidationError]], ...]:
        """Domain constraints shared by the locked verb and choice projections."""
        return ()

    @classmethod
    def visibility_allowed_expression(cls, actor: Any, value: str) -> models.Expression:
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
            locked = system_queryset(type(self), lock=("self",)).get(pk=self.pk)
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
                bind_actor(locked, actor)
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
            if system_queryset(type(self)).filter(condition, pk=self.pk).exists():
                raise error()

    def thread_audience_members(self) -> Iterable[AudienceMember]:
        """Notify the current assignee's existing party without creating a follower."""

        yield from super().thread_audience_members()
        if self.assignee_id is not None:
            person = system_queryset(apps.get_model("parties", "Person")).filter(user_id=self.assignee_id).first()
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

    def _validate_structure(self, *, lock: bool) -> None:
        """Validate milestone scope and the complete parent chain."""

        with system_context(reason="projects.task.validate_structure"):
            if self.milestone_id is not None:
                milestone_model = self._meta.get_field("milestone").related_model
                milestone = milestone_model._base_manager.filter(pk=self.milestone_id).first()
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
        constraints = (
            models.UniqueConstraint(
                fields=("project", "party"),
                name="uq_projects_participant_project_party",
            ),
        )

    def __str__(self) -> str:
        """Return a readable involvement label."""

        return f"{self.party_id} in {self.project_id}"


class ProjectBinding(AuditMixin, RecordRefMixin, AngeeDataModel):
    """One explicit projects-owned statement that a resource belongs to a project."""

    runtime = True
    sqid_prefix = "pbd_"
    binding_fields = frozenset({"project", "project_id", "content_type", "content_type_id", "object_id"})
    allowed_target_models = frozenset(
        {
            "knowledge.vault",
            "messaging.channel",
            "messaging.thread",
            "storage.drive",
            "storage.folder",
        }
    )
    """Concrete input models accepted before MTI target canonicalization."""

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="resource_bindings",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    objects = AngeeManager.from_queryset(ProjectBindingQuerySet)()

    class Meta:
        """Django model options for explicit project resource bindings."""

        abstract = True
        ordering = ("project", "content_type", "object_id", "sqid")
        rebac_resource_type = "projects/project_binding"
        constraints = (
            models.UniqueConstraint(
                fields=("project", "content_type", "object_id"),
                name="uq_projects_binding_project_target",
            ),
        )
        indexes = (models.Index(fields=("content_type", "object_id")),)

    @classmethod
    def validate_target(cls, target: models.Model) -> None:
        """Reject resources outside the single projects-owned binding declaration."""

        target_label = target._meta.label_lower
        for allowed_label in cls.allowed_target_models:
            allowed_model = apps.get_model(allowed_label)
            canonical_model = canonical_record_model(allowed_model)
            if target_label == allowed_label:
                return
            if (
                target_label == canonical_model._meta.label_lower
                and allowed_model._base_manager.filter(pk=target.pk).exists()
            ):
                return
        raise ValidationError(
            {"target": "Project bindings may target only drives, folders, messaging channels, threads, or vaults."}
        )

    def clean(self) -> None:
        """Require a live allowed target."""

        super().clean()
        if self.target is None:
            raise ValidationError({"target": "A project binding target is required."})
        self.validate_target(self.target)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Canonicalize every persisted target through the projects binding owner."""

        target = self.target
        if target is None:
            raise ValidationError({"target": "A project binding target is required."})
        self.validate_target(target)
        update_fields = kwargs.get("update_fields")
        key_is_written = update_fields is None or bool(self.binding_fields.intersection(update_fields))
        canonical = canonical_record_target(target)
        if key_is_written:
            if not self._state.adding:
                previous = (
                    type(self)
                    ._base_manager.filter(pk=self.pk)
                    .values_list("project_id", "content_type_id", "object_id")
                    .first()
                )
                current = (self.project_id, canonical.content_type.pk, canonical.object_id)
                if previous is not None and previous != current:
                    previous_project_id, previous_content_type_id, previous_object_id = previous
                    previous_project = (
                        apps.get_model("projects", "Project").objects.filter(pk=previous_project_id).first()
                    )
                    if previous_project is None:
                        raise PermissionDenied("Share access to the previous binding project is required.")
                    previous_content_type = ContentType.objects.get_for_id(previous_content_type_id)
                    previous_target = previous_content_type.get_object_for_this_type(pk=previous_object_id)
                    require_binding_access(project=previous_project, target=previous_target)
            require_binding_access(project=self.project, target=target)
        self.content_type = canonical.content_type
        self.object_id = canonical.object_id
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Require canonical unbind authority for direct instance deletion."""

        target = self.target
        if target is None:
            raise ValidationError({"target": "A live project binding target is required for deletion."})
        require_binding_access(project=self.project, target=target)
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        """Return a readable project-to-resource binding label."""

        return f"{self.project_id} -> {self.content_type_id}:{self.object_id}"


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

    extends = "storage.Folder"

    class Meta:
        abstract = True


class DriveProjects(ProjectBindingsMixin):
    """Projects-owned reverse collection for storage-drive bindings."""

    extends = "storage.Drive"

    class Meta:
        abstract = True


class IntegrationProjects(ProjectBindingsMixin):
    """Projects-owned reverse collection for canonical messaging-channel bindings."""

    extends = "integrate.Integration"

    class Meta:
        abstract = True


class ThreadProjects(ProjectBindingsMixin):
    """Projects-owned reverse collection for messaging-thread bindings."""

    extends = "messaging.Thread"

    class Meta:
        abstract = True


class VaultProjects(ProjectBindingsMixin):
    """Projects-owned reverse collection for knowledge-vault bindings."""

    extends = "knowledge.Vault"

    class Meta:
        abstract = True
