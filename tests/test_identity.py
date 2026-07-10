"""Public identity owner contracts."""

from __future__ import annotations

from contextlib import nullcontext
from itertools import count
from typing import Any
from unittest.mock import patch

import pytest
from django.db import models
from django.test import override_settings

from angee.base.fields import SqidField
from angee.base.models import SqidPublicIdentity
from angee.graphql.node import AngeeNode

_model_counter = count()


def test_sqid_public_identity_matches_sqid_field_codec() -> None:
    """The third-party-model adapter emits byte-identical IDs to SqidField."""

    field = SqidField(real_field_name="id", prefix="grp", min_length=8)
    identity = SqidPublicIdentity(prefix="grp", min_length=8)

    public_id = field.public_id_from_value(42)

    assert identity.public_id_from_pk(42) == public_id
    assert identity.public_id_to_pk(public_id) == 42


@pytest.mark.parametrize(
    "settings_override",
    [
        {},
        {
            "DJANGO_SQIDS_ALPHABET": "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
            "DJANGO_SQIDS_MIN_LENGTH": 12,
        },
    ],
)
def test_sqid_public_identity_matches_bound_sqid_field(settings_override: dict[str, Any]) -> None:
    """The adapter emits the same ids as a model-bound SqidField."""

    context = override_settings(**settings_override) if settings_override else nullcontext()
    with context:
        model = _bound_sqid_model(prefix="grp")
        field = model._meta.get_field("sqid")
        identity = SqidPublicIdentity(prefix="grp")

        assert isinstance(field, SqidField)
        assert model(id=42).sqid == identity.public_id_from_pk(42)
        assert field.public_id_from_value(42) == identity.public_id_from_pk(42)


@pytest.mark.parametrize("public_id", ["", "usr_abc", "garbage", "grp_"])
def test_sqid_public_identity_decode_invalid_values_returns_none(public_id: str) -> None:
    """Invalid adapter public ids are total decode misses, not exceptions."""

    assert SqidPublicIdentity(prefix="grp").public_id_to_pk(public_id) is None


def test_sqid_public_identity_delegates_encoding_to_sqid_field(monkeypatch: Any) -> None:
    """The adapter uses SqidField's owner API instead of assembling a codec."""

    calls: list[tuple[str, Any]] = []

    def public_id_from_value(self: SqidField, value: Any) -> str:
        calls.append((self.prefix, value))
        return "field-owned-id"

    monkeypatch.setattr(SqidField, "public_id_from_value", public_id_from_value)

    assert SqidPublicIdentity(prefix="grp").public_id_from_pk(7) == "field-owned-id"
    assert calls == [("grp_", 7)]


def test_sqid_public_identity_delegates_decoding_to_sqid_field(monkeypatch: Any) -> None:
    """The adapter uses SqidField's decode API instead of assembling a codec."""

    calls: list[tuple[str, Any]] = []

    def public_id_to_value(self: SqidField, public_id: Any) -> int:
        calls.append((self.prefix, public_id))
        return 7

    monkeypatch.setattr(SqidField, "public_id_to_value", public_id_to_value)

    assert SqidPublicIdentity(prefix="grp").public_id_to_pk("grp_encoded") == 7
    assert calls == [("grp_", "grp_encoded")]


def test_angee_node_id_uses_generic_public_id_boundary_for_plain_model() -> None:
    """The node interface keeps the generic identity boundary for swapped models."""

    instance = _bound_sqid_model(prefix="plain")(id=13)

    with patch("angee.graphql.node.public_id_of", return_value="plain-13") as public_id_of:
        assert AngeeNode.id(instance) == "plain-13"

    public_id_of.assert_called_once_with(instance)


def _bound_sqid_model(*, prefix: str) -> type[models.Model]:
    """Return a throwaway model whose SqidField has run contribute_to_class."""

    index = next(_model_counter)
    meta = type(
        "Meta",
        (),
        {
            "app_label": "tests",
            "managed": False,
        },
    )
    return type(
        f"IdentityOwnerBoundSqidThing{index}",
        (models.Model,),
        {
            "__module__": __name__,
            "sqid": SqidField(real_field_name="id", prefix=prefix),
            "Meta": meta,
        },
    )
