"""Focused checks for GraphQL composition validation."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import override_settings

from angee.graphql.checks import check_graphql_channel_layer, check_graphql_schemas
from angee.graphql.schema import GraphQLSchemas


def test_graphql_check_reports_discovery_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken addon declaration becomes an actionable Django check error."""

    def fail_discovery(cls: type[GraphQLSchemas]) -> Any:
        del cls
        raise RuntimeError("invalid addon schema reference")

    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(fail_discovery))

    [error] = check_graphql_schemas()

    assert error.id == "angee.graphql.E001"
    assert error.msg == "GraphQL schema discovery failed: invalid addon schema reference"
    assert error.hint == "Fix the owning addon manifest or schema declaration, then rerun manage.py check."


@pytest.mark.parametrize(
    ("broker", "eager", "allow", "expected"),
    [
        ("redis://localhost:6379/0", False, False, True),
        ("amqp://guest:guest@localhost//", False, False, True),
        (None, False, False, False),
        ("memory://", True, False, False),
        ("memory://", False, True, False),
    ],
)
def test_graphql_channel_layer_check_matches_publishing_guard(
    broker: str | None,
    eager: bool,
    allow: bool,
    expected: bool,
) -> None:
    with override_settings(
        DEBUG=True,
        CELERY_BROKER_URL=broker,
        CELERY_TASK_ALWAYS_EAGER=eager,
        ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER=allow,
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    ):
        errors = check_graphql_channel_layer()

    assert [error.id for error in errors] == (["angee.graphql.E003"] if expected else [])
    if errors:
        assert "when Celery tasks are not eager" in errors[0].msg
        assert "channels_redis.core.RedisChannelLayer" in errors[0].msg
        assert errors[0].hint == (
            "Configure Redis-backed CHANNEL_LAYERS for worker broadcasts, or use eager tasks in DEBUG."
        )
