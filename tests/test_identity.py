"""Public identity owner contracts."""

from __future__ import annotations

from contextlib import nullcontext
from itertools import count
from typing import Any
from unittest.mock import patch

import pytest
from django.db import connection, models
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from angee.base.fields import SqidField
from angee.base.identity import SqidPublicIdentity, instance_from_public_id, instances_from_public_ids
from angee.base.models import AngeeModel
from angee.graphql.node import AngeeNode
from tests.tables import model_tables

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


@pytest.mark.parametrize("identity_kind", ["plain", "adapter", "sqid", "sqid_pk", "unique", "sqid_unique"])
def test_batch_identity_preserves_field_codec_input_keys_and_queryset_scope(
    transactional_db: None, identity_kind: str,
) -> None:
    """Row and batch reads share decoding, ignore malformed ids, and retain scope."""

    fields: dict[str, Any] = {"code": models.IntegerField(unique=True)}
    is_sqid = identity_kind in {"sqid", "sqid_pk", "sqid_unique"}
    if is_sqid:
        fields["sqid"] = SqidField(
            real_field_name="code" if identity_kind == "sqid_unique" else "pk" if identity_kind == "sqid_pk" else "id",
            prefix="custom",
            alphabet="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
            min_length=12,
        )
    lookup_field = "sqid" if is_sqid else "code" if identity_kind == "unique" else "pk"
    fields["public_id_lookup"] = classmethod(lambda cls, value: {lookup_field: value})
    model = type(
        f"IdentityBatchThing{next(_model_counter)}",
        (AngeeModel if is_sqid or identity_kind == "unique" else models.Model,),
        {
            "__module__": __name__,
            "Meta": type("Meta", (), {"app_label": "tests", "managed": False}),
            **fields,
        },
    )
    adapter = SqidPublicIdentity(prefix="foreign", min_length=10) if identity_kind == "adapter" else None
    with model_tables((model,)), system_context(reason="verify public identity batching"):
        visible = model.objects.create(code=101)
        hidden = model.objects.create(code=202)

        def public_id(instance: models.Model) -> str:
            if adapter is not None:
                return adapter.public_id_from_pk(instance.pk)
            if is_sqid:
                return instance.sqid
            return f"{instance.code if identity_kind == 'unique' else instance.pk:04}"

        visible_id, hidden_id = public_id(visible), public_id(hidden)
        values = (visible_id, "", "garbage", "wrong_prefix", hidden_id, visible_id)
        scoped = model.objects.filter(pk=visible.pk)
        with CaptureQueriesContext(connection) as queries:
            resolved = instances_from_public_ids(model, values, queryset=scoped, public_identity=adapter)
        assert resolved == {visible_id: visible}
        assert len(queries) == 1
        assert instances_from_public_ids(model, values, public_identity=adapter) == {
            visible_id: visible, hidden_id: hidden,
        }
        for value in values:
            assert instance_from_public_id(
                model, value, queryset=scoped, public_identity=adapter,
            ) == resolved.get(value)


@pytest.mark.parametrize("lookup_kind", ["compound", "transform", "nonunique"])
def test_batch_identity_preserves_native_custom_lookup_contract(
    transactional_db: None, lookup_kind: str,
) -> None:
    """Lookup contracts outside a unique field retain Django's first-match rule."""

    def lookup(cls: type[models.Model], value: str) -> dict[str, Any]:
        if lookup_kind == "compound":
            return {"code": value, "active": True}
        if lookup_kind == "transform":
            return {"code__iexact": value}
        return {"code": value}

    model = type(
        f"IdentityCustomLookupThing{next(_model_counter)}",
        (AngeeModel,),
        {
            "__module__": __name__,
            "code": models.CharField(max_length=20),
            "active": models.BooleanField(default=True),
            "public_id_lookup": classmethod(lookup),
            "Meta": type("Meta", (), {"app_label": "tests", "managed": False}),
        },
    )
    with model_tables((model,)), system_context(reason="verify native public lookup contracts"):
        first = model.objects.create(code="alpha")
        model.objects.create(code="alpha")
        assert instances_from_public_ids(model, ["alpha", "missing"]) == {"alpha": first}
        assert instance_from_public_id(model, "alpha") == first


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
