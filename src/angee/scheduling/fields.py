"""Model fields for the Angee scheduling addon."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from strawberry.scalars import JSON

from angee.base.validators import validate_value
from angee.graphql.field_types import register_field_type
from angee.scheduling.due import DueRule
from angee.scheduling.recurrence import Recurrence
from angee.scheduling.zones import validate_zone_name

_DEFAULT_MAX_LENGTH = 255
_ZONE_NAME_MAX_LENGTH = 64


class RecurrenceField(models.CharField):
    """A ``CharField`` holding an RFC-5545 RRULE string; blank means a single event.

    A consumer model composes this field to become recurrence-capable. The stored
    value is the bare rule (e.g. ``"FREQ=WEEKLY"``);
    :class:`~angee.scheduling.recurrence.Recurrence` turns it into occurrences.
    ``full_clean`` (and therefore the Hasura create/update path, which
    ``full_clean``\\ s its input) rejects a malformed rule via
    :meth:`Recurrence.validate`. The column defaults to blank-allowed so
    "non-recurring" is the natural, zero-ceremony state. ``finest_frequency``
    (one of :attr:`Recurrence.FREQUENCIES`) refuses rules that repeat more often,
    for a consumer whose rows must not (a calendar event repeats at most daily).
    """

    description = "RFC-5545 recurrence rule (RRULE)"

    def __init__(self, *args: Any, finest_frequency: str | None = None, **kwargs: Any) -> None:
        """Default to a blank-allowed rule column so 'no recurrence' needs no opt-in."""

        if finest_frequency is not None and finest_frequency not in Recurrence.FREQUENCIES:
            raise ValueError(f"finest_frequency must be one of {', '.join(Recurrence.FREQUENCIES)}.")
        self.finest_frequency = finest_frequency
        kwargs.setdefault("max_length", _DEFAULT_MAX_LENGTH)
        kwargs.setdefault("blank", True)
        kwargs.setdefault("default", "")
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> Any:
        """Keep the frequency floor in the field's reconstruction."""

        name, path, args, kwargs = super().deconstruct()
        if self.finest_frequency is not None:
            kwargs["finest_frequency"] = self.finest_frequency
        return name, path, args, kwargs

    def validate(self, value: Any, model_instance: Any) -> None:
        """Run ``CharField`` validation, then reject a value that is not a parseable, bounded RRULE."""

        super().validate(value, model_instance)
        Recurrence(value).validate(finest=self.finest_frequency)


class TimeZoneField(models.CharField):
    """A ``CharField`` holding an IANA time zone name; blank means the project ``TIME_ZONE``.

    The stored value is the name (``"Europe/Brussels"``), never a ``tzinfo``, so
    it crosses GraphQL and forms as a string;
    :func:`~angee.scheduling.zones.get_zone_info` turns it into the zone computation
    uses. ``full_clean`` rejects a name the installed time zone database does not
    list (:func:`~angee.scheduling.zones.validate_zone_name`). The column defaults to
    blank-allowed, so "the project's zone" needs no opt-in.
    """

    description = "IANA time zone name"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Default to a blank-allowed name column long enough for every IANA name."""

        kwargs.setdefault("max_length", _ZONE_NAME_MAX_LENGTH)
        kwargs.setdefault("blank", True)
        kwargs.setdefault("default", "")
        super().__init__(*args, **kwargs)

    def validate(self, value: Any, model_instance: Any) -> None:
        """Run ``CharField`` validation, then reject a name the zone database does not list."""

        super().validate(value, model_instance)
        validate_zone_name(value)


class DueRuleField(models.JSONField):
    """A JSON column holding a :class:`~angee.scheduling.due.DueRule`; blank (``NULL``) means a hand-set date.

    A consumer row composes it beside its own date or instant column and a JSON
    basis column. The stored value is the rule's canonical JSON
    (:meth:`~angee.scheduling.due.DueRule.as_json`): assigning a ``DueRule`` or its
    JSON stores that form, and ``full_clean`` (the Hasura write path) and an
    instance ``save`` (including ``create`` and ``bulk_create``, which run
    ``pre_save``) refuse a rule its schema rejects, each message naming the path
    inside the rule. ``QuerySet.update()`` and ``bulk_update`` skip ``pre_save``:
    they store a ``DueRule`` in canonical form but write JSON as given, unchecked.
    ``DueRule.from_json(row.due_rule)`` reads the typed rule (``None`` when blank).
    The column crosses GraphQL and forms as ``JSON``. Lookups receive their operand
    as given, so key transforms and containment queries keep working on the JSON.
    """

    description = "Due rule"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Default to a nullable column, so "no rule" needs no opt-in."""

        kwargs.setdefault("null", True)
        kwargs.setdefault("blank", True)
        kwargs.setdefault("default", None)
        super().__init__(*args, **kwargs)

    def to_python(self, value: Any) -> dict[str, Any] | None:
        """Return the rule's canonical JSON, or ``None`` for a blank value; refuse an invalid rule."""

        if value in self.empty_values:
            return None
        try:
            rule = validate_value(DueRule, value, field=self.name)
        except ValidationError as error:
            raise ValidationError(
                [
                    f"{path.removeprefix(f'{self.name}.')}: {message}" if path != self.name else message
                    for path, messages in error.message_dict.items()
                    for message in messages
                ],
                code="invalid",
            ) from None
        return rule.as_json()

    def get_prep_value(self, value: Any) -> Any:
        """Store an assigned ``DueRule`` as its canonical JSON; pass lookup operands through."""

        if isinstance(value, DueRule):
            value = value.as_json()
        return super().get_prep_value(value)

    def pre_save(self, model_instance: models.Model, add: bool) -> dict[str, Any] | None:
        """Validate and normalize the row's rule on every save, as ``full_clean`` would."""

        value = self.to_python(getattr(model_instance, self.attname))
        setattr(model_instance, self.attname, value)
        return value


register_field_type(RecurrenceField, str)
register_field_type(TimeZoneField, str)
register_field_type(DueRuleField, JSON)
