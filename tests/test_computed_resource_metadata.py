"""Stage 0: ``make_data_resource_metadata`` supports computed (non-model) resources.

A computed resource has no Django model — it passes ``model=None`` and a dotted
``app.model`` label. The model handle is ``{"wire": False}`` so the serialized
payload is identical to a model-backed resource.
"""

from __future__ import annotations

import pytest
import strawberry
from django.core.exceptions import ImproperlyConfigured

from angee.graphql.data.metadata import (
    DataResourceRoots,
    DataResourceTypeNames,
    make_data_resource_metadata,
    serialize_data_resources,
)


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
    # Fields derive from the node surface even with no Django model behind it.
    field_names = {field.name for field in metadata.fields}
    assert {"id", "label", "modelCount"} <= field_names

    [wire] = serialize_data_resources((metadata,), schema_name="console")
    assert "model" not in wire  # the Python model handle never reaches the wire
    assert wire["modelLabel"] == "platform.addon"
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
