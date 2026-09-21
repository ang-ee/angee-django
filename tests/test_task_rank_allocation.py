"""Task owns its ordering inventory and delegates allocation to each rank field."""

from __future__ import annotations

from typing import Any

import pytest
from django.db import router
from django.test.utils import isolate_apps

from angee.base.fields import FractionalRankField
from angee.projects.models import Task as AbstractTask
from tests.test_transitions import TransitionRouter


@pytest.mark.parametrize("selection", ["pinned", "explicit"])
def test_task_ordering_allocation_retains_explicit_ranks_and_selected_alias(
    monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    """Unsaved hook dispatch preserves the pin; an explicit using wins over it."""

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
        task._state.db = "selected" if selection == "pinned" else "other"
        routing = TransitionRouter("unavailable-writer")
        monkeypatch.setattr(router, "routers", [routing])
        allocations = []

        def allocate(field: FractionalRankField, instance: Any, *, using: str | None = None) -> float:
            allocations.append((field.name, instance, using))
            return 1024.0

        monkeypatch.setattr(FractionalRankField, "get_append_rank_for_instance", allocate)
        if selection == "pinned":
            task.allocate_ordering_ranks()
        else:
            task.allocate_ordering_ranks(using="selected")
        assert allocations == [("sub_sort_order", task, "selected")]
        assert task.sort_order == 12.5
        assert task.sub_sort_order == 1024.0
        assert routing.writes == []

        task.allocate_ordering_ranks(using="selected")
        assert allocations == [("sub_sort_order", task, "selected")]
