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
    ("broker", "allow", "expected"),
    [
        ("redis://localhost:6379/0", False, True),
        ("amqp://guest:guest@localhost//", False, True),
        ("memory://", False, True),
        (None, False, False),
        ("memory://", True, False),
    ],
)
def test_graphql_channel_layer_check_matches_publishing_guard(
    broker: str | None,
    allow: bool,
    expected: bool,
) -> None:
    with override_settings(
        DEBUG=True,
        CELERY_BROKER_URL=broker,
        ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER=allow,
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    ):
        errors = check_graphql_channel_layer()

    assert [error.id for error in errors] == (["angee.graphql.E003"] if expected else [])
    if errors:
        assert "when a Celery broker is configured" in errors[0].msg
        assert "channels_redis.core.RedisChannelLayer" in errors[0].msg
        assert errors[0].hint == (
            "Configure Redis-backed CHANNEL_LAYERS for worker broadcasts, or run DEBUG without a Celery broker."
        )


def test_the_delivery_check_is_a_serving_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build and data commands never meet angee.graphql.E003; the serving tag runs it."""

    from django.apps import apps
    from django.core.checks.registry import registry

    from angee.graphql.apps import GraphQLConfig
    from angee.server.checks import SERVING_TAG

    monkeypatch.setattr(registry, "registered_checks", registry.registered_checks.copy())
    monkeypatch.setattr(registry, "deployment_checks", registry.deployment_checks.copy())
    GraphQLConfig("angee.graphql", apps.get_app_config("graphql").module).ready()
    assert check_graphql_channel_layer not in registry.get_checks()
    assert check_graphql_channel_layer in registry.get_checks(include_deployment_checks=True)
    assert SERVING_TAG in check_graphql_channel_layer.tags  # type: ignore[attr-defined]
