"""Tests for parts-merge GraphQL schema composition."""

from __future__ import annotations

from typing import Any, cast

import pytest
import strawberry
from django.core.exceptions import ImproperlyConfigured

from angee.base.apps import SCHEMA_PART_KEYS, BaseAddonConfig
from angee.base.graphql.schema import (
    DEFAULT_SCHEMA_NAME,
    GraphQLSchemas,
)


@strawberry.type
class HelloQuery:
    @strawberry.field
    def hello(self) -> str:
        return "hi"


@strawberry.type
class WorldQuery:
    @strawberry.field
    def world(self) -> str:
        return "world"


@strawberry.type
class PingMutation:
    @strawberry.mutation
    def ping(self) -> str:
        return "pong"


class FakeAddon:
    """Stand-in addon config exposing pre-normalized schema parts."""

    def __init__(self, schemas: dict[str, dict[str, tuple]]) -> None:
        self._schemas = schemas

    @property
    def schema_parts(self) -> dict[str, dict[str, tuple]]:
        return self._schemas


def _parts(**buckets: list) -> dict[str, tuple]:
    """Normalize bucket lists like the AppConfig does."""

    return {key: tuple(buckets.get(key, ())) for key in SCHEMA_PART_KEYS}


def addon(**name_to_parts: dict[str, list]) -> BaseAddonConfig:
    """Build a fake addon contributing parts to one or more schema names."""

    schemas = {name: _parts(**parts) for name, parts in name_to_parts.items()}
    return cast(BaseAddonConfig, FakeAddon(schemas))


def test_collect_folds_addons_in_order() -> None:
    """Parts for one name accumulate across addons, deterministically."""

    first = addon(public={"query": [HelloQuery]})
    second = addon(
        public={"query": [WorldQuery]}, console={"query": [HelloQuery]}
    )

    schemas = GraphQLSchemas.from_addons([first, second])
    collected = schemas.parts

    assert collected["public"]["query"] == (HelloQuery, WorldQuery)
    assert set(collected) == {"public", "console"}
    assert schemas.names() == ("console", "public")


def test_collect_dedupes_by_identity() -> None:
    """A surface contributed twice is folded once."""

    collected = GraphQLSchemas.from_addons(
        [
            addon(public={"query": [HelloQuery]}),
            addon(public={"query": [HelloQuery]}),
        ]
    ).parts

    assert collected["public"]["query"] == (HelloQuery,)


def test_build_schema_merges_query_surfaces() -> None:
    """Query surfaces from several addons merge into one root."""

    schema = GraphQLSchemas.from_addons(
        [
            addon(public={"query": [HelloQuery]}),
            addon(public={"query": [WorldQuery]}),
        ]
    ).build("public")

    result = schema.execute_sync("{ hello world }")

    assert result.errors is None
    assert result.data == {"hello": "hi", "world": "world"}


def test_build_schema_includes_mutation_root() -> None:
    """A mutation bucket becomes the schema mutation root."""

    schema = GraphQLSchemas.from_addons(
        [addon(public={"query": [HelloQuery], "mutation": [PingMutation]})]
    ).build("public")

    result = schema.execute_sync("mutation { ping }")

    assert result.errors is None
    assert result.data == {"ping": "pong"}


def test_render_sdl_prints_each_schema() -> None:
    """Named schemas render to deterministic SDL strings."""

    rendered = GraphQLSchemas.from_addons(
        [
            addon(public={"query": [HelloQuery]}),
            addon(console={"query": [WorldQuery]}),
        ]
    ).render_sdl()

    assert set(rendered) == {"public", "console"}
    assert "hello: String!" in rendered["public"]
    assert "world: String!" in rendered["console"]


def test_build_schema_unknown_name_lists_available() -> None:
    """An unknown schema name reports the names that do exist."""

    with pytest.raises(ImproperlyConfigured, match="available schemas"):
        GraphQLSchemas.from_addons(
            [addon(public={"query": [HelloQuery]})]
        ).build("console")


def test_build_schema_requires_query_root() -> None:
    """A schema without any query contribution fails fast."""

    with pytest.raises(ImproperlyConfigured, match="no query root"):
        GraphQLSchemas.from_addons(
            [addon(public={"mutation": [PingMutation]})]
        ).build("public")


def test_merge_root_field_collision() -> None:
    """Two surfaces claiming the same root field fail fast."""

    @strawberry.type
    class OtherHello:
        @strawberry.field
        def hello(self) -> str:
            return "shadow"

    with pytest.raises(ImproperlyConfigured, match="contributed by both"):
        GraphQLSchemas.from_addons(
            [
                addon(public={"query": [HelloQuery]}),
                addon(public={"query": [OtherHello]}),
            ]
        ).build("public")


def test_default_schema_name_is_public() -> None:
    """The default live schema remains the public schema."""

    assert DEFAULT_SCHEMA_NAME == "public"


def test_empty_addons_contribute_no_schemas() -> None:
    """No contributions means no names and no parts."""

    empty: list[Any] = []
    schemas = GraphQLSchemas.from_addons(empty)
    assert schemas.parts == {}
    assert schemas.names() == ()
