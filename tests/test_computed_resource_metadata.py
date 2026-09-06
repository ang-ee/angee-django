"""Stage 0: ``_finalize_data_resource`` supports computed (non-model) resources.

A computed resource has no Django model — it passes ``model=None`` and a dotted
``app.model`` label. The model handle is ``{"wire": False}`` so the serialized
payload is identical to a model-backed resource.
"""

from __future__ import annotations

import dataclasses
import re

import pytest
import strawberry
from django.core.exceptions import ImproperlyConfigured
from strawberry_django_hasura import hasura_config

from angee.data.metadata import (
    DataResourceRoots,
    DataResourceSubtitleMetadata,
    DataResourceTypeNames,
    serialize_data_resources,
)
from angee.graphql.data.metadata import (
    DataResourceContribution,
    DataResourcePolicy,
    attach_data_resource_contribution,
)
from angee.graphql.data.metadata import (
    _finalize_data_resource as _project_final_data_resource,
)
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import SchemaAddon


@strawberry.type
class SubtitleBodyType:
    """Nested projection used by subtitle selection-path tests."""

    word_count: int


@strawberry.type
class SubtitlePageType:
    """Computed projection used by subtitle selection-path tests."""

    created_at: str
    published_at: str
    title: str
    markdown: SubtitleBodyType


@strawberry.type
class ComputedMetadataProbeQuery:
    """Query root anchoring final-schema computed metadata fixtures."""

    ready: bool = True


def _finalize_data_resource(
    *,
    node_type: type | None = None,
    type_names: DataResourceTypeNames,
    **kwargs: object,
) -> object:
    """Project a focused fixture through a real graphql-core schema."""

    active_type_names = type_names
    if node_type is not None and type_names.node is None:
        from angee.graphql.data.metadata import resource_type_name

        active_type_names = dataclasses.replace(
            type_names,
            node=resource_type_name(node_type),
        )
    schema = strawberry.Schema(
        query=ComputedMetadataProbeQuery,
        types=[] if node_type is None else [node_type],
        config=hasura_config(),
    )
    return _project_final_data_resource(
        graphql_schema=schema._schema,
        type_names=active_type_names,
        **kwargs,
    )


def test_computed_resource_metadata_is_model_optional() -> None:
    """A computed resource builds metadata with ``model=None`` and a dotted label."""

    @strawberry.type(name="PlatformAddon")
    class PlatformAddonType:
        id: strawberry.ID
        label: str
        model_count: int

    metadata = _finalize_data_resource(
        model=None,
        model_label="platform.addon",
        node_type=PlatformAddonType,
        roots=DataResourceRoots(
            list_name="platform_addons",
            aggregate_name="platform_addons_aggregate",
        ),
        type_names=DataResourceTypeNames(
            query="platform_addons_Query",
            node="PlatformAddon",
            filter="platform_addons_bool_exp",
            order="platform_addons_order_by",
        ),
        capabilities=("list", "aggregate"),
        filter_fields=("id", "label"),
        order_fields=("label",),
    )

    assert metadata.model is None
    assert metadata.model_label == "platform.addon"
    assert (metadata.app_label, metadata.model_name) == ("platform", "addon")
    assert metadata.roots.list_name == "platform_addons"
    assert metadata.record_representation == "label"
    # Fields derive from the node surface even with no Django model behind it.
    field_names = {field.name for field in metadata.fields}
    assert {"id", "label", "model_count"} <= field_names

    [wire] = serialize_data_resources((metadata,), schema_name="console")
    assert "model" not in wire  # the Python model handle never reaches the wire
    assert wire["modelLabel"] == "platform.addon"
    assert wire["recordRepresentation"] == "label"
    assert wire["roots"]["list"] == "platform_addons"


def test_resource_metadata_row_model_defaults_to_server() -> None:
    """A resource defaults to the server row model and emits it as ``rowModel``."""

    metadata = _finalize_data_resource(
        model=None,
        model_label="platform.addon",
        roots=DataResourceRoots(list_name="platform_addons"),
        type_names=DataResourceTypeNames(),
        capabilities=("list",),
    )

    assert metadata.row_model == "server"
    [wire] = serialize_data_resources((metadata,), schema_name="console")
    assert wire["rowModel"] == "server"


def test_resource_metadata_row_model_client_reaches_wire() -> None:
    """A computed resource marks itself ``client`` on the wire."""

    metadata = _finalize_data_resource(
        model=None,
        model_label="platform.addon",
        roots=DataResourceRoots(list_name="platform_addons"),
        type_names=DataResourceTypeNames(),
        capabilities=("list",),
        row_model="client",
    )

    assert metadata.row_model == "client"
    [wire] = serialize_data_resources((metadata,), schema_name="console")
    assert wire["rowModel"] == "client"


def test_computed_resource_metadata_requires_label_without_model() -> None:
    """Without a model, the dotted ``model_label`` is mandatory."""

    with pytest.raises(ImproperlyConfigured):
        _finalize_data_resource(
            model=None,
            roots=DataResourceRoots(list_name="x"),
            type_names=DataResourceTypeNames(),
            capabilities=("list",),
        )


def test_resource_subtitle_contributions_compose_by_semantic_fact() -> None:
    """Distinct subtitle facts compose while each semantic slot remains singular."""

    @strawberry.type
    class CreatedQuery:
        created_marker: str

    @strawberry.type
    class WordsQuery:
        words_marker: str

    attach_data_resource_contribution(
        CreatedQuery,
        DataResourceContribution(
            model=None,
            model_label="knowledge.page",
            roots=DataResourceRoots(list_name="pages"),
            type_names=DataResourceTypeNames(node="SubtitlePageType"),
            capabilities=("list",),
            policy=DataResourcePolicy(
                subtitle=DataResourceSubtitleMetadata(created="created_at")
            ),
        ),
    )
    attach_data_resource_contribution(
        WordsQuery,
        DataResourceContribution(
            model=None,
            model_label="knowledge.page",
            roots=DataResourceRoots(detail_name="pages_by_pk"),
            type_names=DataResourceTypeNames(node="SubtitlePageType"),
            capabilities=("detail",),
            policy=DataResourcePolicy(
                subtitle=DataResourceSubtitleMetadata(word_count="markdown.word_count")
            ),
        ),
    )
    [merged] = GraphQLSchemas(
        [
            SchemaAddon(
                {
                    "public": {
                        "query": [CreatedQuery, WordsQuery],
                        "types": [SubtitlePageType],
                    }
                }
            )
        ]
    ).build("public").angee_resources

    assert merged.subtitle == DataResourceSubtitleMetadata(
        created="created_at",
        word_count="markdown.word_count",
    )


def test_resource_subtitle_collision_fails_fast() -> None:
    """Conflicting owners of one semantic subtitle fact cannot silently win."""

    @strawberry.type(name="CreatedSubtitleContribution")
    class CreatedSubtitleContribution:
        created_marker: str

    @strawberry.type(name="PublishedSubtitleContribution")
    class PublishedSubtitleContribution:
        published_marker: str

    def contribution(path: str, surface: type) -> None:
        attach_data_resource_contribution(
            surface,
            DataResourceContribution(
                model=None,
                model_label="knowledge.page",
                roots=DataResourceRoots(),
                type_names=DataResourceTypeNames(node="SubtitlePageType"),
                capabilities=(),
                policy=DataResourcePolicy(
                    subtitle=DataResourceSubtitleMetadata(created=path)
                ),
            ),
        )

    with pytest.raises(
        ImproperlyConfigured,
        match=(
            "conflicting subtitle.created: 'created_at' from CreatedSubtitleContribution "
            "and 'published_at' from PublishedSubtitleContribution"
        ),
    ):
        contribution("created_at", CreatedSubtitleContribution)
        contribution("published_at", PublishedSubtitleContribution)
        GraphQLSchemas(
            [
                SchemaAddon(
                    {
                        "public": {
                            "query": [CreatedSubtitleContribution, PublishedSubtitleContribution],
                            "types": [SubtitlePageType],
                        }
                    }
                )
            ]
        ).build("public")


def test_resource_row_model_collision_names_both_contributors() -> None:
    """The row-model singleton reports both contributing schema surfaces."""

    @strawberry.type(name="ServerRowsContribution")
    class ServerRowsContribution:
        server_marker: str

    @strawberry.type(name="ClientRowsContribution")
    class ClientRowsContribution:
        client_marker: str

    def contribution(row_model: str, surface: type) -> None:
        attach_data_resource_contribution(
            surface,
            DataResourceContribution(
                model=None,
                model_label="platform.addon",
                roots=DataResourceRoots(),
                type_names=DataResourceTypeNames(),
                capabilities=(),
                policy=DataResourcePolicy(row_model=row_model),
            ),
        )

    with pytest.raises(
        ImproperlyConfigured,
        match=(
            "conflicting row_model: 'server' from ServerRowsContribution "
            "and 'client' from ClientRowsContribution"
        ),
    ):
        contribution("server", ServerRowsContribution)
        contribution("client", ClientRowsContribution)
        GraphQLSchemas(
            [
                SchemaAddon(
                    {
                        "public": {
                            "query": [ServerRowsContribution, ClientRowsContribution],
                        }
                    }
                )
            ]
        ).build("public")


def test_resource_policy_empty_allowlist_conflicts_with_nonempty_owner() -> None:
    """An explicit empty policy is a real declaration, not an abstention."""

    @strawberry.type
    class EmptyPolicyQuery:
        empty_marker: str

    @strawberry.type
    class NamedPolicyQuery:
        named_marker: str

    for surface, fields in (
        (EmptyPolicyQuery, ()),
        (NamedPolicyQuery, ("title",)),
    ):
        attach_data_resource_contribution(
            surface,
            DataResourceContribution(
                model=None,
                model_label="knowledge.page",
                type_names=DataResourceTypeNames(node="SubtitlePageType"),
                policy=DataResourcePolicy(filter_fields=fields),
            ),
        )

    with pytest.raises(
        ImproperlyConfigured,
        match="conflicting filter_fields from EmptyPolicyQuery and NamedPolicyQuery",
    ):
        GraphQLSchemas(
            [
                SchemaAddon(
                    {
                        "public": {
                            "query": [EmptyPolicyQuery, NamedPolicyQuery],
                            "types": [SubtitlePageType],
                        }
                    }
                )
            ]
        ).build("public")


def test_resource_subtitle_rejects_malformed_selection_path() -> None:
    """Subtitle declarations use GraphQL dotted selection-path grammar."""

    with pytest.raises(ImproperlyConfigured, match="invalid subtitle.word_count selection path"):
        _finalize_data_resource(
            model=None,
            model_label="knowledge.page",
            node_type=SubtitlePageType,
            roots=DataResourceRoots(),
            type_names=DataResourceTypeNames(),
            capabilities=(),
            subtitle=DataResourceSubtitleMetadata(word_count="markdown..word_count"),
        )


@pytest.mark.parametrize(
    "path",
    ("missing", "markdown.missing"),
)
def test_resource_subtitle_rejects_unknown_selection_path(path: str) -> None:
    """Flat and nested subtitle paths must resolve against the node projection."""

    with pytest.raises(
        ImproperlyConfigured,
        match=rf"knowledge\.page.*{re.escape(path)}",
    ):
        _finalize_data_resource(
            model=None,
            model_label="knowledge.page",
            node_type=SubtitlePageType,
            roots=DataResourceRoots(),
            type_names=DataResourceTypeNames(),
            capabilities=(),
            subtitle=DataResourceSubtitleMetadata(word_count=path),
        )


def test_resource_subtitle_rejects_scalar_mid_path() -> None:
    """A dotted subtitle path cannot descend through a projected scalar."""

    with pytest.raises(
        ImproperlyConfigured,
        match=r"knowledge\.page.*title\.word_count.*non-object",
    ):
        _finalize_data_resource(
            model=None,
            model_label="knowledge.page",
            node_type=SubtitlePageType,
            roots=DataResourceRoots(),
            type_names=DataResourceTypeNames(),
            capabilities=(),
            subtitle=DataResourceSubtitleMetadata(word_count="title.word_count"),
        )
