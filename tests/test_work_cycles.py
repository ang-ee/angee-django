"""Queue cycles: generated closed date windows, rolling cadence and an immutable close."""

from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from hypothesis import example, given, settings
from hypothesis import strategies as st
from rebac import system_context

from angee.projects.testing.models import Cycle, Queue, Stage, Task

pytestmark = pytest.mark.django_db


def _queue(key: str = "CYC", **cadence: Any) -> Any:
    return Queue.objects.create(key=key, name=key.title(), slug=key.lower(), cycles_enabled=True, **cadence)


def _windows(cycles: Any) -> list[tuple[int, date, date]]:
    return [(cycle.number, cycle.starts_on, cycle.ends_on) for cycle in cycles]


def test_maintain_cadence_generates_enabled_queues_once() -> None:
    now = datetime(2026, 1, 7, 12, tzinfo=UTC)
    with system_context(reason="tests.work.cycles"):
        enabled = _queue()
        disabled = Queue.objects.create(key="OFF", name="Off", slug="off")
        generated = Cycle.objects.maintain_cadence(now=now)
        assert generated == Cycle._base_manager.filter(queue=enabled).count() > 0
        assert not Cycle._base_manager.filter(queue=disabled).exists()
        assert Cycle.objects.maintain_cadence(now=now) == 0


@pytest.mark.parametrize(
    ("cadence", "window_end", "windows"),
    [
        pytest.param(
            {},
            None,
            [(1, date(2026, 1, 5), date(2026, 1, 18)), (2, date(2026, 1, 19), date(2026, 2, 1)),
             (3, date(2026, 2, 2), date(2026, 2, 15))],
            id="two-weeks-from-monday-keeps-two-upcoming",
        ),
        pytest.param(
            {"cycle_start_day": 6, "cycle_weeks": 1, "cycle_cooldown_weeks": 1, "upcoming_cycle_count": 1},
            None,
            [(1, date(2026, 1, 4), date(2026, 1, 10)), (2, date(2026, 1, 18), date(2026, 1, 24))],
            id="a-cooldown-week-belongs-to-no-cycle",
        ),
        pytest.param(
            {"cycle_weeks": 1, "upcoming_cycle_count": 0},
            date(2026, 1, 26),
            [(1, date(2026, 1, 5), date(2026, 1, 11)), (2, date(2026, 1, 12), date(2026, 1, 18)),
             (3, date(2026, 1, 19), date(2026, 1, 25)), (4, date(2026, 1, 26), date(2026, 2, 1))],
            id="window-end-includes-a-start-on-it",
        ),
    ],
)
def test_generation_lays_out_windows_from_wednesday_7_january(
    cadence: dict[str, Any], window_end: date | None, windows: list[Any]
) -> None:
    with system_context(reason="tests.work.cycles"):
        queue = _queue(**cadence)
        for _rerun in range(2):
            generated = Cycle.objects.generate_for_queue(queue, as_of=date(2026, 1, 7), window_end=window_end)
            assert _windows(generated) == windows


def test_generation_refuses_an_inverted_window_and_skips_a_disabled_queue() -> None:
    with system_context(reason="tests.work.cycles"):
        with pytest.raises(ValidationError, match="precede"):
            Cycle.objects.generate_for_queue(_queue(), as_of=date(2026, 1, 7), window_end=date(2026, 1, 6))
        disabled = Queue.objects.create(key="OFF", name="Off", slug="off")
        assert Cycle.objects.generate_for_queue(disabled, as_of=date(2026, 1, 7)) == ()
        assert not Cycle._base_manager.exists()


@settings(deadline=None, max_examples=20)
# A window's end day is still inside it: the first week stays open on its Sunday.
@example(start_day=0, weeks=1, cooldown=0, upcoming=1, as_of=date(2026, 1, 5), later=6)
@given(
    start_day=st.integers(0, 6),
    weeks=st.integers(1, 3),
    cooldown=st.integers(0, 2),
    upcoming=st.integers(0, 3),
    as_of=st.dates(date(2026, 1, 1), date(2026, 12, 31)),
    later=st.integers(0, 60),
)
def test_a_rolling_cadence_stays_contiguous_and_closes_exactly_the_ended_windows(
    start_day: int, weeks: int, cooldown: int, upcoming: int, as_of: date, later: int
) -> None:
    """Generate on ``as_of``, then again ``later`` days on; each example rolls back its own rows."""

    today = as_of + timedelta(days=later)
    with transaction.atomic(), system_context(reason="tests.work.cycles"):
        queue = _queue(
            cycle_start_day=start_day, cycle_weeks=weeks, cycle_cooldown_weeks=cooldown, upcoming_cycle_count=upcoming
        )
        first = Cycle.objects.generate_for_queue(queue, as_of=as_of)[0]
        cycles = Cycle.objects.generate_for_queue(queue, as_of=today)
        assert first.starts_on <= as_of <= first.ends_on
        assert [cycle.number for cycle in cycles] == list(range(1, len(cycles) + 1))
        for cycle in cycles:
            assert cycle.starts_on.weekday() == start_day
            assert (cycle.ends_on - cycle.starts_on).days == 7 * weeks - 1
            assert (cycle.completed_at is not None) == (cycle.ends_on < today)
        for previous, cycle in pairwise(cycles):
            assert (cycle.starts_on - previous.ends_on).days == 1 + 7 * cooldown
        # The cadence reaches today with its upcoming windows, and is no longer than that needs.
        ahead = [cycle.starts_on > today for cycle in cycles]
        assert cycles[-1].ends_on >= today and sum(ahead) >= upcoming
        assert len(cycles) == 1 or cycles[-2].ends_on < today or sum(ahead[:-1]) < upcoming
        assert Cycle.objects.generate_for_queue(queue, as_of=today) == cycles
        transaction.set_rollback(True)


def test_closing_rolls_only_open_tasks_forward_and_its_snapshot_is_immutable() -> None:
    closed_at = datetime(2026, 1, 19, tzinfo=UTC)
    with system_context(reason="tests.work.cycles"):
        queue = _queue()
        current, following, _ = Cycle.objects.generate_for_queue(queue, as_of=date(2026, 1, 7))
        stages = {stage.category: stage for stage in Stage.objects.filter(queue=queue)}
        tasks = {
            category: Task.objects.create(title=category, queue=queue, stage=stages[category], cycle=current)
            for category in ("backlog", "started", "completed", "canceled")
        }
        current.close(completed_at=closed_at)
        assert current.close(completed_at=closed_at + timedelta(days=1)).completed_at == closed_at
        rolled = {category for category, task in tasks.items() if Task._base_manager.get(pk=task.pk).cycle == following}
        assert rolled == {"backlog", "started"}
        assert current.uncompleted_upon_close == sorted(str(tasks[category].sqid) for category in rolled)
        current.uncompleted_upon_close = []
        with pytest.raises(ValidationError, match="immutable"):
            current.save()


def test_closing_refuses_a_rollover_it_cannot_make() -> None:
    with system_context(reason="tests.work.cycles"):
        current, following, last = Cycle.objects.generate_for_queue(_queue(), as_of=date(2026, 1, 7))
        foreign = Cycle.objects.generate_for_queue(_queue("OTHER"), as_of=date(2026, 1, 7))[1]
        for cycle, next_cycle, refusal in (
            (current, foreign, "same queue"),
            (following, current, "start after this cycle ends"),
            (last, None, "Generate the next cycle"),
        ):
            with pytest.raises(ValidationError, match=refusal):
                cycle.close(next_cycle=next_cycle)
        assert not Cycle._base_manager.filter(completed_at__isnull=False).exists()
