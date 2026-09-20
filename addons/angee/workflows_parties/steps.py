"""Workflow step implementations for the parties dedupe flow.

Three implementations compose one graph (the ``workflows_integrate`` archive
canon): ``parties_dedupe_scan`` proposes deterministic duplicate pairs,
``parties_dedupe_gate`` suspends one rows-table Decision the human edits in the
workflows inbox, and ``parties_dedupe_execute`` (``mode=prepare`` then the
stock ``map`` fan-out into ``mode=unit``) applies the approved verbs through
the parties manager owners. Steps run detached under ``system_context`` — the
Decision is the authorization gate (assigned to the run creator), and the
apply step performs only what its resolution approved.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from pydantic import BaseModel, ConfigDict, RootModel
from rebac import SubjectRef, actor_context, system_context

from angee.base.identity import canonical_subject_ref
from angee.base.serialization import canonical_json_sha256
from angee.parties.fields import normalize_country_code
from angee.workflows.attempts import (
    DecisionGateOutput,
    DecisionResolution,
    RecoveryCapability,
    RecoveryMode,
)
from angee.workflows.decision_actions import (
    ReviewAction,
    ReviewFact,
    ReviewRecordReference,
    build_decision_action,
)
from angee.workflows.steps import (
    DecisionApplyStep,
    GateStep,
    StepEffect,
    StepExecutionMode,
    StepImpl,
    StepOutcome,
    StepResult,
    positive_int,
)

_EXECUTE_MODES = frozenset({"prepare", "unit"})
_ACTIONS = ("merge", "skip", "keep_separate")
_SURVIVORS = ("left", "right")
_IDENTITY_KEYS = ("left", "right", "left_name", "right_name", "evidence")


class IdentityReviewPassThrough(BaseModel):
    """Frozen identity facts emitted when no human Decision is required."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    party_id: str
    context: dict[str, Any]
    current: dict[str, Any]
    proposed: dict[str, Any]
    facts_hash: str
    selection_decision_id: str = ""


class IdentityReviewOutput(RootModel[IdentityReviewPassThrough | DecisionGateOutput]):
    """Typed no-change handoff or retained terminal Decision evidence."""


class IdentityApplyInput(
    RootModel[
        IdentityReviewPassThrough | DecisionGateOutput | dict[str, IdentityReviewPassThrough | DecisionGateOutput]
    ]
):
    """Direct or one-predecessor-envelope identity review input."""


class DedupeUnitInput(BaseModel):
    """One approved duplicate-pair verb consumed by the stock Map body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    left: str
    right: str
    survivor: str
    action: str
    resolved_by: str


class DedupeUnitOutput(BaseModel):
    """Journal result of applying one duplicate-pair verb."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str
    result: str


class DedupeExecuteInput(RootModel[DecisionGateOutput | DedupeUnitInput]):
    """Typed prepare-gate or mapped-unit input variants."""


class DedupeExecuteOutput(RootModel[list[DedupeUnitInput] | DedupeUnitOutput]):
    """Typed prepared batch or mapped-unit result variants."""


class IdentityApplyOutput(BaseModel):
    """Party identity application result returned by the domain verbs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    party_id: str
    context: dict[str, Any]
    name_result: str | None = None
    address_result: str | None = None
    handle_result: str | None = None


class DedupeScanStepImpl(StepImpl):
    """Propose the deterministic duplicate-party pairs for one review batch.

    Delegates entirely to ``PartyQuerySet.duplicate_candidates`` (bounded,
    veto-filtered, deterministic order) and adds only the review projection:
    display names, the shared-handle evidence line, and a proposed survivor per
    pair — a real name beats a numeric one, then the richer handle set, then
    the older row (the fyltr ``_pick_primary`` heuristic, parties-native).
    """

    key = "parties_dedupe_scan"
    label = "Scan for duplicate contacts"
    category = "Activity"
    deterministic = False

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Validate the optional pair limit."""

        super().validate_config(config)
        positive_int(config.get("limit", 50), "Dedupe scan limit")

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Emit the proposed pair rows, routing ``found`` or ``empty``."""

        del now
        limit = positive_int(step_run.step.config.get("limit", 50), "Dedupe scan limit")
        party_model = apps.get_model("parties", "Party")
        with system_context(reason="workflows_parties.dedupe_scan"):
            candidates = party_model.objects.duplicate_candidates(limit=limit)
            pairs = [_pair_row(candidate) for candidate in candidates]
        if not pairs:
            return StepResult.done(output={"pairs": []}, outcome="empty")
        return StepResult.done(output={"pairs": pairs}, outcome="found")


class DedupeGateStepImpl(GateStep):
    """Suspend one rows-table Decision over the scanned pair batch.

    The human edits the batch in the workflows inbox: per pair a survivor
    (left/right) and a verb — ``merge``, ``skip`` (decide later; the pair
    resurfaces on the next scan), or ``keep_separate`` (a durable
    ``MergeVeto``; never suggested again). Identity cells are read-only and
    verified again at prepare time, so a tampered resolution never applies.
    """

    key = "parties_dedupe_gate"
    label = "Review duplicate pairs"
    category = "Control"
    config_model = None

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Validate optional decision action, assignee, and attempt settings."""

        super().validate_config(config)
        if "action" in config and not str(config.get("action") or "").strip():
            raise ValidationError({"config": "Dedupe gate action must be a non-empty string."})
        if "assignee" in config and not str(config.get("assignee") or "").strip():
            raise ValidationError({"config": "Dedupe gate assignee must be a non-empty subject ref."})
        positive_int(config.get("max_attempts", 3), "Dedupe gate max_attempts")

    @classmethod
    def gate_config(cls, step_run: Any) -> Mapping[str, Any]:
        """Author one built-in gate config from the admitted scan output."""

        pairs = _input_pairs(step_run.input)
        config = dict(step_run.step.config)
        review = build_decision_action(
            actions=(
                ReviewAction(
                    value="apply_pairs",
                    label="Apply pair decisions",
                    verdict="COMPLETE",
                    fields=("pairs",),
                    required=("pairs",),
                ),
            ),
            properties={"pairs": _dedupe_pairs_schema()},
            payload={"pairs": pairs},
        )
        from angee.workflows import engine  # Runtime edge; safe after the operation registry imports this module.

        assignee = str(engine.resolve_workflow_actor(config.get("assignee") or step_run.run).subject)
        return {
            "policy": "one_done",
            "action": str(config.get("action") or "dedupe-parties"),
            "slots": [{"assignees": [assignee]}],
            "payload": review.payload,
            "max_attempts": positive_int(config.get("max_attempts", 3), "Dedupe gate max_attempts"),
            "decision_schema": review.decision_schema,
        }


class DedupeExecuteStepImpl(DecisionApplyStep):
    """Prepare a confirmed pair batch or execute one stock-map unit.

    ``mode=prepare`` follows the gate and turns its completed decision into a
    plain verb list (skips dropped); the built-in ``map`` step consumes that
    list and targets a second step with ``mode=unit``, which performs one
    idempotent verb: ``merge`` through ``PartyManager.merge`` (already-merged
    pairs report ``already_merged`` on retry) or ``keep_separate`` through
    ``MergeVetoManager.veto`` (idempotent by construction).
    """

    key = "parties_dedupe_execute"
    label = "Apply duplicate decisions"
    category = "Activity"
    deterministic = False
    input_model = DedupeExecuteInput
    output_model = DedupeExecuteOutput
    outcomes = (
        StepOutcome("prepared", "Prepared"),
        StepOutcome("completed", "Completed"),
    )
    effect = StepEffect.WRITE
    execution_mode = StepExecutionMode.DATABASE_COMMAND
    effect_description = "Applies approved Party merge and separation verbs."
    idempotent = True
    gate_step_class = DedupeGateStepImpl

    @classmethod
    def recovery_capability(cls, *, attempt: Any) -> RecoveryCapability:
        """Allow fresh replay only for the idempotent per-pair Map body."""

        config = getattr(getattr(getattr(attempt, "step_run", None), "step", None), "config", None)
        if isinstance(config, Mapping) and config.get("mode") == "unit":
            return RecoveryCapability(RecoveryMode.FRESH)
        return RecoveryCapability(None, "Only an individual duplicate-decision item can be recovered.")

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Require an explicit prepare/unit execution mode."""

        super().validate_config(config)
        mode = str(config.get("mode") or "")
        if mode not in _EXECUTE_MODES:
            expected = ", ".join(sorted(_EXECUTE_MODES))
            raise ValidationError({"config": f"Dedupe execute mode must be one of {expected}."})

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Prepare the confirmed verb list or apply one pair verb."""

        mode = str(step_run.step.config.get("mode") or "")
        if mode == "prepare":
            return super().run(step_run, now=now)
        return StepResult.done(
            output=_apply_unit(step_run.input),
            outcome="completed",
        )

    def apply_resolution(
        self,
        step_run: Any,
        decision: Any,
        admitted: DecisionResolution,
        *,
        actor: Any,
        record_basis: Any,
        now: datetime,
    ) -> StepResult:
        """Verify the frozen pair basis and emit only approved manager verbs."""

        del step_run, actor, record_basis, now
        approved: list[dict[str, str]] = []
        expected_rows = _pair_rows(decision.payload, owner="payload")
        resolved_rows = _pair_rows(decision.resolution, owner="resolution")
        if len(expected_rows) != len(resolved_rows):
            raise ValidationError({"input": "Dedupe resolution must preserve every proposed pair."})
        for expected, resolved in zip(expected_rows, resolved_rows, strict=True):
            for identity_key in _IDENTITY_KEYS:
                if str(resolved.get(identity_key) or "") != str(expected.get(identity_key) or ""):
                    raise ValidationError({"input": "Dedupe resolution changed a proposed pair."})
            action = str(resolved.get("action") or "")
            survivor = str(resolved.get("survivor") or "")
            if action not in _ACTIONS:
                raise ValidationError({"input": f"Dedupe resolution action must be one of {', '.join(_ACTIONS)}."})
            if survivor not in _SURVIVORS:
                raise ValidationError({"input": "Dedupe resolution survivor must be left or right."})
            if action == "skip":
                continue
            approved.append(
                {
                    "left": str(expected["left"]),
                    "right": str(expected["right"]),
                    "survivor": survivor,
                    "action": action,
                    "resolved_by": admitted.resolved_by,
                }
            )
        return StepResult.done(output=approved, outcome="prepared")


class IdentityReviewStepImpl(GateStep):
    """Freeze one Party identity proposal and request explicit human choices."""

    key = "parties_identity_review"
    label = "Review party identity"
    category = "Control"
    outcomes = (
        StepOutcome("unchanged", "No identity change"),
        StepOutcome("completed", "Review completed"),
        StepOutcome("rejected", "Review rejected"),
        StepOutcome("escalated", "Review escalated"),
        StepOutcome("expired", "Review expired"),
    )
    effect = StepEffect.READ
    output_model = IdentityReviewOutput
    effect_description = "Reads Party identity facts and may create a workflow Decision."
    idempotent = True
    config_model = None

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        del now
        proposal = _identity_input(step_run.input)
        authority: SubjectRef | None = None
        if proposal["selection_decision_id"]:
            from angee.workflows import engine  # Runtime edge; safe after the operation registry imports this module.

            authority, selected = engine.target_read_authority(
                step_run,
                ("selection_decision_id",),
                proposal_gate_path=("resolutions", 0, "decision_id"),
            )
            if str(selected.resolution.get("party_id") or "") != proposal["party_id"]:
                raise ValidationError({"selection_decision_id": "Selection Decision chose a different Party."})
        from angee.workflows import engine  # Runtime edge; safe after the operation registry imports this module.

        party, current = _identity_snapshot(
            proposal["party_id"],
            actor=authority or engine.resolve_workflow_actor(step_run.run).actor,
        )
        facts = (
            ReviewFact(
                pointer="/current",
                label="Current Party identity",
                value=current,
                authority="source",
                subject=ReviewRecordReference(
                    model=party._meta.label,
                    id=str(party.sqid),
                    label="Current Party",
                ),
            ),
            ReviewFact(
                pointer="/proposed",
                label="Proposed Party identity",
                value=proposal["proposed"],
                authority="unverified",
                evidence=tuple(
                    ReviewRecordReference(
                        model=item["source_model"],
                        id=item["source_id"],
                        label=item["label"],
                    )
                    for item in proposal["evidence"]
                    if item.get("source_model") and item.get("source_id")
                ),
            ),
        )
        payload = {
            **proposal,
            "current": current,
            "facts_hash": canonical_json_sha256(current),
        }
        if not _identity_differs(current, proposal["proposed"]):
            return StepResult.done(
                output={
                    "party_id": proposal["party_id"],
                    "context": proposal["context"],
                    "current": current,
                    "proposed": proposal["proposed"],
                    "facts_hash": payload["facts_hash"],
                    "selection_decision_id": proposal["selection_decision_id"],
                },
                outcome="unchanged",
            )
        config = dict(step_run.step.config)
        assignee = str(
            engine.resolve_workflow_actor(proposal.get("assignee") or config.get("assignee") or step_run.run).subject
        )
        review = _identity_review_action(payload=payload, facts=facts)
        return type(self).gate_result(
            step_run,
            config={
                "policy": "one_done",
                "action": str(config.get("action") or "review-party-identity"),
                "slots": [{"assignees": [assignee]}],
                "payload": review.payload,
                "max_attempts": positive_int(config.get("max_attempts", 3), "Identity review max_attempts"),
                "decision_schema": review.decision_schema,
                "targets": [
                    {
                        "model": party._meta.label,
                        "id": str(party.sqid),
                        "tab": "identity",
                        "authority_path": ["selection_decision_id"] if proposal["selection_decision_id"] else [],
                        "authority_gate_path": ["resolutions", 0, "decision_id"]
                        if proposal["selection_decision_id"]
                        else [],
                    }
                ],
                "record_access": [{"model": party._meta.label, "id": str(party.sqid)}],
            },
        )


class IdentityApplyStepImpl(DecisionApplyStep):
    """Apply a completed Party identity Decision through native Party owners."""

    key = "parties_identity_apply"
    label = "Apply party identity review"
    category = "Activity"
    deterministic = False
    input_model = IdentityApplyInput
    output_model = IdentityApplyOutput
    outcomes = (
        StepOutcome("applied", "Identity applied"),
        StepOutcome("unchanged", "Identity unchanged"),
        StepOutcome("conflict", "Identity changed during review"),
    )
    effect = StepEffect.WRITE
    execution_mode = StepExecutionMode.DATABASE_COMMAND
    effect_description = "Applies approved Party, Address, and PartyHandle facts."
    idempotent = True
    gate_step_class = IdentityReviewStepImpl

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        value = _identity_apply_input(step_run.input)
        passthrough = _unchanged_identity_input(value)
        if passthrough is not None:
            from angee.workflows import engine  # Runtime edge; safe after the operation registry imports this module.

            _, current = _identity_snapshot(
                passthrough["party_id"],
                actor=engine.resolve_workflow_actor(step_run.run).actor,
            )
            if canonical_json_sha256(current) != passthrough["facts_hash"]:
                return StepResult.done(
                    output={"party_id": passthrough["party_id"], "context": passthrough["context"]},
                    outcome="conflict",
                )
            return StepResult.done(
                output={
                    "party_id": passthrough["party_id"],
                    "context": passthrough["context"],
                    "name_result": "kept",
                    "address_result": "kept",
                    "handle_result": "kept",
                },
                outcome="unchanged",
            )
        return super().run(step_run, now=now)

    def locked_record_basis(
        self,
        step_run: Any,
        predecessor: Any,
        *,
        actor: Any,
    ) -> Any:
        """Lock the exact Party protected by the predecessor Decision."""

        del step_run, actor
        party_model = apps.get_model("parties", "Party")
        with system_context(reason="workflows_parties.identity_apply.lock_basis"):
            party = party_model.objects.lock_if_supported().from_public_id(predecessor.target_id)
        if party is None:
            raise ValidationError({"party_id": "The reviewed Party is unavailable."})
        return (party,)

    def apply_resolution(
        self,
        step_run: Any,
        decision: Any,
        admitted: DecisionResolution,
        *,
        actor: Any,
        record_basis: Any,
        now: datetime,
    ) -> StepResult:
        """Validate frozen facts and dispatch the approved identity manager verbs."""

        del step_run, admitted, now
        payload, resolution = decision.payload, decision.resolution
        if not isinstance(payload, Mapping) or not isinstance(resolution, Mapping):
            raise ValidationError({"input": "Identity review data is invalid."})
        for key in (
            "party_id",
            "proposed",
            "current",
            "evidence",
            "context",
            "assignee",
            "selection_decision_id",
            "facts_hash",
        ):
            if key in resolution and resolution.get(key) != payload.get(key):
                raise ValidationError({"input": "Identity resolution changed frozen review facts."})
        approved = dict(payload)
        for key, field in _IDENTITY_ACTIONS.items():
            choice = str(resolution.get(key) or "")
            if choice not in field["choices"]:
                raise ValidationError({"input": f"Identity resolution has invalid {key}."})
            approved[key] = choice
        party = next(iter(record_basis))
        party, current = _identity_snapshot(str(party.sqid), actor=actor, lock=True)
        if canonical_json_sha256(current) != approved["facts_hash"]:
            return StepResult.done(
                output={"party_id": approved["party_id"], "context": approved["context"]},
                outcome="conflict",
            )
        results = _apply_identity(party, current=current, approved=approved, actor=actor)
        return StepResult.done(
            output={"party_id": approved["party_id"], "context": approved["context"], **results},
            outcome="applied" if any(value not in {"kept", "matched"} for value in results.values()) else "unchanged",
        )


_ADDRESS_FIELDS = ("po_box", "extended", "street", "city", "region", "postal_code", "country")
_IDENTITY_ACTIONS = {
    "name_action": {
        "label": "Supplier name",
        "description": "Keep the current canonical name or use the proposed name from this source.",
        "choices": {
            "keep": "Keep current supplier name",
            "replace": "Use proposed supplier name",
        },
    },
    "address_action": {
        "label": "Supplier address",
        "description": "Keep current addresses, add the proposed address, or replace the primary address.",
        "choices": {
            "keep": "Keep current supplier addresses",
            "add": "Add proposed supplier address",
            "replace": "Replace primary supplier address",
        },
    },
    "handle_action": {
        "label": "Supplier contact",
        "description": (
            "Keep the email or phone at its current confirmation status, confirm it for this supplier, or dismiss it."
        ),
        "choices": {
            "keep": "Keep current contact status",
            "confirm": "Confirm proposed supplier contact",
            "dismiss": "Dismiss proposed supplier contact",
        },
    },
}


def _identity_apply_input(value: Any) -> Any:
    """Unwrap the native one-predecessor join envelope used by alternative edges."""

    if isinstance(value, Mapping) and len(value) == 1:
        nested = next(iter(value.values()))
        if isinstance(nested, Mapping) and ("resolutions" in nested or "party_id" in nested):
            return nested
    return value


def _unchanged_identity_input(value: Any) -> dict[str, Any] | None:
    """Accept only the review step's no-change pass-through envelope."""

    expected = {"party_id", "context", "current", "proposed", "selection_decision_id", "facts_hash"}
    if not isinstance(value, Mapping) or set(value) != expected:
        return None
    party_id, context = str(value.get("party_id") or ""), value.get("context")
    if (
        not party_id
        or not isinstance(context, Mapping)
        or not isinstance(value.get("current"), Mapping)
        or not isinstance(value.get("proposed"), Mapping)
        or not str(value.get("facts_hash") or "")
    ):
        raise ValidationError({"input": "Identity pass-through carries invalid frozen facts."})
    return dict(value)


def _identity_input(value: Any) -> dict[str, Any]:
    """Normalize the JSON-safe identity proposal while leaving context opaque."""

    if not isinstance(value, Mapping) or not str(value.get("party_id") or ""):
        raise ValidationError({"input": "Identity review requires party_id."})
    proposed = value.get("proposed")
    if not isinstance(proposed, Mapping):
        raise ValidationError({"input": "Identity review requires proposed facts."})
    address = proposed.get("address") or {}
    handle = proposed.get("handle") or {}
    evidence = value.get("evidence") or []
    context = value.get("context") or {}
    assignee = str(value.get("assignee") or "")
    selection_decision_id = str(value.get("selection_decision_id") or "")
    if not isinstance(address, Mapping) or not isinstance(handle, Mapping):
        raise ValidationError({"input": "Proposed address and handle must be objects."})
    if not isinstance(evidence, list) or not all(isinstance(item, Mapping) for item in evidence):
        raise ValidationError({"input": "Identity evidence must be a list of objects."})
    if not isinstance(context, Mapping):
        raise ValidationError({"input": "Identity context must be an object."})
    if assignee:
        try:
            canonical_subject_ref(assignee)
        except (TypeError, ValueError) as error:
            raise ValidationError({"input": "Identity assignee must be a subject reference."}) from error
    normalized = {
        "name": " ".join(str(proposed.get("name") or "").split()),
        "address": {
            "label": " ".join(str(address.get("label") or "Billing").split())[:64],
            **{field: " ".join(str(address.get(field) or "").split()) for field in _ADDRESS_FIELDS},
        },
        "handle": {
            "party_handle_id": str(handle.get("party_handle_id") or ""),
            "evidence": str(handle.get("evidence") or ""),
        },
    }
    return {
        "party_id": str(value["party_id"]),
        "proposed": normalized,
        "evidence": [
            {key: str(item.get(key) or "") for key in ("label", "value", "source_model", "source_id")}
            for item in evidence
        ],
        "context": dict(context),
        "assignee": assignee,
        "selection_decision_id": selection_decision_id,
    }


def _identity_snapshot(party_id: str, *, actor: Any, lock: bool = False) -> tuple[Any, dict[str, Any]]:
    """Read the review-relevant Party facts through the actor's scoped owners."""

    party_model = apps.get_model("parties", "Party")
    address_model = apps.get_model("parties", "Address")
    link_model = apps.get_model("parties", "PartyHandle")
    with actor_context(actor):
        parties = party_model.objects.lock_if_supported() if lock else party_model.objects.all()
        party = parties.from_public_id(party_id)
        if party is None or not party.has_access("read"):
            raise ValidationError({"party_id": "Party was not found."})
        addresses = address_model.objects.filter(party=party)
        links = link_model.objects.filter(party=party)
        if lock:
            addresses = addresses.lock_if_supported()
            links = links.lock_if_supported()
        addresses = list(addresses.order_by("is_primary", "sqid"))
        links = list(links.select_related("handle").order_by("sqid"))
    return party, _snapshot_values(party, addresses, links)


def _address_identity_key(address: Mapping[str, Any]) -> tuple[str, ...]:
    country = str(address["country"])
    try:
        country = normalize_country_code(country)
    except ValidationError:
        pass
    return tuple((country if field == "country" else str(address[field])).casefold() for field in _ADDRESS_FIELDS)


def _identity_differs(current: Mapping[str, Any], proposed: Mapping[str, Any]) -> bool:
    if proposed["name"] and proposed["name"] != current["name"]:
        return True
    address = proposed["address"]
    if any(address[field] for field in _ADDRESS_FIELDS):
        proposed_key = _address_identity_key(address)
        if all(_address_identity_key(row) != proposed_key for row in current["addresses"]):
            return True
    link_id = proposed["handle"]["party_handle_id"]
    return bool(link_id and any(row["id"] == link_id and not row["is_confirmed"] for row in current["handles"]))


def _identity_review_action(*, payload: Mapping[str, Any], facts: Any) -> Any:
    """Build the tagged identity actions and typed frozen fact context."""

    editable = {
        name: {
            "type": "string",
            "enum": list(field["choices"]),
            "default": "keep",
            "label": field["label"],
            "description": field["description"],
            "options": [{"value": value, "label": label} for value, label in field["choices"].items()],
        }
        for name, field in _IDENTITY_ACTIONS.items()
    }
    return build_decision_action(
        actions=(
            ReviewAction(
                value="apply_identity",
                label="Apply identity choices",
                verdict="COMPLETE",
                fields=tuple(editable),
                required=tuple(editable),
            ),
            ReviewAction(
                value="reject_identity",
                label="Reject identity change",
                verdict="REJECT",
                fields=("note",),
                required=("note",),
            ),
            ReviewAction(
                value="escalate_identity",
                label="Escalate identity review",
                verdict="ESCALATE",
                fields=("note",),
                required=("note",),
            ),
        ),
        properties={
            **editable,
            "note": {"type": "string", "minLength": 1, "widget": "textarea"},
        },
        payload=payload,
        facts=facts,
    )


def _apply_identity(
    party: Any,
    *,
    current: Mapping[str, Any],
    approved: Mapping[str, Any],
    actor: Any,
) -> dict[str, str]:
    """Apply approved verbs atomically, delegating each fact to its model owner."""

    address_model = apps.get_model("parties", "Address")
    link_model = apps.get_model("parties", "PartyHandle")
    proposed = approved["proposed"]
    results = {"name_result": "kept", "address_result": "kept", "handle_result": "kept"}
    with transaction.atomic(), actor_context(actor):
        locked = party.__class__._base_manager.select_for_update().get(pk=party.pk)
        if not locked.has_access("write"):
            raise ValidationError({"party_id": "Write access to Party is required."})
        locked_addresses = list(
            address_model._base_manager.select_for_update()
            .filter(
                party=locked,
            )
            .order_by("is_primary", "sqid")
        )
        locked_links = list(
            link_model._base_manager.select_for_update()
            .filter(
                party=locked,
            )
            .select_related("handle")
            .order_by("sqid")
        )
        locked_current = _snapshot_values(locked, locked_addresses, locked_links)
        if canonical_json_sha256(locked_current) != approved["facts_hash"]:
            raise ValidationError({"input": "Party identity facts changed during review."})
        if approved["name_action"] == "replace":
            results["name_result"] = party.__class__.objects.replace_name_exact(
                party=locked,
                expected=current["name"],
                proposed=proposed["name"],
                actor=actor,
            )
        address_action = approved["address_action"]
        if address_action == "add":
            status, _ = address_model.objects.attach_exact(
                party=locked,
                values=proposed["address"],
                actor=actor,
                label=proposed["address"]["label"],
                conflict="append",
            )
            results["address_result"] = status
        elif address_action == "replace":
            primary = next((row for row in current["addresses"] if row["is_primary"]), None)
            status, _ = address_model.objects.replace_primary_exact(
                party=locked,
                values=proposed["address"],
                actor=actor,
                expected_id=(address_model.objects.from_public_id(primary["id"]).pk if primary else None),
                label=proposed["address"]["label"],
            )
            results["address_result"] = status
        handle_action = approved["handle_action"]
        if handle_action != "keep":
            link = link_model.objects.select_for_update().from_public_id(proposed["handle"]["party_handle_id"])
            if link is None or link.party_id != locked.pk:
                raise ValidationError({"handle_action": "The proposed PartyHandle changed during review."})
            getattr(link, handle_action)()
            results["handle_result"] = f"{handle_action}ed"
    return results


def _snapshot_values(party: Any, addresses: list[Any], links: list[Any]) -> dict[str, Any]:
    return {
        "name": party.display_name,
        "addresses": [
            {
                "id": str(row.sqid),
                "label": row.label,
                "is_primary": row.is_primary,
                **{field: getattr(row, field) for field in _ADDRESS_FIELDS},
            }
            for row in addresses
        ],
        "handles": [
            {
                "id": str(link.sqid),
                "handle_id": str(link.handle.sqid),
                "platform": str(link.handle.platform),
                "value": link.handle.value,
                "confidence": link.confidence,
                "is_confirmed": link.is_confirmed,
                "is_dismissed": link.is_dismissed,
            }
            for link in links
        ],
    }


def _pair_row(candidate: Any) -> dict[str, str]:
    """Project one duplicate candidate into an editable review row."""

    left, right = candidate.left, candidate.right
    survivor = "left" if _survivor_score(left) >= _survivor_score(right) else "right"
    return {
        "left": str(left.sqid),
        "right": str(right.sqid),
        "left_name": left.display_name,
        "right_name": right.display_name,
        "evidence": f"shared handle {candidate.normalized_value}",
        "survivor": survivor,
        "action": "merge",
    }


def _survivor_score(party: Any) -> tuple[bool, int, int]:
    """Rank a merge survivor: real name, then handle richness, then age."""

    return (party.has_real_name, party.handle_count, -party.pk)


def _dedupe_pairs_schema() -> dict[str, Any]:
    """Return the editable fixed-row property used by the action builder."""

    return {
        "type": "array",
        "widget": "rows",
        "label": "Duplicate pairs",
        "items": {
            "type": "object",
            "required": list(_IDENTITY_KEYS) + ["survivor", "action"],
            "properties": {
                "left": {"type": "string", "label": "Left id", "readOnly": True},
                "right": {"type": "string", "label": "Right id", "readOnly": True},
                "left_name": {"type": "string", "label": "Left", "readOnly": True},
                "right_name": {"type": "string", "label": "Right", "readOnly": True},
                "evidence": {"type": "string", "label": "Evidence", "readOnly": True},
                "survivor": {"type": "string", "label": "Keep", "enum": list(_SURVIVORS)},
                "action": {"type": "string", "label": "Pair action", "enum": list(_ACTIONS)},
            },
        },
    }


def _input_pairs(value: Any) -> list[dict[str, str]]:
    """Return the scan step's pair rows from this step's input."""

    if not isinstance(value, Mapping) or not isinstance(value.get("pairs"), list):
        raise ValidationError({"input": "Dedupe gate input must contain scanned pairs."})
    pairs = [row for row in value["pairs"] if isinstance(row, Mapping)]
    if len(pairs) != len(value["pairs"]) or not pairs:
        raise ValidationError({"input": "Dedupe gate input pairs must be non-empty mappings."})
    return [dict(row) for row in pairs]


def _pair_rows(value: Any, *, owner: str) -> list[Mapping[str, Any]]:
    """Return the ``pairs`` rows carried by a decision payload or resolution."""

    if not isinstance(value, Mapping) or not isinstance(value.get("pairs"), list):
        raise ValidationError({"input": f"Dedupe decision {owner} must contain pair rows."})
    rows = [row for row in value["pairs"] if isinstance(row, Mapping)]
    if len(rows) != len(value["pairs"]):
        raise ValidationError({"input": f"Dedupe decision {owner} rows must be mappings."})
    return rows


def _apply_unit(value: Any) -> dict[str, str]:
    """Apply one approved pair verb idempotently and report the outcome.

    Verbs act as the retained human Decision resolver, so durable facts (the
    MergeVeto and merge audit trail) carry the accountable actor.
    """

    if not isinstance(value, Mapping):
        raise ValidationError({"input": "Dedupe unit input must be one approved pair."})
    action = str(value.get("action") or "")
    survivor_side = str(value.get("survivor") or "")
    if action not in _ACTIONS or action == "skip" or survivor_side not in _SURVIVORS:
        raise ValidationError({"input": "Dedupe unit input carries an unsupported verb."})

    party_model = apps.get_model("parties", "Party")
    from angee.workflows import engine  # Runtime edge; safe after the operation registry imports this module.

    with actor_context(engine.resolve_workflow_actor(str(value.get("resolved_by") or ""), require_person=True).actor):
        left = party_model.objects.all().from_public_id(str(value.get("left") or ""))
        right = party_model.objects.all().from_public_id(str(value.get("right") or ""))
        if left is None or right is None:
            raise ValidationError({"input": "Dedupe unit pair references a missing party."})

        if action == "keep_separate":
            apps.get_model("parties", "MergeVeto").objects.veto(left, right)
            return {"action": action, "result": "vetoed"}

        into, source = (left, right) if survivor_side == "left" else (right, left)
        if source.canonical().pk == into.canonical().pk:
            # Domain identity also handles a fresh recovery after another merge.
            return {"action": action, "result": "already_merged"}
        party_model.objects.merge(into=into, source=source)
        return {"action": action, "result": "merged"}
