"""``first_per_group`` keeps each group's first rows in one windowed statement."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.test_system_queryset import SystemQueryThing, system_query_tables  # noqa: F401


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("system_query_tables")
def test_first_per_group_keeps_each_groups_first_rows_in_one_statement() -> None:
    """Each group keeps at most ``limit`` rows in its own order; small groups keep all."""

    rows = SystemQueryThing.system_queryset()
    created = {name: [rows.create(name=name) for _ in range(count)] for name, count in (("a", 3), ("b", 1), ("c", 2))}

    with CaptureQueriesContext(connection) as queries:
        kept = list(rows.first_per_group("name", order_by=["-pk"], limit=2).order_by("name", "-pk"))

    assert len(queries) == 1
    assert kept == [*reversed(created["a"][1:]), created["b"][0], *reversed(created["c"])]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("system_query_tables")
def test_first_per_group_ranks_inside_the_callers_scope() -> None:
    """Rows the caller filtered out never take a group's place."""

    rows = SystemQueryThing.system_queryset()
    first, second, third = (rows.create(name="a") for _ in range(3))

    kept = rows.exclude(pk=third.pk).first_per_group("name", order_by=["-pk"], limit=1)

    assert list(kept) == [second]
    assert first not in kept


@pytest.mark.parametrize(("partition", "limit"), [((), 1), (("name",), 0)])
def test_first_per_group_rejects_an_empty_partition_or_limit(partition: tuple[str, ...], limit: int) -> None:
    """A missing partition or a limit below one fails before building SQL."""

    with pytest.raises(ValueError, match="first_per_group"):
        SystemQueryThing.objects.none().first_per_group(*partition, order_by=["pk"], limit=limit)
