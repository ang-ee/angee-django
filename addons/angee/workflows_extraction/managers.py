"""Authorized immutable retention and atomic lineage allocation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from rebac import system_context, to_subject_ref
from referencing import Registry
from referencing.exceptions import Unresolvable

from angee.base.actors import actor_user_id
from angee.base.mixins import AppendOnlyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.refs import record_ref_for
from angee.base.scoping import read_scoped_queryset
from angee.base.serialization import canonical_json_sha256
from angee.workflows_extraction.contracts import (
    CorrectionBinding,
    DocumentPart,
    DocumentResult,
    DocumentSource,
    ExtractionPartKind,
    PageImage,
    PageResult,
    PipelineError,
)
from angee.workflows_extraction.enums import ExtractionErrorCode
from angee.workflows_extraction.pointers import implicit_identity_correspondence, json_pointer_value, result_selectors
from angee.workflows_extraction.profiles import authored_profile_config


class StaleExtraction(ValidationError):
    """The requested revision is no longer the lineage head."""


class EvidenceQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet[Any]):
    """Close generic insertion as well as edits and deletion."""

    def validate_insert(self) -> None:
        raise ValueError("Extraction evidence can only be inserted by its retention owner.")


EvidenceManager: Any = AngeeManager.from_queryset(EvidenceQuerySet)


class EvidenceSystemManager(EvidenceManager):
    """Guarded base reads for Django relations and native permission traversal."""

    def get_queryset(self) -> EvidenceQuerySet:
        return super().get_queryset().system_context(reason="extraction retained relation read")


def _authorize(record: Any, actor: Any) -> None:
    readable = read_scoped_queryset(type(record), actor)
    if actor is None or record.pk is None or readable is None or not readable.filter(pk=record.pk).exists():
        raise PermissionDenied("Read access to extraction evidence and its target is required.")


def _contains_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_contains_nul(key) or _contains_nul(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_nul(item) for item in value)
    return False


class ExtractionManager(EvidenceManager):
    """Retain evidence after actor, schema, source and identity validation."""

    def authorized_document_sources(self, extraction: Any, *, actor: Any) -> tuple[DocumentSource, ...]:
        """Authorize retained inputs before disclosing their carriers to a provider."""
        _authorize(extraction, actor)
        sources = extraction.document_sources()
        for source in sources:
            _authorize(source.file if source.file is not None else source.message_part, actor)
        return sources

    def retain_result(
        self,
        *,
        sources: Sequence[DocumentSource],
        result: DocumentResult,
        target: Any,
        actor: Any,
        profile: str,
        schema: dict[str, Any],
        request_key: str,
        profile_config: Mapping[str, Any] | None = None,
        pages: Sequence[PageImage] = (),
        page_results: Sequence[PageResult] = (),
        model: Any = None,
        recognition_model: Any = None,
        base: Any = None,
        error_code: str = "",
        failure: PipelineError | None = None,
        identity_mapping: Mapping[str, str] | None = None,
        retired_identities: Mapping[str, str] | None = None,
    ) -> Any:
        """Allocate or reuse a complete immutable revision in one transaction.

        ``base`` pins the current head and supplies retained sources/pages.
        Exact request retries return their revision even after head advances.
        """
        _authorize(target, actor)
        if not request_key:
            raise ValidationError("Retention requires a stable request key.")
        if base is not None:
            _authorize(base, actor)
            base.require_target(target)
            retained_sources = base.document_sources()
            if sources and [(s.file, s.message_part, s.content_hash) for s in sources] != [
                (s.file, s.message_part, s.content_hash) for s in retained_sources
            ]:
                raise ValidationError("A retained base owns its source snapshot.")
            if pages or page_results:
                raise ValidationError("A retained base already owns its pages.")
            if canonical_json_sha256(
                [dict(asdict(part), metadata=part.metadata or {}) for part in result.parts]
            ) != canonical_json_sha256([asdict(part) for part in base.document_parts()]):
                raise ValidationError("Inference cannot replace retained evidence parts.")
            sources = retained_sources
        if not sources or [s.source_position for s in sources] != list(range(len(sources))):
            raise ValidationError("Evidence requires contiguous ordered sources.")
        for source in sources:
            if (source.file is None) == (source.message_part is None):
                raise ValidationError("A source requires exactly one file or message part.")
            _authorize(source.file if source.file is not None else source.message_part, actor)
            row = source.file if source.file is not None else source.message_part
            assert row is not None
            digest = row.content_hash if source.file is not None else row.fragment.hash
            if base is None and source.content_hash != str(digest):
                raise ValidationError("The source content identity differs from its retained record.")
        for candidate in (model, recognition_model):
            if candidate is not None:
                _authorize(candidate, actor)
        roles = set(result.used_model_roles)
        if not roles <= {"mapping", "recognition"}:
            raise ValidationError("Extraction reported an unsupported model role.")
        if ("mapping" in roles and model is None) or ("recognition" in roles and recognition_model is None):
            raise ValidationError("Extraction reported use of an unconfigured inference model.")
        profile_type = self.model.impl_field("profile").resolve_class(profile)
        config = authored_profile_config(profile_config or {})
        if "evidence_layout" in config and config["evidence_layout"] != profile_type.evidence_layout:
            raise ValidationError("The selected profile owns its evidence layout.")
        config["evidence_layout"] = profile_type.evidence_layout
        schema_id = schema.get("$id") or schema.get("x-version")
        if schema.get("type") != "object" or not isinstance(result.value, dict) or not isinstance(result.claims, dict):
            raise ValidationError("Extraction schema, result and claims must be objects.")
        if not isinstance(schema_id, str) or not schema_id:
            raise ValidationError("An extraction schema requires a stable $id or x-version.")
        try:
            Draft202012Validator.check_schema(schema)
        except (SchemaError, Unresolvable) as error:
            raise ValidationError("Extraction requires a valid locally resolved schema.") from error
        if len(pages) != len(page_results):
            raise ValidationError("Every page requires exactly one retained result.")
        for part in result.parts:
            if not isinstance(part, DocumentPart) or not 0 <= part.source_position < len(sources):
                raise ValidationError("A part names an absent source.")
            if part.kind not in ExtractionPartKind.values:
                raise ValidationError("A part has an unsupported carrier kind.")
        for pointer, claims in result.claims.items():
            try:
                json_pointer_value(result.value, pointer)
            except (KeyError, ValueError) as error:
                raise ValidationError("A claim names an absent fact.") from error
            if not isinstance(claims, list) or any(
                not isinstance(c, dict)
                or type(c.get("part_position")) is not int
                or not 0 <= c["part_position"] < len(result.parts)
                for c in claims
            ):
                raise ValidationError("A claim names an absent evidence part.")
            for claim in claims:
                text = result.parts[claim["part_position"]].value
                if isinstance(text, str) and (
                    type(claim.get("start")) is not int
                    or type(claim.get("end")) is not int
                    or not 0 <= claim["start"] < claim["end"] <= len(text)
                ):
                    raise ValidationError("A text claim requires a valid retained span.")
        source_values = [
            {
                "position": s.source_position,
                "file_id": s.file.pk if s.file is not None else None,
                "message_part_id": s.message_part.pk if s.message_part is not None else None,
                "content_hash": s.content_hash,
                "mime_type": s.mime_type,
            }
            for s in sources
        ]
        reference = record_ref_for(target)
        lineage_key = canonical_json_sha256(
            {
                "original_source": reference.public_id
                if reference.model_label == "storage.File"
                and any(s.file is not None and s.file.pk == target.pk for s in sources)
                else None,
                "target": {
                    "model_label": reference.model_label,
                    "object_id": str(reference.object_id),
                    "resource_type": reference.resource_type,
                },
            }
        )
        if base is not None and (
            base.lineage_key != lineage_key
            or base.schema_digest != canonical_json_sha256(schema)
            or str(base.profile) != profile
        ):
            raise ValidationError("The retained inference base has a different evidence contract.")
        metadata = dict(result.provider_metadata or {})
        if failure is not None:
            metadata["failure"] = {"stage": failure.stage, "code": failure.code}
            error_code = error_code or failure.code or "pipeline_failed"
        provenance = {
            "claims": result.claims,
            "used_model_roles": list(result.used_model_roles),
            "unresolved_reasons": list(metadata.get("unresolved_reasons", ())),
            "document": metadata,
        }
        page_values: list[dict[str, Any]] = [
            {
                "source_position": p.source_position,
                "source_page": p.page_position,
                "width": p.width,
                "height": p.height,
                "dpi": p.dpi,
                "duration_ms": r.duration_ms,
                "result": r.value,
                "provider_metadata": r.provider_metadata or {},
            }
            for p, r in zip(pages, page_results, strict=True)
        ]
        file_model = self.model._meta.apps.get_model("storage.File")
        readable_files = read_scoped_queryset(file_model, actor)
        for page in page_values:
            carriers = page["provider_metadata"].get("carrier_files", [])
            if not isinstance(carriers, list) or len(carriers) > 1:
                raise ValidationError("Each prepared page has at most one raster carrier.")
            if carriers:
                carrier = readable_files.from_public_id(carriers[0]) if readable_files is not None else None
                if carrier is None:
                    raise PermissionDenied("The raster carrier is absent or inaccessible.")
                page["carrier_file_id"] = carrier.pk
        part_values = [dict(asdict(part), metadata=part.metadata or {}) for part in result.parts]
        request = {
            "lineage": lineage_key,
            "sources": source_values,
            "schema": schema,
            "profile": profile,
            "config": config,
            "result": result.value,
            "provenance": provenance,
            "pages": page_values,
            "parts": part_values,
            "base": base.pk if base else None,
            "mapping": dict(identity_mapping or {}),
            "retired": dict(retired_identities or {}),
            "error": error_code,
            "model": model.pk if model else None,
            "recognition_model": recognition_model.pk if recognition_model else None,
        }
        try:
            json.dumps(request, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValidationError("Retained evidence must contain finite JSON values.") from error
        if _contains_nul(request):
            raise ValidationError("Retained evidence cannot contain null characters.")
        request_digest = canonical_json_sha256(request)
        provenance["request_digest"] = request_digest
        reuse_key = canonical_json_sha256({"lineage": lineage_key, "request": request_key})
        values = dict(
            lineage_key=lineage_key,
            reuse_key=reuse_key,
            status="failed" if error_code else "succeeded",
            error_code=error_code,
            schema_id=schema_id,
            schema_digest=canonical_json_sha256(schema),
            schema=schema,
            profile=profile,
            profile_config=config,
            result=result.value,
            provenance=provenance,
            target=target,
            model=model,
            recognition_model=recognition_model,
            created_by_id=actor_user_id(to_subject_ref(actor)),
        )
        return self._retain(values, source_values, page_values, part_values, base, identity_mapping, retired_identities)

    def _retain(
        self,
        values: dict[str, Any],
        sources: list[dict[str, Any]],
        pages: list[dict[str, Any]],
        parts: list[dict[str, Any]],
        base: Any,
        mapping: Any,
        retirement: Any,
    ) -> Any:
        lineage_model = self.model._meta.apps.get_model("workflows_extraction.ExtractionLineage")
        with transaction.atomic(), system_context(reason="retain authorized extraction snapshot"):
            lineage = lineage_model.objects.filter(pk=values["lineage_key"]).first()
            if lineage is None:
                try:
                    with transaction.atomic():
                        lineage = lineage_model(key=values["lineage_key"])
                        lineage.retain()
                except IntegrityError:
                    lineage = lineage_model.objects.get(pk=values["lineage_key"])
            lineage = lineage_model.objects.lock_if_supported().get(pk=lineage.pk)
            existing = self.model._base_manager.filter(reuse_key=values["reuse_key"]).first()
            if existing is not None:
                if existing.provenance.get("request_digest") != values["provenance"]["request_digest"]:
                    raise ValidationError("This request key already owns different evidence.")
                return existing
            previous = lineage.head
            if values["error_code"] and (mapping or retirement):
                raise ValidationError("A retained source hold cannot remap or retire identities.")
            if base is not None and (previous is None or previous.pk != base.pk):
                raise StaleExtraction("The extraction base is no longer current.")
            authority = previous
            if previous is not None and previous.status != "succeeded" and previous.document_map:
                authority = self._successful_identity_authority(previous)
            document_map: list[dict[str, Any]] = []
            retired: list[dict[str, str]] = []
            if not values["error_code"] or values["result"]:
                try:
                    document_map, retired = self._document_mapping(
                        values["result"], values["profile_config"]["evidence_layout"], authority, mapping, retirement
                    )
                except ValidationError:
                    if previous is None or mapping or retirement:
                        raise
                    values.update(status="failed", error_code=ExtractionErrorCode.IDENTITY_CORRESPONDENCE_REQUIRED)
            if previous is not None and values["error_code"]:
                document_map, retired = previous.document_map, previous.retired_identities
                values["provenance"]["identity_correspondence"] = {
                    "last_known_revision": authority.revision,
                    "expected_base_id": authority.pk,
                }
            if base is not None and authority is not None and not values["error_code"]:
                self._preserve_authority(values, parts, authority, base, document_map, retirement)
            if not values["error_code"]:
                try:
                    validator = Draft202012Validator(
                        values["schema"], format_checker=FormatChecker(), registry=Registry()
                    )
                    if list(validator.iter_errors(values["result"])):
                        raise ValidationError("Extraction output does not match its retained schema.")
                except Unresolvable as error:
                    raise ValidationError("Extraction requires a valid locally resolved schema.") from error
            extraction = self.model(
                revision=previous.revision + 1 if previous else 1,
                document_map=document_map,
                retired_identities=retired,
                **values,
            )
            extraction.retain()
            self._retain_children(extraction, sources, pages, parts, base)
            lineage.advance_head(extraction)
            return extraction

    @staticmethod
    def _preserve_authority(
        values: dict[str, Any],
        parts: list[dict[str, Any]],
        authority: Any,
        base: Any,
        document_map: list[dict[str, Any]],
        retirement: Mapping[str, str] | None,
    ) -> None:
        """Compose fact authority with validated identities and unchanged carriers."""
        retained_parts = tuple(DocumentPart(**part) for part in parts)
        positions = authority.authority_carrier_positions(base)
        mapping = {
            ref["selector"]: ref["identity"] for document in document_map for ref in (document, *document["lines"])
        }
        provenance = values["provenance"]
        result = authority.preserve_authority(
            DocumentResult(
                values["result"], retained_parts, provenance["claims"], provider_metadata=provenance["document"]
            ),
            identity_mapping=mapping,
            retired_identities=retirement,
            claim_part_positions=positions,
        )
        values["result"] = result.value
        provenance.update(
            claims=result.claims,
            document=result.provider_metadata,
            unresolved_reasons=list((result.provider_metadata or {}).get("unresolved_reasons", ())),
            corrections=authority.retained_corrections(
                result.value, identity_mapping=mapping, retired_identities=retirement
            ),
        )

    def _retain_children(
        self,
        extraction: Any,
        sources: list[dict[str, Any]],
        pages: list[dict[str, Any]],
        parts: list[dict[str, Any]],
        base: Any,
    ) -> None:
        registry = self.model._meta.apps
        source_model = registry.get_model("workflows_extraction.ExtractionSource")
        retained = source_model._base_manager.owner_bulk_create(
            [source_model(extraction=extraction, **value) for value in sources]
        )
        by_position = {row.position: row for row in retained}
        if base is not None:
            pages = [
                {
                    "source_position": row.source.position,
                    "source_page": row.source_page,
                    "width": row.width,
                    "height": row.height,
                    "dpi": row.dpi,
                    "duration_ms": row.duration_ms,
                    "result": row.result,
                    "provider_metadata": row.provider_metadata,
                    "carrier_file_id": row.carrier_file_id,
                }
                for row in base.pages.select_related("source").order_by("position")
            ]
        for name, rows in (("ExtractionPage", pages), ("ExtractionPart", parts)):
            child_model = registry.get_model("workflows_extraction", name)
            children = []
            for position, raw in enumerate(rows):
                values = dict(raw)
                source_position = values.pop("source_position")
                if source_position not in by_position:
                    raise ValidationError("A page or part names an absent source.")
                if name == "ExtractionPart":
                    values["metadata"] = values["metadata"] or {}
                    values["claims"] = {
                        pointer: [claim for claim in claims if claim["part_position"] == position]
                        for pointer, claims in extraction.claims.items()
                        if any(claim["part_position"] == position for claim in claims)
                    }
                children.append(
                    child_model(extraction=extraction, source=by_position[source_position], position=position, **values)
                )
            child_model._base_manager.owner_bulk_create(children)

    @staticmethod
    def _document_mapping(
        result: Any, layout: Any, original: Any, identity_mapping: Any, retired_identities: Any
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        requested = result_selectors(result, layout)
        previous = original.document_refs if original else ()
        mapping = dict(identity_mapping or {})
        retirement = dict(retired_identities or {})
        if previous and not mapping:
            mapping = implicit_identity_correspondence(result, layout=layout, original=original) or {}
        old = {ref.identity: ref for ref in previous}
        old_lines = {line.identity for ref in previous for line in ref.lines}
        selectors = {selector for document, lines in requested for selector in (document, *lines)}
        if set(mapping) - selectors:
            raise ValidationError("Identity mapping names an absent selector.")
        carried: set[str] = set()
        rows = []
        for selector, lines in requested:
            identity = mapping.get(selector, "new" if not previous else None)
            if identity == "new":
                identity = uuid4().hex
            elif not isinstance(identity, str) or identity not in old:
                raise ValidationError("Document correspondence requires an existing identity or new.")
            if identity in carried or identity in old_lines:
                raise ValidationError("Document identity is duplicated or has another kind.")
            carried.add(identity)
            prior_lines = {line.identity for line in old[identity].lines} if identity in old else set()
            line_rows = []
            for line in lines:
                line_id = mapping.get(line, "new" if not previous else None)
                if line_id == "new":
                    line_id = uuid4().hex
                elif not isinstance(line_id, str) or line_id not in prior_lines:
                    raise ValidationError("Line correspondence must name an identity in its document or new.")
                if line_id in carried or line_id in old:
                    raise ValidationError("Line identity is duplicated or has another kind.")
                carried.add(line_id)
                line_rows.append({"identity": line_id, "selector": line})
            rows.append({"identity": identity, "selector": selector, "lines": line_rows})
        absent = (set(old) | old_lines) - carried
        if set(retirement) != absent or any(
            not isinstance(reason, str) or not reason.strip() for reason in retirement.values()
        ):
            raise ValidationError("Retired identities require explicit retained reasons.")
        retired = list(original.retired_identities) if original else []
        retired.extend(
            {"identity": identity, "kind": "document" if identity in old else "line", "reason": retirement[identity]}
            for identity in sorted(absent)
        )
        return rows, retired

    def inference_current_head(self, base: Any, *, actor: Any) -> Any:
        """Return this lineage's current actor-readable evidence contract."""
        _authorize(base, actor)
        lineage_model = self.model._meta.apps.get_model("workflows_extraction.ExtractionLineage")
        with system_context(reason="resolve extraction lineage head"):
            head = lineage_model.objects.get(pk=base.lineage_key).head
        if head is None or head.schema_digest != base.schema_digest:
            raise ValidationError("The current evidence contract differs from the base.")
        _authorize(head, actor)
        return head

    def latest_succeeded_identity_authority(self, head: Any, *, actor: Any) -> Any:
        """Resolve the last successful identity authority within one lineage."""
        _authorize(head, actor)
        row = self._successful_identity_authority(head)
        _authorize(row, actor)
        return row

    def _successful_identity_authority(self, head: Any) -> Any:
        """Match copied identities against their successful facts, never a held candidate."""
        row = (
            self.model._base_manager.filter(
                lineage_key=head.lineage_key, revision__lte=head.revision, status="succeeded"
            )
            .order_by("-revision")
            .first()
        )
        if row is None or row.document_map != head.document_map or row.schema_digest != head.schema_digest:
            raise ValidationError("The retained identity authority is unavailable.")
        return row

    def inference_authority_base(self, base: Any, *, actor: Any) -> Any:
        """Resolve a successful base or the successful authority of its source hold."""
        if base.status == "succeeded":
            _authorize(base, actor)
            return base
        return self.latest_succeeded_identity_authority(base, actor=actor)

    def inference_candidate_selectors(self, base: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Expose selectors of a retained correspondence candidate."""
        if not base.awaiting_correspondence:
            raise ValidationError("The evidence is not awaiting correspondence.")
        return result_selectors(base.result, base.profile_config["evidence_layout"])

    def prepare_correction_binding(self, extraction: Any, *, actor: Any) -> tuple[CorrectionBinding, Any]:
        """Freeze readable fact authority and its exact current revision parent."""
        if not isinstance(extraction, self.model) or extraction.pk is None:
            raise ValidationError("Correction authority requires a retained extraction revision.")
        parent = self.inference_current_head(extraction, actor=actor)
        if parent.pk != extraction.pk:
            if not parent.failed_at_inference and (
                not parent.awaiting_correspondence or self.inference_candidate_selectors(parent)
            ):
                raise ValidationError("The correction parent has another retained fact authority.")
            authority = self.latest_succeeded_identity_authority(parent, actor=actor)
            if authority.pk != extraction.pk or parent.retired_identities != extraction.retired_identities:
                raise ValidationError("The correction parent has another retained fact authority.")
        return CorrectionBinding(extraction.reference, parent.reference), parent

    def identity_preserving_pipeline_successor(self, authority: Any, successor: Any, *, actor: Any) -> Any:
        """Validate a later unreviewed revision preserving source and fact identity."""
        _authorize(authority, actor)
        _authorize(successor, actor)
        if (
            authority.status != "succeeded"
            or successor.status != "succeeded"
            or successor.revision <= authority.revision
            or authority.lineage_key != successor.lineage_key
            or authority.schema_digest != successor.schema_digest
            or authority.document_map != successor.document_map
            or successor.corrections
            or authority.retired_identities
            or successor.retired_identities
        ):
            raise ValidationError("The successor changed retained identity or authority.")
        return successor
