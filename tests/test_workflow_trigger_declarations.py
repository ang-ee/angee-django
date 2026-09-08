"""Native trigger declaration contract tests."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from angee.workflows.trigger_declarations import (
    EventTriggerConfig,
    ScheduleTriggerConfig,
    schedule_preview,
    trigger_config_schema,
    trigger_summary,
    validate_trigger_config,
)


def test_event_declaration_preserves_condition_and_legacy_model_alias() -> None:
    declaration = validate_trigger_config(
        "event",
        {
            "model_label": "NOTES.Page",
            "condition": {"state__in": ["ready", "held"]},
            "future_extension": {"kept": True},
        },
    )

    assert isinstance(declaration, EventTriggerConfig)
    assert declaration.model == "notes.page"
    assert declaration.condition == {"state__in": ["ready", "held"]}
    assert declaration.model_extra == {"future_extension": {"kept": True}}
    assert trigger_summary("event", declaration.model_dump()) == "When notes.page changes"


@pytest.mark.parametrize("canonical", [None, "", "   "])
def test_event_legacy_alias_fills_an_empty_canonical_model(canonical: object) -> None:
    declaration = EventTriggerConfig.model_validate({"model": canonical, "model_label": " Notes.Page "})
    assert declaration.model == "notes.page"


def test_event_canonical_model_keeps_precedence_over_legacy_alias() -> None:
    declaration = EventTriggerConfig.model_validate({"model": "CRM.Contact", "model_label": "notes.page"})
    assert declaration.model == "crm.contact"


def test_event_whitespace_model_without_alias_is_invalid() -> None:
    with pytest.raises(ValidationError):
        EventTriggerConfig.model_validate({"model": "  "})


def test_schedule_schema_owns_units_and_advanced_limits() -> None:
    schema = trigger_config_schema("schedule")

    assert schema["properties"]["interval_seconds"]["unit"] == "seconds"
    assert {item.get("minimum") for item in schema["properties"]["cooldown_seconds"]["anyOf"]} == {0, None}
    assert {item.get("exclusiveMinimum") for item in schema["properties"]["hourly_cap"]["anyOf"]} == {0, None}


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"cron": "* * * * *", "interval_seconds": 60},
        {"cron": "not a cron"},
        {"interval_seconds": 0},
    ],
)
def test_schedule_declaration_rejects_incomplete_or_invalid_rules(config: object) -> None:
    with pytest.raises(ValidationError):
        ScheduleTriggerConfig.model_validate(config)


def test_interval_preview_uses_scheduler_catch_up_semantics() -> None:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    declaration = ScheduleTriggerConfig.model_validate({"interval_seconds": "60"})

    assert declaration.next_fire_at(after=now.replace(hour=11, minute=55), now=now) == now.replace(minute=1)
    assert declaration.next_fire_at(after=now.replace(minute=5), now=now) == now.replace(minute=6)
    assert schedule_preview({"interval_seconds": 60}, now=now) == (
        now.replace(minute=1),
        now.replace(minute=2),
        now.replace(minute=3),
    )
    assert trigger_summary("schedule", {"interval_seconds": 60}) == "Every 60 seconds"


def test_schedule_preview_is_bounded() -> None:
    with pytest.raises(ValueError, match="between 0 and 10"):
        schedule_preview({"interval_seconds": 1}, now=datetime(2026, 9, 9, tzinfo=UTC), count=11)


def test_cron_preview_uses_the_same_validated_expression() -> None:
    now = datetime(2026, 9, 9, 12, 34, tzinfo=UTC)

    assert schedule_preview({"cron": "0 * * * *"}, now=now, count=2) == (
        datetime(2026, 9, 9, 13, 0, tzinfo=UTC),
        datetime(2026, 9, 9, 14, 0, tzinfo=UTC),
    )
    assert trigger_summary("schedule", {"cron": "0 * * * *"}) == "Cron 0 * * * *"


def test_manual_is_permissive_but_common_limits_remain_validated() -> None:
    declaration = validate_trigger_config("manual", {"legacy": True, "cooldown_seconds": "0"})
    assert declaration.model_extra == {"legacy": True}
    assert declaration.cooldown_seconds == 0

    with pytest.raises(ValidationError):
        validate_trigger_config("manual", {"hourly_cap": -1})
