"""Model fields for the Angee scheduling addon."""

from __future__ import annotations

from typing import Any

from django.db import models

from angee.graphql.field_types import register_field_type
from angee.scheduling.recurrence import Recurrence

_DEFAULT_MAX_LENGTH = 255


class RecurrenceField(models.CharField):
    """A ``CharField`` holding an RFC-5545 RRULE string; blank means a single event.

    A consumer model composes this field to become recurrence-capable. The stored
    value is the bare rule (e.g. ``"FREQ=WEEKLY"``);
    :class:`~angee.scheduling.recurrence.Recurrence` turns it into occurrences.
    ``full_clean`` (and therefore the Hasura create/update path, which
    ``full_clean``\\ s its input) rejects a malformed rule via
    :meth:`Recurrence.validate`. The column defaults to blank-allowed so
    "non-recurring" is the natural, zero-ceremony state.
    """

    description = "RFC-5545 recurrence rule (RRULE)"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Default to a blank-allowed rule column so 'no recurrence' needs no opt-in."""

        kwargs.setdefault("max_length", _DEFAULT_MAX_LENGTH)
        kwargs.setdefault("blank", True)
        kwargs.setdefault("default", "")
        super().__init__(*args, **kwargs)

    def validate(self, value: Any, model_instance: Any) -> None:
        """Run ``CharField`` validation, then reject a value that is not a parseable RRULE."""

        super().validate(value, model_instance)
        Recurrence(value).validate()


register_field_type(RecurrenceField, str)
