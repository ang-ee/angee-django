"""Tests for IAM identity helpers."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpRequest

from angee.iam.identity import (
    user_display_label,
    user_from_public_id,
    user_principal,
    user_public_id,
)


@pytest.mark.django_db
def test_user_principal_accepts_raw_public_id() -> None:
    """IAM owns principal resolution for permission-hub and addon callers."""

    user = get_user_model().objects.create_user(
        username="identity-target",
        email="identity@example.com",
        first_name="Identity",
        last_name="Target",
    )
    node_id = str(getattr(user, "sqid", user.pk))

    assert user_principal(str(user.pk)) == user
    assert user_principal(node_id) == user
    assert user_from_public_id(node_id) == user
    assert user_public_id(user.pk) == node_id
    assert user_display_label(str(user.pk)) == "Identity Target"


@pytest.mark.django_db
def test_user_display_label_memoizes_per_request(django_assert_num_queries: object) -> None:
    """A request-scoped memo resolves each author once — the audited-list N+1 fix.

    The label resolver runs its own ORM read per row, which the optimizer cannot
    batch; the memo de-duplicates repeated authors within one request so a list
    sharing an author queries the user once instead of per row.
    """

    user = get_user_model().objects.create_user(
        username="memo-target",
        email="memo@example.com",
        first_name="Memo",
        last_name="Target",
    )
    request = HttpRequest()

    assert user_display_label(user.pk, request=request) == "Memo Target"
    with django_assert_num_queries(0):  # type: ignore[operator]
        assert user_display_label(user.pk, request=request) == "Memo Target"

    missing_pk = user.pk + 10_000
    assert user_display_label(missing_pk, request=request) is None
    with django_assert_num_queries(0):  # type: ignore[operator]
        assert user_display_label(missing_pk, request=request) is None


@pytest.mark.django_db
def test_user_principal_rejects_encoded_relay_id() -> None:
    """Encoded Relay IDs are not accepted at Angee public-id boundaries."""

    user = get_user_model().objects.create_user(username="identity-other", email="other@example.com")
    encoded_id = "VXNlclR5cGU6" + str(getattr(user, "sqid", user.pk))

    with pytest.raises(ValueError, match="User principal"):
        user_principal(encoded_id)
