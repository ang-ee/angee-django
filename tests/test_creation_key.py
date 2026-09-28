"""Stored creation identities are unique within their declared scope."""

import pytest
from django.db import IntegrityError, transaction

from angee.base.mixins import CreationKeyConflict
from tests.conftest import create_user
from tests.core_persistence import CreationRow

pytestmark = pytest.mark.django_db


def test_matching_fingerprint_replays_the_original_row(django_assert_num_queries):
    owner = create_user("owner")
    row = CreationRow.objects.create(created_by=owner, client_creation_key="request", creation_fingerprint="a" * 64)
    with django_assert_num_queries(1):
        assert CreationRow.objects.for_creation_key(owner, "request", "a" * 64) == row
    assert CreationRow.objects.count() == 1


def test_changed_fingerprint_raises_conflict_without_modifying_original():
    owner = create_user("owner")
    row = CreationRow.objects.create(created_by=owner, client_creation_key="request", creation_fingerprint="a" * 64)
    with pytest.raises(CreationKeyConflict):
        CreationRow.objects.for_creation_key(owner, "request", "b" * 64)
    row.refresh_from_db()
    assert row.creation_fingerprint == "a" * 64


def test_empty_legacy_fingerprint_allows_replay_without_claiming_new_content():
    owner = create_user("owner")
    row = CreationRow.objects.create(created_by=owner, client_creation_key="request")
    assert CreationRow.objects.for_creation_key(owner, "request", "new") == row
    row.refresh_from_db()
    assert row.creation_fingerprint == ""


def test_same_key_in_another_scope_is_an_independent_creation():
    first, second = create_user("first"), create_user("second")
    a = CreationRow.objects.create(created_by=first, client_creation_key="request", creation_fingerprint="a")
    b = CreationRow.objects.create(created_by=second, client_creation_key="request", creation_fingerprint="b")
    assert CreationRow.objects.for_creation_key(first.pk, "request", "a") == a
    assert CreationRow.objects.for_creation_key(second, "request", "b") == b
    assert CreationRow.objects.for_creation_key(second, "absent", "b") is None


def test_creation_lookup_preserves_existing_queryset_filters():
    owner = create_user("owner")
    CreationRow.objects.create(created_by=owner, client_creation_key="request", title="hidden")
    assert CreationRow.objects.filter(title="visible").for_creation_key(owner, "request", "a") is None


@pytest.mark.parametrize("scope,key", [(None, "request"), (1, None), (None, None)])
def test_missing_identity_does_not_query(scope, key, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert CreationRow.objects.for_creation_key(scope, key, "a") is None


def test_database_refuses_duplicate_key_but_allows_multiple_null_keys():
    owner = create_user("owner")
    CreationRow.objects.create(created_by=owner, client_creation_key="request")
    with pytest.raises(IntegrityError), transaction.atomic():
        CreationRow.objects.create(created_by=owner, client_creation_key="request")
    CreationRow.objects.create(created_by=owner)
    CreationRow.objects.create(created_by=owner)
    assert CreationRow.objects.count() == 3
