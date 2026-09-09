"""Native resource field classification boundary regressions."""

from __future__ import annotations

from typing import Any, cast

from django.db import models

from angee.data.field_classification import RESOURCE_FIELD_SCALARS, is_to_one_relation


def test_uuid_is_a_supported_resource_scalar() -> None:
    assert "UUID" in RESOURCE_FIELD_SCALARS


def test_generic_relation_without_one_fixed_model_is_not_a_to_one_axis() -> None:
    generic = cast(models.Field[Any, Any], type("GenericRelation", (), {
        "many_to_one": True,
        "one_to_one": False,
        "related_model": None,
    })())
    assert is_to_one_relation(generic) is False
