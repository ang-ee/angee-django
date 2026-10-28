"""Task ordering persists through the rank field's connection-aware save hooks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection
from rebac import system_context

from angee.base.fields.ordering import FractionalRankField
from angee.projects.testing.models import Task


@pytest.mark.django_db(transaction=True)
def test_task_ordering_allocation_retains_explicit_ranks() -> None:
    """An omitted rank appends once on save; explicit ranks survive repeated saves."""

    with system_context(reason="test.task_rank_allocation"):
        Task.objects.create(title="Existing tail", sort_order=100.0, sub_sort_order=4096.0)
        task = Task(title="Append child order", sort_order=12.5)
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
