"""Projects, milestones, tasks, involvement, relations, and external links."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from datetime import datetime
from enum import StrEnum
from graphlib import TopologicalSorter
from typing import Any, ClassVar, Self, cast

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, router, transaction
from django.db.models import F, Q
from django.utils import timezone
from django_compose.models import Composable
from rebac import (
    PermissionDenied,
    actor_context,
    current_actor,
    generic_target,
    system_context,
    to_object_ref,
    to_subject_ref,
    user_subject_type,
)
from rebac.backends import backend
from rebac.field_backing import canonical_model

from angee.base.exceptions import RecordAccessSubjectRefused
from angee.base.fields.ordering import FractionalRankExhausted, FractionalRankField
from angee.base.fields.relations import RetainedGenericRelation
from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.hierarchy import HierarchyMixin, HierarchyQuerySet
from angee.base.models.identity import instances_from_public_ids
from angee.base.models.locks import ImmutableFieldsMixin, OptimisticLockMixin
from angee.base.models.merge import MergeableMixin
from angee.base.models.merge_references import MergePolicy
from angee.base.models.ownership import ItemOwnershipMixin, OwnerMixin, OwnerQuerySet
from angee.base.models.refs import RecordRefMixin
from angee.base.models.trash import TrashMixin, TrashQuerySet
from angee.base.permissions import actor_user_id, candidate_relationships
from angee.base.querysets import MergeableQuerySet
from angee.base.serialization import strip_null_bytes
from angee.base.validators import validate_value
from angee.messaging.models import AudienceMember, ThreadedModelMixin
from angee.projects.access import require_binding_access, require_target_binding_access
from angee.projects.events import (
    milestone_reached,
    project_phase_changed,
    project_status_changed,
    task_promoted,
)
from angee.projects.inputs import MilestoneTemplate, TaskOrder
from angee.provenance.context import changeset, changeset_model, require_changeset
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager, AngeeHistoryMergeableUnscopedManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryMergeableUnscopedQuerySet, AngeeHistoryQuerySet
from angee.provenance.statements import Aspect, SourceKind, StatementRecords, StatementsMixin
from angee.scheduling.due import Anchor, Due, DueRule, DueStatus, Term
from angee.scheduling.fields import DueRuleField
from angee.tags.models import TaggedModel

STATED_BY: tuple[SourceKind, ...] = (SourceKind.PERSON, SourceKind.SYSTEM_OF_RECORD)
"""Source precedence of every projects aspect.

A person's normal stance outranks a plain stream, a system of reference; an
authoritative stream (``SyncStream.authoritative``) outranks it as the system of
record (``Aspect.priority``). A person's confirm or dismiss stays on top.
"""


class StatedRecordMixin(StatementsMixin):
    """A projects row a system of record states through the ``integrate.RecordLink`` naming it.

    A local row stays an ordinary record: a person's edit becomes a stance only
    once a source states it (``person_stances_once_stated``). A source is admitted
    by ``write_statements`` alone, kept out of ``read`` and ``write`` so no row
    inheriting them reaches a source. Statements protect a stated row and every
    row they name, so deleting them is refused.
    """

    person_stances_once_stated = True
    statement_write_permission = "write_statements"
    record_links = RetainedGenericRelation(
        "integrate.RecordLink", content_type_field="target_content_type", object_id_field="target_object_id"
    )

    class Meta:
        abstract = True

    def delete_blocker(self) -> str | None:
        """Name the refusal the statement tables' protected key enforces on every delete path."""

        if self.carries_statements():
            return f"This {self._meta.verbose_name} carries source statements and is retained."
        return cast(Any, super()).delete_blocker()


SET_ANCHOR = "set"
"""The due anchor naming a row's own hand-set date (``DueRule(terms=[Term(anchor="set")])``)."""

CREATED_ANCHOR = "created"
"""The built-in due anchor naming the subject's creation instant."""

HELD = "held"
"""``due_basis`` key: a person's or a source's date holds the row against its rule."""

REPEATS = "repeats"
"""``due_basis`` key: a repetition's resolution inputs, stored when it is created."""

UNRESOLVED = "unresolved"
"""``due_basis`` key: why the rule could not resolve when its anchors last moved."""

_ROW_BASIS_KEYS = (HELD, REPEATS, UNRESOLVED)

logger = logging.getLogger(__name__)


class RuleDatedMixin(models.Model):
    """A projects row whose date may come from a due rule (``angee.scheduling.due``).

    ``due_rule`` (``NULL``: the date is set by hand) resolves against the
    subject's due anchors (:meth:`due_anchor_values`) into the row's own date
    column (``due_date_field``, and ``due_instant_field`` when the row keeps an
    instant). ``due_basis`` is the rule's resolution (``Due.as_json()``) plus the
    row's own keys: ``held``, ``repeats`` (a repetition's inputs) and
    ``unresolved`` (a resolution failure recorded when anchors moved).

    Precedence: a person's or a source's write of the date **holds** the row,
    whatever the value, and the basis keeps the rule's value; anchor moves and
    rule edits then move only rows that are not held, and
    :meth:`reset_due_to_rule` releases the hold. An insert without a date
    follows its rule; one with a date is held. The instant is only ever the
    rule's: a held row keeps a date and no instant. A row's date is history once
    it is closed (:meth:`due_history_q`): anchor moves and resets leave it. Rules
    over counters are refused, because projects supply no counter readings.
    """

    due_rule = DueRuleField()
    due_basis = models.JSONField(default=dict, blank=True, editable=False)

    due_date_field: ClassVar[str]
    """The row's date column the rule resolves into."""
    due_instant_field: ClassVar[str | None] = None
    """The row's aware instant column, when the row keeps one."""
    due_subject_fields: ClassVar[tuple[str, ...]] = ("project", "project_id")
    """Columns whose change moves the row to other due anchors."""
    _due_rule_write: bool = False
    _due_subject_anchor_values: dict[str, Any] | None = None

    class Meta:
        abstract = True

    @classmethod
    def due_history_q(cls) -> models.Q:
        """Rows whose date is history (a closed task, a reached milestone): rules no longer move it."""

        raise NotImplementedError

    def due_is_history(self) -> bool:
        """Whether this stored row's date is history (:meth:`due_history_q`)."""

        return self.pk is not None and type(self)._base_manager.filter(self.due_history_q(), pk=self.pk).exists()

    def due_subject_anchors(self) -> dict[str, Any]:
        """The due anchors of the row's subject: its project's, read fresh unless an owner operation handed them in.

        :meth:`Project.resolve_due_rules` and the template copy compute the map
        once and give it to every row they resolve (:meth:`use_subject_anchors`).
        """

        if self._due_subject_anchor_values is not None:
            return self._due_subject_anchor_values
        project_id = cast(Any, self).project_id
        if project_id is None:
            return {}
        project_model = apps.get_model("projects", "Project")
        project = project_model._base_manager.only("created_at", "is_template", "due_anchors").get(pk=project_id)
        return project.due_anchor_values()

    def use_subject_anchors(self, anchors: dict[str, Any]) -> Self:
        """Resolve against ``anchors``, the subject's map an owner operation computed once for all its rows."""

        self._due_subject_anchor_values = anchors
        return self

    def due_anchor_values(self) -> dict[str, Any]:
        """The anchors this row's rule resolves against: its subject's, plus ``set``, its own date."""

        return {**self.due_subject_anchors(), SET_ANCHOR: getattr(self, self.due_date_field)}

    def resolve_due(self, rule: DueRule) -> Due:
        """Resolve ``rule`` for this row: against its due anchors, by default."""

        return rule.resolve(anchors=self.due_anchor_values())

    def validate_due_rule(self) -> None:
        """Refuse a rule projects cannot resolve: its subject supplies no counter readings."""

        rule = DueRule.from_json(self.due_rule)
        if rule is not None and any(term.counter for term in rule.terms):
            raise ValidationError({"due_rule": "A project row's rule measures calendar time; it has no counters."})

    def clean(self) -> None:
        """Validate the rule the row can resolve, beside the model's own checks."""

        super().clean()
        self.validate_due_rule()

    def _due_point_fields(self) -> tuple[str, ...]:
        return (self.due_date_field, *((self.due_instant_field,) if self.due_instant_field else ()))

    def _due_point(self) -> tuple[Any, ...]:
        return tuple(getattr(self, name) for name in self._due_point_fields())

    def _point_of(self, due: Due | None) -> tuple[Any, ...]:
        local_date = due.local_date if due is not None else None
        at = due.at if due is not None else None
        return (local_date, *((at,) if self.due_instant_field else ()))

    def rule_due(self) -> Due | None:
        """The rule's resolution stored in ``due_basis`` (its row keys set aside), or ``None``."""

        if not self.due_basis or UNRESOLVED in self.due_basis:
            return None
        return Due.from_json({key: value for key, value in self.due_basis.items() if key not in _ROW_BASIS_KEYS})

    def due_held(self) -> bool:
        """Whether a person's or a source's date holds the row against its rule."""

        return bool(self.due_basis.get(HELD))

    def follows_due_rule(self) -> bool:
        """Whether the row's date is its rule's: it has a rule and no hand or stated date holds it."""

        return self.due_rule is not None and not self.due_held()

    def apply_due_rule(self, *, hold: bool | None = None, record_failure: bool = False) -> set[str]:
        """Resolve the rule into ``due_basis`` and, unless the row is held, into its date.

        ``hold`` sets (``True``) or releases (``False``) the hold; ``None`` keeps
        it. With ``record_failure`` a resolution error is recorded in the basis
        (``unresolved``) and the date stays; otherwise it raises. Writes nothing:
        returns the changed field names for the caller's save. Without a rule the
        basis and the instant clear and the date stays.
        """

        changed: set[str] = set()
        instant = self.due_instant_field
        if self.due_rule is None:
            if self.due_basis:
                self.due_basis = {}
                changed.add("due_basis")
            if instant and getattr(self, instant) is not None:
                setattr(self, instant, None)
                changed.add(instant)
            return changed
        held = self.due_held() if hold is None else hold
        kept = {key: self.due_basis[key] for key in (REPEATS,) if key in self.due_basis}
        rule = DueRule.from_json(self.due_rule)
        assert rule is not None  # a blank rule returned above
        due: Due | None
        try:
            due = self.resolve_due(rule)
        except ValidationError as error:
            if not record_failure:
                raise
            due = None
            basis = {**kept, UNRESOLVED: "; ".join(error.messages), **({HELD: True} if held else {})}
        else:
            basis = {**due.as_json(), **kept, **({HELD: True} if held else {})}
        if basis != self.due_basis:
            self.due_basis = basis
            changed.add("due_basis")
        if due is not None and not held:
            for name, value in zip(self._due_point_fields(), self._point_of(due), strict=True):
                if getattr(self, name) != value:
                    setattr(self, name, value)
                    changed.add(name)
        elif held and instant and getattr(self, instant) is not None:
            setattr(self, instant, None)
            changed.add(instant)
        return changed

    @contextmanager
    def due_rule_write(self) -> Iterator[None]:
        """Mark the saves inside as the rule's own: they move the date without holding it."""

        self._due_rule_write = True
        try:
            yield
        finally:
            self._due_rule_write = False

    def save_due_rule(self, *, hold: bool | None = None, reason: str) -> bool:
        """Apply the rule and save the result as a system write; record a failure instead of raising.

        The caller authorized the change. A system write records no person's
        stance on a stated row. Returns whether anything changed.
        """

        changed = self.apply_due_rule(hold=hold, record_failure=True)
        if not changed:
            return False
        with self.due_rule_write(), system_context(reason=reason):
            cast(Any, self).save(update_fields=(*sorted(changed), "updated_at"))
        return True

    def hold_due(self, values: Mapping[str, Any]) -> None:
        """Hold the row at a person's or a source's stated date values (the ``schedule`` aspect).

        Writes the values even when they equal the rule's, so the hold is recorded.
        """

        changed = False
        for name, value in values.items():
            attname = cast(models.Field, self._meta.get_field(name)).attname
            if getattr(self, attname) != value:
                setattr(self, attname, value)
                changed = True
        if changed or (self.due_rule is not None and not self.due_held()):
            cast(Any, self).save(update_fields=(*values, "updated_at"))

    def apply_schedule(self, *, member: Mapping[str, Any], values: Mapping[str, Any], statement: Any | None) -> None:
        """Apply a surviving ``schedule`` stance as a hold (:meth:`hold_due`), even at the rule's own value.

        No surviving stance (a withdrawal) or an unknown value keeps the date.
        """

        del member
        if statement is None or statement.snak == statement.StatementSnak.SOME_VALUE:
            return
        self.hold_due(values)

    def due_status(self, *, now: datetime | None = None) -> DueStatus | None:
        """Where ``now`` stands against the row's date, computed on read and never stored.

        A row following its rule reads the rule's basis (its warn and grace
        windows included); a hand-set or stated date resolves through
        ``DueRule(terms=[Term(anchor="set")])``. ``None`` without a date.
        """

        moment = now or timezone.now()
        if self.follows_due_rule():
            due = self.rule_due()
            return due.get_status(now=moment) if due is not None else None
        day = getattr(self, self.due_date_field)
        if day is None:
            return None
        return DueRule(terms=(Term(anchor=SET_ANCHOR),)).resolve(anchors={SET_ANCHOR: day}).get_status(now=moment)

    def reset_due_to_rule(self, *, expected_revision: int | None = None) -> Self:
        """Release a hand-set or stated date and hand the row's date back to its due rule.

        A closed task's or a reached milestone's date is history and is refused.
        The reset is the rule's write: it records no person's stance.
        """

        row = cast(Any, self)
        actor, bypass = row.effective_actor(strict=True)
        with transaction.atomic():
            locked = type(row).system_queryset(lock=("self",)).get(pk=row.pk)
            if not bypass and not locked.with_actor(actor).has_access("write"):
                raise PermissionDenied(f"Write access to the {self._meta.verbose_name} is required to reset its date.")
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if locked.due_rule is None:
                raise ValidationError({"due_rule": f"This {self._meta.verbose_name} has no due rule."})
            if locked.due_is_history():
                raise ValidationError({"due_rule": f"This {self._meta.verbose_name} is closed; its date is history."})
            changed = locked.apply_due_rule(hold=False)
            if changed:
                # The rule's write, not the person's: it records no stance a source's statement competes with.
                with locked.due_rule_write(), system_context(reason="projects.reset_due_to_rule"):
                    locked.save(update_fields=(*sorted(changed), "updated_at"))
            row.refresh_from_db()
        return self

    def _written_by_hand(self, update_fields: Any) -> bool:
        """Whether this save writes the row's date as a person's or a source's value.

        The rule's own writes (:meth:`due_rule_write`) never hold. An insert with
        a date and a save naming the date column hold it; a full save holds it
        when the date differs from the stored one.
        """

        if self._due_rule_write or self.due_rule is None:
            return False
        fields = self._due_point_fields()
        if self._state.adding:
            return getattr(self, self.due_date_field) is not None
        if update_fields is not None:
            return bool(set(fields).intersection(update_fields))
        stored = type(self)._base_manager.filter(pk=cast(Any, self).pk).values_list(*fields).first()
        return stored is not None and tuple(stored)[0] != getattr(self, self.due_date_field)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Hold a hand or stated date, then re-resolve the rule when the rule, the date or the subject is written."""

        update_fields = kwargs.get("update_fields")
        watched = {"due_rule", *self._due_point_fields(), *self.due_subject_fields}
        if self._state.adding or update_fields is None or watched.intersection(update_fields):
            self.validate_due_rule()
            hold = True if self._written_by_hand(update_fields) else None
            changed = self.apply_due_rule(hold=hold, record_failure=self._due_rule_write)
            if update_fields is not None and changed:
                kwargs["update_fields"] = {*update_fields, *changed}
        super().save(*args, **kwargs)


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

        project, _created = self._promote(task, expected_revision=expected_revision)
        return project

    def _promote(
        self,
        task: Task,
        *,
        expected_revision: int | None = None,
        defaults: Mapping[str, Any] | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> tuple[ProjectT, bool]:
        """Return ``task``'s one promoted project and whether this call created it, under the task's lock.

        A new project is titled, described and led from the task (a blank one
        takes ``defaults``), takes ``defaults`` for every other value and
        ``values`` over both, in one insert.
        """

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
                return (existing.with_actor(actor) if actor is not None else existing), False

            promoted = {"title": locked_task.title, "body": locked_task.note, "lead_id": locked_task.assignee_id}
            given = {name: value for name, value in promoted.items() if value}
            fields = {**(defaults or {}), **given, **(values or {})}
            if "lead" in fields:
                fields.pop("lead_id", None)
            project = self.model(**fields, converted_from_id=locked_task.pk)
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
                task.refresh_from_db()
                return (project.with_actor(actor) if actor is not None else project), False
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
            return (project.with_actor(actor) if actor is not None else project), True

    def create_from_template(
        self,
        template: ProjectT,
        *,
        idempotency_key: str,
        values: Mapping[str, Any] | None = None,
        task: Task | None = None,
        anchors: Mapping[str, Any] | None = None,
        role_mapping: Mapping[str, Any] | None = None,
    ) -> ProjectT:
        """Create a project from a readable template with its items, atomically and idempotently.

        Requires template read and project create. The new project takes the
        template's title and what each owner's ``template_copy_values`` names;
        ``values`` override them, in one insert. With ``task``, the project is
        the task's promoted project, which also requires write on the task: it is
        titled, described and led from the task (a blank value takes the
        template's), under the task's lock, and a task that already has a project
        is refused. ``anchors`` are the new project's named due anchors
        (:meth:`Project.set_due_anchor`); ``role_mapping`` maps a template task's
        ``assignee_role`` to the person who takes it. What the copy carries is
        :meth:`Project._copy_template`'s. The key belongs to the creating actor:
        replay returns the created project, and the same key with other content
        conflicts. Template edits afterwards change only later copies.
        """

        actor, bypass = template.effective_actor(strict=True)
        actor_id = actor_user_id(actor)
        if actor_id is None and not bypass:
            raise ValidationError({"idempotency_key": "Creating from a template requires a user actor."})
        named = {
            self.model.due_anchor_name(name): validate_value(Anchor, value, field=f"anchors.{name}").as_json()
            for name, value in sorted((anchors or {}).items()) if value is not None
        }
        values = dict(values or {})
        roles = {str(role): person for role, person in sorted((role_mapping or {}).items())}
        with changeset(fresh=True), transaction.atomic():
            # The claim's unique key serializes same-key creations; the template is only read.
            source = type(template).system_queryset().get(pk=template.pk)
            if not bypass and not source.with_actor(actor).has_access("read"):
                raise PermissionDenied("Read access to the template is required.")
            result = changeset_model().objects.claim_created(
                f"projects.template:{actor_id}",
                idempotency_key,
                values={"template": source, "task": task, "values": values, "anchors": named, "roles": roles},
                model=self.model,
                unavailable=ValidationError({"idempotency_key": "The created project is unavailable."}),
            )
            if result is not None:
                if not bypass and not result.with_actor(actor).has_access("read"):
                    raise PermissionDenied("Read access to the created project is required for replay.")
                return result
            if not source.is_template:
                raise ValidationError({"template": "Choose a template project."})
            source.validate_template()
            copied = source.template_copy_values()
            if task is None:
                project = self.model(**{"title": source.title, **copied, **values})
                project.full_clean(validate_unique=False, validate_constraints=False)
                if bypass:
                    project.sudo(reason="projects.project.create_from_template").save()
                    project.unsudo()
                else:
                    project = self.with_actor(actor).insert(project)
            else:
                project, created = self._promote(task, defaults=copied, values=values)
                if not created:
                    raise ValidationError({"task": "This task already has a project."})
            project._copy_template(source, anchors=named, role_mapping=roles)
            require_changeset().record_created(project)
            if actor is not None:
                project.with_actor(actor)
            return project


class TaskOrderingQuerySet[TaskT: Task](models.QuerySet[TaskT]):
    """Keep every tracked Task rank writer inside its one ordering transaction."""

    @contextmanager
    def _ordering_write(
        self, fields: Iterable[str] | None = None, *,
        project_ids: Iterable[Any] = (), parent_ids: Iterable[Any] = (),
    ) -> Iterator[Self]:
        if fields is not None and not self.model.ORDERING_FIELDS.intersection(fields):
            yield self
            return
        with self.model.ordering_write(rows=self, project_ids=project_ids, parent_ids=parent_ids) as rows:
            yield cast(Self, rows)

    def update(self, **kwargs: Any) -> int:
        """Lock selected source and destination contexts before native tracked writes.

        Django evaluates project/parent expressions once for the selected rows.
        Freeze those destinations so a later subquery change cannot write into
        a context outside this lock set.
        """

        if not self.model.ORDERING_FIELDS.intersection(kwargs):
            return super().update(**kwargs)
        rows = self
        destinations = {}
        for name in ("project", "parent"):
            key = next((key for key in (name, f"{name}_id") if key in kwargs), None)
            if key is None:
                continue
            field = self.model._meta.get_field(name)
            assert isinstance(field, models.ForeignKey)
            value = kwargs.pop(key)
            if isinstance(value, models.Model):
                value = field.target_field.value_from_object(value)
            if not isinstance(value, (models.expressions.BaseExpression, models.F, models.QuerySet)):
                value = models.Value(value, output_field=field.target_field)
            destinations[name] = value
        project_ids, parent_ids = (), ()
        if destinations:
            aliases = {f"_ordering_{name}": value for name, value in destinations.items()}
            selected = list(self.annotate(**aliases).values_list("pk", *aliases))
            rows = self.filter(pk__in=[row[0] for row in selected])
            contexts = {}
            for index, name in enumerate(destinations, 1):
                field = self.model._meta.get_field(name)
                assert isinstance(field, models.ForeignKey)
                values = tuple(row[index] for row in selected)
                contexts[name] = values
                if all(value is None for value in values):
                    # Django casts neither integer output fields nor typed NULL Values here.
                    # PostgreSQL infers an all-NULL CASE as text, so write native NULL.
                    kwargs[field.attname] = None
                else:
                    kwargs[field.attname] = models.Case(
                        *(
                            models.When(pk=row[0], then=models.Value(row[index], output_field=field.target_field))
                            for row in selected
                        ),
                        default=models.Value(None),
                        output_field=field.target_field,
                    )
            project_ids, parent_ids = contexts.get("project", ()), contexts.get("parent", ())
        with rows._ordering_write(kwargs, project_ids=project_ids, parent_ids=parent_ids) as locked:
            return super(TaskOrderingQuerySet, locked).update(**kwargs)

    def bulk_update(self, objs: Any, fields: Any, *args: Any, **kwargs: Any) -> int:
        """Lock only this batch's persisted and requested ordering contexts."""

        fields, objs = tuple(fields), tuple(objs)
        rows = self.filter(pk__in=[row.pk for row in objs])
        projects = [row.project_id for row in objs] if {"project", "project_id"}.intersection(fields) else ()
        parents = [row.parent_id for row in objs] if {"parent", "parent_id"}.intersection(fields) else ()
        with rows._ordering_write(fields, project_ids=projects, parent_ids=parents) as locked:
            return super(TaskOrderingQuerySet, locked).bulk_update(objs, fields, *args, **kwargs)

    def _allocate_missing_ranks(self, rows: Sequence[TaskT]) -> None:
        for name in self.model.ORDERING_RANK_FIELDS:
            field = self.model._meta.get_field(name)
            assert isinstance(field, FractionalRankField)
            field.allocate_missing_ranks(rows)

    def bulk_create(
        self, objs: Any, batch_size: int | None = None, ignore_conflicts: bool = False,
        update_conflicts: bool = False, update_fields: Iterable[str] | None = None,
        unique_fields: Iterable[str] | None = None,
    ) -> list[TaskT]:
        """Lock inserted destinations and any native upsert conflict sources."""

        rows = list(objs)
        unique_fields = tuple(unique_fields) if unique_fields is not None else None
        sources = self.none()
        if update_conflicts and unique_fields:
            fields = []
            for name in unique_fields:
                field = self.model._meta.pk if name == "pk" else self.model._meta.get_field(name)
                assert isinstance(field, models.Field)
                fields.append(field)
            matches = Q(pk__in=[])
            for row in rows:
                matches |= Q(**{field.attname: getattr(row, field.attname) for field in fields})
            sources = self.model._base_manager.filter(matches)
        for row in rows:
            row._default_root_subrank()
        with sources._ordering_write(
            project_ids=(row.project_id for row in rows), parent_ids=(row.parent_id for row in rows),
        ):
            self._allocate_missing_ranks(rows)
            return super().bulk_create(
                rows, batch_size, ignore_conflicts, update_conflicts,
                tuple(update_fields) if update_fields is not None else None, unique_fields,
            )


class TaskOrderingUnscopedQuerySet[TaskT: Task](
    TaskOrderingQuerySet[TaskT], AngeeHistoryMergeableUnscopedQuerySet[TaskT],
):
    """Retain tracked base-manager writes under Task's ordering policy."""


_TaskBaseManagerBase = AngeeHistoryMergeableUnscopedManager.from_queryset(TaskOrderingUnscopedQuerySet)


class TaskBaseManager[TaskT: Task](_TaskBaseManagerBase[TaskT]):
    """Importable captured base manager composing the Task ordering contract."""


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
    TaskOrderingQuerySet[TaskT],
    TaskVisibilityQuerySet[TaskT],
    MergeableQuerySet[TaskT],
    TrashQuerySet[TaskT],
    OwnerQuerySet[TaskT],
    AngeeHistoryQuerySet[TaskT],
):
    """Task rows with hierarchy, merge, trash, and ownership contracts."""

    def create_forest(self, rows: Sequence[TaskT]) -> list[TaskT]:
        """Bracket the hierarchy owner's native insert/path initialization once."""

        for row in rows:
            row._default_root_subrank()
        with self.none()._ordering_write(
            project_ids=(row.project_id for row in rows), parent_ids=(row.parent_id for row in rows),
        ):
            return super().create_forest(rows)

    def waiting_on(self, actor: Any) -> Self:
        """Open, untrashed tasks assigned to this actor, within the caller's scope."""

        return self.filter(self.waiting_on_filter(actor))

    def waiting_on_filter(self, actor: Any) -> models.Q:
        """Assignment attention on an already readable task, without another read scope."""

        identity = actor_user_id(to_subject_ref(actor)) if actor is not None else None
        return models.Q(pk__in=[]) if identity is None else self.untrashed_q() & models.Q(
            status=self.model.TaskStatus.OPEN, assignee_id=identity,
        ) & ~models.Q(self.in_template_expression())

    def in_template_expression(self) -> models.Exists:
        """Whether a task belongs to a template project: always a boolean, false for a task about no project.

        Template tasks are copied by ``create_from_template`` and are nobody's work.
        """

        projects = self.model._meta.get_field("project").related_model
        return models.Exists(projects._base_manager.filter(pk=models.OuterRef("project_id"), is_template=True))

    def for_record_filter(self, record: Any, *, include_done: bool = True) -> models.Q:
        """Subject membership and optional open state, independent of task authorization."""

        predicate = models.Q(**generic_target(record).lookups(self.model, "subject"))
        return predicate if include_done else predicate & models.Q(status=self.model.TaskStatus.OPEN)

    def for_record(self, record: Any, *, include_done: bool = True) -> Self:
        """Return ``record``'s tasks this queryset reads: open first, then by due date and title.

        The task schema answers who reads them through its declared subject.
        """

        self.model.validate_target(record)
        tasks = self.filter(self.for_record_filter(record, include_done=include_done))
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

        if isinstance(record, self.model) and record.subject_object_id is not None:
            # Its access is its record's: a task about it would answer to nobody's.
            raise ValidationError("A record task takes no record tasks of its own.")
        title = strip_null_bytes(title or "").strip()
        if not title:
            raise ValidationError({"title": "A record task needs a title."})
        assignee_id = assignee.pk if isinstance(assignee, models.Model) else assignee
        assignee_id = assignee_id or actor_user_id(current_actor())
        if assignee_id is None:
            raise ValidationError({"assignee": "A record task needs an assignee."})
        kinds = self.model.activity_type.field.related_model
        with transaction.atomic():
            self.model.validate_target(record)
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
        """Delete an admitted binding; an absent or out-of-scope binding returns zero.

        Native scoped deletion checks the same authority as ``bind`` on every
        affected row. It never probes a hidden binding outside the actor scope.
        """

        key = generic_target(target).lookups(self.model, "target")
        deleted, _by_model = self.filter(project=project, **key).delete()
        return deleted


class Project(
    TaggedModel,
    OwnerMixin,
    ItemOwnershipMixin,
    OptimisticLockMixin,
    ThreadedModelMixin,
    StatedRecordMixin,
    HistoryMixin,
    AngeeDataModel,
    concrete=True,
):
    """A bounded endeavor whose access is owned by direct ReBAC grants."""

    sqid_prefix = "prj_"
    owned_relations = ("milestones",)
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
    is_template = models.BooleanField(default=False, db_index=True)
    """A template is copied by :meth:`ProjectManager.create_from_template` and kept out of ordinary lists."""
    created_from_template = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="created_projects",
    )
    due_anchors = models.JSONField(default=dict, blank=True, editable=False)
    """Named due anchors (name to ``angee.scheduling.due.Anchor`` JSON), written by :meth:`set_due_anchor`."""
    links = GenericRelation(
        "projects.Link",
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="project",
    )

    def apply_lifecycle(self, *, member: Mapping[str, Any], values: Mapping[str, Any], statement: Any | None) -> None:
        """Apply the surviving lifecycle stance through the status owner.

        ``_set_status`` changes, stamps and announces the status; a stated change
        time then replaces its stamp. No surviving stance (a withdrawal) or an
        unknown value keeps the lifecycle.
        """

        del member
        if statement is None or statement.snak == statement.StatementSnak.SOME_VALUE:
            return
        self._set_status(values["status"] or self.ProjectStatus.OPEN)
        stated = values["status_changed_at"]
        if stated is not None and self.status_changed_at != stated:
            self.status_changed_at = stated
            self.save(update_fields=("status_changed_at", "updated_at"))

    statements = StatementRecords(
        link="integrate.RecordLink",
        aspects={
            "identity": Aspect(fields=("title", "body"), precedence=STATED_BY),
            "lifecycle": Aspect(fields=("status", "status_changed_at"), precedence=STATED_BY, apply=apply_lifecycle),
            "schedule": Aspect(
                fields=("start_date", "start_date_resolution", "target_date", "target_date_resolution"),
                precedence=STATED_BY,
            ),
            "assignment": Aspect(fields=("lead",), precedence=STATED_BY),
        },
        sqid_prefix="pjs_",
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
    merge_policies: ClassVar[Mapping[str, Any]] = {"converted_from": MergePolicy.KEEP}

    def __str__(self) -> str:
        """Return the project title."""

        return self.title

    @property
    def on_path(self) -> bool:
        """Whether this lifecycle state keeps the milestone path active."""

        return self.status == self.ProjectStatus.OPEN

    def validate_template(self) -> None:
        """Refuse a template a source states; contributors refuse their own facts through ``super()``.

        A template's copies carry no statements, so the template holds none.
        """

        if self.carries_statements():
            raise ValidationError({"is_template": "A project a source states cannot be a template."})

    def validate_becoming_template(self) -> None:
        """Refuse to turn a project with work history (a closed task, a reached phase) into a template."""

        self.validate_template()
        if self.pk is None:
            return
        tasks = apps.get_model("projects", "Task")._base_manager.filter(project_id=self.pk)
        milestones = apps.get_model("projects", "Milestone")._base_manager.filter(project_id=self.pk)
        if tasks.exclude(status="open").exists() or milestones.filter(reached_at__isnull=False).exists():
            raise ValidationError(
                {"is_template": "A project with closed tasks or reached phases cannot be a template."},
            )

    def template_copy_values(self) -> dict[str, Any]:
        """Project values a project created from this template carries; contributors extend through ``super()``.

        Dates, lifecycle, lead, folder and grants are the new project's own.
        """

        return {
            "body": self.body,
            "owns_items": self.owns_items,
            "start_date_resolution": self.start_date_resolution,
            "target_date_resolution": self.target_date_resolution,
        }

    def _copy_template(
        self, template: Project, *, anchors: Mapping[str, Any], role_mapping: Mapping[str, Any] | None = None,
    ) -> None:
        """Copy ``template``'s items into this new project; only :meth:`ProjectManager.create_from_template` calls it.

        That caller checked template read and ``is_template``. Copies, through
        each row's own insert under the creating actor (so every contributor's
        insert lifecycle runs): its milestones, in order, through
        :meth:`Milestone.setup_template_values` (the one milestone input); its
        open tasks and subtasks the actor reads, with the relations between
        copied tasks, through :meth:`Task.template_copy_values`; and the tags of
        the project and of every copied task, through the tag owner. A task
        holding an ``assignee_role`` takes the person ``role_mapping`` names for
        it, else no assignee: the role stays until someone is assigned. Due rules
        copy and resolve against ``anchors`` (a rule that cannot resolve records
        why on its row); dates, statements, grants, participants, bindings,
        links and files never copy. The template's current phase (else the
        first) becomes the project's current phase.
        """

        actor, bypass = self.effective_actor(strict=True)

        def insert(row: Any) -> Any:
            if isinstance(row, RuleDatedMixin):
                row.use_subject_anchors(resolved)
            with row.due_rule_write() if isinstance(row, RuleDatedMixin) else nullcontext():
                if not bypass:
                    return type(row).objects.with_actor(actor).insert(row)
                row.sudo(reason="projects.project.copy_template").save()
            row.unsudo()
            return row

        self.created_from_template = template
        self.due_anchors = dict(anchors)
        self.save(update_fields=("created_from_template", "due_anchors", "updated_at"))
        resolved = self.due_anchor_values()  # one anchor map for every copied row
        milestone_model = apps.get_model("projects", "Milestone")
        phases: dict[Any, Any] = {}
        for phase in milestone_model.system_queryset().filter(project_id=template.pk).order_by("sort_order", "pk"):
            values = milestone_model.setup_template_values(phase.template_copy_values())
            phases[phase.pk] = insert(milestone_model(project=self, **values))
        task_model = apps.get_model("projects", "Task")
        readable = task_model.system_queryset() if bypass else task_model.objects.with_actor(actor)
        sources = {
            row.pk: row
            for row in readable.filter(project_id=template.pk, status=task_model.TaskStatus.OPEN)
            .untrashed()
            .canonical()
            .order_by("pk")
        }
        order = TopologicalSorter({
            pk: (row.parent_id,) if row.parent_id in sources else () for pk, row in sources.items()
        }).static_order()
        copies: dict[Any, Any] = {}
        for pk in order:
            source = sources[pk]
            if source.parent_id is not None and source.parent_id not in copies:
                continue  # its parent is closed, removed or unreadable: the branch stays behind
            values = source.template_copy_values()
            if values.get("assignee_role"):
                person = (role_mapping or {}).get(values["assignee_role"])
                values["assignee_id"] = person.pk if isinstance(person, models.Model) else person
            copies[pk] = insert(task_model(
                project=self,
                milestone=phases.get(source.milestone_id),
                parent=copies.get(source.parent_id),
                **values,
            ))
        relation_model = apps.get_model("projects", "TaskRelation")
        for relation in relation_model._base_manager.filter(
            task_id__in=copies, related_task_id__in=copies,
        ).order_by("pk"):
            insert(relation_model(
                task=copies[relation.task_id], related_task=copies[relation.related_task_id], kind=relation.kind,
            ))
        assignments: Any = type(self)._meta.get_field("tag_assignments").related_model
        with system_context(reason="projects.project.copy_template.tags") if bypass else actor_context(actor):
            assignments.objects.copy([(template, self), *((sources[pk], copy) for pk, copy in copies.items())])
        first = phases.get(template.current_milestone_id) or next(iter(phases.values()), None)
        if first is not None:
            self.set_current_milestone(first)

    def due_anchor_values(self) -> dict[str, Any]:
        """The due anchors this project's rules resolve against; a template has none, so its rules stay pending.

        ``created`` is the project's creation instant; the named ``due_anchors``
        follow it (:meth:`set_due_anchor`).
        """

        if self.is_template:
            return {}
        return {
            CREATED_ANCHOR: self.created_at or timezone.now(),
            **{name: Anchor.from_json(value) for name, value in sorted(self.due_anchors.items())},
        }

    @staticmethod
    def due_anchor_name(name: str) -> str:
        """Return a named due anchor's canonical name; ``created`` and ``set`` are built in."""

        canonical = strip_null_bytes(str(name or "")).strip()
        if not canonical or len(canonical) > 64 or canonical in {CREATED_ANCHOR, SET_ANCHOR}:
            raise ValidationError({"name": "Name a due anchor other than created or set, in at most 64 characters."})
        return canonical

    def set_due_anchor(self, name: str, value: Any, *, expected_revision: int | None = None) -> Project:
        """Record (``None`` clears) one named due anchor, then re-resolve the rows that follow rules.

        ``value`` is an :class:`~angee.scheduling.due.Anchor`, its JSON, or a bare
        aware instant or date. ``created`` and ``set`` are built in, and a
        template holds rules, not anchors.
        """

        name = self.due_anchor_name(name)
        anchor = validate_value(Anchor, value, field="value").as_json() if value is not None else None
        actor, bypass = self.effective_actor(strict=True)
        with transaction.atomic():
            locked = type(self).system_queryset(lock=("self",)).get(pk=self.pk)
            if not bypass and not locked.with_actor(actor).has_access("write"):
                raise PermissionDenied("Write access to the project is required to set its due anchors.")
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if locked.is_template:
                raise ValidationError({"name": "A template holds due rules, not due anchors."})
            anchors = dict(locked.due_anchors)
            if anchor is None:
                anchors.pop(name, None)
            else:
                anchors[name] = anchor
            if anchors != locked.due_anchors:
                locked.due_anchors = anchors
                locked.sudo(reason="projects.project.set_due_anchor").save(update_fields=("due_anchors", "updated_at"))
                locked.unsudo()
                locked.resolve_due_rules()
            self.refresh_from_db()
        return self

    def resolve_due_rules(self) -> None:
        """Re-resolve this project's open rule-dated milestones and tasks against its due anchors.

        A system write: a following row's date is the project's fact, whoever may
        write the row; a held date stays and only its basis moves. A reached
        milestone and a closed task keep theirs: their dates are history. A row
        whose rule cannot resolve records why in its basis (``unresolved``) and
        keeps its date, so one row never blocks the project's anchors.
        """

        anchors = self.due_anchor_values()
        for model_name in ("Milestone", "Task"):
            model = apps.get_model("projects", model_name)
            rows = model.system_queryset(lock=("self",)).filter(project_id=self.pk, due_rule__isnull=False)
            for row in rows.exclude(model.due_history_q()).order_by("pk"):
                row.project = self  # the saves read the project they already hold
                row.use_subject_anchors(anchors).save_due_rule(reason="projects.project.resolve_due_rules")

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
        """Require target authority before a folder edit can widen project access; guard the template flag.

        A project becoming a template is validated (:meth:`validate_becoming_template`),
        and its rule-dated rows re-resolve whenever the flag changes: a template
        supplies no due anchors, so its rules wait.
        """

        update_fields = kwargs.get("update_fields")
        flag_changed = False
        if update_fields is None or "is_template" in update_fields:
            stored = None if self._state.adding else (
                type(self)._base_manager.filter(pk=self.pk).values_list("is_template", flat=True).first()
            )
            flag_changed = stored is not None and stored != self.is_template
            if self.is_template and (self._state.adding or flag_changed):
                self.validate_becoming_template()
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
        if flag_changed:
            self.resolve_due_rules()

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
                # Survivorship may re-apply a source's stance inside the save, and announces that change itself.
                if locked.status == status:
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
        """Unfinished milestones past their target on open projects; a template's phases are never overdue."""

        return self.filter(
            target_date__lt=timezone.localdate(),
            reached_at__isnull=True,
            project__status="open",
            project__is_template=False,
        )


_MilestoneManagerBase = AngeeHistoryManager.from_queryset(MilestoneQuerySet)


class _MilestoneManager[MilestoneT: Milestone](_MilestoneManagerBase[MilestoneT]):
    """Preserve the native milestone scheduling queryset on manager reads."""


class Milestone(
    RuleDatedMixin,
    StatedRecordMixin,
    HistoryMixin,
    OptimisticLockMixin,
    ImmutableFieldsMixin,
    AuditMixin,
    AngeeDataModel,
    concrete=True,
):
    """A named marker reached within one project; its target date may come from a due rule."""

    sqid_prefix = "mls_"
    immutable_fields = ("reached_at", "reached_by_id")
    due_date_field = "target_date"

    objects = _MilestoneManager()

    def lifecycle_candidates(self, rows: Sequence[Any]) -> Sequence[Any]:
        """A stamped receipt is historical: only the stances stating it compete, so no other one applies."""

        if self.reached_at is None:
            return rows
        return [row for row in rows if row.stated_value("reached_at") == self.reached_at]

    def apply_lifecycle(self, *, member: Mapping[str, Any], values: Mapping[str, Any], statement: Any | None) -> None:
        """Stamp a stated receipt through ``mark_reached``, at the stated time."""

        del member
        if statement is None or statement.snak != statement.StatementSnak.VALUE or values["reached_at"] is None:
            return
        self.mark_reached(at=values["reached_at"])

    statements = StatementRecords(
        link="integrate.RecordLink",
        aspects={
            "identity": Aspect(fields=("name", "description"), precedence=STATED_BY),
            "lifecycle": Aspect(
                fields=("reached_at",),
                precedence=STATED_BY,
                candidates=lifecycle_candidates,
                apply=apply_lifecycle,
            ),
            "schedule": Aspect(
                fields=("start_date", "target_date"), precedence=STATED_BY, apply=RuleDatedMixin.apply_schedule,
            ),
        },
        sqid_prefix="mss_",
    )

    @classmethod
    def setup_template_values(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        """Validate the milestone-owned portion of a setup template."""

        return MilestoneTemplate.values(values)

    @classmethod
    def due_history_q(cls) -> models.Q:
        """A reached milestone's target date is history."""

        return models.Q(reached_at__isnull=False)

    def template_copy_values(self) -> dict[str, Any]:
        """This template phase as a setup template; contributors extend through ``super()``.

        A copy carries the rule, never the dates: they resolve in the new project.
        """

        return {"name": self.name, "description": self.description, "due_rule": self.due_rule}

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

    def mark_reached(self, *, at: datetime | None = None, expected_revision: int | None = None) -> Milestone:
        """Stamp and announce the first historical receipt, retaining it on replay.

        ``at`` is a source's stated receipt time; the stamp is now without one.
        """

        actor, bypass = self.effective_actor(strict=True)
        with transaction.atomic():
            locked = type(self).system_queryset(lock=("self",)).get(pk=self.pk)
            if not bypass and not locked.with_actor(actor).has_access("reach"):
                raise PermissionDenied("Reach access to the milestone is required.")
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if locked.reached_at is None:
                locked.reached_at = at or timezone.now()
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


class ActivityType(AuditMixin, AngeeDataModel, concrete=True):
    """Resource-declared kinds of record task; tasks keep the stable key."""

    DEFAULT_KEY: ClassVar[str] = "todo"
    """The plain to-do projects seeds: a record task's kind when none is named."""

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
    def completion_line(cls, kind: Self | None, title: str) -> str:
        """Return the record chatter line announcing a done record task of ``kind``."""

        if kind is None or kind.key == cls.DEFAULT_KEY:
            return f"Done: {title}"
        return f"{kind.name} done: {title}"


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
    RuleDatedMixin,
    StatedRecordMixin,
    HistoryMixin,
    AngeeDataModel,
    concrete=True,
):
    """The platform's one table for a discrete human action.

    ``due_date`` is the local day lists, boards and timelines read; ``due_at`` is
    the aware instant a due rule gives when time of day matters (``RuleDatedMixin``).
    """

    _angee_base = TaskBaseManager()

    sqid_prefix = "tsk_"
    due_date_field = "due_date"
    due_instant_field = "due_at"
    MERGED_PERMISSION = "write"
    retains_merged = True
    merge_policy = MergePolicy.MOVE
    on_target_delete = models.CASCADE
    thread_post_access = "comment"
    owner_container = "project"
    immutable_fields: ClassVar[tuple[str, ...]] = ("visibility",)
    rebac_grantable = {"reader": "share", "editor": "share"}
    # A repetition's lineage stays on the original retained identity.
    merge_policies: ClassVar[Mapping[str, Any]] = {"repeated_from": MergePolicy.KEEP}

    ORDERING_RANK_FIELDS: ClassVar[tuple[str, ...]] = ("sort_order", "sub_sort_order")
    ORDERING_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "project", "project_id", "parent", "parent_id", *ORDERING_RANK_FIELDS,
    })

    @classmethod
    @contextmanager
    def ordering_write(
        cls, *, rows: models.QuerySet | None = None,
        project_ids: Iterable[Any] = (), parent_ids: Iterable[Any] = (),
    ) -> Iterator[models.QuerySet]:
        """Lock project order and non-root sibling order before target-row locks.

        Cross-project children still share their parent's sibling context.
        Root subranks have no ordering meaning; project ranks order roots.
        Freeze selected keys and refuse a concurrent context move after waiting.
        """

        rows = cls._base_manager.none() if rows is None else rows
        observed = {pk: (project, parent) for pk, project, parent in rows.order_by().values_list(
            "pk", "project_id", "parent_id",
        )}
        projects = {*project_ids, *(project for project, _parent in observed.values())}
        parents = {*parent_ids, *(parent for _project, parent in observed.values())} - {None}
        owner = cls._meta.get_field("sort_order").model
        scopes = sorted({
            *(f"project:{project}" for project in projects),
            *(f"parent:{parent}" for parent in parents),
        })
        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    for scope in scopes:
                        cursor.execute(
                            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            [f"angee.projects.task.ordering:{owner._meta.db_table}:{scope}"],
                        )
            selected = cls._base_manager.filter(pk__in=observed)
            if any(observed[pk] != (project, parent) for pk, project, parent in selected.values_list(
                "pk", "project_id", "parent_id",
            )):
                raise ValidationError({"task_order": "The task moved; reload it before changing order."})
            yield rows.filter(pk__in=observed)

    def _default_root_subrank(self) -> None:
        """An omitted root subrank is inert, independent of other root rows."""

        parent = type(self)._meta.get_field("parent")
        if self.sub_sort_order is None and self.parent_id is None and parent.get_cached_value(self, None) is None:
            self.sub_sort_order = FractionalRankField.STEP

    def apply_input_extensions(self, *, task_order: Any = None, **values: Any) -> None:
        """Place a board drop in the persisted project order under native admission."""

        if task_order is not None:
            order = validate_value(TaskOrder, task_order, field="task_order")
            with type(self).ordering_write(rows=type(self).system_queryset().filter(pk=self.pk)):
                actor, bypass = self.effective_actor(strict=True)
                project_id = type(self).system_queryset().values_list("project_id", flat=True).get(pk=self.pk)
                readable = (
                    type(self).system_queryset() if bypass
                    else type(self).objects.with_actor(actor).for_write()
                )
                requested = [*order.preceding, *order.following]
                neighbors = instances_from_public_ids(type(self), requested, queryset=readable)
                if len(set(requested)) != len(requested) or any(
                    key not in neighbors or neighbors[key].pk == self.pk for key in requested
                ):
                    raise ValidationError({"task_order": "The visible ordering neighbors changed; refresh the board."})
                preceding = next((
                    neighbors[key] for key in reversed(order.preceding)
                    if neighbors[key].project_id == project_id
                ), None)
                following = next((
                    neighbors[key] for key in order.following
                    if neighbors[key].project_id == project_id
                ), None)
                if preceding is not None and following is not None and preceding.sort_order >= following.sort_order:
                    raise ValidationError({"task_order": "The project order changed; refresh the board."})
                # Only ranks are read outside actor scope. Hidden or other-lane
                # rows participate in uniqueness without exposing their identity.
                ranks = type(self).system_queryset().filter(project_id=project_id).exclude(pk=self.pk)
                lower: float | None
                upper: float | None
                if following is not None:
                    upper = following.sort_order
                    lower = ranks.filter(sort_order__lt=upper).order_by("-sort_order").values_list(
                        "sort_order", flat=True,
                    ).first()
                elif preceding is not None:
                    lower = preceding.sort_order
                    upper = ranks.filter(sort_order__gt=lower).order_by("sort_order").values_list(
                        "sort_order", flat=True,
                    ).first()
                else:
                    lower = ranks.order_by("-sort_order").values_list("sort_order", flat=True).first()
                    upper = None
                try:
                    self.sort_order = FractionalRankField.get_rank_between(lower, upper)
                except FractionalRankExhausted:
                    field = type(self)._meta.get_field("sort_order")
                    assert isinstance(field, FractionalRankField)
                    field.rebalance(type(self).system_queryset().filter(project_id=project_id))
                    return self.apply_input_extensions(task_order=task_order, **values)
                # Keep the original actor: native write/write__sort_order and
                # history capture decide this save, including extension-only writes.
                self.save(update_fields=("sort_order", "updated_at"))
        super().apply_input_extensions(**values)

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
    assignee_role = models.CharField(max_length=64, blank=True, default="")
    """A consumer-named role a template task is assigned by; ``create_from_template`` maps it to a person."""
    delegate = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="delegated_project_tasks",
    )
    priority = StateField(choices_enum=TaskPriority, default=TaskPriority.NONE)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    due_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)
    repeated_from = models.OneToOneField(
        "projects.Task",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="repetition",
    )
    """The completed task whose repeating due rule created this one; each task repeats once."""
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

    def apply_lifecycle(self, *, member: Mapping[str, Any], values: Mapping[str, Any], statement: Any | None) -> None:
        """Apply the surviving lifecycle stance through the lifecycle verbs.

        ``complete``, ``drop`` and ``reopen`` own the state, its stamps and every
        capability projecting them, so a queued task enters the stage that
        projects the stated state and its status and stage never disagree. A
        state already held writes nothing; a stated close time then replaces the
        verb's stamp. No surviving stance (a withdrawal) or an unknown value keeps
        the lifecycle.
        """

        del member
        if statement is None or statement.snak == statement.StatementSnak.SOME_VALUE:
            return
        status = self.TaskStatus(values["status"] or self.TaskStatus.OPEN)
        reason = values["dropped_reason"] or self.dropped_reason or self.TaskDroppedReason.OBSOLETE
        if status == self.TaskStatus.DONE and self.status != status:
            self.complete()
        elif status == self.TaskStatus.DROPPED and (self.status != status or self.dropped_reason != reason):
            self.drop(reason)
        elif status == self.TaskStatus.OPEN and self.status != status:
            self.reopen()
        stamp = {self.TaskStatus.DONE: "done_at", self.TaskStatus.DROPPED: "dropped_at"}.get(status)
        if stamp is not None and values[stamp] is not None and getattr(self, stamp) != values[stamp]:
            setattr(self, stamp, values[stamp])
            self.save(update_fields=(stamp, "updated_at"))

    statements = StatementRecords(
        link="integrate.RecordLink",
        aspects={
            "identity": Aspect(fields=("title", "note"), precedence=STATED_BY),
            "lifecycle": Aspect(
                fields=("status", "done_at", "dropped_at", "dropped_reason"),
                precedence=STATED_BY,
                apply=apply_lifecycle,
            ),
            "schedule": Aspect(fields=("due_date",), precedence=STATED_BY, apply=RuleDatedMixin.apply_schedule),
            "priority": Aspect(fields=("priority",), precedence=STATED_BY),
            "assignment": Aspect(fields=("assignee",), precedence=STATED_BY),
            "structure": Aspect(fields=("project", "milestone", "parent"), precedence=STATED_BY),
        },
        sqid_prefix="tks_",
    )

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
                name="ck_%(app_label)s_%(class)s_subject_pair",
            ),
            models.UniqueConstraint(
                fields=("project", "sort_order"),
                name="uq_%(app_label)s_%(class)s_project_rank",
                nulls_distinct=False,
            ),
            models.UniqueConstraint(
                fields=("parent", "sub_sort_order"),
                condition=models.Q(parent__isnull=False),
                name="uq_%(app_label)s_%(class)s_parent_sub_rank",
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
    def due_history_q(cls) -> models.Q:
        """A closed (done or dropped) task's date is history."""

        return ~models.Q(status=cls.TaskStatus.OPEN)

    def due_subject_anchors(self) -> dict[str, Any]:
        """A task's due anchors are its project's; a task about no project has its own ``created``."""

        if self.project_id is None:
            return {CREATED_ANCHOR: self.created_at or timezone.now()}
        return super().due_subject_anchors()

    def resolve_due(self, rule: DueRule) -> Due:
        """A repetition steps from the inputs stored on it when it was created; a first task resolves its anchors.

        ``due_basis.repeats`` holds the previous due point and the completion
        (``DueRule.resolve_next``), so reopening the completed task never moves
        its repetition; a moved anchor still reaches a from-completion repeat.
        """

        inputs = self.due_basis.get(REPEATS) if self.due_basis else None
        if inputs:
            prior = Due.from_json(inputs["previous"])
            if prior is not None:
                following = rule.resolve_next(
                    prior, done=datetime.fromisoformat(inputs["done"]), anchors=self.due_anchor_values(),
                )
                if following is not None:
                    return following
        return super().resolve_due(rule)

    def repetition_values(self) -> dict[str, Any]:
        """Values the next task of this task's repeating rule carries; contributors extend through ``super()``.

        It keeps this task's place, subject, audience, owner, people and rule;
        its ranks, lifecycle, date, subtasks and direct shares are its own.
        """

        return {
            "project_id": self.project_id,
            "milestone_id": self.milestone_id,
            "parent_id": self.parent_id,
            "subject_content_type_id": self.subject_content_type_id,
            "subject_object_id": self.subject_object_id,
            "visibility": self.visibility,
            "owner_id": self.owner_id,
            "title": self.title,
            "note": self.note,
            "priority": self.priority,
            "assignee_id": self.assignee_id,
            "assignee_role": self.assignee_role,
            "delegate_id": self.delegate_id,
            "activity_type_id": self.activity_type_id,
            "due_rule": self.due_rule,
        }

    def create_repetition(self) -> Task | None:
        """Create the next task this completed task's repeating rule asks for, once per task.

        Runs when a save moves the stored status to done, whichever path completes
        it (a verb, a stage, a source). A system write: the rule asks for the
        work, whoever completed it. The repetition takes :meth:`repetition_values`
        and this task's tags, and is due at ``rule.resolve_next`` from this task's
        basis and completion, both stored on it (``due_basis.repeats``). Nothing
        is created when the rule ends or does not repeat, when this task already
        repeated (a reopened task completed again keeps its one repetition), when
        its project is closed or its parent task is no longer open; a reached
        milestone does not take the repetition.
        """

        rule = DueRule.from_json(self.due_rule)
        prior = self.rule_due()
        if rule is None or rule.repeat.kind == "once" or prior is None or self.done_at is None:
            return None
        if rule.repeat.kind == "calendar" and prior.local_date is None:
            return None
        if rule.resolve_next(prior, done=self.done_at, anchors=self.due_anchor_values()) is None:
            return None
        tasks = type(self)._base_manager
        if tasks.filter(repeated_from_id=self.pk).exists():
            return None
        projects = type(self).project.field.related_model._base_manager
        if self.project_id is not None and not projects.filter(
            pk=self.project_id, status__in=("open", "paused"),
        ).exists():
            return None
        if self.parent_id is not None and not tasks.filter(pk=self.parent_id, status=self.TaskStatus.OPEN).exists():
            return None
        values = self.repetition_values()
        milestones = type(self).milestone.field.related_model._base_manager
        if values.get("milestone_id") is not None and milestones.filter(
            pk=values["milestone_id"], reached_at__isnull=False,
        ).exists():
            values["milestone_id"] = None
        repetition = type(self)(
            **values,
            repeated_from=self,
            due_basis={REPEATS: {"previous": prior.as_json(), "done": self.done_at.isoformat()}},
        )
        with repetition.due_rule_write(), system_context(reason="projects.task.create_repetition"):
            repetition.save()
        assignments: Any = type(self)._meta.get_field("tag_assignments").related_model
        with system_context(reason="projects.task.create_repetition.tags"):
            assignments.objects.copy([(self, repetition)])
        return repetition

    def _completes(self, update_fields: Any) -> bool:
        """Whether this save moves a repeating task's stored status to done."""

        if self._state.adding or self.status != self.TaskStatus.DONE or self.due_rule is None:
            return False
        if update_fields is not None and "status" not in update_fields:
            return False
        stored = type(self)._base_manager.filter(pk=self.pk).values_list("status", flat=True).first()
        return stored is not None and stored != self.TaskStatus.DONE

    def _repeat_after_completion(self) -> None:
        """Create the repetition in its own savepoint: if it fails, the completion stands and the failure is logged."""

        try:
            with transaction.atomic():
                self.create_repetition()
        except Exception:  # noqa: BLE001 -- the person's completion outranks the rule's consequence
            logger.exception("Task %s was completed, but its repetition could not be created.", self.pk)

    def template_copy_values(self) -> dict[str, Any]:
        """Task values a copy of this template task carries; contributors extend through ``super()``.

        The rule and the audience copy and the date resolves in the new
        project; lifecycle, the record subject and grants are the copy's own.
        """

        return {
            "title": self.title,
            "note": self.note,
            "visibility": self.visibility,
            "priority": self.priority,
            "assignee_id": self.assignee_id,
            "assignee_role": self.assignee_role,
            "delegate_id": self.delegate_id,
            "activity_type_id": self.activity_type_id,
            "due_rule": self.due_rule,
            "sort_order": self.sort_order,
            "sub_sort_order": self.sub_sort_order,
        }

    @classmethod
    def lock_merge_records(cls, records: Sequence[Self]) -> None:
        """Acquire every merge participant's contexts before any Task row lock."""

        keys = [record.pk for record in records]
        affected = cls.system_queryset().filter(Q(pk__in=keys) | Q(parent_id__in=keys))
        with cls.ordering_write(rows=affected, parent_ids=keys):
            super().lock_merge_records(records)

    @classmethod
    def merge_contract(cls, reference: Any, rows: Any, *, survivor: Any) -> Any:
        """Append reparented children through the rank and hierarchy owners."""

        if reference.name != "parent":
            return NotImplemented
        list(cls.system_queryset(lock=("self",)).filter(parent=survivor).order_by("pk"))
        for child in rows.order_by("sub_sort_order", "pk"):
            child.parent = survivor
            child.sub_sort_order = None
            child.save(update_fields=("parent", "sub_sort_order", "updated_at"))

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

        if (rank := self.__dict__.get("_priority_rank")) is not None:
            return rank
        return list(type(self).priority.field.choices_enum).index(self.priority)

    def promoted_phase(self) -> str | None:
        """Use the annotation or fetch the phase through the same scoped expression."""

        phase = self.__dict__.get("_promoted_phase", models.DEFERRED)
        if phase is not models.DEFERRED:
            return phase
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
        """Save the row, then create the repetition a repeating rule asks for when the save completes it.

        If the repetition fails the completion stands and the failure is logged
        (:meth:`_repeat_after_completion`).
        """

        completing = self._completes(kwargs.get("update_fields"))
        self._save_row(*args, **kwargs)
        if completing and self.status == self.TaskStatus.DONE:
            self._repeat_after_completion()

    def _save_row(self, *args: Any, **kwargs: Any) -> None:
        """Authorize project moves and assignment before revalidating task structure."""

        fields = kwargs.get("update_fields")
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        guard = (
            type(self).ordering_write(
                rows=type(self).system_queryset().filter(pk=self.pk),
                project_ids=(self.project_id,), parent_ids=(self.parent_id,),
            )
            if self._state.adding or fields is None or self.ORDERING_FIELDS.intersection(fields)
            else nullcontext()
        )
        with guard:
            if self._state.adding or fields is None or {"parent", "parent_id", "sub_sort_order"}.intersection(fields):
                self._default_root_subrank()
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
                with transaction.atomic(using=using):
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
            self._write_status(self.TaskStatus.DONE)
            self.done_at = self.done_at or timezone.now()
            cast(Any, self).dropped_reason = None
            self.dropped_at = None
            self.save(update_fields=("status", "done_at", "dropped_reason", "dropped_at", "updated_at"))
            if self.status == self.TaskStatus.DONE:  # survivorship may re-apply a source's stance in the save
                self.post_completion(feedback)
        return self

    def _lock_lifecycle(self) -> None:
        """Hold this row's lock and load its stored lifecycle, so a concurrent verb replays."""

        if self.pk is None:
            return
        self.refresh_from_db(
            fields=("status", "done_at", "dropped_at", "dropped_reason"),
            from_queryset=type(self).system_queryset(lock=("self",)),
        )

    def _write_status(self, status: TaskStatus) -> None:
        """Write the lifecycle state; a projecting capability may guard this field."""

        self.status = status

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
        body = kinds.completion_line(self.activity_type, self.title)
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
            reason_member = self.TaskDroppedReason(reason)
        except ValueError as error:
            raise ValidationError({"reason": "Choose duplicate, declined, or obsolete."}) from error
        with transaction.atomic():
            self._lock_lifecycle()
            if self.status == self.TaskStatus.DROPPED and self.dropped_reason == reason_member and self.dropped_at:
                return self
            self.validate_action("drop")
            self._write_status(self.TaskStatus.DROPPED)
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
            self._write_status(self.TaskStatus.OPEN)
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


class TaskRelation(HistoryMixin, AuditMixin, AngeeDataModel, concrete=True):
    """One canonical directed relation between two tasks."""

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


class Participant(HistoryMixin, AuditMixin, AngeeDataModel, concrete=True):
    """A party's involvement in a project, never an access grant."""

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


class ProjectBinding(HistoryMixin, AuditMixin, RecordRefMixin, AngeeDataModel, concrete=True):
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


class Link(HistoryMixin, AuditMixin, RecordRefMixin, AngeeDataModel, concrete=True):
    """A URL-keyed external reference attached to a project or task."""

    merge_policy = MergePolicy.MOVE
    merge_identity = (("content_type", "object_id", "url"),)
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


class FolderProjects(ProjectBindingsMixin, Composable, extends="storage.Folder"):
    """Compose project binding collection onto storage folders."""

    class Meta:
        abstract = True


class DriveProjects(ProjectBindingsMixin, Composable, extends="storage.Drive"):
    """Projects-owned reverse collection for storage-drive bindings."""

    class Meta:
        abstract = True


class IntegrationProjects(ProjectBindingsMixin, Composable, extends="integrate.Integration"):
    """Projects-owned reverse collection for canonical messaging-channel bindings."""

    class Meta:
        abstract = True


class ThreadProjects(ProjectBindingsMixin, Composable, extends="messaging.Thread"):
    """Projects-owned reverse collection for messaging-thread bindings."""

    class Meta:
        abstract = True


class VaultProjects(ProjectBindingsMixin, Composable, extends="knowledge.Vault"):
    """Projects-owned reverse collection for knowledge-vault bindings."""

    class Meta:
        abstract = True
