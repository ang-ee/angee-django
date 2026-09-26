"""Task owns its ordering inventory and delegates allocation to each rank field."""

from __future__ import annotations

from typing import Any

import pytest
from django.test.utils import isolate_apps

from angee.base.fields import FractionalRankField
from angee.projects.models import Task as AbstractTask


def test_task_ordering_allocation_retains_explicit_ranks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allocate omitted ranks once without changing explicitly assigned ranks."""

    with isolate_apps():

        class RankTask(AbstractTask):
            class Meta(AbstractTask.Meta):
                abstract = False
                app_label = "tests"

        # The isolated registry omits the unrelated nullable relation targets.
        task = RankTask(
            sort_order=12.5,
            **{field.attname: None for field in RankTask._meta.fields if field.many_to_one},
        )
        allocations = []

        def allocate(field: FractionalRankField, instance: Any) -> float:
            allocations.append((field.name, instance))
            return 1024.0

        monkeypatch.setattr(FractionalRankField, "_get_append_rank_for_instance", allocate)
        task.allocate_ordering_ranks()
        assert allocations == [("sub_sort_order", task)]
        assert task.sort_order == 12.5
        assert task.sub_sort_order == 1024.0

        task.allocate_ordering_ranks()
        assert allocations == [("sub_sort_order", task)]
