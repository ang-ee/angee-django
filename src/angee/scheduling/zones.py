"""Named time zones: the one rule for an IANA zone name and for an aware moment.

Scheduling owns "what day is it" (:class:`~angee.scheduling.apps.SchedulingConfig`).
A zone is stored and exchanged as its IANA name, never as a ``tzinfo``, so the same
value crosses a model column, a GraphQL ``String``, a form and a Pydantic input. A
blank name means the project ``TIME_ZONE``. :func:`zone_names` is the admission set;
:data:`ZoneName` (Pydantic) and :class:`~angee.scheduling.fields.TimeZoneField`
(Django) both read it, and :func:`zone_info` turns a name into the ``tzinfo`` that
computation uses.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from functools import cache
from typing import Annotated
from zoneinfo import ZoneInfo, available_timezones

from django.core.exceptions import ValidationError
from django.utils import timezone
from pydantic import AfterValidator

ZONE_NAME_MESSAGE = "Choose an IANA time zone name, such as Europe/Brussels."
"""The refusal every zone input shares, whichever validation contract reports it."""


@cache
def zone_names() -> frozenset[str]:
    """Return the IANA names the installed time zone database lists.

    ``Factory`` is left out: it is the database's placeholder for an unset zone,
    not a place. The set is read once per process; the database does not change
    while it runs.
    """

    return frozenset(available_timezones()) - {"Factory"}


def validate_zone_name(name: str) -> None:
    """Raise Django's :class:`~django.core.exceptions.ValidationError` unless the name is blank or listed."""

    if name and name not in zone_names():
        raise ValidationError(ZONE_NAME_MESSAGE, code="invalid_time_zone")


def _admit_zone_name(name: str) -> str:
    """Report :func:`validate_zone_name`'s refusal as the ``ValueError`` Pydantic collects."""

    try:
        validate_zone_name(name)
    except ValidationError as error:
        raise ValueError(ZONE_NAME_MESSAGE) from error
    return name


ZoneName = Annotated[str, AfterValidator(_admit_zone_name)]
"""An IANA zone name or blank (the project ``TIME_ZONE``), for Pydantic values and inputs."""


def zone_info(name: str = "") -> tzinfo:
    """Return the zone a name stands for: the named IANA zone, or the project ``TIME_ZONE`` when blank."""

    if not name:
        return timezone.get_default_timezone()
    validate_zone_name(name)
    return ZoneInfo(name)


def require_aware(**moments: datetime) -> None:
    """Raise :class:`~django.core.exceptions.ValidationError` if any moment is naive.

    Scheduling projects every moment into a zone; a naive datetime has no offset to
    project, so accepting one would silently reinterpret it in the system zone.
    Reject it at the door instead, naming the offending argument.
    """

    naive = [name for name, moment in moments.items() if timezone.is_naive(moment)]
    if naive:
        raise ValidationError(
            f"Scheduling needs timezone-aware datetimes; {', '.join(sorted(naive))} is naive.",
            code="naive_datetime",
        )
