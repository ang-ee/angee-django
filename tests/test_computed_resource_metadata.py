"""Stage 0: ``make_data_resource_metadata`` supports computed (non-model) resources.

A computed resource has no Django model — it passes ``model=None`` and a dotted
``app.model`` label. The model handle is ``{"wire": False}`` so the serialized
payload is identical to a model-backed resource.
"""

from __future__ import annotations

import re

import pytest
import strawberry
from django.core.exceptions import ImproperlyConfigured

from angee.graphql.data.metadata import (
    DataResourceMetadata,
    DataResourceRoots,
    DataResourceSubtitleMetadata,
    DataResourceTypeNames,
    attach_data_resource_metadata,
    data_resource_metadata,
    make_data_resource_metadata,
    merge_data_resources,
    serialize_data_resources,
)


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


def test_computed_resource_metadata_is_model_optional() -> None:
    """A computed resource builds metadata with ``model=None`` and a dotted label."""

    @strawberry.type(name="PlatformAddon")
    class PlatformAddonType:
        id: strawberry.ID
        label: str
        model_count: int

    metadata = make_data_resource_metadata(
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

    metadata = make_data_resource_metadata(
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

    metadata = make_data_resource_metadata(
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
        make_data_resource_metadata(
            model=None,
            roots=DataResourceRoots(list_name="x"),
            type_names=DataResourceTypeNames(),
            capabilities=("list",),
        )


def test_resource_subtitle_contributions_compose_by_semantic_fact() -> None:
    """Distinct subtitle facts compose while each semantic slot remains singular."""

    created = make_data_resource_metadata(
        model=None,
        model_label="knowledge.page",
        node_type=SubtitlePageType,
        roots=DataResourceRoots(list_name="pages"),
        type_names=DataResourceTypeNames(),
        capabilities=("list",),
        subtitle=DataResourceSubtitleMetadata(created="created_at"),
    )
    words = make_data_resource_metadata(
        model=None,
        model_label="knowledge.page",
        node_type=SubtitlePageType,
        roots=DataResourceRoots(detail_name="pages_by_pk"),
        type_names=DataResourceTypeNames(),
        capabilities=("detail",),
        subtitle=DataResourceSubtitleMetadata(word_count="markdown.word_count"),
    )

    [merged] = merge_data_resources((created, words))

    assert merged.subtitle == DataResourceSubtitleMetadata(
        created="created_at",
        word_count="markdown.word_count",
    )


def test_resource_subtitle_collision_fails_fast() -> None:
    """Conflicting owners of one semantic subtitle fact cannot silently win."""

    @strawberry.type(name="CreatedSubtitleContribution")
    class CreatedSubtitleContribution:
        marker: str

    @strawberry.type(name="PublishedSubtitleContribution")
    class PublishedSubtitleContribution:
        marker: str

    def contribution(path: str, surface: type) -> DataResourceMetadata:
        attach_data_resource_metadata(
            surface,
            make_data_resource_metadata(
                model=None,
                model_label="knowledge.page",
                node_type=SubtitlePageType,
                roots=DataResourceRoots(),
                type_names=DataResourceTypeNames(),
                capabilities=(),
                subtitle=DataResourceSubtitleMetadata(created=path),
            ),
        )
        [metadata] = data_resource_metadata(surface)
        return metadata

    with pytest.raises(
        ImproperlyConfigured,
        match=(
            "conflicting subtitle.created: 'created_at' from CreatedSubtitleContribution "
            "and 'published_at' from PublishedSubtitleContribution"
        ),
    ):
        merge_data_resources(
            (
                contribution("created_at", CreatedSubtitleContribution),
                contribution("published_at", PublishedSubtitleContribution),
            )
        )


def test_resource_row_model_collision_names_both_contributors() -> None:
    """The row-model singleton reports both contributing schema surfaces."""

    @strawberry.type(name="ServerRowsContribution")
    class ServerRowsContribution:
        marker: str

    @strawberry.type(name="ClientRowsContribution")
    class ClientRowsContribution:
        marker: str

    def contribution(row_model: str, surface: type) -> DataResourceMetadata:
        attach_data_resource_metadata(
            surface,
            make_data_resource_metadata(
                model=None,
                model_label="platform.addon",
                roots=DataResourceRoots(),
                type_names=DataResourceTypeNames(),
                capabilities=(),
                row_model=row_model,
            ),
        )
        [metadata] = data_resource_metadata(surface)
        return metadata

    with pytest.raises(
        ImproperlyConfigured,
        match=(
            "conflicting row_model: 'server' from ServerRowsContribution "
            "and 'client' from ClientRowsContribution"
        ),
    ):
        merge_data_resources(
            (
                contribution("server", ServerRowsContribution),
                contribution("client", ClientRowsContribution),
            )
        )


def test_resource_subtitle_rejects_malformed_selection_path() -> None:
    """Subtitle declarations use GraphQL dotted selection-path grammar."""

    with pytest.raises(ImproperlyConfigured, match="invalid subtitle.word_count selection path"):
        make_data_resource_metadata(
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
        make_data_resource_metadata(
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
        make_data_resource_metadata(
            model=None,
            model_label="knowledge.page",
            node_type=SubtitlePageType,
            roots=DataResourceRoots(),
            type_names=DataResourceTypeNames(),
            capabilities=(),
            subtitle=DataResourceSubtitleMetadata(word_count="title.word_count"),
        )
