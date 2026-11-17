"""Model fields for the Angee scheduling addon."""

from __future__ import annotations

from typing import Any

from django.db import models

from angee.graphql.field_types import register_field_type
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


class TimeZoneField(models.CharField):
    """A ``CharField`` holding an IANA time zone name; blank means the project ``TIME_ZONE``.

    The stored value is the name (``"America/Puerto_Rico"``), never a ``tzinfo``, so
    it crosses GraphQL and forms as a string;
    :func:`~angee.scheduling.zones.zone_info` turns it into the zone computation
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


register_field_type(RecurrenceField, str)
register_field_type(TimeZoneField, str)
