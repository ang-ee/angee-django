"""``first_per_group`` keeps each group's first rows in one windowed statement."""

from collections.abc import Iterator

import pytest
from django.db import connection, models
from django.test.utils import CaptureQueriesContext

from angee.base.models.base import AngeeModel
from tests.tables import model_tables
from tests.test_system_queryset import SystemQueryThing, system_query_tables  # noqa: F401


class OrderedGroupThing(AngeeModel):
    """A row whose model ordering names a column outside any grouping."""

    name = models.CharField(max_length=32)
    rank = models.IntegerField(default=0)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"
        ordering = ("rank",)


@pytest.fixture
def ordered_group_tables() -> Iterator[None]:
    with model_tables((OrderedGroupThing,)):
        yield


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


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("ordered_group_tables")
def test_first_per_group_ranks_a_grouped_selection_without_the_models_ordering() -> None:
    """``Meta.ordering`` never splits a grouped selection into one group per ordering value."""

    rows = OrderedGroupThing.system_queryset()
    for name, ranks in (("a", (3, 1, 2)), ("b", (5,))):
        for rank in ranks:
            rows.create(name=name, rank=rank)
    grouped = rows.values("name").annotate(_top=models.Max("rank"))

    kept = list(grouped.first_per_group("name", order_by=["-_top"], limit=1).order_by("name"))

    assert kept == [{"name": "a", "_top": 3}, {"name": "b", "_top": 5}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("system_query_tables")
def test_filters_chained_after_first_per_group_apply_before_the_ranking() -> None:
    """A later filter narrows the rows before they rank, so the next row takes the excluded one's place."""

    rows = SystemQueryThing.system_queryset()
    first, second = rows.create(name="a"), rows.create(name="a")

    kept = rows.first_per_group("name", order_by=["-pk"], limit=1).exclude(pk=second.pk)

    assert list(kept) == [first]


@pytest.mark.parametrize(("partition", "limit"), [((), 1), (("name",), 0)])
def test_first_per_group_rejects_an_empty_partition_or_limit(partition: tuple[str, ...], limit: int) -> None:
    """A missing partition or a limit below one fails before building SQL."""

    with pytest.raises(ValueError, match="first_per_group"):
        SystemQueryThing.objects.none().first_per_group(*partition, order_by=["pk"], limit=limit)
