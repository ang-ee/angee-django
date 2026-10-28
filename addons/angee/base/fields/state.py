"""Enum-backed state fields and their presentation contract."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol, cast, overload
from django.core import checks
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models.query_utils import DeferredAttribute
from django.utils.functional import Promise
from django_choices_field import TextChoicesField

StatusDisplay = Literal["pill", "dot", "text"]


class StateAssignmentGuard(Protocol):
    """A declaration validates writes without making its field import a model owner."""

    def assign(
        self, instance: models.Model, field: StateField, value: Any, load: Callable[[], Any],
    ) -> None: ...


class StateAttribute(DeferredAttribute):
    """Use Django's native field descriptor, delegating only declared guarded writes."""

    def __set__(self, instance: models.Model, value: Any) -> None:
        field = cast(StateField, self.field)
        if field.transition_guard is None:
            instance.__dict__[field.attname] = value
        else:
            field.transition_guard.assign(instance, field, value, lambda: self.__get__(instance, type(instance)))


class StateField[C: models.TextChoices | None](TextChoicesField[C]):
    """A finite-state column backed by a ``TextChoices`` enum.

    ``docs/stack.md`` names ``django-choices-field`` the owner of enum-backed
    model fields; this is the ``StateField`` semantic wrapper it lists. The
    enum is the single source of truth — ``strawberry-django`` emits the
    GraphQL enum straight from ``choices_enum`` and the column ``max_length``
    is derived from it, so a state column never restates its choices. Declared
    natively, e.g. ``StateField(choices_enum=Note.Status, default=...)``.
    Serialized ``choices`` reconstruct a dynamic ``TextChoices`` through the
    upstream field, retaining nullability without claiming the original enum type.
    ``status_display`` declares its migration-inert ``pill``, ``dot``, or ``text`` presentation.
    """

    descriptor_class = StateAttribute
    transition_guard: StateAssignmentGuard | None = None
    db_index: bool
    angee_widget = "select"

    @overload
    def __init__[E: models.TextChoices](
        self: StateField[E], *, choices_enum: type[E], null: Literal[False] = False,
        status_display: StatusDisplay | None = None, **kwargs: Any,
    ) -> None: ...

    @overload
    def __init__[E: models.TextChoices](
        self: StateField[E | None], *, choices_enum: type[E], null: Literal[True],
        status_display: StatusDisplay | None = None, **kwargs: Any,
    ) -> None: ...

    @overload
    def __init__(
        self: StateField[models.TextChoices], *, choices: Sequence[tuple[str | None, str | Promise]],
        choices_enum: None = None, null: Literal[False] = False,
        status_display: StatusDisplay | None = None, **kwargs: Any,
    ) -> None: ...

    @overload
    def __init__(
        self: StateField[models.TextChoices | None], *, choices: Sequence[tuple[str | None, str | Promise]],
        choices_enum: None = None, null: Literal[True],
        status_display: StatusDisplay | None = None, **kwargs: Any,
    ) -> None: ...

    def __init__(
        self,
        *,
        choices_enum: type[models.TextChoices] | None = None,
        null: bool = False,
        status_display: StatusDisplay | None = None,
        **kwargs: Any,
    ) -> None:
        """Index state columns and leave invalid optional declarations to checks."""

        kwargs.update(choices_enum=choices_enum, null=null)

        self.angee_status_display = status_display
        invalid_optional = bool(kwargs.get("blank")) and not bool(kwargs.get("null"))
        if invalid_optional:
            kwargs["blank"] = False
        kwargs.setdefault("db_index", True)
        super().__init__(**kwargs)
        if invalid_optional:
            self.blank = True

    def contribute_to_class(
        self, cls: type[models.Model], name: str, private_only: bool = False,
    ) -> None:
        """Keep a declaration's guard when Django reconstructs an inherited field.

        Abstract field copies carry the guard directly. A native clone is built
        from migration arguments, so recover the same declaration from its
        source model's field, in Django MRO order, without changing the model.
        """

        super().contribute_to_class(cls, name, private_only=private_only)
        if self.transition_guard is not None:
            return
        for parent in cls.__mro__[1:]:
            if not issubclass(parent, models.Model):
                continue
            try:
                inherited = parent._meta.get_field(name)
            except FieldDoesNotExist:
                continue
            if isinstance(inherited, StateField) and inherited.transition_guard is not None:
                self.transition_guard = inherited.transition_guard
                break

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
        """Preserve index opt-outs despite Django's opposite constructor default."""

        name, path, args, kwargs = super().deconstruct()
        kwargs["db_index"] = self.db_index
        return name, path, args, kwargs

    def check(self, **kwargs: Any) -> list[checks.CheckMessage]:
        """Reject unreconstructable enum values and non-null optional states."""

        errors = super().check(**kwargs)
        reserved = {"names", "values", "labels", "choices"}
        collisions = sorted(reserved.intersection(str(value) for value, _ in self.flatchoices))
        if collisions:
            errors.append(
                checks.Error(
                    f"StateField values collide with Django ChoicesType attributes: {', '.join(collisions)}.",
                    hint="Choose stored values that can be reconstructed by django-choices-field.",
                    obj=self,
                    id="angee.E028",
                )
            )
        model = getattr(self, "model", None)
        if self.blank and not self.null and model is not None and not model._meta.abstract:
            errors.append(
                checks.Error(
                    "Optional StateField requires null=True.",
                    hint="Declare null=True and migrate stored empty strings to NULL.",
                    obj=self,
                    id="angee.E019",
                )
            )
        return errors

    def to_python(self, value: Any) -> Any:
        """Accept stored values and GraphQL enum member names for this state."""

        choices_enum = cast(Any, self.choices_enum)
        member = enum_member_for(choices_enum, value)
        if member is not None:
            return member
        raw = getattr(value, "value", value)
        return super().to_python(raw)

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        """Normalize the in-memory value before Django writes and returns it."""

        value = self.to_python(super().pre_save(model_instance, add))
        setattr(model_instance, self.attname, value)
        return value


class ToneField(StateField):
    """A presentation tone whose stored values are the frontend tone names.

    Each value names a palette of the shared tone vocabulary (``neutral``,
    ``brand``, ``info`` …), so the ``tone`` widget renders it as a chip in its
    own colour and picks among them, instead of treating it as a status.
    """

    angee_widget = "tone"
def enum_member_for(choices_enum: Any, value: Any) -> Any | None:
    """Return the enum member represented by ``value`` or ``None`` when unknown."""

    if isinstance(value, choices_enum):
        return value
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    if not text:
        return None
    member = getattr(choices_enum, "__members__", {}).get(text)
    if member is not None:
        return member
    try:
        return choices_enum(raw)
    except ValueError:
        return None
