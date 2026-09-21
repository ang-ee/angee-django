"""Workflow step implementations for the parties dedupe flow.

Three implementations compose one graph:
``parties_dedupe_scan`` proposes deterministic duplicate pairs,
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
from typing import Any, TypedDict

from django.apps import apps
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, RootModel
from rebac import system_context

from angee.base.db import get_write_alias, related_on
from angee.base.identity import canonical_subject_ref
from angee.base.serialization import canonical_json_sha256
from angee.workflows.attempts import (
    DecisionGateOutput,
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

    decision_id: int
    pair_index: int


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
        alias = get_write_alias(type(step_run), instance=step_run)
        step = related_on(step_run, "step", using=alias)
        if step is None:
            raise apps.get_model("workflows", "Step").DoesNotExist("StepRun has no step.")
        step_run.step = step

        del now
        limit = positive_int(step_run.step.config.get("limit", 50), "Dedupe scan limit")
        party_model = apps.get_model("parties", "Party")
        with system_context(reason="workflows_parties.dedupe_scan"):
            candidates = party_model.objects.db_manager(alias).duplicate_candidates(limit=limit)
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
        alias = get_write_alias(type(step_run), instance=step_run)
        step = related_on(step_run, "step", using=alias)
        if step is None:
            raise apps.get_model("workflows", "Step").DoesNotExist("StepRun has no step.")
        step_run.step = step

        mode = str(step_run.step.config.get("mode") or "")
        if mode == "prepare":
            return super().run(step_run, now=now)
        return StepResult.done(
            output=_apply_unit(step_run, using=alias),
            outcome="completed",
        )

    def invoke_command(self, step_run: Any, *, decision_id: int, actor: Any, now: datetime) -> StepResult:
        """Prepare plain pair values from a locked, validated workflow resolution."""
        alias = get_write_alias(type(step_run), instance=step_run)

        del now
        with apps.get_model("workflows", "Decision").objects.db_manager(alias).locked_resolution(
            decision_id,
            actor=actor,
            consumer_step_run_id=step_run.pk,
        ) as decision:
            rows = apps.get_model("parties", "Party").objects.db_manager(alias).prepare_duplicate_pairs(
                decision.payload.get("pairs"),
                decision.resolution.get("pairs"),
            )
            return StepResult.done(
                output=[{"decision_id": decision.pk, "pair_index": index} for index, _row in enumerate(rows)],
                outcome="prepared",
            )


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
        alias = get_write_alias(type(step_run), instance=step_run)
        step = related_on(step_run, "step", using=alias)
        if step is None:
            raise apps.get_model("workflows", "Step").DoesNotExist("StepRun has no step.")
        step_run.step = step
        run: Any = related_on(step_run, "run", using=alias)
        del now
        proposal = _identity_input(step_run.input)
        from angee.workflows import engine  # Runtime edge after operation registry loading.

        actor = engine.resolve_workflow_actor(run, using=alias).subject
        party, current = apps.get_model("parties", "Party").objects.db_manager(alias).identity_snapshot(
            proposal["party_id"],
            actor=actor,
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
        if not party.identity_differs(current, proposal["proposed"]):
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
            engine.resolve_workflow_actor(
                proposal.get("assignee") or config.get("assignee") or run, using=alias
            ).subject
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
    replay_mode = RecoveryMode.FRESH
    effect_description = "Applies approved Party, Address, and PartyHandle facts."
    idempotent = True
    gate_step_class = IdentityReviewStepImpl

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        alias = get_write_alias(type(step_run), instance=step_run)
        run: Any = related_on(step_run, "run", using=alias)
        value = _identity_apply_input(step_run.input)
        passthrough = _unchanged_identity_input(value)
        if passthrough is not None:
            from angee.workflows import engine  # Runtime edge; safe after the operation registry imports this module.

            _, current = apps.get_model("parties", "Party").objects.db_manager(alias).identity_snapshot(
                passthrough["party_id"],
                actor=engine.resolve_workflow_actor(run, using=alias).subject,
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

    def invoke_command(self, step_run: Any, *, decision_id: int, actor: Any, now: datetime) -> StepResult:
        """Compose workflow validation with the parties-owned identity operation."""
        alias = get_write_alias(type(step_run), instance=step_run)

        del now
        with apps.get_model("workflows", "Decision").objects.db_manager(alias).locked_resolution(
            decision_id,
            actor=actor,
            consumer_step_run_id=step_run.pk,
        ) as decision:
            payload = decision.payload
            if decision.target_model.lower() != "parties.party" or decision.target_id != payload.get("party_id"):
                raise ValidationError({"party_id": "Identity review does not target its retained Party."})
            outcome, results = apps.get_model("parties", "Party").objects.db_manager(alias).apply_identity(
                party_id=decision.target_id,
                expected_facts_hash=payload["facts_hash"],
                proposed=payload["proposed"],
                choices={name: decision.resolution.get(name, "") for name in _IDENTITY_ACTIONS},
                actor=actor,
            )
            return StepResult.done(
                output={"party_id": decision.target_id, "context": payload["context"], **results},
                outcome=outcome,
            )


_ADDRESS_FIELDS = ("po_box", "extended", "street", "city", "region", "postal_code", "country")


class _IdentityAction(TypedDict):
    label: str
    description: str
    choices: dict[str, str]


_IDENTITY_ACTIONS: dict[str, _IdentityAction] = {
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


def _apply_unit(step_run: Any, *, using: str) -> dict[str, str]:
    """Revalidate the retained pair resolution before calling the domain command."""

    unit = DedupeUnitInput.model_validate(step_run.input)
    decisions = apps.get_model("workflows", "Decision").objects.db_manager(using)
    with system_context(reason="workflows_parties.dedupe_resolver"):
        decision = decisions.get(pk=unit.decision_id)
    actor = decision.resolution_actor_subject()
    with decisions.locked_resolution(unit.decision_id, actor=actor, consumer_step_run_id=step_run.pk) as decision:
        parties = apps.get_model("parties", "Party").objects.db_manager(using)
        pairs = parties.prepare_duplicate_pairs(decision.payload.get("pairs"), decision.resolution.get("pairs"))
        if unit.pair_index < 0 or unit.pair_index >= len(pairs):
            raise ValidationError({"pair_index": "The reviewed pair is unavailable."})
        pair = pairs[unit.pair_index]
        result = parties.apply_duplicate_pair(
            left_id=pair["left"],
            right_id=pair["right"],
            survivor=pair["survivor"],
            action=pair["action"],
            actor=actor,
        )
        return {"action": pair["action"], "result": result}
