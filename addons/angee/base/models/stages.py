"""Ordered, data-owned pipeline stages.

Lifecycles whose vocabulary belongs to code use
:class:`angee.base.models.StateTransitions`.  A pipeline whose names and
ordering belong to users composes :class:`Stage` into an addon-owned concrete
stage model instead.  The concrete model supplies only its container relation
and optional category field; records compose :class:`StagedModelMixin` and
declare the matching container and stage foreign keys.
"""

from __future__ import annotations

from typing import Any, ClassVar, Self, cast

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models
from django.db.models.expressions import Combinable

from angee.base.fields.ordering import FractionalRankField
from angee.base.fields.state import ToneField


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
    foreign key (for example ``"queue"``).  If it stores a closed semantic
    category, it also sets :attr:`category_field_name`; consumers read that
    value through :meth:`get_category` rather than assuming a field name. The
    concrete model declares a unique constraint on its container and position;
    omitted positions append through the shared fractional-rank field.
    """

    container_field_name: ClassVar[str] = ""
    category_field_name: ClassVar[str] = ""
    default_stage_field_name: ClassVar[str] = "default_stage"

    name = models.CharField(max_length=160)
    tone = ToneField(choices_enum=StageTone, default=StageTone.NEUTRAL)
    position = FractionalRankField()

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
    def resolve_default(cls, container: models.Model) -> Self | None:
        """Return the container's configured default stage, or resolve its fallback.

        The container is the single owner of an explicit default.  A stage model
        never carries an ``is_default`` flag; if no explicit default is set, the
        fallback hook selects from the container's ordered stages.
        """

        default_id = getattr(container, f"{cls.default_stage_field_name}_id", None)
        stages = cls.for_container(container)
        if default_id is not None:
            configured = stages.filter(pk=default_id).first()
            if configured is not None:
                return configured
        return cls.resolve_default_fallback(stages)

    @classmethod
    def resolve_default_fallback(cls, stages: models.QuerySet[Self]) -> Self | None:
        """Select the first ordered stage when no valid configured default exists.

        Overrides receive the container-scoped, ordered queryset and can refine
        fallback selection without replacing explicit-default resolution.
        """

        return stages.first()

    def get_category(self) -> Any | None:
        """Return this stage's addon-owned category value, if one is declared."""

        if not self.category_field_name:
            return None
        try:
            self._meta.get_field(self.category_field_name)
        except FieldDoesNotExist as error:
            raise ImproperlyConfigured(
                f"{self._meta.label}.category_field_name names unknown field {self.category_field_name!r}."
            ) from error
        return getattr(self, self.category_field_name)


class StagedModelMixin[StageT: Stage = Stage](models.Model):
    """Validate a record's stage against its owning container.

    Consumers declare ordinary foreign keys named by :attr:`stage_field_name`
    and :attr:`stage_container_field_name`.  The mixin owns the cross-container
    invariant and delegates default resolution to the related stage model.
    """

    stage_field_name: ClassVar[str] = "stage"
    stage_container_field_name: ClassVar[str] = ""

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

    def resolve_default_stage(self) -> StageT | None:
        """Resolve this record's container-owned default stage."""

        container = self._stage_container()
        if container is None:
            return None
        return self.stage_model().resolve_default(container)

    def validate_stage_scope(self) -> None:
        """Reject a stage that does not belong to this record's container."""

        stage_id = getattr(self, f"{self.stage_field_name}_id", None)
        if stage_id is None:
            return
        container = self._stage_container()
        if container is None:
            raise ValidationError({self.stage_container_field_name: "A staged record requires its stage container."})
        stage_model = self.stage_model()
        if not stage_model.for_container(container).filter(pk=stage_id).exists():
            raise ValidationError({self.stage_field_name: "Stage must belong to the record's container."})

    def clean(self) -> None:
        """Run model cleaning, then enforce the stage/container invariant."""

        super().clean()
        self.validate_stage_scope()

    def _stage_container(self) -> models.Model | None:
        """Return the native (possibly cached) container, including unsaved field edits."""

        if not self.stage_container_field_name:
            raise ImproperlyConfigured(f"{self._meta.label} must declare stage_container_field_name.")
        try:
            self._meta.get_field(self.stage_container_field_name)
        except FieldDoesNotExist as error:
            raise ImproperlyConfigured(
                f"{self._meta.label}.stage_container_field_name names unknown field "
                f"{self.stage_container_field_name!r}."
            ) from error
        return getattr(self, self.stage_container_field_name)
