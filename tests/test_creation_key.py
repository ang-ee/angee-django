"""Changesets own idempotency claims independently of created product rows."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction

from angee.base.changesets import (
    changeset,
    changeset_model_label,
    creation_fingerprint,
    current_changeset,
)
from angee.base.models import CreationKeyConflict

pytestmark = pytest.mark.django_db(transaction=True)


def _model() -> Any:
    return apps.get_model(changeset_model_label())


def _claim(scope: str, key: str, fingerprint: str, *, created_row: Any = None) -> Any | None:
    with changeset(fresh=True):
        with transaction.atomic():
            replay = _model().objects.claim(scope, key, fingerprint=fingerprint)
            if replay is None and created_row is not None:
                current_changeset(required=True).record_created(created_row)
            return replay


def test_matching_claim_replays_the_boundary_and_created_row() -> None:
    target = ContentType.objects.order_by("pk").first()
    assert target is not None
    assert _claim("tests.create", "request", "a" * 64, created_row=target) is None

    replay = _claim("tests.create", "request", "a" * 64)

    assert replay is not None
    assert replay.created_row == target
    assert _model()._base_manager.filter(scope="tests.create", key="request").count() == 1


def test_changed_fingerprint_conflicts_without_modifying_the_claim() -> None:
    _claim("tests.create", "request", "a" * 64)

    with pytest.raises(CreationKeyConflict):
        _claim("tests.create", "request", "b" * 64)

    stored = _model()._base_manager.get(scope="tests.create", key="request")
    assert stored.fingerprint == "a" * 64


def test_same_key_in_another_scope_is_an_independent_claim() -> None:
    assert _claim("tests.first", "request", "a") is None
    assert _claim("tests.second", "request", "b") is None
    assert _claim("tests.first", "request", "a") is not None
    assert _claim("tests.second", "request", "b") is not None


@pytest.mark.parametrize(
    ("scope", "key", "fingerprint"),
    [
        ("", "request", "a"),
        ("tests.create", "", "a"),
        ("tests.create", "  ", "a"),
        ("x" * 256, "request", "a"),
        ("tests.create", "x" * 256, "a"),
        ("tests.create", "request", "x" * 65),
    ],
)
def test_claim_validates_its_complete_identity(scope: str, key: str, fingerprint: str) -> None:
    with pytest.raises(ValidationError):
        _claim(scope, key, fingerprint)


def test_claim_requires_an_outer_transaction_and_precedes_boundary_writes() -> None:
    with changeset(fresh=True):
        with pytest.raises(RuntimeError, match="transaction.atomic"):
            _model().objects.claim("tests.create", "request", fingerprint="a")
        with transaction.atomic():
            current_changeset(required=True, record=True)
            with pytest.raises(RuntimeError, match="before the boundary records any writes"):
                _model().objects.claim("tests.create", "request", fingerprint="a")


def test_fingerprints_use_canonical_values_and_model_identity() -> None:
    target = ContentType.objects.order_by("pk").first()
    assert target is not None
    first = creation_fingerprint({"when": "2026-01-01", "target": target, "values": [1, 2]})
    assert creation_fingerprint({"values": [1, 2], "target": target, "when": "2026-01-01"}) == first
    assert creation_fingerprint({"values": [2, 1], "target": target, "when": "2026-01-01"}) != first
