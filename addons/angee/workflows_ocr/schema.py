"""Authorized read surfaces for document extraction evidence."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import models
from strawberry import auto
from strawberry.scalars import JSON

from angee.base.scoping import read_scoped_queryset
from angee.graphql.data import hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID, instance_for_id, require_public_id
from angee.graphql.node import AngeeNode
from angee.iam.permissions import session_user

Extraction = apps.get_model("workflows_ocr", "Extraction")
ExtractionSource = apps.get_model("workflows_ocr", "ExtractionSource")
ExtractionPage = apps.get_model("workflows_ocr", "ExtractionPage")
File = apps.get_model("storage", "File")
InferenceModel = apps.get_model("agents", "InferenceModel")


def _read_queryset(model: type[models.Model]):
    def get_queryset(info: strawberry.Info) -> models.QuerySet[Any]:
        scoped = read_scoped_queryset(model, session_user(info), action="read")
        return model.objects.none() if scoped is None else scoped

    return get_queryset


@strawberry_django.type(Extraction)
class ExtractionType(AngeeNode):
    """Safe list projection; raw document values live only on the detail field."""

    revision: auto
    status: auto
    error_code: auto
    schema_id: auto
    schema_digest: auto
    engine: auto
    created_at: auto

    @strawberry_django.field(only=["model_id"])
    def model(self) -> strawberry.ID:
        return require_public_id(InferenceModel, cast(Any, self).model_id)


@strawberry_django.type(ExtractionSource)
class ExtractionSourceType(AngeeNode):
    """Ordered source identity without extracted document values."""

    position: auto
    content_hash: auto

    @strawberry_django.field(only=["file_id"])
    def file(self) -> strawberry.ID:
        return require_public_id(File, cast(Any, self).file_id)


@strawberry_django.type(ExtractionPage)
class ExtractionPageType(AngeeNode):
    """Safe page metrics for evidence lists."""

    position: auto
    source_page: auto
    width: auto
    height: auto
    dpi: auto
    duration_ms: auto


@strawberry.type
class ExtractionPageEvidence:
    """Raw page evidence returned only after extraction authorization."""

    position: int
    source_page: int
    result: JSON


@strawberry.type
class ExtractionEvidence:
    """Authorized raw evidence detail for one extraction revision."""

    extraction: ExtractionType
    result: JSON
    schema: JSON
    provenance: JSON
    sources: list[ExtractionSourceType]
    pages: list[ExtractionPageEvidence]


@strawberry.type
class ExtractionEvidenceQuery:
    """Resolve raw values through the extraction row's native read scope."""

    @strawberry.field
    def extraction_evidence(self, info: strawberry.Info, id: PublicID) -> ExtractionEvidence | None:
        queryset = _read_queryset(Extraction)(info)
        row = instance_for_id(Extraction, id, queryset=queryset)
        if row is None:
            return None
        return ExtractionEvidence(
            extraction=cast(ExtractionType, row),
            result=cast(JSON, row.result),
            schema=cast(JSON, row.schema),
            provenance=cast(JSON, row.provenance),
            sources=list(row.sources.order_by("position")),
            pages=[
                ExtractionPageEvidence(
                    position=page.position,
                    source_page=page.source_page,
                    result=cast(JSON, page.result),
                )
                for page in row.pages.order_by("position")
            ],
        )


_EXTRACTION_RESOURCE = hasura_model_resource(
    ExtractionType,
    model=Extraction,
    name="workflow_ocr_extractions",
    filterable=["id", "status", "schema_id", "engine", "model", "created_at"],
    sortable=["revision", "status", "schema_id", "created_at"],
    aggregatable=["id", "revision"],
    groupable=["status", "schema_id", "engine", "model"],
    insert=False,
    update=False,
    delete=False,
    get_queryset=_read_queryset(Extraction),
    field_id_decode={"model": public_pk_decoder(InferenceModel)},
)
_SOURCE_RESOURCE = hasura_model_resource(
    ExtractionSourceType,
    model=ExtractionSource,
    name="workflow_ocr_extraction_sources",
    filterable=["id", "extraction", "file", "position"],
    sortable=["extraction", "position"],
    aggregatable=["id", "position"],
    groupable=["extraction", "file"],
    insert=False,
    update=False,
    delete=False,
    get_queryset=_read_queryset(ExtractionSource),
    field_id_decode={"extraction": public_pk_decoder(Extraction), "file": public_pk_decoder(File)},
)
_PAGE_RESOURCE = hasura_model_resource(
    ExtractionPageType,
    model=ExtractionPage,
    name="workflow_ocr_extraction_pages",
    filterable=["id", "extraction", "source", "position"],
    sortable=["extraction", "position"],
    aggregatable=["id", "position", "duration_ms"],
    groupable=["extraction", "source"],
    insert=False,
    update=False,
    delete=False,
    get_queryset=_read_queryset(ExtractionPage),
    field_id_decode={
        "extraction": public_pk_decoder(Extraction),
        "source": public_pk_decoder(ExtractionSource),
    },
)

schemas = {
    "console": {
        "query": [
            ExtractionEvidenceQuery,
            _EXTRACTION_RESOURCE.query,
            _SOURCE_RESOURCE.query,
            _PAGE_RESOURCE.query,
        ],
        "types": [
            ExtractionType,
            ExtractionSourceType,
            ExtractionPageType,
            ExtractionPageEvidence,
            ExtractionEvidence,
            *_EXTRACTION_RESOURCE.types,
            *_SOURCE_RESOURCE.types,
            *_PAGE_RESOURCE.types,
        ],
    }
}
