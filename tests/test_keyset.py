"""Keyset paging is independent of Messaging models, public IDs and chronology."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from django.core import signing
from django.db import models
from django.db.models.functions import Substr
from rebac import SubjectRef

from angee.base.pagination import InvalidKeysetCursor, KeysetOrder
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
