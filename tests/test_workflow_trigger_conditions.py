from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import models

from angee.workflows.trigger_conditions import EventConditionCatalogue, EventConditionClause


class ConditionSubject(models.Model):
    state = models.CharField(max_length=30, verbose_name="State")
    count = models.IntegerField()
    ratio = models.FloatField()
    active = models.BooleanField()
    happened_on = models.DateField()

    class Meta:
        app_label = "workflows"
        managed = False


@pytest.fixture
def catalogue() -> EventConditionCatalogue:
    return EventConditionCatalogue.from_model(
        ConditionSubject,
        readable_fields=("state", "count", "ratio", "active", "happened_on", "missing"),
    )


def test_catalogue_uses_django_fields_and_lookups(catalogue: EventConditionCatalogue) -> None:
    by_name = {field.name: field for field in catalogue.fields}
    assert by_name["state"].label == "State"
    assert by_name["state"].scalar == "string"
    assert "icontains" in by_name["state"].lookups
    assert "regex" not in by_name["state"].lookups
    assert by_name["count"].scalar == "integer"
    assert "missing" not in by_name


def test_catalogue_owns_authored_keys_labels_and_value_schemas(
    catalogue: EventConditionCatalogue,
) -> None:
    state = {lookup.name: lookup for lookup in catalogue.authored_lookups("state")}
    assert state["exact"].key == "state"
    assert state["exact"].label == "Is"
    assert state["exact"].value_schema == {
        "type": "string",
        "label": "Value",
        "nullable": True,
        "presenceRequired": True,
    }
    assert state["icontains"].key == "state__icontains"
    assert state["icontains"].label == "Contains (case insensitive)"
    assert state["in"].value_schema["type"] == "array"
    assert state["in"].value_schema["widget"] == "list"
    assert state["in"].value_schema["items"]["nullable"] is True
    count = {lookup.name: lookup for lookup in catalogue.authored_lookups("count")}
    assert count["range"].value_schema["minItems"] == 2
    assert count["range"].value_schema["maxItems"] == 2
    assert count["range"].value_schema["items"]["type"] == "integer"
    assert count["isnull"].value_schema["type"] == "boolean"


def test_decode_and_encode_preserve_values_and_exact_key_spelling(
    catalogue: EventConditionCatalogue,
) -> None:
    raw: dict[str, Any] = {
        "state": None,
        "count__gte": 0,
        "ratio__exact": 0.0,
        "active": False,
        "state__in": ["ready", None],
        "opaque__nested": {"zero": 0, "false": False, "items": [1, None]},
    }
    decoded = catalogue.decode(raw)
    assert decoded.errors == ()
    assert decoded.opaque == {"opaque__nested": raw["opaque__nested"]}
    assert catalogue.encode(decoded.clauses, opaque=decoded.opaque) == raw


def test_semantic_exact_duplicates_remain_opaque_and_cannot_be_overwritten(
    catalogue: EventConditionCatalogue,
) -> None:
    decoded = catalogue.decode({"state": "ready", "state__exact": "done"})
    assert decoded.clauses == ()
    assert decoded.opaque == {"state": "ready", "state__exact": "done"}
    assert decoded.errors
    with pytest.raises(ValidationError, match="duplicated"):
        catalogue.encode(
            [EventConditionClause("state", "exact", "later")],
            opaque=decoded.opaque,
        )


def test_encode_rejects_duplicate_clause_keys(catalogue: EventConditionCatalogue) -> None:
    with pytest.raises(ValidationError, match="duplicated"):
        catalogue.encode(
            [
                EventConditionClause("count", "gte", 0),
                EventConditionClause("count", "gte", 1),
            ],
            opaque={},
        )


def test_encode_does_not_reuse_a_source_key_after_field_or_lookup_changes(
    catalogue: EventConditionCatalogue,
) -> None:
    assert catalogue.encode(
        [EventConditionClause("count", "gte", 2, source_key="state")],
        opaque={},
    ) == {"count__gte": 2}


def test_decode_keeps_operator_or_scalar_shape_mismatches_opaque(
    catalogue: EventConditionCatalogue,
) -> None:
    decoded = catalogue.decode(
        {
            "count": {"unexpected": 1},
            "count__in": [0, False],
            "count__range": [0],
            "active__isnull": 0,
            "state__regex": ".*",
            "state__contains": None,
            "count__gt": None,
            "happened_on__gte": "not-a-date",
        }
    )
    assert decoded.clauses == ()
    assert decoded.opaque == {
        "count": {"unexpected": 1},
        "count__in": [0, False],
        "count__range": [0],
        "active__isnull": 0,
        "state__regex": ".*",
        "state__contains": None,
        "count__gt": None,
        "happened_on__gte": "not-a-date",
    }


@pytest.mark.parametrize("lookup", ["state__contains", "count__gt"])
def test_none_matches_django_nonexact_lookup_rejection(lookup: str) -> None:
    with pytest.raises(ValueError, match="Cannot use None"):
        ConditionSubject.objects.filter(**{lookup: None}).query.sql_with_params()


def test_invalid_date_matches_django_lookup_rejection() -> None:
    with pytest.raises(ValidationError, match="valid date"):
        ConditionSubject.objects.filter(happened_on__gte="not-a-date")


@pytest.mark.parametrize("value", [{"bad": 1}, [1], float("inf")])
def test_encode_rejects_invalid_edited_operands(
    catalogue: EventConditionCatalogue,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match="value.*invalid"):
        catalogue.encode(
            [EventConditionClause("ratio", "exact", value)],
            opaque={},
        )
