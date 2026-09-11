"""Keyset paging is independent of Messaging models, public IDs and chronology."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from django.core import signing
from django.db import models
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
    assert list(first["rows"]) == list(reversed(rows[-2:]))
    second = queryset.keyset_page(before_cursor=first["older_cursor"], **options)
    assert list(second["rows"]) == list(reversed(rows[1:3]))
    newer = queryset.keyset_page(after_cursor=second["newer_cursor"], **options)
    assert list(newer["rows"]) == list(first["rows"])

    # A deleted anchor is still a cut; absence must not discard older history.
    SystemQueryThing._base_manager.filter(pk__in=[row.pk for row in rows[-2:]]).delete()
    empty = queryset.keyset_page(through_cursor=first["older_cursor"], **options)
    assert list(empty["rows"]) == []
    assert empty["count"] == 3
    assert empty["has_more_in_window"] is False
    assert empty["has_older_than_through"] is True
    assert list(queryset.keyset_page(before_cursor=first["older_cursor"], **options)["rows"]) == list(
        reversed(rows[1:3])
    )
    with pytest.raises(InvalidKeysetCursor):
        queryset.keyset_page(order=order, cursor_scope=("other",), before_cursor=first["older_cursor"])
    with pytest.raises(InvalidKeysetCursor):
        queryset.with_actor(SubjectRef.of("iam/user", "other")).keyset_page(
            before_cursor=first["older_cursor"], **options
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
