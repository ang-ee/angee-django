"""Keyset paging is independent of Messaging models, public IDs and chronology."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from django.core import signing
from django.db import connection, models
from django.db.models.functions import Collate, Substr
from django.test.utils import CaptureQueriesContext, isolate_apps
from rebac import SubjectRef

from angee.base.pagination import InvalidKeysetCursor, KeysetOrder
from angee.base.querysets import AngeeQuerySet
from tests.tables import model_tables
from tests.test_system_queryset import SystemQueryThing, system_query_tables  # noqa: F401


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("system_query_tables")
def test_native_integer_keys_traverse_ties_and_keep_deleted_cuts() -> None:
    """A model without messaging fields or Sqids uses the same bounded window."""

    rows = [SystemQueryThing._base_manager.create(name=str(index)) for index in range(5)]
    SystemQueryThing._base_manager.update(created_at=datetime(2026, 1, 1, tzinfo=UTC))
    queryset = SystemQueryThing.objects.with_actor(SubjectRef.of("iam/user", "reader"))
    order = KeysetOrder("created_at")
    options = {"order": order, "cursor_scope": ("things",), "limit": 2}
    first = queryset.keyset_page(**options)
    assert list(first.rows) == list(reversed(rows[-2:]))
    second = queryset.keyset_page(before_cursor=first.older_cursor, **options)
    assert list(second.rows) == list(reversed(rows[1:3]))
    newer = queryset.keyset_page(after_cursor=second.newer_cursor, **options)
    assert list(newer.rows) == list(first.rows)

    # A deleted anchor is still a cut; absence must not discard older history.
    SystemQueryThing._base_manager.filter(pk__in=[row.pk for row in rows[-2:]]).delete()
    empty = queryset.keyset_page(through_cursor=first.older_cursor, **options)
    assert list(empty.rows) == []
    assert empty.count == 3
    assert empty.has_more_in_window is False
    assert empty.has_older is False
    assert empty.has_newer is False
    assert empty.has_older_than_through is True
    assert list(queryset.keyset_page(before_cursor=first.older_cursor, **options).rows) == list(
        reversed(rows[1:3])
    )
    with pytest.raises(InvalidKeysetCursor):
        queryset.keyset_page(order=order, cursor_scope=("other",), before_cursor=first.older_cursor)
    with pytest.raises(InvalidKeysetCursor):
        queryset.with_actor(SubjectRef.of("iam/user", "other")).keyset_page(
            before_cursor=first.older_cursor, **options
        )


@pytest.mark.parametrize("pk_field,pk", [(models.IntegerField(), 42), (models.UUIDField(), uuid4())])
def test_cursor_decoding_uses_native_primary_key_type(pk_field: models.Field, pk: object) -> None:
    """Integer and UUID keys round-trip without a public-ID decoder."""

    signer = signing.Signer(salt="keyset-test")
    at = datetime(2026, 1, 1, tzinfo=UTC)
    cursor = signer.sign_object([at.isoformat(), str(pk)])
    assert KeysetOrder("created_at").unsign(cursor, signer, pk_field) == (at, pk)


@pytest.mark.parametrize("value", [[], ["2026-01-01", "42"], ["invalid", "42"], ["2026-01-01T00:00:00+00:00", "bad"]])
@pytest.mark.parametrize("pk_field", [models.IntegerField(), models.UUIDField()])
def test_malformed_signed_cuts_have_one_error_contract(value: object, pk_field: models.Field) -> None:
    """Invalid UUIDs and dates fail as cursor errors, without leaking field errors."""

    signer = signing.Signer(salt="keyset-test")
    with pytest.raises(InvalidKeysetCursor):
        KeysetOrder("created_at").unsign(signer.sign_object(value), signer, pk_field)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("system_query_tables")
def test_mixed_tie_breakers_preserve_forward_reverse_and_around_windows() -> None:
    """Pages and signed boundaries use the same complete multi-column ordering."""

    rows = [SystemQueryThing._base_manager.create(name=name) for name in ("a2", "b0", "a1", "b1", "a1", "c0")]
    SystemQueryThing._base_manager.update(created_at=datetime(2026, 1, 1, tzinfo=UTC))
    queryset = SystemQueryThing.objects.with_actor(SubjectRef.of("iam/user", "reader")).annotate(
        band=Substr("name", 1, 1),
    )
    order = KeysetOrder("created_at", tie_breakers=("band", "-name"))
    options = {"order": order, "cursor_scope": ("mixed-ties",), "limit": 2}
    expected = [rows[index].pk for index in (0, 4, 2, 3, 1, 5)]
    first = queryset.keyset_page(**options)
    second = queryset.keyset_page(before_cursor=first.older_cursor, **options)
    third = queryset.keyset_page(before_cursor=second.older_cursor, **options)
    assert [row.pk for page in (first, second, third) for row in page.rows] == expected
    assert [row.pk for row in queryset.keyset_page(after_cursor=third.newer_cursor, **options).rows] == expected[2:4]
    anchor = queryset.get(pk=expected[3])
    around = queryset.keyset_page(order=order, cursor_scope=("mixed-ties",), around=anchor, limit=3)
    assert [row.pk for row in around.rows] == expected[2:5]
    bounded = queryset.keyset_page(through_cursor=second.older_cursor, **{**options, "limit": 10})
    assert [row.pk for row in bounded.rows] == expected[:4]
    assert bounded.has_older_than_through is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("system_query_tables")
def test_projected_final_key_preserves_native_cuts_and_cursor_values() -> None:
    """A composed relation declares its normalized key without another decoder."""
    rows = [SystemQueryThing._base_manager.create(name=str(index)) for index in range(4)]
    at = datetime(2026, 1, 1, tzinfo=UTC)
    SystemQueryThing._base_manager.update(created_at=at)
    queryset = SystemQueryThing._base_manager.annotate(projected_key=models.F("pk"))
    native = KeysetOrder("created_at")
    projected = KeysetOrder("created_at", key_field="projected_key")
    signer = signing.Signer(salt="projected-key-test")
    anchor = queryset.get(pk=rows[2].pk)
    assert projected.ordering == ("-created_at", "-projected_key")
    assert projected.position(anchor) == native.position(anchor)
    token = projected.sign(anchor, signer)
    assert token == native.sign(anchor, signer)
    cut = projected.unsign(token, signer, SystemQueryThing._meta.pk)
    assert list(queryset.filter(projected.before(cut)).order_by(*projected.ordering)) == list(reversed(rows[:2]))
    assert list(queryset.filter(projected.after(cut)).order_by(*projected.ordering)) == rows[3:]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("system_query_tables")
@pytest.mark.parametrize("case", ["head", "before", "through", "both", "around", "after", "end", "moved-cut"])
def test_older_metadata_uses_the_same_complete_order_and_authorized_scope(case: str) -> None:
    """Window evidence and strict older cuts agree for every native orientation."""
    at = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [SystemQueryThing._base_manager.create(name=str(index)) for index in range(7)]
    SystemQueryThing._base_manager.update(created_at=at)
    queryset = SystemQueryThing._base_manager.exclude(pk=rows[0].pk)
    order = KeysetOrder("created_at")
    options = {"order": order, "cursor_scope": ("older-evidence",), "limit": 2}
    first = queryset.keyset_page(**options)
    second = queryset.keyset_page(before_cursor=first.older_cursor, **options)
    cuts = {}
    if case in {"before", "both", "moved-cut"}:
        cuts["before_cursor"] = first.older_cursor
    if case in {"through", "both"}:
        cuts["through_cursor"] = second.older_cursor
    if case == "end":
        cuts["before_cursor"] = second.older_cursor
    if case == "around":
        cuts["around"] = queryset.get(pk=rows[3].pk)
    if case == "after":
        cuts["after_cursor"] = second.newer_cursor
    if case == "moved-cut":
        SystemQueryThing._base_manager.filter(pk=rows[-2].pk).update(created_at=at - timedelta(days=1))
    page = queryset.keyset_page(**cuts, **options)
    selected = list(page.rows)
    assert selected
    assert page.has_older is queryset.filter(order.before(order.position(selected[-1]))).exists()
    assert page.has_newer is queryset.filter(order.after(order.position(selected[0]))).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("older", [False, True])
@pytest.mark.parametrize("collated", [False, True])
def test_duplicate_join_boundary_does_not_prove_a_strictly_older_row(older: bool, collated: bool) -> None:
    """A genuine join repeats one complete tuple across the limit boundary."""
    if collated and connection.vendor != "sqlite":
        pytest.skip("SQLite's native nocase collation exercises unequal Python tie values.")
    with isolate_apps("tests"):
        class KeysetParent(models.Model):
            created_at = models.DateTimeField()

            class Meta:
                app_label = "tests"

        class KeysetOccurrence(models.Model):
            parent = models.ForeignKey(KeysetParent, on_delete=models.CASCADE, related_name="occurrences")
            label = models.CharField(max_length=8, default="a")

            class Meta:
                app_label = "tests"

        with model_tables((KeysetParent, KeysetOccurrence)):
            at = datetime(2026, 1, 1, tzinfo=UTC)
            newest = KeysetParent.objects.create(created_at=at)
            KeysetOccurrence.objects.bulk_create([
                KeysetOccurrence(parent=newest, label="a"), KeysetOccurrence(parent=newest, label="A"),
            ])
            if older:
                previous = KeysetParent.objects.create(created_at=at - timedelta(days=1))
                KeysetOccurrence.objects.create(parent=previous)
            queryset = AngeeQuerySet(model=KeysetParent).filter(occurrences__isnull=False)
            if collated:
                queryset = queryset.annotate(band=Collate("occurrences__label", "nocase"))
            order = KeysetOrder("created_at", tie_breakers=("band",) if collated else ())
            with CaptureQueriesContext(connection) as reads:
                page = queryset.keyset_page(order=order, cursor_scope=("join",), limit=1)
            assert page.has_more_in_window is True
            assert page.has_older is older
            assert page.count == (3 if older else 2)
            assert list(page.rows.values_list("pk", flat=True)) == [newest.pk, newest.pk]
            # Count, selection, strict-older fallback and newer probe remain.
            assert len(reads) == 4


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("system_query_tables")
def test_distinct_descending_page_omits_the_redundant_older_probe() -> None:
    """The bounded extra tuple itself proves an older continuation."""
    for index in range(3):
        SystemQueryThing._base_manager.create(name=str(index))
    with CaptureQueriesContext(connection) as reads:
        page = SystemQueryThing._base_manager.keyset_page(
            order=KeysetOrder("created_at"), cursor_scope=("probe-count",), limit=1,
        )
    assert page.has_more_in_window and page.has_older
    # Count, bounded selection and the unchanged newer probe only.
    assert len(reads) == 3
