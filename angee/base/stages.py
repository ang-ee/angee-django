"""Ordered, data-owned pipeline stages.

Lifecycles whose vocabulary belongs to code use
:class:`angee.base.transitions.StateTransitions`.  A pipeline whose names and
ordering belong to users composes :class:`Stage` into an addon-owned concrete
stage model instead.  The concrete model supplies only its container relation
and optional category field; records compose :class:`StagedModelMixin` and
declare the matching container and stage foreign keys.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models

from angee.base.db import get_write_alias, refresh_deferred, related_on
from angee.base.fields import StateField


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
    value through :meth:`get_category` rather than assuming a field name.
    """

    container_field_name: ClassVar[str] = ""
    category_field_name: ClassVar[str] = ""
    default_stage_field_name: ClassVar[str] = "default_stage"

    name = models.CharField(max_length=160)
    tone = StateField(choices_enum=StageTone, default=StageTone.NEUTRAL)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        """Django model options for the abstract ordered-stage primitive."""

        abstract = True
        ordering = ("position", "name")

    def __str__(self) -> str:
        """Return the user-owned stage name."""

        return self.name

    @classmethod
    def container_field(cls) -> models.Field[Any, Any]:
        """Return the declared container relation, failing fast if misconfigured."""

        if not cls.container_field_name:
            raise ImproperlyConfigured(
                f"{cls._meta.label} must declare container_field_name."
            )
        try:
            field = cls._meta.get_field(cls.container_field_name)
        except FieldDoesNotExist as error:
            raise ImproperlyConfigured(
                f"{cls._meta.label}.container_field_name names unknown field "
                f"{cls.container_field_name!r}."
            ) from error
        if not field.is_relation:
            raise ImproperlyConfigured(
                f"{cls._meta.label}.{cls.container_field_name} must be a relation."
            )
        return cast(Any, field)

    @classmethod
    def for_container(cls, container: models.Model, *, using: str | None = None) -> models.QuerySet[Any]:
        """Return every stage configured for ``container`` in pipeline order."""

        using = get_write_alias(cls, using=using, instance=container)
        cls.container_field()
        queryset = cls._base_manager.db_manager(using).all()
        sudo = getattr(queryset, "sudo", None)
        if callable(sudo):
            queryset = sudo(reason="base.stage.for_container")
        return queryset.filter(**{cls.container_field_name: container}).order_by("position", "pk")

    @classmethod
    def resolve_default(cls, container: models.Model, *, using: str | None = None) -> Any | None:
        """Return the container's configured default stage, or its first stage.

        The container is the single owner of an explicit default.  A stage model
        never carries an ``is_default`` flag; if no explicit default is set, the
        deterministic ordered first row is the primitive's fallback.
        """

        using = get_write_alias(cls, using=using, instance=container)
        container._state.db = using
        default_attname = f"{cls.default_stage_field_name}_id"
        refresh_deferred(container, using=using, fields=(default_attname,))
        default_id = getattr(container, default_attname, None)
        stages = cls.for_container(container)
        if default_id is not None:
            configured = stages.filter(pk=default_id).first()
            if configured is not None:
                return configured
        return stages.first()

    def get_category(self) -> Any | None:
        """Return this stage's addon-owned category value, if one is declared."""

        if not self.category_field_name:
            return None
        try:
            self._meta.get_field(self.category_field_name)
        except FieldDoesNotExist as error:
            raise ImproperlyConfigured(
                f"{self._meta.label}.category_field_name names unknown field "
                f"{self.category_field_name!r}."
            ) from error
        return getattr(self, self.category_field_name)


class StagedModelMixin(models.Model):
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
    def stage_model(cls) -> type[Stage]:
        """Return the concrete stage model declared by this record."""

        try:
            field = cls._meta.get_field(cls.stage_field_name)
        except FieldDoesNotExist as error:
            raise ImproperlyConfigured(
                f"{cls._meta.label}.stage_field_name names unknown field "
                f"{cls.stage_field_name!r}."
            ) from error
        related_model = getattr(field, "related_model", None)
        if not isinstance(related_model, type) or not issubclass(related_model, Stage):
            raise ImproperlyConfigured(
                f"{cls._meta.label}.{cls.stage_field_name} must relate to a Stage subclass."
            )
        return related_model

    def resolve_default_stage(self, *, using: str | None = None) -> Stage | None:
        """Resolve this record's container-owned default stage."""

        using = get_write_alias(type(self), using=using if using is not None else self._state.db, instance=self)
        container = self._stage_container(using=using)
        if container is None:
            return None
        return cast(Stage | None, self.stage_model().resolve_default(container))

    def validate_stage_scope(self, *, using: str | None = None) -> None:
        """Reject a stage that does not belong to this record's container."""

        using = get_write_alias(type(self), using=using if using is not None else self._state.db, instance=self)
        stage_attname = f"{self.stage_field_name}_id"
        refresh_deferred(self, using=using, fields=(stage_attname,))
        stage_id = getattr(self, stage_attname, None)
        if stage_id is None:
            return
        container = self._stage_container(using=using)
        if container is None:
            raise ValidationError({self.stage_container_field_name: "A staged record requires its stage container."})
        stage_model = self.stage_model()
        if not stage_model.for_container(container).filter(pk=stage_id).exists():
            raise ValidationError({self.stage_field_name: "Stage must belong to the record's container."})

    def clean(self) -> None:
        """Run model cleaning, then enforce the stage/container invariant."""

        super().clean()
        self.validate_stage_scope()

    def _stage_container(self, *, using: str) -> models.Model | None:
        """Return the declared container object, failing fast on a bad convention."""

        if not self.stage_container_field_name:
            raise ImproperlyConfigured(f"{self._meta.label} must declare stage_container_field_name.")
        try:
            self._meta.get_field(self.stage_container_field_name)
        except FieldDoesNotExist as error:
            raise ImproperlyConfigured(
                f"{self._meta.label}.stage_container_field_name names unknown field "
                f"{self.stage_container_field_name!r}."
            ) from error
        return related_on(self, self.stage_container_field_name, using=using)
