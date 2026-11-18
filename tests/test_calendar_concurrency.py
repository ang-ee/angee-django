"""PostgreSQL races on a calendar's overlap rule: the calendar row serializes the saves that take time."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone
from rebac import system_context

from angee.calendar.testing.models import Calendar, CalendarEvent
from angee.messaging.testing.models import Person
from tests.lock_waits import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 13, hour, minute, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _before_these_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """The overlap check judges from now (every thread reads the pinned clock): pin it before these July events."""

    monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 7, 1, tzinfo=UTC))


def test_a_second_overlapping_save_waits_for_the_first_and_is_refused() -> None:
    with system_context(reason="tests.calendar.concurrency.setup"):
        person = Person.objects.create(display_name="Racer")
        calendar = Calendar.objects.create(party=person, name="Racing", overlap_policy="refuse")

    def contend() -> str:
        with system_context(reason="tests.calendar.concurrency.contender"):
            try:
                CalendarEvent.objects.create(
                    calendar_id=calendar.pk, title="Second", start=_utc(10, 30), end=_utc(11, 30),
                )
            except ValidationError:
                return "refused"
            return "saved"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="tests.calendar.concurrency.holder"), transaction.atomic():
            # The first save locks the calendar row and holds it until this block commits.
            CalendarEvent.objects.create(calendar=calendar, title="First", start=_utc(10), end=_utc(11))
            contender, pid = submit(pool, contend)
            wait_for_lock(pid, contender)
        assert contender.result(timeout=10) == "refused"

    with system_context(reason="tests.calendar.concurrency.read"):
        assert list(CalendarEvent.objects.filter(calendar=calendar).values_list("title", flat=True)) == ["First"]


@pytest.mark.parametrize(
    ("policy", "contender_fields"),
    [
        # Item 2: the calendar lock is taken on any policy, and the series is read again under it.
        pytest.param(
            "accept",
            {"title": "Moved", "original_start": datetime(2026, 7, 20, 9, tzinfo=UTC),
             "start": datetime(2026, 7, 21, 9, tzinfo=UTC), "end": datetime(2026, 7, 21, 10, tzinfo=UTC)},
            id="override-of-a-removed-instance",
        ),
        # Overlap after save and rebase: judged against the committed move, not the series it replaced.
        pytest.param(
            "refuse",
            {"title": "One-off", "start": datetime(2026, 7, 20, 10, 30, tzinfo=UTC),
             "end": datetime(2026, 7, 20, 11, 30, tzinfo=UTC)},
            id="one-off-meeting-the-moved-series",
        ),
    ],
)
def test_a_save_waits_for_a_series_move_and_is_judged_against_the_moved_series(
    policy: str, contender_fields: dict[str, object],
) -> None:
    """A concurrent save reads the series only after the move commits, so it cannot pass on the old schedule."""

    with system_context(reason="tests.calendar.concurrency.setup"):
        person = Person.objects.create(display_name="Planner")
        calendar = Calendar.objects.create(party=person, name="Planning", overlap_policy=policy)
        master = CalendarEvent.objects.create(
            calendar=calendar, title="Weekly", recurrence="FREQ=WEEKLY", start=_utc(9), end=_utc(10),
        )
    series = {"series_id": master.pk} if "original_start" in contender_fields else {}

    def contend() -> str:
        with system_context(reason="tests.calendar.concurrency.contender"):
            try:
                # The write path's order: validated first, then saved (which waits on the move).
                event = CalendarEvent(calendar_id=calendar.pk, **series, **contender_fields)
                event.full_clean()
                event.save()
            except ValidationError:
                return "refused"
            return "saved"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="tests.calendar.concurrency.mover"), transaction.atomic():
            moving = CalendarEvent.objects.get(pk=master.pk)
            moving.start, moving.end = _utc(10), _utc(11)
            moving.save()
            contender, pid = submit(pool, contend)
            wait_for_lock(pid, contender)
        # Once the move commits, 09:00 is no instance and 10:00 is the series' time.
        assert contender.result(timeout=10) == "refused"
