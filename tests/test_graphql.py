"""Tests for parts-merge GraphQL schema composition."""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest
import strawberry
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rebac import MissingActorError, PermissionDenied, RebacMixin
from rebac.graphql.strawberry import RebacExtension
from rebac.graphql.strawberry_django import RebacDjangoOptimizerExtension
from rebac.managers import RebacManager
from strawberry.extensions import SchemaExtension

from angee.graphql.schema import (
    DEFAULT_SCHEMA_NAME,
    SCHEMA_PART_KEYS,
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


class CustomExtension(SchemaExtension):
    """Sentinel extension contributed by an addon."""


class ManagedThing(RebacMixin):
    """Concrete REBAC model with the library manager intact."""

    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the managed test model."""

        app_label = "tests"
        rebac_resource_type = "tests/managed"


class UnmanagedThing(RebacMixin):
    """Concrete REBAC model with an unsafe default manager."""

    objects = models.Manager()
    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the unmanaged test model."""

        app_label = "tests"
        rebac_resource_type = "tests/unmanaged"


@strawberry.type
class ThingNode(strawberry.relay.Node):
    """Relay node surface reused across named schemas."""

    id: strawberry.relay.NodeID[int]


@strawberry.type
class NodeQuery:
    """Query exposing a relay node field that two schemas share."""

    thing: ThingNode | None = strawberry.relay.node()


@strawberry.type
class DenialQuery:
    @strawberry.field
    def missing_actor(self) -> str:
        raise MissingActorError("missing actor")

    @strawberry.field
    def permission_denied(self) -> str:
        raise PermissionDenied("denied")


@strawberry.type
class ManagedThingType:
    """GraphQL type exposing a safely managed REBAC model."""

    name: str
    __strawberry_django_definition__ = type(
        "DjangoDefinition",
        (),
        {"model": ManagedThing},
    )()


@strawberry.type
class UnmanagedThingType:
    """GraphQL type exposing an unsafely managed REBAC model."""

    name: str
    __strawberry_django_definition__ = type(
        "DjangoDefinition",
        (),
        {"model": UnmanagedThing},
    )()


class FakeAddon(AppConfig):
    """Stand-in addon config exposing raw schema declarations."""


def _parts(**buckets: list) -> dict[str, tuple]:
    """Return a raw schema declaration with all known buckets."""

    return {key: tuple(buckets.get(key, ())) for key in SCHEMA_PART_KEYS}


def _module(name: str) -> ModuleType:
    """Return a synthetic module with a Django app filesystem path."""

    module = ModuleType(name)
    module.__file__ = __file__
    return module


def addon(**name_to_parts: dict[str, list]) -> AppConfig:
    """Build a fake addon contributing parts to one or more schema names."""

    schemas = {name: _parts(**parts) for name, parts in name_to_parts.items()}
    config = FakeAddon("tests.fake_graphql", _module("tests.fake_graphql"))
    config.schemas = schemas
    return config


def test_collect_folds_addons_in_order() -> None:
    """Parts for one name accumulate across addons, deterministically."""

    first = addon(public={"query": [HelloQuery]})
    second = addon(public={"query": [WorldQuery]}, console={"query": [HelloQuery]})

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

    from angee.graphql.errors import AngeeSchema

    schema = GraphQLSchemas.from_addons(
        [
            addon(public={"query": [HelloQuery]}),
            addon(public={"query": [WorldQuery]}),
        ]
    ).build("public")

    result = schema.execute_sync("{ hello world }")

    assert result.errors is None
    assert result.data == {"hello": "hi", "world": "world"}
    assert isinstance(schema, AngeeSchema)


def test_build_schema_installs_universal_rebac_extensions() -> None:
    """REBAC brackets every schema while addon extensions keep their slot."""

    schema = GraphQLSchemas.from_addons(
        [
            addon(
                public={
                    "query": [HelloQuery],
                    "extensions": [CustomExtension],
                }
            )
        ]
    ).build("public")

    assert schema.extensions == (
        RebacExtension,
        CustomExtension,
        RebacDjangoOptimizerExtension,
    )


def test_denial_errors_get_graphql_codes() -> None:
    """REBAC denials surface with stable GraphQL error codes."""

    schema = GraphQLSchemas.from_addons([addon(public={"query": [DenialQuery]})]).build("public")

    missing_actor = schema.execute_sync("{ missingActor }")
    denied = schema.execute_sync("{ permissionDenied }")

    assert missing_actor.errors is not None
    assert denied.errors is not None
    missing_actor_extensions = missing_actor.errors[0].extensions
    denied_extensions = denied.errors[0].extensions
    assert missing_actor_extensions is not None
    assert denied_extensions is not None
    assert missing_actor_extensions["code"] == "UNAUTHENTICATED"
    assert denied_extensions["code"] == "PERMISSION_DENIED"


def test_graphql_identity_exports_relay_node_and_connection() -> None:
    """The framework exposes one relay node and cursor connection seam."""

    from strawberry_django.relay import DjangoCursorConnection

    from angee.graphql.node import AngeeConnection as Connection
    from angee.graphql.node import AngeeNode

    assert issubclass(AngeeNode, strawberry.relay.Node)
    assert issubclass(Connection, DjangoCursorConnection)


def test_rebac_graphql_types_require_rebac_default_manager() -> None:
    """GraphQL-exposed REBAC models fail fast without the library manager."""

    assert isinstance(ManagedThing._default_manager, RebacManager)
    GraphQLSchemas.from_addons(
        [
            addon(
                public={
                    "query": [HelloQuery],
                    "types": [ManagedThingType],
                }
            )
        ]
    ).build("public")

    with pytest.raises(ImproperlyConfigured, match="RebacManager"):
        GraphQLSchemas.from_addons(
            [
                addon(
                    public={
                        "query": [HelloQuery],
                        "types": [UnmanagedThingType],
                    }
                )
            ]
        ).build("public")


def test_build_schema_includes_mutation_root() -> None:
    """A mutation bucket becomes the schema mutation root."""

    schema = GraphQLSchemas.from_addons([addon(public={"query": [HelloQuery], "mutation": [PingMutation]})]).build(
        "public"
    )

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


def test_relay_surface_shared_across_named_schemas_renders() -> None:
    """A relay node field reused in two schemas keeps independent fields.

    Relay field extensions mutate their field in place when a schema is
    built; a surface contributed to more than one named schema must not hand
    the same field object to two builds.
    """

    rendered = GraphQLSchemas.from_addons(
        [addon(public={"query": [NodeQuery]}, console={"query": [NodeQuery]})]
    ).render_sdl()

    assert "thing(" in rendered["public"]
    assert "thing(" in rendered["console"]


def test_build_schema_unknown_name_lists_available() -> None:
    """An unknown schema name reports the names that do exist."""

    with pytest.raises(ImproperlyConfigured, match="available schemas"):
        GraphQLSchemas.from_addons([addon(public={"query": [HelloQuery]})]).build("console")


def test_build_schema_requires_query_root() -> None:
    """A schema without any query contribution fails fast."""

    with pytest.raises(ImproperlyConfigured, match="no query root"):
        GraphQLSchemas.from_addons([addon(public={"mutation": [PingMutation]})]).build("public")


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
