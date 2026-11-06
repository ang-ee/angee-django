"""Page hooks on a queryset chain: every owner's projection survives the next hook."""

from __future__ import annotations

import pytest
from django.db.models.query import ValuesIterable

from rebac import system_context
from tests.chatterdemo.models import ChatterDoc


@pytest.mark.django_db
def test_page_hooks_chain_in_order_over_one_materialization() -> None:
    with system_context(reason="tests.page_hooks"):
        ChatterDoc.objects.create(title="first")
        ChatterDoc.objects.create(title="second")
    seen: list[tuple[str, int]] = []

    def first(queryset, rows):
        seen.append(("first", len(rows)))
        for row in rows:
            row.first_hook = True

    def second(queryset, rows):
        seen.append(("second", len(rows)))
        assert all(getattr(row, "first_hook", False) for row in rows)

    with system_context(reason="tests.page_hooks"):
        rows = list(ChatterDoc.objects.order_by("title").with_page_hook(first).with_page_hook(second))

    assert [row.title for row in rows] == ["first", "second"]
    assert seen == [("first", 2), ("second", 2)]


@pytest.mark.django_db
def test_page_hook_refuses_a_values_queryset() -> None:
    with system_context(reason="tests.page_hooks"):
        rows = ChatterDoc.objects.values("title")
        assert rows._iterable_class is ValuesIterable
        with pytest.raises(TypeError):
            rows.with_page_hook(lambda queryset, page: None)
