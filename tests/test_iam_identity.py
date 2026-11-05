"""Tests for IAM identity helpers."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.http import HttpRequest
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from angee.iam import identity as identity_module
from angee.iam.identity import (
    user_display_label,
    user_display_labels,
    user_from_public_id,
    user_label,
    user_label_queryset,
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


def test_user_principal_has_no_identity_passthrough_helper() -> None:
    """The principal id is consumed directly; no identity helper should survive."""

    assert not hasattr(identity_module, "_user_principal_node_id")


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


@pytest.mark.django_db
def test_mixed_principal_and_public_labels_share_one_batch() -> None:
    """Canonical grants and public input share native identity decoding and one read."""

    users = [get_user_model().objects.create_user(
        username=f"batch-{index}", first_name=f"Person {index}",
    ) for index in range(3)]
    ids = (users[0].pk, str(users[1].pk), users[2].public_id, users[0].public_id, "bad-public-id")
    request = HttpRequest()
    with CaptureQueriesContext(connection) as queries:
        labels = user_display_labels(ids, request=request)
    assert labels == {
        users[0].pk: "Person 0", str(users[1].pk): "Person 1",
        users[2].public_id: "Person 2", users[0].public_id: "Person 0", "bad-public-id": None,
    }
    assert len(queries) == 1
    assert sum(query["sql"].lstrip().upper().startswith("SELECT") for query in queries) == 1
    with CaptureQueriesContext(connection) as repeated:
        assert user_display_labels(reversed(ids), request=request) == labels
    assert not repeated


def test_label_projection_survives_user_field_redaction(composed_tables: None) -> None:
    """A readable record can disclose its author label without the User field gate."""

    from tests.conftest import create_user

    del composed_tables
    viewer = create_user("label-viewer")
    author = create_user("label-author")
    with actor_context(viewer):
        projected = user_label_queryset().get(pk=author.pk)
        assert projected.__dict__["_iam_display_label"] == "label-author"
        assert user_label(projected) == "label-author"


@pytest.mark.django_db
def test_user_str_keeps_structural_name_fallback() -> None:
    user = get_user_model().objects.create_user(
        username="identity-str", first_name="Identity", last_name="Reader",
    )
    with system_context(reason="tests.iam.user_str"):
        assert str(get_user_model().objects.only("pk").get(pk=user.pk)) == "Identity Reader"
