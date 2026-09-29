from __future__ import annotations

from typing import Any

import pytest
import strawberry_django
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from rebac import actor_context, system_context

from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.subscriptions import changes
from angee.workflows.states import TriggerKind
from angee.workflows.testing.models import Step, Trigger, Workflow
from angee.workflows.trigger_conditions import EventConditionCatalogue, EventConditionClause
from tests.conftest import SchemaAddon
from tests.messaging_models import Party
from tests.proposals_models import Proposal


class UUIDHintCharField(models.CharField):
    angee_scalar_hint = "UUID"


class ConditionSubject(models.Model):
    state = models.CharField(max_length=30, verbose_name="State")
    count = models.IntegerField()
    ratio = models.FloatField()
    active = models.BooleanField()
    happened_on = models.DateField()
    happened_at = models.DateTimeField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reference = models.UUIDField()
    metadata = models.JSONField()
    hinted_reference = UUIDHintCharField(max_length=40)

    class Meta:
        app_label = "workflows"
        managed = False


@strawberry_django.type(Party, fields=["display_name"])
class ConditionPublisherType(AngeeNode):
    """Public identity and a readable scalar for condition catalogue tests."""


@pytest.fixture
def catalogue() -> EventConditionCatalogue:
    return EventConditionCatalogue.from_model(
        ConditionSubject,
        readable_fields=(
            "state", "count", "ratio", "active", "happened_on", "happened_at",
            "amount", "reference", "metadata", "hinted_reference", "missing",
        ),
    )


def test_catalogue_uses_django_fields_and_lookups(catalogue: EventConditionCatalogue) -> None:
    by_name = {field.name: field for field in catalogue.fields}
    assert by_name["state"].label == "State"
    assert by_name["state"].scalar == "string"
    assert "icontains" in by_name["state"].lookups
    assert "regex" not in by_name["state"].lookups
    assert by_name["count"].scalar == "integer"
    assert by_name["happened_at"].scalar == "datetime"
    assert by_name["amount"].scalar == "number"
    assert by_name["reference"].scalar == "string"
    assert by_name["hinted_reference"].scalar == "string"
    assert "metadata" not in by_name
    assert "missing" not in by_name


def test_catalogue_excludes_declared_field_gated_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """A schema-readable field with its own read gate cannot become a condition."""

    readable_fields = frozenset({"state", "staffing"})
    catalogue = EventConditionCatalogue.from_model(Proposal, readable_fields=readable_fields)
    assert [field.name for field in catalogue.fields] == ["state"]

    schemas = GraphQLSchemas([])
    monkeypatch.setattr(schemas, "change_publisher_models", lambda: (Proposal,))
    monkeypatch.setattr(schemas, "model_readable_fields", lambda model: readable_fields)
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
    trigger = Trigger(
        kind=TriggerKind.EVENT,
        config={"model": Proposal._meta.label_lower, "condition": {"staffing": "hidden"}},
    )

    with pytest.raises(ValidationError, match="outside the declared catalogue"):
        trigger.validated_config()


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


@pytest.fixture
def condition_publisher(monkeypatch: pytest.MonkeyPatch) -> GraphQLSchemas:
    """Declare a readable condition field on the existing secured test model."""

    resource = hasura_model_resource(
        ConditionPublisherType,
        model=Party,
        name="condition_subjects",
        filterable=["display_name"],
        sortable=["display_name"],
        aggregatable=[],
        writable=[],
    )
    schemas = GraphQLSchemas([
        SchemaAddon({"public": {
            "query": (resource.query,),
            "subscription": (changes(Party, field="conditionSubjectChanged"),),
        }}),
    ])
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
    return schemas


@pytest.mark.parametrize("condition", ({"id": 1}, {"id__lte": 1}, {"id__in": [1]}))
def test_catalogue_excludes_primary_keys_from_readable_schema_fields(
    condition_publisher: GraphQLSchemas,
    condition: dict[str, Any],
) -> None:
    """A public identity projection never makes its internal key a condition axis."""

    readable_fields = condition_publisher.model_readable_fields(Party)
    assert "id" in readable_fields
    catalogue = EventConditionCatalogue.from_model(Party, readable_fields=readable_fields)
    assert "id" not in {field.name for field in catalogue.fields}
    trigger = Trigger(
        kind=TriggerKind.EVENT,
        config={"model": Party._meta.label_lower, "condition": condition},
    )

    with pytest.raises(ValidationError, match="outside the declared catalogue"):
        trigger.validated_config()


@pytest.mark.parametrize("require_publisher", (False, True))
@pytest.mark.parametrize("lookup", ("created_by__username", "display_name__regex", "created_by_id"))
def test_trigger_validation_rejects_conditions_outside_declared_catalogue(
    condition_publisher: GraphQLSchemas,
    require_publisher: bool,
    lookup: str,
) -> None:
    """Valid ORM traversals, unlisted fields and operators cannot become conditions."""

    del condition_publisher
    trigger = Trigger(
        kind=TriggerKind.EVENT,
        config={"model": Party._meta.label_lower, "condition": {lookup: "hidden"}},
    )

    with pytest.raises(ValidationError, match="outside the declared catalogue"):
        trigger.validated_config(require_publisher=require_publisher)


def test_trigger_conditions_cannot_observe_rows_hidden_from_execution_actor(
    composed_tables: None,
    condition_publisher: GraphQLSchemas,
    no_workflow_queue: None,
) -> None:
    """Even elevated delivery evaluates conditions using the trigger's acting user."""

    del composed_tables, condition_publisher, no_workflow_queue
    user_model = get_user_model()
    with system_context(reason="test trigger condition visibility setup"):
        actor = user_model.objects.create_user(username="condition-actor")
        other = user_model.objects.create_user(username="condition-other")
        visible = Party.objects.create(display_name="Ready", created_by=actor)
        hidden = Party.objects.create(display_name="Ready", created_by=other)
        workflow = Workflow.objects.create(name="Condition visibility", created_by=actor)
        Step.objects.create(
            workflow=workflow, key="start", name="Start", step_class="fixture", is_entry=True,
        )
        workflow.publish()
        trigger = Trigger.objects.create(
            workflow=workflow,
            execution_actor=actor,
            kind=TriggerKind.EVENT,
            config={"model": Party._meta.label_lower, "condition": {"display_name": "Ready"}},
        )
    with actor_context(actor):
        trigger.enable()

    assert Party.objects.with_actor(actor).filter(pk=visible.pk).exists()
    assert not Party.objects.with_actor(actor).filter(pk=hidden.pk).exists()
    with system_context(reason="test elevated trigger event delivery"):
        assert Trigger.objects.start_event(
            trigger.pk, subject=hidden, occurrence_id="hidden", timestamp=timezone.now(), actor=other,
        ) is None
        run = Trigger.objects.start_event(
            trigger.pk, subject=visible, occurrence_id="visible", timestamp=timezone.now(), actor=other,
        )

    assert run is not None
    assert run.created_by_id == actor.pk
