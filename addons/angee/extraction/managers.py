"""Authorized immutable retention and atomic lineage allocation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from rebac import system_context, to_subject_ref

from angee.base.actors import actor_user_id
from angee.base.evidence import EvidenceReference, readable_records
from angee.base.identity import public_id_of
from angee.base.jsonschema import validate
from angee.base.mixins import AppendOnlyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.refs import canonical_record_target, record_ref_for
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.base.serialization import canonical_json_sha256, strip_null_bytes
from angee.decisions.forms import Action
from angee.decisions.states import Verdict
from angee.extraction.acquisition import ExtractionConfig, PageCarrier, PartCarrier, PreparedDocument, prepare_pages
from angee.extraction.contracts import (
    CorrectionBinding,
    DocumentPart,
    DocumentResult,
    DocumentSource,
    ExtractionPartKind,
    PipelineError,
)
from angee.extraction.enums import ExtractionErrorCode, ExtractionRole, ExtractionStatus
from angee.extraction.pointers import json_pointer_value, result_selectors


class StaleExtraction(ValidationError):
    """The requested revision is no longer the lineage head."""


class EvidenceQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet[Any]):
    """Close generic insertion as well as edits and deletion."""

    def validate_insert(self) -> None:
        raise ValueError("Extraction evidence can only be inserted by its retention owner.")


EvidenceManager: Any = AngeeManager.from_queryset(EvidenceQuerySet)


class ExtractionManager(EvidenceManager):
    """Retain evidence after actor, schema, source and identity validation."""

    def prepare_pages(
        self, files: Sequence[Any], message_parts: Sequence[Any], *, profile: Any,
        config: ExtractionConfig, actor: Any, heartbeat: Any,
    ) -> PreparedDocument:
        """Acquire bounded carriers through the extraction domain owner."""
        return prepare_pages(files, message_parts, profile=profile, config=config, actor=actor, heartbeat=heartbeat)

    @staticmethod
    def _require_target_write(target: Any, actor: Any) -> None:
        """Appending evidence and advancing its shared lineage require target write."""
        if actor is None or not target.with_actor(actor).has_access("write"):
            raise PermissionDenied("Write access to the extraction target is required.")

    def authorized_document_sources(self, extraction: Any, *, actor: Any) -> tuple[DocumentSource, ...]:
        """Authorize retained inputs before disclosing their carriers to inference."""
        if actor is None or not (extraction).with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to retained evidence and its sources is required.")
        references = tuple(source.record_ref for source in extraction.sources.order_by("position"))
        readable_records(
            tuple(EvidenceReference(model=ref.model_label, id=ref.public_id) for ref in references),
            (actor,),
        )
        return extraction.document_sources()

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
        pages: Sequence[PageCarrier] = (),
        part_carriers: Sequence[PartCarrier] = (),
        model: Any = None,
        recognition_model: Any = None,
        base: Any = None,
        error_code: str | None = None,
        failure: PipelineError | None = None,
        identity_mapping: Mapping[str, str] | None = None,
        retired_identities: Mapping[str, str] | None = None,
    ) -> Any:
        """Allocate or reuse a complete immutable revision in one transaction.

        ``base`` pins the current head and supplies retained sources/pages.
        Exact request retries return their revision even after head advances.
        """
        if target._meta.label not in {"storage.File", "messaging.Message"}:
            raise ValidationError("Extraction targets must be a file or message.")
        if error_code == "":
            raise ValidationError("An empty failure code is not an outcome.")
        self._require_target_write(target, actor)
        if actor is None or not (target).with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to retained evidence and its sources is required.")
        if not request_key:
            raise ValidationError("Retention requires a stable request key.")
        if base is not None:
            if actor is None or not (base).with_actor(actor).has_access("read"):
                raise PermissionDenied("Read access to retained evidence and its sources is required.")
            base.require_target(target)
            retained_sources = base.document_sources()
            if sources and [(s.file, s.message_part, s.content_hash) for s in sources] != [
                (s.file, s.message_part, s.content_hash) for s in retained_sources
            ]:
                raise ValidationError("A retained base owns its source snapshot.")
            if pages:
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
            if not (
                source.file is not None and target._meta.label == "storage.File" and source.file.pk == target.pk
                or source.file is not None and target._meta.label == "messaging.Message"
                and system_queryset(target.parts.model).filter(message_id=target.pk, file_id=source.file.pk).exists()
                or source.message_part is not None and target._meta.label == "messaging.Message"
                and source.message_part.message_id == target.pk
            ):
                raise ValidationError("Every source must be the extraction target or belongs to the extraction target.")
        readable_records(tuple(source.evidence_reference for source in sources), (actor,))
        for source in sources:
            row = source.record
            digest = row.content_hash if source.file is not None else row.fragment.hash
            if base is None and source.content_hash != str(digest):
                raise ValidationError("The source content identity differs from its retained record.")
        for candidate in (model, recognition_model):
            if candidate is not None:
                if actor is None or not (candidate).with_actor(actor).has_access("read"):
                    raise PermissionDenied("Read access to retained evidence and its sources is required.")
        roles = set(result.used_model_roles)
        if not roles <= set(ExtractionRole):
            raise ValidationError("Extraction reported an unsupported model role.")
        if (ExtractionRole.MAPPING in roles and model is None) or (
            ExtractionRole.RECOGNITION in roles and recognition_model is None
        ):
            raise ValidationError("Extraction reported use of an unconfigured inference model.")
        profile_type = self.model.impl_field("profile").resolve_class(profile)
        if (
            profile_config is not None and "evidence_layout" in profile_config
            and profile_config["evidence_layout"] != profile_type.evidence_layout.model_dump()
        ):
            raise ValidationError("The selected profile owns its evidence layout.")
        config = profile_type.normalize_config(
            {key: value for key, value in (profile_config or {}).items() if key != "evidence_layout"}
        )
        config["evidence_layout"] = profile_type.evidence_layout.model_dump()
        schema_id = profile_type.check_schema(schema)
        if not isinstance(result.value, dict) or not isinstance(result.claims, dict):
            raise ValidationError("Extraction result and claims must be objects.")
        for part in result.parts:
            if not isinstance(part, DocumentPart) or not 0 <= part.source_position < len(sources):
                raise ValidationError("A part names an absent source.")
            if part.kind not in ExtractionPartKind.values:
                raise ValidationError("A part has an unsupported carrier kind.")
        for pointer, claims in result.claims.items():
            try:
                json_pointer_value(result.value, pointer)
            except (KeyError, ValueError):
                raise ValidationError("A claim names an absent fact.") from None
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
        source_values = []
        for source in sources:
            target_ref = canonical_record_target(source.record)
            source_values.append({
                "position": source.source_position,
                "file_id": source.file.pk if source.file is not None else None,
                "message_part_id": source.message_part.pk if source.message_part is not None else None,
                "content_type_id": target_ref.content_type.pk,
                "object_id": target_ref.object_id,
                "content_hash": source.content_hash,
                "mime_type": source.mime_type,
            })
        reference = record_ref_for(target)
        lineage_identity = canonical_json_sha256(
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
            base.lineage.identity != lineage_identity
            or base.schema_digest != canonical_json_sha256(schema)
            or str(base.profile) != profile
        ):
            raise ValidationError("The retained inference base has a different evidence contract.")
        metadata = dict(result.provider_metadata or {})
        if failure is not None:
            error_code = error_code or failure.code or "pipeline_failed"
        outcome: dict[str, Any] = {
            "claims": result.claims,
            "used_model_roles": list(result.used_model_roles),
            "unresolved_reasons": list(metadata.get("unresolved_reasons", ())),
            "document": metadata,
            "kind": ExtractionStatus.FAILED if error_code else ExtractionStatus.SUCCEEDED,
        }
        if error_code is not None:
            outcome["code"] = error_code
            outcome["stage"] = failure.stage if failure is not None else None
        page_values = []
        file_model = self.model._meta.apps.get_model("storage.File")
        readable_files = read_scoped_queryset(file_model, actor)
        for page in pages:
            carrier = None
            if page.image_file_id:
                carrier = readable_files.from_public_id(page.image_file_id) if readable_files is not None else None
                if carrier is None:
                    raise PermissionDenied("The raster carrier is absent or inaccessible.")
                if carrier.content_hash != page.image_digest:
                    raise ValidationError("The raster carrier content identity differs from its retained record.")
            page_values.append(
                {
                    "source_position": page.source_position,
                    "source_page": page.page_position,
                    "width": page.width,
                    "height": page.height,
                    "dpi": page.dpi,
                    "carrier_file_id": carrier.pk if carrier else None,
                }
            )
        part_values = [dict(asdict(part), metadata=part.metadata or {}) for part in result.parts]
        request = {
            "lineage": lineage_identity,
            "sources": source_values,
            "schema": schema,
            "profile": profile,
            "config": config,
            "result": result.value,
            "outcome": outcome,
            "pages": page_values,
            "parts": part_values,
            "base": base.pk if base else None,
            "mapping": dict(identity_mapping or {}),
            "retired": dict(retired_identities or {}),
            "model": model.pk if model else None,
            "recognition_model": recognition_model.pk if recognition_model else None,
        }
        try:
            request_digest = canonical_json_sha256(request)
        except TypeError, ValueError:
            raise ValidationError("Retained evidence must contain finite JSON values.") from None
        outcome["request_digest"] = request_digest
        reuse_key = canonical_json_sha256({"lineage": lineage_identity, "request": request_key})
        values = dict(
            lineage_identity=lineage_identity,
            reuse_key=reuse_key,
            outcome=outcome,
            schema_id=schema_id,
            schema_digest=canonical_json_sha256(schema),
            schema=schema,
            profile=profile,
            profile_config=config,
            result=result.value,
            file=target if target._meta.label == "storage.File" else None,
            message=target if target._meta.label == "messaging.Message" else None,
            model=model,
            recognition_model=recognition_model,
            created_by_id=actor_user_id(to_subject_ref(actor)),
        )
        return self._retain(
            values, source_values, page_values, part_values, base, identity_mapping, retired_identities,
            actor=actor, part_carriers=part_carriers,
        )

    def _retain(
        self,
        values: dict[str, Any],
        sources: list[dict[str, Any]],
        pages: list[dict[str, Any]],
        parts: list[dict[str, Any]],
        base: Any,
        mapping: Any,
        retirement: Any,
        *,
        correction: bool = False,
        actor: Any = None,
        part_carriers: Sequence[PartCarrier] = (),
    ) -> Any:
        envelope = [values[name] for name in ("schema", "result", "outcome", "profile_config", "schema_id")]
        envelope.extend((sources, pages, parts, mapping, retirement))
        if strip_null_bytes(envelope) != envelope:
            raise ValidationError("Retained evidence cannot contain null characters.")
        lineage_model = self.model._meta.apps.get_model("extraction.ExtractionLineage")
        with transaction.atomic(), system_context(reason="retain authorized extraction snapshot"):
            lineage = lineage_model.objects.filter(identity=values["lineage_identity"]).first()
            if lineage is None:
                try:
                    with transaction.atomic():
                        lineage = lineage_model(identity=values["lineage_identity"])
                        lineage.retain()
                except IntegrityError:
                    lineage = lineage_model.objects.get(identity=values["lineage_identity"])
            lineage = lineage_model.objects.lock_if_supported().get(pk=lineage.pk)
            existing = self.model._base_manager.filter(reuse_key=values["reuse_key"]).first()
            if existing is not None:
                if existing.outcome["request_digest"] != values["outcome"]["request_digest"]:
                    raise ValidationError("This request key already owns different evidence.")
                return existing
            previous = lineage.head
            if values["outcome"]["kind"] == ExtractionStatus.FAILED and (mapping or retirement):
                raise ValidationError("A retained source hold cannot remap or retire identities.")
            if base is not None and (previous is None or previous.pk != base.pk):
                raise StaleExtraction("The extraction base is no longer current.")
            authority = previous
            if (
                previous is not None and previous.outcome["kind"] != ExtractionStatus.SUCCEEDED
                and previous.document_map
            ):
                authority = self._successful_identity_authority(previous)
            document_map: list[dict[str, Any]] = []
            retired: list[dict[str, str]] = []
            if values["outcome"]["kind"] == ExtractionStatus.SUCCEEDED or values["result"]:
                try:
                    document_map, retired = self._document_mapping(
                        values["result"], values["profile_config"]["evidence_layout"], authority, mapping, retirement
                    )
                except ValidationError:
                    if previous is None or mapping or retirement:
                        raise
                    values["outcome"].update(
                        kind=ExtractionStatus.FAILED,
                        code=ExtractionErrorCode.IDENTITY_CORRESPONDENCE_REQUIRED,
                        stage="correspondence",
                    )
            if previous is not None and values["outcome"]["kind"] == ExtractionStatus.FAILED:
                document_map, retired = previous.document_map, previous.retired_identities
                values["outcome"]["identity_correspondence"] = {
                    "last_known_revision": authority.revision,
                    "expected_base_id": public_id_of(authority),
                }
            if (
                base is not None and authority is not None
                and values["outcome"]["kind"] == ExtractionStatus.SUCCEEDED and not correction
            ):
                self._preserve_authority(values, parts, authority, base, document_map, retirement)
            if values["outcome"]["kind"] == ExtractionStatus.SUCCEEDED:
                try:
                    validate(values["schema"], values["result"])
                except ValidationError:
                    raise ValidationError("Extraction output does not match its retained schema.") from None
            if base is not None:
                retained_parts = list(base.parts.order_by("position"))
                if len(retained_parts) != len(parts):
                    raise ValidationError("The retained base has different evidence parts.")
                for part, retained in zip(parts, retained_parts, strict=True):
                    part["carrier_file_id"] = retained.carrier_file_id
                    part["carrier_digest"] = retained.carrier_digest
            else:
                if part_carriers and len(part_carriers) != len(parts):
                    raise ValidationError("Each evidence part requires its matching carrier.")
                file_model = self.model._meta.apps.get_model("storage.File")
                first_file_id = next((source["file_id"] for source in sources if source["file_id"]), None)
                drive_id = (
                    str(file_model._base_manager.select_related("drive").get(pk=first_file_id).drive.sqid)
                    if first_file_id else ""
                )
                for position, part in enumerate(parts):
                    carrier = (
                        part_carriers[position] if part_carriers else
                        PartCarrier.retain(DocumentPart(**part), actor=actor, drive_id=drive_id)
                    )
                    file = file_model._base_manager.get(sqid=carrier.file_id)
                    recovered = carrier.read(file)
                    if dict(asdict(recovered), metadata=recovered.metadata or {}) != part:
                        raise ValidationError("The part carrier differs from the retained evidence.")
                    part["carrier_file_id"] = file.pk
                    part["carrier_digest"] = carrier.content_hash
            values["lineage"] = lineage
            values.pop("lineage_identity", None)
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
        outcome = values["outcome"]
        result = authority.preserve_authority(
            DocumentResult(
                values["result"], retained_parts, outcome["claims"], provider_metadata=outcome["document"]
            ),
            identity_mapping=mapping,
            retired_identities=retirement,
            claim_part_positions=positions,
        )
        values["result"] = result.value
        outcome.update(
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
        source_model = registry.get_model("extraction.ExtractionSource")
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
                    "provider_metadata": row.provider_metadata,
                    "carrier_file_id": row.carrier_file_id,
                }
                for row in base.pages.order_by("position")
            ]
        for name, rows in (("ExtractionPage", pages), ("ExtractionPart", parts)):
            child_model = registry.get_model("extraction", name)
            children = []
            for position, raw in enumerate(rows):
                values = dict(raw)
                source_position = values.pop("source_position")
                if source_position not in by_position:
                    raise ValidationError("A page or part names an absent source.")
                if name == "ExtractionPart":
                    values["metadata"] = values["metadata"] or {}
                    values.pop("value")
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
            mapping = original.implicit_identity_correspondence(result, layout=layout) or {}
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
        if actor is None or not (base).with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to retained evidence and its sources is required.")
        with system_context(reason="resolve extraction lineage head"):
            head = self.model._meta.apps.get_model("extraction.ExtractionLineage").objects.get(pk=base.lineage_id).head
        if head is None or head.schema_digest != base.schema_digest:
            raise ValidationError("The current evidence contract differs from the base.")
        if actor is None or not (head).with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to retained evidence and its sources is required.")
        return head

    def reused_inference(self, base: Any, request_key: str, *, actor: Any) -> Any | None:
        """Find an exact retained attempt before comparing its base with the head."""
        self._require_target_write(base.target, actor)
        reuse_key = canonical_json_sha256({"lineage": base.lineage.identity, "request": request_key})
        with system_context(reason="resolve retained extraction retry"):
            row = self.model._base_manager.filter(reuse_key=reuse_key, lineage_id=base.lineage_id).first()
        if row is not None and (
            not row.with_actor(actor).has_access("read") or row.schema_digest != base.schema_digest
        ):
            raise PermissionDenied("Read access to retained evidence and its sources is required.")
        return row

    def latest_succeeded_identity_authority(self, head: Any, *, actor: Any) -> Any:
        """Resolve the last successful identity authority within one lineage."""
        if actor is None or not (head).with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to retained evidence and its sources is required.")
        row = self._successful_identity_authority(head)
        if actor is None or not (row).with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to retained evidence and its sources is required.")
        return row

    def _successful_identity_authority(self, head: Any) -> Any:
        """Match copied identities against their successful facts, never a held candidate."""
        row = (
            self.model._base_manager.filter(
                lineage_id=head.lineage_id, revision__lte=head.revision, outcome__kind=ExtractionStatus.SUCCEEDED
            )
            .order_by("-revision")
            .first()
        )
        if row is None or row.document_map != head.document_map or row.schema_digest != head.schema_digest:
            raise ValidationError("The retained identity authority is unavailable.")
        return row

    def inference_authority_base(self, base: Any, *, actor: Any) -> Any:
        """Resolve a successful base or the successful authority of its source hold."""
        if base.outcome["kind"] == ExtractionStatus.SUCCEEDED:
            if actor is None or not (base).with_actor(actor).has_access("read"):
                raise PermissionDenied("Read access to retained evidence and its sources is required.")
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
        self._require_correction_parent(extraction, parent)
        return CorrectionBinding(extraction.reference, parent.reference), parent

    def _require_correction_parent(self, original: Any, parent: Any) -> None:
        """Prove immutable parent authority, independent of the lineage's later head."""
        if parent.pk != original.pk and (
            parent.lineage_id != original.lineage_id
            or parent.revision <= original.revision
            or (
                not parent.failed_at_inference
                and (not parent.awaiting_correspondence or self.inference_candidate_selectors(parent))
            )
            or self._successful_identity_authority(parent).pk != original.pk
            or parent.retired_identities != original.retired_identities
        ):
            raise ValidationError("The correction parent has another retained fact authority.")

    def identity_preserving_pipeline_successor(self, authority: Any, successor: Any, *, actor: Any) -> Any:
        """Validate a later unreviewed revision preserving source and fact identity."""
        if actor is None or not (authority).with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to retained evidence and its sources is required.")
        if actor is None or not (successor).with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to retained evidence and its sources is required.")
        if (
            authority.outcome["kind"] != ExtractionStatus.SUCCEEDED
            or successor.outcome["kind"] != ExtractionStatus.SUCCEEDED
            or successor.revision <= authority.revision
            or authority.lineage_id != successor.lineage_id
            or authority.schema_digest != successor.schema_digest
            or authority.document_map != successor.document_map
            or successor.corrections
            or authority.retired_identities
            or successor.retired_identities
        ):
            raise ValidationError("The successor changed retained identity or authority.")
        return successor

    def _correction_basis(self, decision: Any, *, actor: Any) -> tuple[Any, Any]:
        """Resolve the immutable decision basis and its exact revision parent.

        Canonicalize only the decision match; access uses the retained concrete target.
        """
        basis = decision.basis
        if not isinstance(basis, dict):
            raise ValidationError("The correction decision basis must be an object.")
        original = self.model._base_manager.filter(sqid=basis.get("extraction_id")).first()
        if (
            original is None
            or type(basis.get("extraction_revision")) is not int
            or (basis["extraction_revision"] != original.revision)
        ):
            raise ValidationError("The decision names another extraction revision.")
        raw = basis.get("correction_binding")
        if raw is not None and (
            not isinstance(raw, dict)
            or any(type(raw.get(key)) is not str for key in (
                "authority_extraction_id", "revision_parent_extraction_id",
            ))
            or any(type(raw.get(key)) is not int for key in (
                "authority_extraction_revision", "revision_parent_extraction_revision",
            ))
        ):
            raise ValidationError("The decision has an invalid correction binding.")
        parent = (
            original
            if raw is None
            else self.model._base_manager.filter(
                sqid=raw.get("revision_parent_extraction_id"),
                revision=raw.get("revision_parent_extraction_revision"),
            ).first()
        )
        if parent is None or (
            raw is not None and raw != CorrectionBinding(original.reference, parent.reference).payload()
        ):
            raise ValidationError("The decision has an invalid correction binding.")
        self._require_correction_parent(original, parent)
        target = original.target
        canonical_target = canonical_record_target(target) if target is not None else None
        if canonical_target is None or (decision.subject_content_type_id, str(decision.subject_object_id)) != (
            canonical_target.content_type.pk,
            str(canonical_target.object_id),
        ):
            raise ValidationError("The correction decision names another target.")
        for record in (original, parent, target):
            if not record.with_actor(actor).has_access("read"):
                raise PermissionDenied("Correction evidence and its target must remain readable.")
        return original, parent

    def revise_from_decision(
        self,
        decision_id: Any,
        *,
        actor: Any,
        result: Mapping[str, Any],
        actions: Sequence[type[Action]],
        expected_action: str,
        expected_resolution_action: str,
        identity_mapping: Mapping[str, str] | None = None,
        retired_identities: Mapping[str, str] | None = None,
        confirmed_paths: Sequence[str] = (),
    ) -> Any:
        """Retain an authorized correction from a settled, revalidated Decisions answer.

        ``expected_action`` is the decision kind. Its ``basis`` freezes
        extraction_id/extraction_revision and optional CorrectionBinding.payload().
        The caller supplies its declared action types and interprets that answer.
        """
        decisions = self.model._meta.apps.get_model("decisions.Decision")
        with transaction.atomic():
            resolved = decisions.objects.resolution(decision_id, actor=actor, actions=actions)
            decision = resolved.decision
            if (
                decision.kind != expected_action
                or decision.verdict != Verdict.COMPLETED
                or resolved.action is None
                or (resolved.action.key != expected_resolution_action)
            ):
                raise ValidationError("The correction decision has another resolution action.")
            original, parent = self._correction_basis(decision, actor=actor)
            self._require_target_write(original.target, actor)
            self._correction_basis(decision, actor=resolved.resolver)
            self.authorized_document_sources(original, actor=actor)
            self.authorized_document_sources(original, actor=resolved.resolver)
            mapping = dict(
                identity_mapping
                or original.implicit_identity_correspondence(
                    result,
                    layout=original.profile_config["evidence_layout"],
                )
                or {}
            )
            retired = dict(retired_identities or {})
            claims, paths = original.reviewed_facts(
                result,
                identity_mapping=mapping,
                retired_identities=retired,
                confirmed_paths=tuple(confirmed_paths),
            )
            correction = {
                "original_extraction_id": str(original.sqid),
                "original_extraction_revision": original.revision,
                "revision_parent_extraction_id": str(parent.sqid),
                "revision_parent_extraction_revision": parent.revision,
                "decision_id": str(decision.sqid),
                "decision_resolved_by": public_id_of(resolved.resolver),
                "corrected_paths": paths,
                "result_digest": canonical_json_sha256(result),
            }
            outcome = {
                **deepcopy(original.outcome),
                "kind": ExtractionStatus.SUCCEEDED,
                "claims": claims,
                "used_model_roles": [],
                "corrections": [
                    *original.retained_corrections(
                        result,
                        identity_mapping=mapping,
                        retired_identities=retired,
                    ),
                    correction,
                ],
            }
            outcome.pop("code", None)
            outcome.pop("stage", None)
            values = {
                name: getattr(original, name)
                for name in (
                    "schema_id",
                    "schema_digest",
                    "schema",
                    "profile",
                    "profile_config",
                    "file_id",
                    "message_id",
                    "model_id",
                    "recognition_model_id",
                )
            }
            values["lineage_identity"] = original.lineage.identity
            values.update(
                result=deepcopy(dict(result)),
                outcome=outcome,
                created_by_id=actor_user_id(to_subject_ref(actor)),
                correction_decision=decision,
                reuse_key=canonical_json_sha256({"correction": str(decision.sqid)}),
            )
            request = {
                "result": result,
                "mapping": mapping,
                "retired": retired,
                "confirmed_paths": sorted(confirmed_paths),
            }
            outcome["request_digest"] = canonical_json_sha256(request)
            sources = list(
                original.sources.values(
                    "position", "file_id", "message_part_id", "content_type_id", "object_id",
                    "content_hash", "mime_type",
                )
            )
            parts = [asdict(part) for part in original.document_parts()]
            return self._retain(values, sources, [], parts, parent, mapping, retired, correction=True)

    def reviewed_correction_authority(
        self,
        successor: Any,
        *,
        actor: Any,
        actions: Sequence[type[Action]],
        expected_action: str,
        expected_resolution_action: str,
        decision: Any = None,
    ) -> tuple[Any, Any]:
        """Revalidate the exact decision and direct successor retained by a correction."""
        if not successor.with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to reviewed correction evidence is required.")
        if not successor.corrections:
            raise ValidationError("The extraction has no reviewed correction.")
        correction = successor.corrections[-1]
        decisions = self.model._meta.apps.get_model("decisions.Decision")
        if successor.correction_decision_id is None or (
            decision is not None
            and (not isinstance(decision, decisions) or decision.pk != successor.correction_decision_id)
        ):
            raise ValidationError("The reviewed correction is not the direct retained successor.")
        resolved = decisions.objects.resolution(successor.correction_decision_id, actor=actor, actions=actions)
        original, parent = self._correction_basis(resolved.decision, actor=actor)
        self._correction_basis(resolved.decision, actor=resolved.resolver)
        if (
            resolved.action is None
            or str(resolved.decision.sqid) != correction.decision_id
            or resolved.decision.verdict != Verdict.COMPLETED
            or resolved.action.key != expected_resolution_action
            or (
                resolved.decision.kind != expected_action
                or successor.outcome["kind"] != ExtractionStatus.SUCCEEDED
                or successor.lineage_id != parent.lineage_id
                or successor.revision != parent.revision + 1
                or successor.schema_digest != parent.schema_digest
                or original.reference.public_id != correction.original_extraction_id
                or original.revision != correction.original_revision
                or str(parent.sqid) != correction.revision_parent_extraction_id
                or parent.revision != correction.revision_parent_revision
                or successor.outcome["corrections"][-1]["result_digest"] != canonical_json_sha256(successor.result)
            )
        ):
            raise ValidationError("The reviewed correction is not the direct retained successor.")
        return original, resolved.decision
