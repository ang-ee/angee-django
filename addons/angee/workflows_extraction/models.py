"""Immutable evidence and identity-to-value access owned by extraction rows."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from rebac import RelationshipTuple, SubjectRef, write_relationships
from rebac.resources import model_resource_type, to_object_ref

from angee.base.fields import StateField
from angee.base.impl import ImplClassField
from angee.base.mixins import AppendOnlyModel, AuditMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.base.permissions import effective_rebac_definition
from angee.base.refs import RecordRefMixin
from angee.base.serialization import canonical_json, canonical_json_sha256
from angee.workflows_extraction.contracts import (
    CorrectionRef,
    DocumentPart,
    DocumentRef,
    DocumentResult,
    DocumentSource,
    ExtractionPartKind,
    ExtractionRef,
    FactAuthority,
    LineRef,
    PageRef,
    SourceRef,
)
from angee.workflows_extraction.enums import ExtractionErrorCode, ExtractionStatus
from angee.workflows_extraction.inference import RETAINED_AUTHORITY_COMPLETION_REVIEW
from angee.workflows_extraction.managers import EvidenceManager, ExtractionManager
from angee.workflows_extraction.pointers import (
    JSON_POINTER_MISSING,
    fact_pointers,
    json_pointer_value,
    json_pointer_value_or_missing,
    materialize_missing_json_pointer_path,
    result_selectors,
    set_json_pointer,
)
from angee.workflows_extraction.profiles import ExtractionProfile


class RetainedEvidence(AppendOnlyModel):
    """Let the retention owner admit inserts while generic insertion stays closed."""

    class Meta:
        abstract = True

    def retain(self) -> None:
        """Insert an owner-validated new evidence row, never update a retained row."""
        self._owner_insert()


class ExtractionLineage(RetainedEvidence, AngeeModel):
    """Private row locked while allocating a revision and advancing its head."""

    runtime = True
    key = models.CharField(max_length=64, primary_key=True)
    head = models.ForeignKey("workflows_extraction.Extraction", null=True, on_delete=models.PROTECT, related_name="+")
    objects = EvidenceManager()

    class Meta:
        abstract = True

    def advance_head(self, extraction: Any) -> None:
        """Advance under the retention transaction after checking the lineage."""
        if extraction.lineage_key != self.pk:
            raise ValueError("The head must belong to this lineage.")
        type(self).objects.filter(pk=self.pk).owner_update(head=extraction)
        self.head = extraction


class Extraction(RetainedEvidence, SqidMixin, AuditMixin, RecordRefMixin, AngeeModel):
    """One immutable schema candidate and the evidence supporting its facts."""

    runtime = True
    sqid_prefix = "ext_"
    rebac_grantable = {"viewer": "share"}
    revision = models.PositiveIntegerField(default=1, editable=False)
    lineage_key = models.CharField(max_length=64, db_index=True, editable=False)
    reuse_key = models.CharField(max_length=64, unique=True, editable=False)
    status = StateField(choices_enum=ExtractionStatus, editable=False)
    error_code = models.CharField(max_length=100, blank=True, editable=False)
    schema_id = models.CharField(max_length=255, editable=False)
    schema_digest = models.CharField(max_length=64, editable=False)
    schema = models.JSONField(editable=False)
    profile = ImplClassField(
        base_class=ExtractionProfile, registry_setting="ANGEE_EXTRACTION_PROFILE_CLASSES", editable=False
    )
    model = models.ForeignKey(
        "agents.InferenceModel", null=True, blank=True, on_delete=models.PROTECT, related_name="extraction_evidence"
    )
    recognition_model = models.ForeignKey(
        "agents.InferenceModel",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="recognition_extraction_evidence",
    )
    profile_config = models.JSONField(default=dict, editable=False)
    correction_decision = models.ForeignKey(
        "decisions.Decision",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="extraction_corrections",
        editable=False,
    )
    result = models.JSONField(editable=False)
    provenance = models.JSONField(default=dict, editable=False)
    document_map = models.JSONField(default=list, editable=False)
    retired_identities = models.JSONField(default=list, editable=False)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.CharField(max_length=255)
    target = GenericForeignKey("content_type", "object_id")
    objects = ExtractionManager()

    class Meta:
        abstract = True
        ordering = ("lineage_key", "-revision")
        rebac_resource_type = "workflows_extraction/extraction"
        constraints = (models.UniqueConstraint(fields=("lineage_key", "revision"), name="uniq_extraction_revision"),)

    def retain(self) -> None:
        """Insert evidence and mirror supported target read access atomically."""
        super().retain()
        if (relationship := self.target_relationship()) is not None:
            write_relationships([relationship])

    def target_relationship(self) -> RelationshipTuple | None:
        """Project the immutable target using the schema-owned allowed types.

        Native field backing cannot traverse a generic foreign key. Retention
        and the manager's resync therefore share this derived relationship.
        """
        target = self.target
        definition = effective_rebac_definition(type(self))
        relation = next((item for item in definition.relations if item.name == "target"), None) if definition else None
        if target is None or relation is None or model_resource_type(type(target)) not in {
            subject.type for subject in relation.allowed_subjects
        }:
            return None
        return RelationshipTuple(
            resource=to_object_ref(self), relation="target", subject=SubjectRef(to_object_ref(target))
        )

    @property
    def reference(self) -> ExtractionRef:
        """Return a stable reference to this exact evidence revision."""
        return ExtractionRef(self.lineage_key, str(self.sqid), self.revision)

    @property
    def document_refs(self) -> tuple[DocumentRef, ...]:
        """Expose document and line identity independently of printed values."""
        return tuple(
            DocumentRef(
                row["identity"],
                row["selector"],
                tuple(LineRef(line["identity"], line["selector"]) for line in row["lines"]),
            )
            for row in self.document_map
        )

    def document_ref(self, identity: str) -> DocumentRef:
        """Resolve an exact identity; unknown and retired identities raise KeyError."""
        for reference in self.document_refs:
            if reference.identity == identity:
                return reference
        raise KeyError(identity)

    def fact(self, pointer: str) -> Any:
        """Read a retained fact; missing raises KeyError while explicit null is None."""
        return json_pointer_value(self.result, pointer)

    def preserve_authority(
        self,
        candidate: DocumentResult,
        *,
        identity_mapping: Mapping[str, str],
        retired_identities: Mapping[str, str] | None = None,
        claim_part_positions: Mapping[int, int] | None = None,
    ) -> DocumentResult:
        """Fill only facts without source or correction authority, using validated correspondence.

        The caller validates carrier continuity. Unchanged ordered carriers need
        no ``claim_part_positions``; remapped carriers require an exact position map.
        Neither retained evidence nor the candidate is mutated.
        """
        value, claims = deepcopy(candidate.value), deepcopy(candidate.claims)
        protected = {pointer for pointer, entries in self.claims.items() if entries}
        protected.update(path for correction in self.corrections for path in correction.corrected_paths)
        completed_missing = False
        for pointer in sorted(protected, key=lambda path: (len(path), path)):
            destination = self._authority_destination(pointer, identity_mapping, retired_identities or {})
            if destination is None:
                continue
            retained_value = json_pointer_value_or_missing(self.result, pointer)
            if retained_value is JSON_POINTER_MISSING:
                raise ValidationError("A retained authoritative fact is absent.")
            try:
                if json_pointer_value_or_missing(value, destination) is JSON_POINTER_MISSING:
                    materialize_missing_json_pointer_path(
                        value, destination, source=self.result, source_pointer=pointer
                    )
                    completed_missing = True
                set_json_pointer(value, destination, deepcopy(retained_value))
            except KeyError:
                raise ValidationError("The candidate omitted an authoritative fact path.") from None
            claims = {
                path: entries
                for path, entries in claims.items()
                if path != destination and not path.startswith(f"{destination}/")
            }
            if pointer in self.claims:
                try:
                    claims[destination] = [
                        {
                            **deepcopy(claim),
                            "part_position": claim["part_position"]
                            if claim_part_positions is None
                            else claim_part_positions[claim["part_position"]],
                        }
                        for claim in self.claims[pointer]
                    ]
                except KeyError:
                    raise ValidationError("An authoritative claim lacks a retained carrier.") from None
        metadata = deepcopy(candidate.provider_metadata or {})
        if completed_missing:
            metadata["unresolved_reasons"] = list(
                dict.fromkeys(
                    [
                        *metadata.get("unresolved_reasons", ()),
                        RETAINED_AUTHORITY_COMPLETION_REVIEW,
                    ]
                )
            )
        return replace(candidate, value=value, claims=claims, provider_metadata=metadata)

    def _authority_destination(
        self, pointer: str, mapping: Mapping[str, str], retired: Mapping[str, str]
    ) -> str | None:
        """Map one authoritative fact through its closest retained logical identity."""
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            raise ValidationError("A retained authority pointer is invalid.")
        references: list[DocumentRef | LineRef] = []
        for document in self.document_refs:
            references.extend((document, *document.lines))
        matched = max(
            (
                ref
                for ref in references
                if not ref.selector or pointer == ref.selector or pointer.startswith(f"{ref.selector}/")
            ),
            key=lambda ref: len(ref.selector),
            default=None,
        )
        if matched is not None and matched.identity in retired:
            return None
        if any(ref.selector and ref.selector.startswith(f"{pointer}/") for ref in references):
            raise ValidationError("An authoritative fact cannot cover a document or line identity container.")
        if matched is None:
            return pointer
        destination = next((selector for selector, identity in mapping.items() if identity == matched.identity), None)
        if destination is None:
            raise ValidationError("Authoritative facts require explicit identity correspondence.")
        return destination + pointer[len(matched.selector) :]

    def retained_corrections(
        self,
        candidate: Mapping[str, Any],
        *,
        identity_mapping: Mapping[str, str],
        retired_identities: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Carry prior correction provenance only where logical identity and value agree."""
        retained = []
        for correction in self.provenance.get("corrections", ()):
            paths = correction.get("corrected_paths")
            if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
                raise ValidationError("The retained correction provenance is invalid.")
            retained_paths = []
            for pointer in paths:
                destination = self._authority_destination(pointer, identity_mapping, retired_identities or {})
                before = json_pointer_value_or_missing(self.result, pointer)
                after = (
                    json_pointer_value_or_missing(candidate, destination)
                    if destination is not None
                    else JSON_POINTER_MISSING
                )
                if (
                    before is not JSON_POINTER_MISSING
                    and after is not JSON_POINTER_MISSING
                    and canonical_json(before) == canonical_json(after)
                ):
                    retained_paths.append(destination)
            retained.append({**deepcopy(correction), "corrected_paths": retained_paths})
        return retained

    def selected_document(self, identity: str) -> tuple[Mapping[str, Any], DocumentRef]:
        """Resolve a document identity to its object and retained reference."""
        if self.awaiting_correspondence:
            raise ValueError("The candidate requires correspondence before identity-based access.")
        reference = self.document_ref(identity)
        value = self.fact(reference.selector)
        if not isinstance(value, Mapping):
            raise ValueError("The document selector does not point to an object.")
        return value, reference

    def reviewed_facts(
        self,
        value: Mapping[str, Any],
        *,
        identity_mapping: Mapping[str, str],
        retired_identities: Mapping[str, str],
        confirmed_paths: tuple[str, ...],
    ) -> tuple[dict[str, Any], list[str]]:
        """Carry unchanged source claims and attribute changed or confirmed scalar facts."""
        unchanged = {}
        for pointer in fact_pointers(self.result):
            destination = self._authority_destination(pointer, identity_mapping, retired_identities)
            if destination is not None:
                after = json_pointer_value_or_missing(value, destination)
                if after is not JSON_POINTER_MISSING and canonical_json(self.fact(pointer)) == canonical_json(after):
                    unchanged[destination] = pointer
        paths = set(fact_pointers(value))
        if not set(confirmed_paths) <= paths:
            raise ValidationError("Confirmed paths must name retained scalar facts.")
        claims = {
            destination: deepcopy(self.claims[pointer])
            for destination, pointer in unchanged.items()
            if pointer in self.claims and destination not in confirmed_paths
        }
        return claims, sorted(paths - unchanged.keys() | set(confirmed_paths))

    def selected_line(self, document_identity: str, line_identity: str) -> tuple[Mapping[str, Any], LineRef]:
        """Resolve a line within its owning document without positional fallback."""
        _document, document_ref = self.selected_document(document_identity)
        for reference in document_ref.lines:
            if reference.identity == line_identity:
                value = self.fact(reference.selector)
                if not isinstance(value, Mapping):
                    raise ValueError("The line selector does not point to an object.")
                return value, reference
        raise KeyError(line_identity)

    def implicit_identity_correspondence(self, result: Any, *, layout: Any) -> dict[str, str] | None:
        """Return a complete correspondence only when retained positions are unambiguous."""
        requested = result_selectors(result, layout)
        previous = tuple(self.document_refs)
        if not previous:
            return {}
        if canonical_json(self.result) == canonical_json(result):
            return {
                selector: identity
                for ref in previous
                for selector, identity in (
                    (ref.selector, ref.identity),
                    *((line.selector, line.identity) for line in ref.lines),
                )
            }
        if len(previous) != 1 or len(requested) != 1:
            return None
        document = previous[0]
        selector, line_selectors = requested[0]
        if document.selector != selector:
            return None
        mapping = {selector: document.identity}
        if not document.lines:
            return mapping | dict.fromkeys(line_selectors, "new")
        if tuple(line.selector for line in document.lines) != line_selectors:
            return None
        if len(line_selectors) == 1:
            return mapping | {line_selectors[0]: document.lines[0].identity}
        try:
            unchanged = all(
                canonical_json(json_pointer_value(self.result, path))
                == canonical_json(json_pointer_value(result, path))
                for path in line_selectors
            )
        except KeyError:
            return None
        return mapping | {line.selector: line.identity for line in document.lines} if unchanged else None

    def document_parts(self) -> tuple[DocumentPart, ...]:
        """Return retained raw carriers without rereading or preparing source bytes."""
        return tuple(
            DocumentPart(
                source_position=row.source.position,
                source_page=row.source_page,
                mime_type=row.mime_type,
                kind=ExtractionPartKind(row.kind),
                value=row.value,
                method=row.method,
                content_hash=row.content_hash,
                width=row.width,
                height=row.height,
                dpi=row.dpi,
                duration_ms=row.duration_ms,
                metadata=row.metadata,
            )
            for row in self.parts.order_by("position")
        )

    def authority_carrier_positions(self, current: Extraction) -> dict[int, int]:
        """Map uniquely matched physical carriers; absent or ambiguous matches are omitted.

        Required non-retired claims fail in ``preserve_authority`` when their
        position is absent. Source ordering and acquisition telemetry are not identity.
        """
        positions: dict[tuple[Any, ...], list[int]] = {}
        for part in current.parts.order_by("position"):
            positions.setdefault(part.carrier_identity, []).append(part.position)
        return {
            part.position: matches[0]
            for part in self.parts.order_by("position")
            if len(matches := positions.get(part.carrier_identity, [])) == 1
        }

    def document_sources(self) -> tuple[DocumentSource, ...]:
        """Return retained source identities; inference consumes the retained parts."""
        return tuple(
            DocumentSource(
                source_position=row.position,
                content_hash=row.content_hash,
                mime_type=row.mime_type,
                content=b"",
                file=row.file,
                message_part=row.message_part,
            )
            for row in self.sources.order_by("position")
        )

    def require_target(self, target: Any) -> None:
        """Reject reinterpretation of retained evidence against another target."""
        if self.content_type_id != ContentType.objects.get_for_model(target).pk or str(self.object_id) != str(
            target.pk
        ):
            raise ValidationError("The extraction names a different target.")

    @property
    def used_model_roles(self) -> tuple[str, ...]:
        """Return model roles recorded during acquisition and interpretation."""
        return tuple(self.provenance.get("used_model_roles", ()))

    @property
    def claims(self) -> Mapping[str, Any]:
        """Return scalar grounding by JSON pointer."""
        return self.provenance.get("claims", {})

    @property
    def stage_provenance(self) -> Mapping[str, Any]:
        """Return acquisition and processing metadata."""
        return self.provenance.get("document", {})

    @property
    def unresolved_reasons(self) -> tuple[str, ...]:
        """Return explicit reasons a retained candidate requires attention."""
        return tuple(self.provenance.get("unresolved_reasons", ()))

    @property
    def awaiting_correspondence(self) -> bool:
        """Whether a candidate awaits explicit document/line correspondence."""
        return (
            self.status == ExtractionStatus.FAILED
            and self.error_code == ExtractionErrorCode.IDENTITY_CORRESPONDENCE_REQUIRED
        )

    @property
    def failed_at_inference(self) -> bool:
        """Whether the retained failure is explicitly attributed to inference."""
        return (
            self.status == ExtractionStatus.FAILED
            and self.stage_provenance.get("failure", {}).get("stage") == "inference"
        )

    @property
    def corrections(self) -> tuple[CorrectionRef, ...]:
        """Read retained correction references without interpreting decision rows."""
        return tuple(
            CorrectionRef(
                item["original_extraction_id"],
                item["original_extraction_revision"],
                item["decision_id"],
                tuple(item.get("corrected_paths", ())),
                item.get("revision_parent_extraction_id"),
                item.get("revision_parent_extraction_revision"),
            )
            for item in self.provenance.get("corrections", ())
        )

    def fact_authority(self, pointer: str) -> FactAuthority:
        """Classify source grounding or retained correction authority for a fact."""
        self.fact(pointer)
        if self.claims.get(pointer):
            return FactAuthority("source")
        for correction in reversed(self.corrections):
            if any(pointer == path or pointer.startswith(f"{path}/") for path in correction.corrected_paths):
                return FactAuthority("correction", correction.decision_id)
        return FactAuthority("unverified")


class ExtractionSource(RetainedEvidence, SqidMixin, AngeeModel):
    """One source's immutable position and content identity."""

    runtime = True
    sqid_prefix = "exs_"
    extraction = models.ForeignKey("workflows_extraction.Extraction", on_delete=models.PROTECT, related_name="sources")
    file = models.ForeignKey(
        "storage.File", null=True, blank=True, on_delete=models.PROTECT, related_name="extraction_sources"
    )
    message_part = models.ForeignKey(
        "messaging.Part", null=True, blank=True, on_delete=models.PROTECT, related_name="extraction_sources"
    )
    position = models.PositiveIntegerField(editable=False)
    content_hash = models.CharField(max_length=64, editable=False)
    mime_type = models.CharField(max_length=200, editable=False)
    objects = EvidenceManager()

    class Meta:
        abstract = True
        ordering = ("position",)
        rebac_resource_type = "workflows_extraction/extraction_source"
        constraints = (
            models.UniqueConstraint(fields=("extraction", "position"), name="uniq_extraction_source_position"),
            models.CheckConstraint(
                condition=models.Q(file__isnull=False, message_part__isnull=True)
                | models.Q(file__isnull=True, message_part__isnull=False),
                name="extraction_source_one_input",
            ),
            models.UniqueConstraint(
                fields=("extraction", "file"),
                condition=models.Q(file__isnull=False),
                name="uniq_extraction_source_file",
            ),
            models.UniqueConstraint(
                fields=("extraction", "message_part"),
                condition=models.Q(message_part__isnull=False),
                name="uniq_extraction_source_part",
            ),
        )

    @property
    def reference(self) -> SourceRef:
        """Return the exact file or message-part identity retained here."""
        return SourceRef(
            "file" if self.file_id is not None else "message_part",
            str((self.file if self.file_id is not None else self.message_part).sqid),
            self.content_hash,
        )


class ExtractionPage(RetainedEvidence, SqidMixin, AngeeModel):
    """One ordered page's recognized content and safe provider metadata."""

    runtime = True
    sqid_prefix = "exp_"
    extraction = models.ForeignKey("workflows_extraction.Extraction", on_delete=models.PROTECT, related_name="pages")
    source = models.ForeignKey("workflows_extraction.ExtractionSource", on_delete=models.PROTECT, related_name="pages")
    position = models.PositiveIntegerField(editable=False)
    source_page = models.PositiveIntegerField(editable=False)
    width = models.PositiveIntegerField(editable=False)
    height = models.PositiveIntegerField(editable=False)
    dpi = models.PositiveIntegerField(editable=False)
    duration_ms = models.PositiveIntegerField(default=0, editable=False)
    provider_metadata = models.JSONField(default=dict, editable=False)
    carrier_file = models.ForeignKey(
        "storage.File", null=True, blank=True, on_delete=models.PROTECT, related_name="extraction_pages"
    )
    objects = EvidenceManager()

    class Meta:
        abstract = True
        ordering = ("position",)
        rebac_resource_type = "workflows_extraction/extraction_page"
        constraints = (
            models.UniqueConstraint(fields=("extraction", "position"), name="uniq_extraction_page_position"),
            models.UniqueConstraint(fields=("source", "source_page"), name="uniq_extraction_source_page"),
        )

    @property
    def reference(self) -> PageRef:
        """Expose the source/page and its retained raster carrier identities."""
        carriers = (str(self.carrier_file.sqid),) if self.carrier_file_id else ()
        return PageRef(self.source.reference, self.source_page, carriers)


class ExtractionPart(RetainedEvidence, SqidMixin, AngeeModel):
    """One immutable text or structured carrier supporting a candidate."""

    runtime = True
    sqid_prefix = "exr_"
    extraction = models.ForeignKey("workflows_extraction.Extraction", on_delete=models.PROTECT, related_name="parts")
    source = models.ForeignKey("workflows_extraction.ExtractionSource", on_delete=models.PROTECT, related_name="parts")
    position = models.PositiveIntegerField(editable=False)
    source_page = models.PositiveIntegerField(null=True, blank=True, editable=False)
    mime_type = models.CharField(max_length=200, editable=False)
    kind = models.CharField(max_length=32, choices=ExtractionPartKind, editable=False)
    method = models.CharField(max_length=128, editable=False)
    content_hash = models.CharField(max_length=64, editable=False)
    width = models.PositiveIntegerField(null=True, blank=True, editable=False)
    height = models.PositiveIntegerField(null=True, blank=True, editable=False)
    dpi = models.PositiveIntegerField(null=True, blank=True, editable=False)
    value = models.JSONField(editable=False)
    metadata = models.JSONField(default=dict, editable=False)
    duration_ms = models.PositiveIntegerField(default=0, editable=False)
    objects = EvidenceManager()

    @property
    def carrier_identity(self) -> tuple[Any, ...]:
        """Physical source and carrier facts, independent of ordinal positions and telemetry."""
        return (
            self.source.reference,
            self.source_page,
            self.mime_type,
            self.kind,
            self.method,
            self.content_hash,
            self.width,
            self.height,
            self.dpi,
            canonical_json_sha256(self.value),
        )

    class Meta:
        abstract = True
        ordering = ("position",)
        rebac_resource_type = "workflows_extraction/extraction_part"
        constraints = (
            models.UniqueConstraint(fields=("extraction", "position"), name="uniq_extraction_part_position"),
        )
