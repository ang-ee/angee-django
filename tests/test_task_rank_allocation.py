"""Task ordering persists through the rank field's connection-aware save hooks."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection
from rebac import system_context

from angee.base.fields.ordering import FractionalRankExhausted, FractionalRankField
from angee.projects.testing.models import Task


@pytest.mark.django_db(transaction=True)
def test_task_ordering_allocation_retains_explicit_ranks() -> None:
    """An omitted rank appends once on save; explicit ranks survive repeated saves."""

    with system_context(reason="test.task_rank_allocation"):
        root = Task.objects.create(title="Root", sort_order=100.0)
        Task.objects.create(title="Existing tail", parent=root, sort_order=200.0, sub_sort_order=4096.0)
        task = Task(title="Append child order", parent=root, sort_order=12.5)
        allocations: list[str] = []

        def record_rank_reads(
            execute: Callable[..., Any], sql: str, params: Any, many: bool, context: dict[str, Any],
        ) -> Any:
            # Observe the real contextual tail lookup rather than replacing its owner.
            if sql.startswith("SELECT") and sql.split(" FROM ", 1)[0].endswith(' AS "sub_sort_order"'):
                allocations.append(context["connection"].alias)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(record_rank_reads):
            task.save(using=connection.alias)
            assert task.sort_order == 12.5
            assert task.sub_sort_order == 4096.0 + FractionalRankField.STEP
            task.save(using=connection.alias)

        assert allocations == [connection.alias]
        task.refresh_from_db(using=connection.alias)
        assert task.sort_order == 12.5
        assert task.sub_sort_order == 4096.0 + FractionalRankField.STEP


@pytest.mark.parametrize(("previous", "following", "expected"), [
    (None, None, 1024.0), (None, 1024.0, 0.0),
    (1024.0, 2048.0, 1536.0), (2048.0, None, 3072.0),
    (-1e308, 1e308, 0.0),
])
def test_persisted_rank_gap_allocation(previous, following, expected):
    """The numeric owner allocates strict gaps without overflowing endpoints."""

    assert FractionalRankField.get_rank_between(previous, following) == expected


def test_persisted_rank_gap_exhaustion_refuses_instead_of_reusing_a_neighbor():
    """Adjacent binary64 endpoints cannot silently turn a move into a collision."""

    with pytest.raises(FractionalRankExhausted):
        FractionalRankField.get_rank_between(1.0, math.nextafter(1.0, math.inf))


@pytest.mark.parametrize("previous,following", [(2, 1), (1, 1)])
def test_reversed_rank_neighbors_are_invalid_instead_of_exhausted(previous, following):
    with pytest.raises(ValueError) as error:
        FractionalRankField.get_rank_between(previous, following)
    assert not isinstance(error.value, FractionalRankExhausted)


@pytest.mark.django_db(transaction=True)
def test_rank_rebalance_preserves_order_with_an_exhausted_tail():
    with system_context(reason="tests.rank.rebalance"):
        first = Task.objects.create(title="First", sort_order=1.0)
        second = Task.objects.create(title="Second", sort_order=math.nextafter(1.0, math.inf))
        third = Task.objects.create(title="Last", sort_order=1e308)
        field = Task._meta.get_field("sort_order")
        with Task.ordering_write(using=connection.alias, project_ids=(None,)):
            field.rebalance(Task.system_queryset())
        assert list(Task._base_manager.order_by("sort_order").values_list("pk", "sort_order")) == [
            (first.pk, 1024), (second.pk, 2048), (third.pk, 3072),
        ]
