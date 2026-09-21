"""Append-only evidence collections and atomic revision allocation."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from enum import StrEnum
from typing import Any
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from jsonschema import Draft202012Validator
from rebac import system_context, to_subject_ref

from angee.base.actors import actor_user_id
from angee.base.db import get_write_alias, related_on
from angee.base.mixins import AppendOnlyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.refs import record_ref_for
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.base.serialization import canonical_json_sha256
from angee.workflows.attempts import json_values_equal
from angee.workflows_extraction.contracts import CorrectionBinding, DocumentRef
from angee.workflows_extraction.engines import DocumentPart, DocumentSource, PageImage, PageResult
from angee.workflows_extraction.pointers import (
    implicit_identity_correspondence,
    result_selectors,
)
from angee.workflows_extraction.service import (
    _changed_fact_pointers,
    _confirmed_fact_pointers,
    _json_object,
    _lineage_key,
    _retained_source_facts,
    _reviewed_correction_unresolved_reasons,
    _unchanged_claims,
    _unchanged_corrections,
    _validated_schema,
)


class RetiredIdentityKind(StrEnum):
    """Closed identity axes retained by document correspondence."""

    DOCUMENT = "document"
    LINE = "line"


def _same_fact_identity(authority: Any, evidence: Any) -> bool:
    return (
        authority.content_type_id == evidence.content_type_id
        and str(authority.object_id) == str(evidence.object_id)
        and authority.document_map == evidence.document_map
    )


def _same_identity_basis(authority: Any, evidence: Any) -> bool:
    return (
        _same_fact_identity(authority, evidence)
        and authority.schema_id == evidence.schema_id
        and authority.schema_digest == evidence.schema_digest
    )


class EvidenceQuerySet(
    AppendOnlyQuerySet[Any],
    AngeeQuerySet[Any],
):
    """Keep generic inserts closed and retained evidence append-only."""

    def immutable_error(self, operation: str) -> Exception:
        """Return extraction's established error for each forbidden mutation."""

        if operation == "delete":
            return ValueError("Extraction evidence is retained and cannot be deleted through the ORM.")
        if operation == "_raw_delete":
            return ValueError("Extraction evidence cannot be directly deleted.")
        return ValueError("Extraction evidence is immutable.")

    def validate_insert(self) -> None:
        """Keep ordinary create and bulk_create outside retention closed."""

        raise ValueError("Extraction evidence can only be inserted by the retention owner.")

    def _retain_rows(self, rows: Sequence[Any]) -> list[Any]:
        """Insert the retention manager's ordered children without conflict updates.

        Skip only AppendOnlyQuerySet's closed generic insertion entrypoint;
        AngeeQuerySet and the upstream actor-aware bulk insertion still run.
        """

        return super(AppendOnlyQuerySet, self).bulk_create(rows)


EvidenceManager: Any = AngeeManager.from_queryset(EvidenceQuerySet)


class ExtractionManager(EvidenceManager):
    """Persist one authorized result and all of its ordered evidence atomically."""

    def automatic_inference_mapping(
        self, base: Any, *, result: Any | None = None,
    ) -> dict[str, str]:
        """Carry identity only when the inferred candidate proves unchanged structure."""

        previous = tuple(base.document_refs)
        if result is None:
            if not previous:
                return {}
            if len(previous) == 1 and previous[0].selector == "" and not previous[0].lines:
                return {"": previous[0].identity}
            raise ValidationError({
                "inference": "Existing document or line identities require reviewed correspondence."
            })
        mapping = implicit_identity_correspondence(
            result,
            layout=base.engine_config.get("evidence_layout", {}),
            original=base,
        )
        if mapping is None:
            raise ValidationError({
                "inference": "Existing document or line identities require reviewed correspondence."
            })
        return mapping

    def correction_identity_mapping(
        self, original: Any, result: Any, *, identity_mapping: Any,
        retired_identities: Any,
    ) -> dict[str, str]:
        """Resolve omission through the one safe matcher; keep explicit mapping explicit."""

        if identity_mapping is None:
            implicit = implicit_identity_correspondence(
                result, layout=original.engine_config.get("evidence_layout", {}),
                original=original,
            )
            if implicit is None:
                raise ValidationError({
                    "extraction": "Changed document or line structure requires explicit correspondence."
                })
            return implicit
        mapping = dict(identity_mapping)
        if not mapping and not retired_identities:
            mapping = implicit_identity_correspondence(
                result, layout=original.engine_config.get("evidence_layout", {}),
                original=original,
            ) or {}
        return mapping

    def inference_current_head(self, base: Any, *, actor: Any) -> Any:
        """Resolve the exact actor-readable lineage head without selecting another lineage."""

        lineage_model = self.model._meta.apps.get_model("workflows_extraction", "ExtractionLineage")
        with system_context(reason="workflows_extraction.infer.current_head"):
            lineage = lineage_model._base_manager.using(self._db).select_related("head").filter(
                key=base.lineage_key,
            ).first()
        current = lineage.head if lineage is not None else None
        if current is None:
            raise ValidationError({"inference": "The retained lineage is unavailable."})
        if not current.with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to the current extraction is required.")
        if (
            current.lineage_key != base.lineage_key
            or current.content_type_id != base.content_type_id
            or str(current.object_id) != str(base.object_id)
            or current.schema_id != base.schema_id
            or current.schema_digest != base.schema_digest
        ):
            raise ValidationError({"inference": "The current extraction differs from the retained base."})
        return current

    def _inference_authority_base(self, base: Any, *, using: str | None) -> Any:
        """Resolve one hold's immutable successful authority without authorizing it."""

        if base.status == "succeeded":
            return base
        if (
            base.status != "failed"
            or base.error_code != "source_hold:identity_correspondence_required"
        ):
            raise ValidationError({"inference": "The extraction is not a correspondence hold."})
        correspondence = base.provenance.get("identity_correspondence", {})
        revision = correspondence.get("last_known_revision")
        expected_base_id = correspondence.get("expected_base_id")
        if type(revision) is not int or revision < 1 or revision >= base.revision:
            raise ValidationError({"inference": "The correspondence hold lacks a valid authority base."})
        with system_context(reason="workflows_extraction.infer.authority_base"):
            authority = self.model._base_manager.using(using).filter(
                lineage_key=base.lineage_key, revision=revision,
            ).first()
        if authority is None or authority.status != "succeeded":
            raise ValidationError({"inference": "The retained authority base is unavailable."})
        if (
            not _same_fact_identity(authority, base)
            or type(expected_base_id) is not int
            or expected_base_id != authority.pk
        ):
            raise ValidationError({
                "inference": "The retained authority base differs from the correspondence hold."
            })
        return authority

    def inference_authority_base(self, base: Any, *, actor: Any) -> Any:
        """Resolve the exact actor-readable fact owner retained by a correspondence hold."""

        authority = self._inference_authority_base(base, using=self._db)
        if not authority.with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to the retained authority base is required.")
        return authority

    def latest_succeeded_identity_authority(self, head: Any, *, actor: Any) -> Any:
        """Resolve the latest successful fact owner for one exact lineage head."""

        with system_context(reason="workflows_extraction.infer.latest_succeeded_authority"):
            authority = (
                self.model._base_manager.using(self._db)
                .filter(
                    lineage_key=head.lineage_key,
                    revision__lt=head.revision,
                    status="succeeded",
                )
                .order_by("-revision")
                .first()
            )
        if authority is None:
            raise ValidationError({"inference": "The lineage has no successful identity authority."})
        if not authority.with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to the retained identity authority is required.")
        if not _same_fact_identity(authority, head):
            raise ValidationError({
                "inference": "The retained identity authority differs from the current extraction."
            })
        return authority

    def identity_preserving_pipeline_successor(
        self,
        authority: Any,
        successor: Any,
        *,
        actor: Any,
    ) -> Any:
        """Validate one unreviewed pipeline successor without granting fact authority."""

        if (
            not isinstance(authority, self.model)
            or authority.pk is None
            or not isinstance(successor, self.model)
            or successor.pk is None
        ):
            raise ValidationError({"inference": "Retained extraction evidence is required."})
        if (
            not authority.with_actor(actor).has_access("read")
            or not successor.with_actor(actor).has_access("read")
        ):
            raise PermissionDenied("Read access to the retained extraction evidence is required.")
        if (
            authority.status != "succeeded"
            or successor.status != "succeeded"
            or successor.revision <= authority.revision
            or successor.lineage_key != authority.lineage_key
            or not _same_identity_basis(authority, successor)
            or authority.retired_identities
            or successor.retired_identities
            or successor.corrections
        ):
            raise ValidationError({
                "inference": "The pipeline successor changed retained source identity."
            })
        return successor

    def _validated_correction_revision_parent(
        self,
        authority: Any,
        revision_parent: Any,
        *,
        actor: Any,
        using: str | None,
    ) -> Any:
        """Validate the sole non-direct parent supported by human correction."""

        revision_parent = self._correction_revision_parent(
            authority,
            revision_parent,
            using=using,
        )
        if (
            not authority.with_actor(actor).has_access("read")
            or not revision_parent.with_actor(actor).has_access("read")
        ):
            raise PermissionDenied(
                "Read access to the correction authority and revision parent is required."
            )
        return revision_parent

    def _correction_revision_parent(
        self,
        authority: Any,
        revision_parent: Any,
        *,
        using: str | None,
    ) -> Any:
        """Validate immutable adjacency facts without granting record access."""

        if (
            not isinstance(authority, self.model)
            or authority.pk is None
            or not isinstance(revision_parent, self.model)
            or revision_parent.pk is None
        ):
            raise ValidationError(
                {"extraction": "Correction authority requires retained extraction revisions."}
            )
        if revision_parent.pk == authority.pk:
            return revision_parent
        selectors = self.inference_candidate_selectors(revision_parent)
        retained_authority = self._inference_authority_base(revision_parent, using=using)
        if (
            selectors
            or retained_authority.pk != authority.pk
            or authority.retired_identities != revision_parent.retired_identities
        ):
            raise ValidationError({
                "extraction": "The correction revision parent has another retained fact authority."
            })
        return revision_parent

    def prepare_correction_binding(
        self,
        extraction: Any,
        *,
        actor: Any,
    ) -> tuple[Any, Any]:
        """Freeze the fact authority and exact current parent before human review."""

        revision_parent = self.inference_current_head(extraction, actor=actor)
        revision_parent = self._validated_correction_revision_parent(
            extraction,
            revision_parent,
            actor=actor,
            using=self._db,
        )
        return (
            CorrectionBinding(extraction.reference, revision_parent.reference),
            revision_parent,
        )

    def _reviewed_correction_revision_basis(
        self,
        successor: Any,
        *,
        actor: Any,
        using: str | None,
    ) -> tuple[Any, Any, Any]:
        """Resolve the fact authority and direct parent recorded by a correction."""

        if not isinstance(successor, self.model) or successor.pk is None:
            raise ValidationError(
                {"extraction": "Reviewed correction requires a retained extraction row."}
            )
        if not successor.with_actor(actor).has_access("read"):
            raise PermissionDenied(
                "Read access to the reviewed correction evidence is required."
            )
        try:
            correction = successor.corrections[-1]
        except (IndexError, KeyError, TypeError, ValueError):
            correction = None
        original = None
        revision_parent = None
        if correction is not None:
            with system_context(reason="workflows_extraction.correction.original"):
                original = self.model._base_manager.using(using).filter(
                    sqid=correction.original_extraction_id,
                    revision=correction.original_revision,
                ).first()
                revision_parent = self.model._base_manager.using(using).filter(
                    sqid=(
                        correction.revision_parent_extraction_id
                        or correction.original_extraction_id
                    ),
                    revision=(
                        correction.revision_parent_revision
                        or correction.original_revision
                    ),
                ).first()
        if original is None or revision_parent is None or correction is None:
            raise ValidationError(
                {"extraction": "The reviewed correction is not the direct retained successor."}
            )
        self._validated_correction_revision_parent(
            original,
            revision_parent,
            actor=actor,
            using=using,
        )
        if (
            successor.status != "succeeded"
            or successor.lineage_key != revision_parent.lineage_key
            or successor.revision != revision_parent.revision + 1
            or successor.content_type_id != revision_parent.content_type_id
            or str(successor.object_id) != str(revision_parent.object_id)
            or successor.schema_id != revision_parent.schema_id
            or successor.schema_digest != revision_parent.schema_digest
            or correction.original_extraction_id != str(original.sqid)
            or correction.original_revision != original.revision
        ):
            raise ValidationError(
                {"extraction": "The reviewed correction is not the direct retained successor."}
            )
        return original, revision_parent, correction

    def validate_correction_binding(
        self,
        payload: Any,
        *,
        extraction: Any,
    ) -> tuple[Any, Any]:
        """Resolve the exact fact authority and frozen revision parent in a Decision."""

        if not isinstance(extraction, self.model) or extraction.pk is None:
            raise ValidationError({"extraction": "Correction authority requires a retained extraction."})
        if not isinstance(payload, Mapping):
            raise ValidationError({"decision": "The correction Decision payload must be an object."})
        if (
            type(payload.get("extraction_id")) is not str
            or payload["extraction_id"] != str(extraction.sqid)
        ):
            raise ValidationError({"decision": "The correction Decision names a different extraction."})
        bound_revision = payload.get("extraction_revision")
        if type(bound_revision) is not int or bound_revision != extraction.revision:
            raise ValidationError(
                {"decision": "The correction Decision names a different extraction revision."}
            )
        raw_binding = payload.get("correction_binding")
        if raw_binding is None:
            return CorrectionBinding(extraction.reference, extraction.reference), extraction
        expected = {
            "authority_extraction_id",
            "authority_extraction_revision",
            "revision_parent_extraction_id",
            "revision_parent_extraction_revision",
        }
        if (
            not isinstance(raw_binding, Mapping)
            or set(raw_binding) != expected
            or raw_binding.get("authority_extraction_id") != str(extraction.sqid)
            or type(raw_binding.get("authority_extraction_revision")) is not int
            or raw_binding["authority_extraction_revision"] != extraction.revision
            or type(raw_binding.get("revision_parent_extraction_id")) is not str
            or type(raw_binding.get("revision_parent_extraction_revision")) is not int
        ):
            raise ValidationError(
                {"decision": "The correction Decision has an invalid revision-parent binding."}
            )
        with system_context(reason="workflows_extraction.correction.revision_parent"):
            revision_parent = self.model._base_manager.using(self._db).filter(
                sqid=raw_binding["revision_parent_extraction_id"],
                revision=raw_binding["revision_parent_extraction_revision"],
            ).first()
        if revision_parent is None:
            raise ValidationError(
                {"decision": "The correction Decision revision parent is unavailable."}
            )
        revision_parent = self._correction_revision_parent(
            extraction,
            revision_parent,
            using=self._db,
        )
        binding = CorrectionBinding(extraction.reference, revision_parent.reference)
        if raw_binding != binding.payload():
            raise ValidationError(
                {"decision": "The correction Decision has another revision-parent binding."}
            )
        return binding, revision_parent

    def revise_from_decision(
        self,
        decision_id: int,
        *,
        actor: Any,
        result: Mapping[str, Any],
        expected_action: str,
        expected_resolution_action: str,
        identity_mapping: Mapping[str, str] | None = None,
        retired_identities: Mapping[str, str] | None = None,
        confirmed_paths: Sequence[str] = (),
    ) -> Any:
        """Retain one domain correction from its canonical completed Decision.

        The caller interprets the domain resolution into ``result`` and its
        correspondence. This owner reloads the Decision, pins the resolver,
        validates its frozen extraction/target binding and action, and locks
        workflow ancestry before the extraction lineage and retained sources.
        The resolver needs current read access to the original evidence, its
        revision parent, and the target independently of Decision assignment;
        a pending Decision's temporary read grants have already been removed.
        The run's admitted user owns the revision; the human resolver is kept
        in correction provenance. The unique reuse key makes exact retries
        return the existing revision, including after the lineage advances.
        """

        if actor is None:
            raise PermissionDenied("Correction resolver required.")
        if not expected_action or not expected_resolution_action:
            raise ValueError("Correction actions must be explicit.")
        alias = get_write_alias(self.model, bound=self)
        manager = self.db_manager(alias)
        registry = self.model._meta.apps
        decision_model = registry.get_model("workflows", "Decision")
        lineage_model = registry.get_model("workflows_extraction", "ExtractionLineage")
        with (
            decision_model.objects.db_manager(alias).locked_resolution(
                decision_id,
                actor=actor,
                expected_action=expected_action,
            ) as decision,
            system_context(reason="workflows_extraction.extraction.revise_from_decision"),
        ):
            run = decision.step_run.run
            if (
                not isinstance(decision.resolution, Mapping)
                or decision.resolution.get("action") != expected_resolution_action
            ):
                raise ValidationError({"decision": "The correction Decision has another resolution action."})
            if run.admission_actor_subject() is None:
                raise ValidationError({"actor": "The correction run requires its admitted actor."})
            if not isinstance(decision.payload, Mapping):
                raise ValidationError({"decision": "The correction Decision payload must be an object."})
            extraction = (
                self.model._base_manager.using(alias)
                .filter(
                    sqid=decision.payload.get("extraction_id"),
                )
                .first()
            )
            if extraction is None:
                raise ValidationError({"extraction": "The retained extraction is unavailable."})
            lineage_model._base_manager.using(alias).lock_if_supported().get(key=extraction.lineage_key)
            binding, selected_parent = manager.validate_correction_binding(
                decision.payload,
                extraction=extraction,
            )
            original = system_queryset(self.model, using=alias, lock=("self",)).get(pk=extraction.pk)
            revision_parent = system_queryset(self.model, using=alias, lock=("self",)).get(pk=selected_parent.pk)
            if original.reference != binding.authority or revision_parent.reference != binding.revision_parent:
                raise ValidationError({"extraction": "The frozen correction revision parent changed before review."})
            unresolved_target = original.target
            if unresolved_target is None:
                raise ValidationError({"extraction": "The retained extraction target is unavailable."})
            target = system_queryset(type(unresolved_target), using=alias, lock=("self",)).get(
                pk=unresolved_target.pk,
            )
            target_ref = record_ref_for(target)
            if decision.target_model != target_ref.model_label or decision.target_id != target_ref.public_id:
                raise ValidationError({"decision": "The correction Decision names another target."})
            for record in (original, revision_parent, target):
                readable = read_scoped_queryset(type(record), actor)
                if readable is None or not readable.using(alias).filter(pk=record.pk).exists():
                    raise PermissionDenied("Correction evidence, revision parent, and target must remain readable.")
            retained_sources = tuple(original.sources.db_manager(alias).lock_if_supported().order_by("position"))
            file_model = registry.get_model("storage", "File")
            part_model = registry.get_model("messaging", "Part")
            fragment_model = registry.get_model("messaging", "Fragment")
            file_ids = {source.file_id for source in retained_sources if source.file_id is not None}
            part_ids = {source.message_part_id for source in retained_sources if source.message_part_id is not None}
            files = {
                row.pk: row
                for row in system_queryset(file_model, using=alias, lock=("self",))
                .filter(
                    pk__in=file_ids,
                )
                .order_by("pk")
            }
            parts = {
                row.pk: row
                for row in system_queryset(part_model, using=alias, lock=("self",))
                .filter(
                    pk__in=part_ids,
                )
                .order_by("pk")
            }
            if set(files) != file_ids or set(parts) != part_ids:
                raise ValidationError({"extraction": "The retained extraction source is unavailable."})
            fragment_ids = {part.fragment_id for part in parts.values() if part.fragment_id is not None}
            fragments = {
                row.pk: row
                for row in system_queryset(fragment_model, using=alias, lock=("self",))
                .filter(
                    pk__in=fragment_ids,
                )
                .order_by("pk")
            }
            if set(fragments) != fragment_ids:
                raise ValidationError({"extraction": "The retained extraction fragment is unavailable."})
            for part in parts.values():
                if part.fragment_id is not None:
                    part.fragment = fragments[part.fragment_id]
            for source in retained_sources:
                if source.file_id is not None:
                    source.file = files[source.file_id]
                if source.message_part_id is not None:
                    source.message_part = parts[source.message_part_id]

            original_ref = record_ref_for(original)
            decision_ref = record_ref_for(decision)
            normalized_schema = _validated_schema(original.schema)
            schema_id = str(normalized_schema.get("$id") or normalized_schema.get("x-version") or "")
            if (
                not schema_id
                or schema_id != str(original.schema_id)
                or canonical_json_sha256(normalized_schema) != str(original.schema_digest)
            ):
                raise ValidationError({"extraction": "The retained extraction schema identity is invalid."})
            normalized_result = _json_object(result, field="result")
            errors = sorted(
                Draft202012Validator(normalized_schema).iter_errors(normalized_result),
                key=lambda error: list(error.path),
            )
            if errors:
                raise ValidationError({"result": "Corrected output does not match the retained extraction schema."})

            source_facts = _retained_source_facts(retained_sources)
            target_ref = record_ref_for(target)
            lineage_key = _lineage_key(source_facts=source_facts, target_ref=target_ref)
            if lineage_key != str(original.lineage_key):
                raise ValidationError({"extraction": "The retained extraction source identity is invalid."})

            original_provenance = _json_object(original.provenance, field="extraction")
            retirement = dict(retired_identities or {})
            effective_mapping = manager.correction_identity_mapping(
                original,
                normalized_result,
                identity_mapping=identity_mapping,
                retired_identities=retirement,
            )
            claims = _unchanged_claims(
                original_provenance.get("claims", {}),
                before=original.result,
                after=normalized_result,
                original_refs=original.document_refs,
                identity_mapping=effective_mapping,
                retired_identities=retirement,
            )
            corrections = original_provenance.get("corrections", [])
            if not isinstance(corrections, list) or not all(isinstance(entry, Mapping) for entry in corrections):
                raise ValidationError({"extraction": "The retained correction provenance is invalid."})
            carried_corrections = _unchanged_corrections(
                corrections,
                before=original.result,
                after=normalized_result,
                original_refs=original.document_refs,
                identity_mapping=effective_mapping,
                retired_identities=retirement,
            )
            changed_paths = _changed_fact_pointers(
                original.result,
                normalized_result,
                original_refs=original.document_refs,
                identity_mapping=effective_mapping,
            )
            confirmed = _confirmed_fact_pointers(
                confirmed_paths,
                before=original.result,
                after=normalized_result,
                original_refs=original.document_refs,
                identity_mapping=effective_mapping,
                retired_identities=retirement,
            )
            correction = {
                "kind": "human_correction",
                "original_extraction_id": original_ref.public_id,
                "original_extraction_revision": original.revision,
                "decision_id": decision_ref.public_id,
                "decision_resolved_by": str(decision.resolved_by),
                "recorded_by": str(to_subject_ref(actor)),
                "corrected_paths": sorted(changed_paths | confirmed),
                "result_digest": canonical_json_sha256(normalized_result),
            }
            if revision_parent.pk != original.pk:
                revision_parent_ref = record_ref_for(revision_parent)
                correction.update(
                    revision_parent_extraction_id=revision_parent_ref.public_id,
                    revision_parent_extraction_revision=revision_parent.revision,
                )
            provenance = {
                **original_provenance,
                "claims": claims,
                "used_model_roles": [],
                "unresolved_reasons": _reviewed_correction_unresolved_reasons(original_provenance),
                "corrections": [*carried_corrections, correction],
            }
            reuse_basis = {
                "extraction_id": original_ref.public_id,
                "extraction_revision": original.revision,
                "decision_id": decision_ref.public_id,
            }
            if revision_parent.pk != original.pk:
                reuse_basis.update(
                    revision_parent_id=str(revision_parent.sqid),
                    revision_parent_revision=revision_parent.revision,
                )
            reuse_key = canonical_json_sha256({"human_correction": reuse_basis})
            return manager.create_revision_from_evidence(
                original,
                revision_parent=revision_parent,
                lineage_key=lineage_key,
                reuse_key=reuse_key,
                expected_base_id=original.pk,
                expected_head_id=revision_parent.pk,
                identity_mapping=effective_mapping,
                retired_identities=retirement,
                status="succeeded",
                error_code="",
                schema_id=schema_id,
                schema=normalized_schema,
                schema_digest=str(original.schema_digest),
                engine=str(original.engine),
                model_id=original.model_id,
                recognition_model_id=original.recognition_model_id,
                engine_config=original.engine_config,
                result=normalized_result,
                provenance=provenance,
                content_type_id=original.content_type_id,
                object_id=original.object_id,
                created_by_id=actor_user_id(run.admission_actor_subject()),
            )

    def reviewed_correction_authority(
        self,
        successor: Any,
        *,
        actor: Any,
        expected_action: str,
        expected_resolution_action: str,
        decision: Any | None = None,
    ) -> tuple[Any, Any]:
        """Resolve and validate one direct human-reviewed revision and its Decision."""

        if (
            not isinstance(expected_action, str)
            or not expected_action.strip()
            or not isinstance(expected_resolution_action, str)
            or not expected_resolution_action.strip()
        ):
            raise ValueError("Reviewed correction actions must be explicit.")
        original, revision_parent, correction = (
            self._reviewed_correction_revision_basis(successor, actor=actor, using=self._db)
        )
        decision_model = self.model._meta.apps.get_model("workflows", "Decision")
        if decision is None:
            with system_context(reason="workflows_extraction.correction.authority"):
                decision = (
                    decision_model._base_manager.using(self._db)
                    .filter(sqid=correction.decision_id)
                    .first()
                )
        if (
            not isinstance(decision, decision_model)
            or decision.pk is None
            or str(decision.sqid) != correction.decision_id
            or not decision.with_actor(actor).has_access("read")
        ):
            raise PermissionDenied(
                "Read access to the reviewed correction Decision is required."
            )
        _binding, bound_parent = self.validate_correction_binding(
            decision.payload,
            extraction=original,
        )
        if bound_parent.pk != revision_parent.pk:
            raise ValidationError(
                {"decision": "The correction Decision names another revision parent."}
            )
        resolution = (
            decision.resolution if isinstance(decision.resolution, Mapping) else {}
        )
        if (
            str(decision.verdict) != "completed"
            or not str(decision.resolved_by or "")
            or decision.action != expected_action
            or resolution.get("action") != expected_resolution_action
        ):
            raise ValidationError(
                {"decision": "The reviewed correction Decision has a different authority basis."}
            )
        return original, decision

    def inference_candidate_selectors(
        self, base: Any
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Expose selectors only for an exact retained correspondence candidate."""

        if (
            not isinstance(base, self.model)
            or base.pk is None
            or base.status != "failed"
            or base.error_code
            != "source_hold:identity_correspondence_required"
            or not isinstance(base.result, dict)
            or not base.result
        ):
            raise ValidationError(
                {"inference": "A retained correspondence candidate is required."}
            )
        return result_selectors(
            base.result,
            base.engine_config.get("evidence_layout", {}),
        )

    def create_revision(
        self,
        *,
        sources: Sequence[DocumentSource],
        pages: Sequence[PageImage],
        page_results: Sequence[PageResult],
        parts: Sequence[DocumentPart],
        **values: Any,
    ) -> Any:
        """Reuse exact requests or allocate the next revision with fresh engine evidence."""

        anchor = next(
            (
                source.file if source.file is not None else source.message_part
                for source in sources
                if source.file is not None or source.message_part is not None
            ),
            None,
        )
        alias = get_write_alias(self.model, bound=self, instance=anchor)
        return self.db_manager(alias)._create_revision(
            values=values,
            evidence=(sources, pages, page_results, parts),
            using=alias,
        )

    def create_revision_from_evidence(
        self,
        original: Any,
        *,
        revision_parent: Any | None = None,
        **values: Any,
    ) -> Any:
        """Allocate a revision by cloning an authorized immutable evidence snapshot.

        ``original`` owns the retained fact/evidence basis. Its exact current
        revision parent may differ only by one validated empty correspondence
        hold frozen into the correction Decision. Exact retries reuse the
        correction already allocated for the same authority key.
        """

        alias = get_write_alias(self.model, bound=self, instance=original)
        manager = self.db_manager(alias)
        revision_parent = revision_parent or original
        if revision_parent.pk != original.pk:
            manager._correction_revision_parent(
                original,
                revision_parent,
                using=alias,
            )
        expected_head_id = values.get("expected_head_id", values.get("expected_base_id"))
        if expected_head_id != revision_parent.pk:
            raise ValidationError({
                "extraction": "The correction revision parent differs from its frozen head."
            })
        return manager._create_revision(
            values=values,
            original=original,
            revision_parent=revision_parent,
            using=alias,
        )

    def _create_revision(
        self,
        *,
        values: dict[str, Any],
        evidence: tuple[
            Sequence[DocumentSource],
            Sequence[PageImage],
            Sequence[PageResult],
            Sequence[DocumentPart],
        ] | None = None,
        original: Any | None = None,
        revision_parent: Any | None = None,
        using: str | None,
    ) -> Any:
        """Own revision locking, retry, reuse, and child persistence once."""

        if (evidence is None) == (original is None):
            raise TypeError("Provide either fresh evidence or one retained extraction.")
        if original is None and revision_parent is not None:
            raise TypeError("Fresh evidence cannot name a retained revision parent.")
        alias = get_write_alias(self.model, using=using, bound=self, instance=original)
        revision_parent = revision_parent or original
        expected_base_id = values.pop("expected_base_id", None)
        expected_head_id = values.pop("expected_head_id", expected_base_id)
        identity_mapping = values.pop("identity_mapping", None)
        retired_identities = values.pop("retired_identities", None)
        if not isinstance(values.get("provenance"), dict):
            raise ValidationError({"extraction": "Retention provenance must be an object."})
        values["provenance"] = {
            **values["provenance"],
            "identity_correspondence": {
                "expected_base_id": expected_base_id,
                "continuing_or_new": dict(identity_mapping or {}),
                "retired": dict(retired_identities or {}),
            },
        }
        with system_context(reason="workflows_extraction.extraction.create_revision"):
            for attempt in range(3):
                try:
                    with transaction.atomic(using=alias):
                        lineage_model = self.model._meta.apps.get_model("workflows_extraction", "ExtractionLineage")
                        lineage = lineage_model._base_manager.using(alias).filter(key=values["lineage_key"]).first()
                        if lineage is None:
                            lineage = lineage_model(key=values["lineage_key"])
                            lineage.allocate(using=alias)
                        lineage = lineage_model._base_manager.using(alias).lock_if_supported().get(pk=lineage.pk)
                        previous: Any = related_on(lineage, "head", using=alias)
                        existing = self.db_manager(alias).filter(reuse_key=values["reuse_key"]).first()
                        unresolved_failure = (
                            values.get("status") == "failed"
                            and (
                                values.get("result") == {}
                                or values.get("error_code")
                                == "source_hold:identity_correspondence_required"
                            )
                            and (
                                previous is not None if existing is None else
                                "last_known_revision" in existing.provenance.get("identity_correspondence", {})
                            )
                        )
                        correspondence_failure = (
                            values.get("error_code")
                            == "source_hold:identity_correspondence_required"
                        )
                        if unresolved_failure:
                            if identity_mapping or retired_identities:
                                raise ValidationError({
                                    "extraction": "A retained source hold cannot remap or retire known identities."
                                })
                            if existing is not None:
                                authority_revision = existing.provenance.get(
                                    "identity_correspondence", {}
                                ).get("last_known_revision")
                            elif correspondence_failure:
                                authority = self.db_manager(alias).filter(
                                    pk=expected_base_id,
                                    lineage_key=values["lineage_key"],
                                    status="succeeded",
                                ).first()
                                if (
                                    authority is None
                                    or previous is None
                                    or authority.revision > previous.revision
                                    or not _same_fact_identity(authority, previous)
                                ):
                                    raise ValidationError({
                                        "extraction": "The request lacks a valid identity authority."
                                    })
                                authority_revision = authority.revision
                            else:
                                authority_revision = previous.revision
                            values["provenance"]["identity_correspondence"][
                                "last_known_revision"
                            ] = authority_revision
                        if existing is not None:
                            return self._validated_reuse(
                                existing,
                                original=original,
                                revision_parent=revision_parent,
                                values=values,
                                evidence=evidence,
                                using=alias,
                            )
                        if (previous.pk if previous is not None else None) != expected_head_id:
                            raise ValidationError({"extraction": "The request's extraction base is no longer current."})
                        if revision_parent is not None and (
                            previous is None or previous.pk != revision_parent.pk
                        ):
                            raise ValidationError({
                                "extraction": "The correction source is no longer the current extraction revision."
                            })
                        if unresolved_failure:
                            document_map = deepcopy(previous.document_map)
                            retired = deepcopy(previous.retired_identities)
                        else:
                            document_map, retired = _document_mapping(
                                values["result"],
                                layout=values["engine_config"].get("evidence_layout", {}),
                                original=(original or previous),
                                identity_mapping=identity_mapping,
                                retired_identities=retired_identities,
                            )
                        extraction = self.model(
                            revision=previous.revision + 1 if previous else 1,
                            document_map=document_map,
                            retired_identities=retired,
                            **values,
                        )
                        extraction.retain(using=alias)
                        if evidence is not None:
                            self._persist_fresh_evidence(extraction, *evidence, using=alias)
                        else:
                            self._clone_retained_evidence(extraction, original, using=alias)
                        lineage.advance_head(extraction, using=alias)
                        return extraction
                except IntegrityError:
                    duplicate = self.db_manager(alias).filter(reuse_key=values["reuse_key"]).first()
                    if duplicate is not None:
                        return self._validated_reuse(
                            duplicate,
                            original=original,
                            revision_parent=revision_parent,
                            values=values,
                            evidence=evidence,
                            using=alias,
                        )
                    if attempt == 2:
                        raise
        raise AssertionError("Unreachable revision allocation state.")

    def _validated_reuse(
        self,
        existing: Any,
        *,
        original: Any | None,
        revision_parent: Any | None,
        values: dict[str, Any],
        evidence: Any,
        using: str | None,
    ) -> Any:
        if (
            existing.lineage_key != values["lineage_key"]
            or any(
                not _field_equal(existing, field, requested)
                for field, requested in values.items()
                if field not in {"created_by_id", "provenance"}
            )
            or existing.schema_digest != values["schema_digest"]
            or not json_values_equal(existing.result, values["result"])
            or not json_values_equal(
                _stable_correction_provenance(existing.provenance),
                _stable_correction_provenance(values["provenance"]),
            )
            or (
                revision_parent is not None
                and existing.revision != revision_parent.revision + 1
            )
        ):
            raise ValidationError({"extraction": "This request identity already owns different retained facts."})
        if evidence is not None and not self._same_fresh_evidence(existing, evidence, using=using):
            raise ValidationError({"extraction": "This request identity already owns different source evidence."})
        return existing

    def _same_fresh_evidence(self, existing: Any, evidence: Any, *, using: str | None) -> bool:
        sources, pages, page_results, parts = evidence
        retained = list(existing.sources.db_manager(using).order_by("position"))
        if len(retained) != len(sources):
            return False
        if any(
            row.position != source.source_position or row.file_id != getattr(source.file, "pk", None)
            or row.message_part_id != getattr(source.message_part, "pk", None)
            or row.content_hash != source.content_hash
            for row, source in zip(retained, sources)
        ):
            return False
        retained_pages = list(existing.pages.db_manager(using).select_related("source").order_by("position"))
        if len(retained_pages) != len(pages) or len(pages) != len(page_results):
            return False
        if any(
            row.source.position != page.source_position or row.source_page != page.page_position
            or row.position != position or row.width != page.width or row.height != page.height
            or row.dpi != page.dpi or row.duration_ms != max(result.duration_ms, 0)
            or not json_values_equal(row.result, result.value)
            or not json_values_equal(row.engine_metadata, result.engine_metadata or {})
            for position, (row, page, result) in enumerate(zip(retained_pages, pages, page_results))
        ):
            return False
        retained_parts = list(existing.parts.db_manager(using).select_related("source").order_by("position"))
        return len(retained_parts) == len(parts) and all(
            row.position == position and row.source.position == part.source_position
            and row.source_page == part.source_page and row.mime_type == part.mime_type
            and row.kind == part.kind and row.method == part.method
            and row.content_hash == part.content_hash and json_values_equal(row.value, part.value)
            and row.width == part.width and row.height == part.height and row.dpi == part.dpi
            and row.duration_ms == max(part.duration_ms, 0)
            and json_values_equal(row.metadata, part.metadata or {})
            and json_values_equal(row.claims, _claims_for_part(existing.claims, position))
            for position, (row, part) in enumerate(zip(retained_parts, parts))
        )

    def _evidence_models(self) -> tuple[type[Any], type[Any], type[Any]]:
        registry = self.model._meta.apps
        return (
            registry.get_model("workflows_extraction", "ExtractionSource"),
            registry.get_model("workflows_extraction", "ExtractionPage"),
            registry.get_model("workflows_extraction", "ExtractionPart"),
        )

    def _persist_fresh_evidence(
        self,
        extraction: Any,
        sources: Sequence[DocumentSource],
        pages: Sequence[PageImage],
        page_results: Sequence[PageResult],
        parts: Sequence[DocumentPart],
        *,
        using: str | None,
    ) -> None:
        source_model, page_model, part_model = self._evidence_models()
        retained_sources = source_model._base_manager.using(using)._retain_rows(
            [
                source_model(
                    extraction=extraction,
                    position=source.source_position,
                    file=source.file,
                    message_part=source.message_part,
                    content_hash=source.content_hash,
                )
                for source in sources
            ]
        )
        source_by_position = {source.position: source for source in retained_sources}
        page_model._base_manager.using(using)._retain_rows(
            [
                page_model(
                    extraction=extraction,
                    source=source_by_position[page.source_position],
                    position=position,
                    source_page=page.page_position,
                    width=page.width,
                    height=page.height,
                    dpi=page.dpi,
                    duration_ms=max(result.duration_ms, 0),
                    result=result.value,
                    engine_metadata=result.engine_metadata or {},
                )
                for position, (page, result) in enumerate(zip(pages, page_results))
            ]
        )
        claims = extraction.provenance["claims"]
        part_model._base_manager.using(using)._retain_rows(
            [
                part_model(
                    extraction=extraction,
                    source=source_by_position[part.source_position],
                    position=position,
                    source_page=part.source_page,
                    mime_type=part.mime_type,
                    kind=part.kind,
                    method=part.method,
                    content_hash=part.content_hash,
                    width=part.width,
                    height=part.height,
                    dpi=part.dpi,
                    value=part.value,
                    claims=_claims_for_part(claims, position),
                    metadata=part.metadata or {},
                    duration_ms=max(part.duration_ms, 0),
                )
                for position, part in enumerate(parts)
            ]
        )

    def _clone_retained_evidence(self, extraction: Any, original: Any, *, using: str | None) -> None:
        source_model, page_model, part_model = self._evidence_models()
        original_sources = list(
            source_model._base_manager.using(using).filter(extraction=original).order_by("position")
        )
        if [source.position for source in original_sources] != list(range(len(original_sources))):
            raise ValidationError({"extraction": "The retained source ordering is invalid."})
        retained_sources = source_model._base_manager.using(using)._retain_rows(
            [
                source_model(
                    extraction=extraction,
                    position=source.position,
                    file_id=source.file_id,
                    message_part_id=source.message_part_id,
                    content_hash=source.content_hash,
                )
                for source in original_sources
            ]
        )
        source_by_id = {
            original_source.pk: retained_source
            for original_source, retained_source in zip(original_sources, retained_sources)
        }
        original_pages = list(
            page_model._base_manager.using(using).filter(extraction=original).order_by("position")
        )
        if [page.position for page in original_pages] != list(range(len(original_pages))):
            raise ValidationError({"extraction": "The retained page ordering is invalid."})
        page_model._base_manager.using(using)._retain_rows(
            [
                page_model(
                    extraction=extraction,
                    source=source_by_id[page.source_id],
                    position=page.position,
                    source_page=page.source_page,
                    width=page.width,
                    height=page.height,
                    dpi=page.dpi,
                    duration_ms=page.duration_ms,
                    result=page.result,
                    engine_metadata=page.engine_metadata,
                )
                for page in original_pages
            ]
        )
        original_parts = list(
            part_model._base_manager.using(using).filter(extraction=original).order_by("position")
        )
        if [part.position for part in original_parts] != list(range(len(original_parts))):
            raise ValidationError({"extraction": "The retained part ordering is invalid."})
        claims = extraction.provenance["claims"]
        part_model._base_manager.using(using)._retain_rows(
            [
                part_model(
                    extraction=extraction,
                    source=source_by_id[part.source_id],
                    position=part.position,
                    source_page=part.source_page,
                    mime_type=part.mime_type,
                    kind=part.kind,
                    method=part.method,
                    content_hash=part.content_hash,
                    width=part.width,
                    height=part.height,
                    dpi=part.dpi,
                    value=part.value,
                    claims=_claims_for_part(claims, part.position),
                    metadata=part.metadata,
                    duration_ms=part.duration_ms,
                )
                for part in original_parts
            ]
        )


class ExtractionSystemManager(ExtractionManager):
    """Expose guarded unscoped rows to Django and field-backed REBAC traversal."""

    def get_queryset(self) -> EvidenceQuerySet:
        return super().get_queryset().system_context(
            reason="workflows_extraction.extraction.base_manager"
        )


def _claims_for_part(claims: dict[str, list[dict[str, Any]]], position: int) -> dict[str, list[dict[str, Any]]]:
    return {
        pointer: matching
        for pointer, entries in claims.items()
        if (matching := [claim for claim in entries if claim["part_position"] == position])
    }


def _stable_correction_provenance(provenance: Any) -> dict[str, Any]:
    """Exclude the first materializer actor from exact authority reuse checks."""

    value = deepcopy(provenance)
    corrections = value.get("corrections")
    if isinstance(corrections, list) and corrections and isinstance(corrections[-1], dict):
        corrections[-1].pop("recorded_by", None)
    return value


def _field_equal(existing: Any, field: str, requested: Any) -> bool:
    if field in {"model", "recognition_model", "content_type"}:
        return getattr(existing, f"{field}_id") == (requested.pk if requested is not None else None)
    if field in {"content_type_id", "created_by_id"}:
        return getattr(existing, field) == requested
    if field == "object_id":
        return str(existing.object_id) == str(requested)
    return json_values_equal(getattr(existing, field), requested)


def _document_mapping(
    result: Any, *, layout: Any, original: Any | None, identity_mapping: Any,
    retired_identities: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Allocate once, or carry reviewed correspondence without matching printed facts."""

    requested = result_selectors(result, layout)
    previous: tuple[DocumentRef, ...] = tuple(original.document_refs) if original is not None else ()
    mapping = dict(identity_mapping or {})
    retirement = dict(retired_identities or {})
    if original is not None and previous and not mapping:
        implicit = implicit_identity_correspondence(
            result, layout=layout, original=original,
        )
        if implicit is not None:
            mapping = implicit
    old_docs = {ref.identity: ref for ref in previous}
    old_lines = {line.identity: line for ref in previous for line in ref.lines}
    if original is not None and len(previous) > 1 and mapping == {} and requested:
        raise ValidationError({"extraction": "Multiple logical documents require explicit identity correspondence."})
    rows: list[dict[str, Any]] = []
    carried_docs: set[str] = set()
    carried_lines: set[str] = set()
    for selector, line_selectors in requested:
        identity = mapping.get(selector)
        if not previous and identity not in (None, "new"):
            raise ValidationError({"extraction": "Initial document identities are allocated by retention."})
        if (identity is None and len(previous) == len(requested) == 1
                and previous[0].selector == selector):
            identity = previous[0].identity
        if identity == "new" or (identity is None and not previous):
            identity = uuid4().hex
        if identity is None:
            raise ValidationError({"extraction": "A new logical document must be explicitly mapped."})
        if (
            identity in carried_docs
            or identity in old_lines
            or (previous and identity not in old_docs and selector not in mapping)
        ):
            raise ValidationError({"extraction": "The document identity correspondence is invalid."})
        if previous and identity not in old_docs and mapping.get(selector) != "new":
            raise ValidationError({"extraction": "A newly introduced document must be declared new."})
        carried_docs.add(identity)
        prior_lines = old_docs[identity].lines if identity in old_docs else ()
        if len(prior_lines) > 1 and not all(selector in mapping for selector in line_selectors):
            raise ValidationError({"extraction": "Multiple source lines require explicit identity correspondence."})
        line_rows = []
        for line_selector in line_selectors:
            line_id = mapping.get(line_selector)
            if not previous and line_id not in (None, "new"):
                raise ValidationError({"extraction": "Initial source-line identities are allocated by retention."})
            if (line_id is None and len(prior_lines) == len(line_selectors) == 1
                    and prior_lines[0].selector == line_selector):
                line_id = prior_lines[0].identity
            if line_id == "new" or (line_id is None and not previous):
                line_id = uuid4().hex
            if line_id is None:
                raise ValidationError({"extraction": "A new source line must be explicitly mapped."})
            if line_id in carried_lines or line_id in old_docs:
                raise ValidationError({"extraction": "The source-line correspondence is invalid."})
            if previous and line_id not in old_lines and mapping.get(line_selector) != "new":
                raise ValidationError({"extraction": "A newly introduced source line must be declared new."})
            carried_lines.add(line_id)
            line_rows.append({"identity": line_id, "selector": line_selector})
        rows.append({"identity": identity, "selector": selector, "lines": line_rows})
    if set(mapping) - {selector for selector, lines in requested for selector in (selector, *lines)}:
        raise ValidationError({"extraction": "Identity mapping references absent selectors."})
    absent_docs = set(old_docs) - carried_docs
    absent_lines = set(old_lines) - carried_lines
    if set(retirement) != absent_docs | absent_lines or any(
        not isinstance(reason, str) or not reason.strip() for reason in retirement.values()
    ):
        raise ValidationError({"extraction": "Retired identities need explicit retained reasons."})
    retired = [
        {
            "identity": identity,
            "kind": RetiredIdentityKind.DOCUMENT if identity in absent_docs else RetiredIdentityKind.LINE,
            "reason": reason,
        }
        for identity, reason in sorted(retirement.items())
    ]
    return rows, retired
