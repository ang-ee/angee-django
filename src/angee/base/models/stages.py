"""Ordered, data-owned pipeline stages.

Lifecycles whose vocabulary belongs to code use
:class:`angee.base.models.transitions.StateTransitions`.  A pipeline whose names and
ordering belong to users composes :class:`Stage` into an addon-owned concrete
stage model instead.  The concrete model supplies its container relation and any
addon-owned vocabulary (a category, reserved rows); records compose
:class:`StagedModelMixin` and declare the matching container and stage foreign keys.

The primitive owns what every such pipeline shares: identity, tone and fractional
order; container scoping and per-record applicability; the default-stage rule; the
``fold`` and ``rotting_days`` board hints; idempotent provisioning; the
persisted-previous-stage read under lock; hand-versus-system admission through
:meth:`Stage.validate_transition`; and the :meth:`StagedModelMixin.project_stage`
hook that stamps ``stage_entered_at`` and lets a consumer project its lifecycle.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from typing import Any, ClassVar, Self, cast

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models, router, transaction
from django.db.models.expressions import Combinable
from django.utils import timezone
from rebac.actors import is_sudo

from angee.base.fields.ordering import FractionalRankField
from angee.base.fields.state import ToneField
from angee.base.querysets import row_lock_paths


class StageTone(models.TextChoices):
    """Semantic stage colors shared with the frontend tone vocabulary."""

    NEUTRAL = "neutral", "Neutral"
    BRAND = "brand", "Brand"
    ACCENT = "accent", "Accent"
    INFO = "info", "Info"
    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    DANGER = "danger", "Danger"
    PURPLE = "purple", "Purple"
    PINK = "pink", "Pink"


class Stage(models.Model):
    """Abstract base for one user-named stage in a container-scoped pipeline.

    A concrete stage model sets :attr:`container_field_name` to its owning
    foreign key (for example ``"queue"`` or ``"company"``).  The concrete model
    declares a unique constraint on its container and position; omitted positions
    append through the shared fractional-rank field.

    ``fold`` is a board's default-collapsed hint and ``rotting_days`` the dwell
    time after which a record resting in the stage is overdue for attention;
    both are stage facts users edit, never written back by a board.
    ``description`` tells a user what the stage requires.
    """

    container_field_name: ClassVar[str] = ""
    default_stage_field_name: ClassVar[str] = "default_stage"

    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    tone = ToneField(choices_enum=StageTone, default=StageTone.NEUTRAL)
    position = FractionalRankField()
    fold = models.BooleanField(default=False)
    rotting_days = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        """Django model options for the abstract ordered-stage primitive."""

        abstract = True
        ordering = ("position", "name")

    def __str__(self) -> str:
        """Return the user-owned stage name."""

        return self.name

    @classmethod
    def container_field(cls) -> models.ForeignKey:
        """Return the declared container relation, failing fast if misconfigured."""

        if not cls.container_field_name:
            raise ImproperlyConfigured(f"{cls._meta.label} must declare container_field_name.")
        try:
            field = cls._meta.get_field(cls.container_field_name)
        except FieldDoesNotExist as error:
            raise ImproperlyConfigured(
                f"{cls._meta.label}.container_field_name names unknown field {cls.container_field_name!r}."
            ) from error
        if not isinstance(field, models.ForeignKey):
            raise ImproperlyConfigured(f"{cls._meta.label}.{cls.container_field_name} must be a foreign key.")
        return field

    @classmethod
    def for_container(cls, container: models.Model | Combinable) -> models.QuerySet[Self]:
        """Return every stage configured for ``container`` in pipeline order."""

        cls.container_field()
        return cls._base_manager.filter(**{cls.container_field_name: container}).order_by("position", "pk")

    @classmethod
    def for_record(cls, record: StagedModelMixin[Any]) -> models.QuerySet[Self]:
        """Return the stages ``record`` may hold, in pipeline order.

        The default is every stage of the record's container.  A concrete model
        narrows it when some of its stages apply to only part of the container
        (stages restricted to particular jobs or projects); scope validation and
        default resolution both read this, so one override governs both.
        """

        container = record.stage_container()
        if container is None:
            return cls._base_manager.none()
        return cls.for_container(container)

    @classmethod
    def resolve_default(
        cls, container: models.Model, *, stages: models.QuerySet[Self] | None = None
    ) -> Self | None:
        """Return the container's configured default stage, or resolve its fallback.

        The container is the single owner of an explicit default.  A stage model
        never carries an ``is_default`` flag; if no explicit default is set, the
        fallback hook selects from the ordered stages.  ``stages`` narrows the
        candidates (a record's applicable stages); it defaults to the container's.
        """

        default_id = getattr(container, f"{cls.default_stage_field_name}_id", None)
        if stages is None:
            stages = cls.for_container(container)
        if default_id is not None:
            configured = stages.filter(pk=default_id).first()
            if configured is not None:
                return configured
        return cls.resolve_default_fallback(stages)

    @classmethod
    def resolve_default_fallback(cls, stages: models.QuerySet[Self]) -> Self | None:
        """Select the first ordered stage when no valid configured default exists.

        Overrides receive the scoped, ordered queryset and can refine fallback
        selection without replacing explicit-default resolution.
        """

        return stages.first()

    @classmethod
    def validate_transition(
        cls,
        previous: Self | None,
        following: Self | None,
        *,
        manual: bool,
        admitted: bool,
        adding: bool,
    ) -> None:
        """Refuse a stage move the pipeline's rules do not admit; the default admits every move.

        ``manual`` marks a hand move (a board drop, a verb acting for a user); a
        system write passes ``manual=False``.  ``admitted`` marks a move an owning
        verb vouches for through ``save(admitted_stage=)``.  ``adding`` marks the
        record's first save.  A concrete model raises ``ValidationError`` keyed on
        the record's stage field.
        """

        return None

    @classmethod
    def provision(
        cls, container: models.Model, rows: Iterable[Mapping[str, Any]], *, key: str = "name"
    ) -> dict[Any, Self]:
        """Create the container's missing stages once; return every row by its ``key`` value.

        Each row names its ``key`` plus the values a new stage takes (``name``,
        ``tone``, ``position``, a category …).  An existing stage with that key keeps
        its values, so a re-run is a no-op; omitted positions append through the
        rank field.  Callers run this inside the actor context that may create rows.
        """

        cls.container_field()
        provisioned: dict[Any, Self] = {}
        for row in rows:
            values = dict(row)
            lookup = values.pop(key)
            stage, _created = cls._default_manager.get_or_create(
                **{cls.container_field_name: container, key: lookup}, defaults=values,
            )
            provisioned[lookup] = stage
        return provisioned


class StagedModelMixin[StageT: Stage = Stage](models.Model):
    """Own a record's stage: scope, admission, projection and the entered-at stamp.

    Consumers declare ordinary foreign keys named by :attr:`stage_field_name`
    and :attr:`stage_container_field_name`.  The mixin owns the cross-container
    and applicability invariants, reads the persisted previous stage under lock,
    asks the stage model to admit the move, and projects its companion facts
    through :meth:`project_stage` in the same transaction as the save.
    """

    stage_field_name: ClassVar[str] = "stage"
    stage_container_field_name: ClassVar[str] = ""

    stage_entered_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        """Django model options for the abstract staged-record mixin."""

        abstract = True

    @classmethod
    def stage_model(cls) -> type[StageT]:
        """Return the concrete stage model declared by this record."""

        try:
            field = cls._meta.get_field(cls.stage_field_name)
        except FieldDoesNotExist as error:
            raise ImproperlyConfigured(
                f"{cls._meta.label}.stage_field_name names unknown field {cls.stage_field_name!r}."
            ) from error
        related_model = field.related_model
        if not isinstance(related_model, type) or not issubclass(related_model, Stage):
            raise ImproperlyConfigured(f"{cls._meta.label}.{cls.stage_field_name} must relate to a Stage subclass.")
        return cast(type[StageT], related_model)

    def stage_container(self) -> models.Model | None:
        """Return the native (possibly cached) container, including unsaved field edits."""

        return getattr(self, self._stage_container_field().name)

    def resolve_default_stage(self) -> StageT | None:
        """Resolve this record's container-owned default among its applicable stages."""

        container = self.stage_container()
        if container is None:
            return None
        stage_model = self.stage_model()
        return stage_model.resolve_default(container, stages=stage_model.for_record(self))

    def validate_stage_scope(self) -> None:
        """Reject a stage outside this record's container or inapplicable to the record."""

        stage_id = getattr(self, f"{self.stage_field_name}_id", None)
        if stage_id is None:
            return
        container = self.stage_container()
        if container is None:
            raise ValidationError({self.stage_container_field_name: "A staged record requires its stage container."})
        stage_model = self.stage_model()
        if stage_model.for_record(self).filter(pk=stage_id).exists():
            return
        if not stage_model.for_container(container).filter(pk=stage_id).exists():
            raise ValidationError({self.stage_field_name: "Stage must belong to the record's container."})
        raise ValidationError({self.stage_field_name: "Stage does not apply to this record."})

    def stage_transition(
        self,
        *,
        target: StageT | None = None,
        manual: bool = False,
        admitted: bool = False,
        lock: bool = False,
    ) -> tuple[StageT | None, StageT | None]:
        """Read the persisted previous stage and the move's target, then have the stage owner admit it.

        ``target`` defaults to the record's current stage field.  ``lock`` holds
        the record row until the transaction ends.  Admission runs for hand moves
        and for every non-sudo write; a system write is trusted unless the caller
        marks it ``manual``.
        """

        previous_id, _container_id = self._persisted_stage_binding(lock=lock)
        target_id = target.pk if target is not None else getattr(self, f"{self.stage_field_name}_id")
        return self._admit_stage_move(previous_id, target_id, manual=manual, admitted=admitted)

    def project_stage(self, previous: StageT | None, following: StageT | None) -> set[str]:
        """Write the companion facts a stage move implies and return the fields written.

        The primitive stamps ``stage_entered_at`` when the stage changes and clears
        it when the record leaves every stage.  A consumer extends this through
        ``super()`` with its own projection (a code-owned lifecycle written through
        ``StateTransitions.write_state``, timestamps, a cleared reason) and returns
        the union; the save merges it into ``update_fields``.
        """

        previous_id = previous.pk if previous is not None else None
        following_id = following.pk if following is not None else None
        if previous_id == following_id:
            return set()
        self.stage_entered_at = timezone.now() if following is not None else None
        return {"stage_entered_at"}

    def clean(self) -> None:
        """Admit and project the pending stage move, run model cleaning, then enforce scope."""

        previous, following = self.stage_transition()
        self.project_stage(previous, following)
        super().clean()
        self.validate_stage_scope()

    def save(self, *args: Any, admitted_stage: StageT | None = None, **kwargs: Any) -> None:
        """Persist the record with its stage move admitted, scoped and projected in one transaction.

        An owning verb passes the system stage it admits as ``admitted_stage``; the
        save must write exactly that stage.  A partial save stores the persisted
        stage and container it omits, so admission, scope and projection judge
        those stored values rather than a stale instance's.
        """

        stage_attname = f"{self.stage_field_name}_id"
        if admitted_stage is not None and (
            admitted_stage.pk is None or getattr(self, stage_attname) != admitted_stage.pk
        ):
            raise ValidationError({self.stage_field_name: "Save must write the stage admitted by its owning verb."})
        container_field = self._stage_container_field()
        update_fields = set(kwargs["update_fields"]) if kwargs.get("update_fields") is not None else None
        written = None if self._state.adding else update_fields
        stage_written = written is None or bool({self.stage_field_name, stage_attname} & written)
        container_written = written is None or bool({container_field.name, container_field.attname} & written)
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            previous_id, persisted_container_id = self._persisted_stage_binding(lock=True)
            stage_id = getattr(self, stage_attname) if stage_written else previous_id
            container_id = getattr(self, container_field.attname) if container_written else persisted_container_id
            previous, following = self._admit_stage_move(
                previous_id, stage_id, manual=False, admitted=admitted_stage is not None
            )
            self._stored_stage_record(stage_id, container_id).validate_stage_scope()
            projected = self.project_stage(previous, following) if stage_written else set()
            if update_fields is not None:
                update_fields.update(projected)
                kwargs["update_fields"] = update_fields
            super().save(*args, **kwargs)

    def _stage_container_field(self) -> models.ForeignKey:
        """Return the declared container relation, failing fast if misconfigured."""

        if not self.stage_container_field_name:
            raise ImproperlyConfigured(f"{self._meta.label} must declare stage_container_field_name.")
        try:
            field = self._meta.get_field(self.stage_container_field_name)
        except FieldDoesNotExist as error:
            raise ImproperlyConfigured(
                f"{self._meta.label}.stage_container_field_name names unknown field "
                f"{self.stage_container_field_name!r}."
            ) from error
        return cast(models.ForeignKey, field)

    def _persisted_stage_binding(self, *, lock: bool) -> tuple[Any, Any]:
        """Return the stored stage and container ids, read under the row lock when ``lock`` is set."""

        if self._state.adding:
            return None, None
        model = type(self)
        rows = model._base_manager.filter(pk=self.pk)
        if lock:
            rows = rows.select_for_update(of=row_lock_paths(model))
        stage_id, container_id = rows.values_list(
            f"{self.stage_field_name}_id", self._stage_container_field().attname
        ).get()
        return stage_id, container_id

    def _admit_stage_move(
        self, previous_id: Any, target_id: Any, *, manual: bool, admitted: bool
    ) -> tuple[StageT | None, StageT | None]:
        """Load both stage rows once and let the stage owner admit the move between them."""

        stage_model = self.stage_model()
        stage_ids = {pk for pk in (previous_id, target_id) if pk is not None}
        stages = {stage.pk: stage for stage in stage_model._base_manager.filter(pk__in=stage_ids)} if stage_ids else {}
        previous = stages.get(previous_id) if previous_id is not None else None
        following = stages.get(target_id) if target_id is not None else None
        if manual or not is_sudo():
            stage_model.validate_transition(
                previous, following, manual=manual, admitted=admitted, adding=self._state.adding
            )
        return previous, following

    def _stored_stage_record(self, stage_id: Any, container_id: Any) -> Self:
        """Return this record as the save stores its stage binding; a stale instance answers through a copy."""

        stage_attname = f"{self.stage_field_name}_id"
        container_attname = self._stage_container_field().attname
        loaded = self.__dict__
        if loaded.get(stage_attname, models.DEFERRED) == stage_id and (
            loaded.get(container_attname, models.DEFERRED) == container_id
        ):
            return self
        stored = copy.copy(self)
        setattr(stored, stage_attname, stage_id)
        setattr(stored, container_attname, container_id)
        return stored
