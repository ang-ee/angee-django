"""Tests for IAM identity helpers."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

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
    assert user_public_id(user.pk) == str(user.pk)
    assert user_display_label(str(user.pk)) == "Identity Target"


@pytest.mark.django_db
def test_user_principal_rejects_encoded_relay_id() -> None:
    """Encoded Relay IDs are not accepted at Angee public-id boundaries."""

    user = get_user_model().objects.create_user(username="identity-other", email="other@example.com")
    encoded_id = "VXNlclR5cGU6" + str(getattr(user, "sqid", user.pk))

    with pytest.raises(ValueError, match="User principal"):
        user_principal(encoded_id)
