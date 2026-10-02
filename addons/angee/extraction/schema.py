"""Immutable evidence reads composed through the framework resource owner."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db.models import Prefetch
from strawberry import auto
from strawberry.scalars import JSON

from angee.base.identity import public_id_for
from angee.base.scoping import read_scoped_queryset
from angee.graphql.data import hasura_model_resource
from angee.graphql.ids import PublicID, optional_public_id
from angee.graphql.node import AngeeNode
from angee.graphql.relations import RecordReferenceNode, actor_scoped_to_many, actor_scoped_to_one
from angee.iam.permissions import request_from_info
from angee.storage.schema import FileType

Extraction = apps.get_model("extraction.Extraction")
ExtractionSource = apps.get_model("extraction.ExtractionSource")
ExtractionPage = apps.get_model("extraction.ExtractionPage")
ExtractionPart = apps.get_model("extraction.ExtractionPart")


def _readable_target(field: str) -> Callable[[strawberry.Info], Prefetch]:
    """Batch only the viewer-readable target behind one extraction label."""

    def prefetch(info: strawberry.Info) -> Prefetch:
        model = apps.get_model("storage.File" if field == "file" else "messaging.Message")
        readable = read_scoped_queryset(model, request_from_info(info).user)
        return Prefetch(field, queryset=readable, to_attr=f"_readable_{field}")

    return prefetch


@strawberry_django.type(Extraction)
class ExtractionType(AngeeNode):
    """One exact retained revision and its separately authorized source records."""

    @strawberry_django.field(
        only=["file_id", "message_id"], prefetch_related=[_readable_target("file"), _readable_target("message")],
    )
    def display_name(self) -> str:
        """Use the target's label only while the viewer can read that record."""
        row = cast(Any, self)
        target = getattr(row, "_readable_file" if row.file_id is not None else "_readable_message", None)
        return str(target) if target is not None else str(row._meta.verbose_name)

    inference_configured: bool = strawberry_django.field(only=["model_id"])
    revision: auto
    schema_id: auto
    schema_digest: auto
    profile: auto
    profile_config: JSON
    schema: JSON
    result: JSON | None
    outcome: JSON | None
    document_map: JSON | None
    retired_identities: JSON | None
    created_at: auto
    sources: list[ExtractionSourceType] = actor_scoped_to_many("sources")
    pages: list[ExtractionPageType] = actor_scoped_to_many("pages")
    parts: list[ExtractionPartType] = actor_scoped_to_many("parts")
    record_model_label: str = strawberry_django.field(only=["file_id", "message_id"])
    record_public_id: str = strawberry_django.field(only=["file_id", "message_id"])

    @strawberry_django.field(only=["model_id"])
    def model_id(self) -> PublicID | None:
        """Identify the mapping deployment through its native public-ID owner."""
        return optional_public_id(public_id_for(apps.get_model("agents.InferenceModel"), cast(Any, self).model_id))

    @strawberry_django.field(only=["recognition_model_id"])
    def recognition_model_id(self) -> PublicID | None:
        """Identify the recognition deployment without loading its private configuration."""
        return optional_public_id(public_id_for(
            apps.get_model("agents.InferenceModel"), cast(Any, self).recognition_model_id,
        ))


@strawberry_django.type(ExtractionSource)
class ExtractionSourceType(RecordReferenceNode):
    """The retained source identity; linked bytes retain storage authorization."""

    extraction: ExtractionType | None = actor_scoped_to_one("extraction")
    file: FileType | None = actor_scoped_to_one("file")
    position: auto
    content_hash: auto
    mime_type: auto

    @strawberry_django.field(only=["message_part_id"])
    def message_part_id(self) -> PublicID | None:
        """Project the retained part only while its current record remains readable."""
        return RecordReferenceNode.reference_id(self) if cast(Any, self).message_part_id else None


@strawberry_django.type(ExtractionPage)
class ExtractionPageType(AngeeNode):
    """Page dimensions and its independently authorized raster."""

    extraction: ExtractionType | None = actor_scoped_to_one("extraction")
    source: ExtractionSourceType | None = actor_scoped_to_one("source")
    carrier_file: FileType | None = actor_scoped_to_one("carrier_file")
    position: auto
    source_page: auto
    width: auto
    height: auto
    dpi: auto
    duration_ms: auto
    provider_metadata: JSON


@strawberry_django.type(ExtractionPart)
class ExtractionPartType(AngeeNode):
    """A retained carrier, readable through its evidence revision's policy."""

    extraction: ExtractionType | None = actor_scoped_to_one("extraction")
    source: ExtractionSourceType | None = actor_scoped_to_one("source")
    position: auto
    source_page: auto
    mime_type: auto
    kind: auto
    method: auto
    content_hash: auto
    metadata: JSON
    width: auto
    height: auto
    dpi: auto
    duration_ms: auto

    @strawberry_django.field(only=["carrier_file_id"])
    def value(self) -> JSON:
        """Read part content from its retained storage carrier."""
        return cast(Any, self).document_part().value


_EXTRACTIONS = hasura_model_resource(
    ExtractionType,
    model=Extraction,
    filterable=["id", "schema_id", "revision"],
    sortable=["created_at", "revision"],
    aggregatable=["id"],
    groupable=["schema_id"],
    insert=False,
    update=False,
    delete=False,
)
_SOURCES = hasura_model_resource(
    ExtractionSourceType,
    model=ExtractionSource,
    filterable=["id", "extraction", "file"],
    sortable=["position"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
)
_PAGES = hasura_model_resource(
    ExtractionPageType,
    model=ExtractionPage,
    filterable=["id", "extraction", "source"],
    sortable=["position"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
)
_PARTS = hasura_model_resource(
    ExtractionPartType,
    model=ExtractionPart,
    filterable=["id", "extraction", "source", "kind"],
    sortable=["position"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
)
_RESOURCES = (_EXTRACTIONS, _SOURCES, _PAGES, _PARTS)
schemas = {
    "console": {
        "query": [resource.query for resource in _RESOURCES],
        "types": [
            ExtractionType,
            ExtractionSourceType,
            ExtractionPageType,
            ExtractionPartType,
            *(type_ for resource in _RESOURCES for type_ in resource.types),
        ],
    }
}
