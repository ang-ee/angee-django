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
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rebac import actor_context, system_context
from rebac.actors import to_subject_ref

from angee.workflows.steps import DecisionSpec, StepImpl, StepResult, positive_int

_EXECUTE_MODES = frozenset({"prepare", "unit"})
_ACTIONS = ("merge", "skip", "keep_separate")
_SURVIVORS = ("left", "right")
_IDENTITY_KEYS = ("left", "right", "left_name", "right_name", "evidence")


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


class DedupeGateStepImpl(StepImpl):
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

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Validate optional decision action, assignee, and attempt settings."""

        super().validate_config(config)
        if "action" in config and not str(config.get("action") or "").strip():
            raise ValidationError({"config": "Dedupe gate action must be a non-empty string."})
        if "assignee" in config and not str(config.get("assignee") or "").strip():
            raise ValidationError({"config": "Dedupe gate assignee must be a non-empty subject ref."})
        positive_int(config.get("max_attempts", 3), "Dedupe gate max_attempts")

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Author the pair-review form from scan output and suspend one decision."""

        del now
        pairs = _input_pairs(step_run.input)
        config = dict(step_run.step.config)
        assignee = str(config.get("assignee") or _run_owner_subject(step_run.run))
        return StepResult.suspend(
            resume_state={"gate": {"policy": "one_done"}},
            decisions=(
                DecisionSpec(
                    assignees=(assignee,),
                    action=str(config.get("action") or "dedupe-parties"),
                    payload={"pairs": pairs},
                    max_attempts=positive_int(config.get("max_attempts", 3), "Dedupe gate max_attempts"),
                    decision_schema=_dedupe_form_schema(),
                ),
            ),
        )


class DedupeExecuteStepImpl(StepImpl):
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

        del now
        mode = str(step_run.step.config.get("mode") or "")
        if mode == "prepare":
            return StepResult.done(output=_prepared_pairs(step_run.input), outcome="prepared")
        return StepResult.done(
            output=_apply_unit(step_run.input, run=step_run.run),
            outcome="completed",
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

    name = (party.display_name or "").strip()
    has_name = bool(name) and not all(character in "0123456789+ " for character in name)
    return (has_name, party.handle_count, -party.pk)


def _dedupe_form_schema() -> dict[str, Any]:
    """Return the serializable fixed-row pair-review form."""

    return {
        "type": "object",
        "required": ["pairs"],
        "properties": {
            "pairs": {
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
                        "survivor": {
                            "type": "string",
                            "label": "Keep",
                            "enum": list(_SURVIVORS),
                        },
                        "action": {
                            "type": "string",
                            "label": "Action",
                            "enum": list(_ACTIONS),
                        },
                    },
                },
            }
        },
    }


def _run_owner_subject(run: Any) -> str:
    """Return the run creator's REBAC subject ref as the review assignee."""

    return str(to_subject_ref(_run_owner(run)))


def _input_pairs(value: Any) -> list[dict[str, str]]:
    """Return the scan step's pair rows from this step's input."""

    if not isinstance(value, Mapping) or not isinstance(value.get("pairs"), list):
        raise ValidationError({"input": "Dedupe gate input must contain scanned pairs."})
    pairs = [row for row in value["pairs"] if isinstance(row, Mapping)]
    if len(pairs) != len(value["pairs"]) or not pairs:
        raise ValidationError({"input": "Dedupe gate input pairs must be non-empty mappings."})
    return [dict(row) for row in pairs]


def _prepared_pairs(value: Any) -> list[dict[str, str]]:
    """Load the completed decision and return the verified, approved verb list."""

    if not isinstance(value, Mapping) or not isinstance(value.get("decisions"), list):
        raise ValidationError({"input": "Dedupe prepare input must contain decision ids."})
    decision_ids = [str(decision_id) for decision_id in value["decisions"]]
    if not decision_ids or any(not decision_id for decision_id in decision_ids):
        raise ValidationError({"input": "Dedupe prepare input requires completed decisions."})

    decision_model = apps.get_model("workflows", "Decision")
    with system_context(reason="workflows_parties.dedupe_execute.decisions"):
        decisions = {
            str(decision.sqid): decision for decision in decision_model._base_manager.filter(sqid__in=decision_ids)
        }
    if set(decisions) != set(decision_ids):
        raise ValidationError({"input": "Dedupe review decision was not found."})

    approved: list[dict[str, str]] = []
    for decision_id in decision_ids:
        decision = decisions[decision_id]
        verdict = str(getattr(decision.verdict, "value", decision.verdict))
        if verdict != "completed":
            raise ValidationError({"input": "Dedupe review decision must be completed."})
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
                }
            )
    return approved


def _pair_rows(value: Any, *, owner: str) -> list[Mapping[str, Any]]:
    """Return the ``pairs`` rows carried by a decision payload or resolution."""

    if not isinstance(value, Mapping) or not isinstance(value.get("pairs"), list):
        raise ValidationError({"input": f"Dedupe decision {owner} must contain pair rows."})
    rows = [row for row in value["pairs"] if isinstance(row, Mapping)]
    if len(rows) != len(value["pairs"]):
        raise ValidationError({"input": f"Dedupe decision {owner} rows must be mappings."})
    return rows


def _apply_unit(value: Any, *, run: Any) -> dict[str, str]:
    """Apply one approved pair verb idempotently and report the outcome.

    Verbs act AS the run creator — the human whose Decision approved the batch —
    so durable facts (the MergeVeto, the merge audit trail) carry an accountable
    actor instead of an anonymous system write.
    """

    if not isinstance(value, Mapping):
        raise ValidationError({"input": "Dedupe unit input must be one approved pair."})
    action = str(value.get("action") or "")
    survivor_side = str(value.get("survivor") or "")
    if action not in _ACTIONS or action == "skip" or survivor_side not in _SURVIVORS:
        raise ValidationError({"input": "Dedupe unit input carries an unsupported verb."})

    party_model = apps.get_model("parties", "Party")
    with actor_context(_run_owner(run)):
        left = party_model.objects.all().from_public_id(str(value.get("left") or ""))
        right = party_model.objects.all().from_public_id(str(value.get("right") or ""))
        if left is None or right is None:
            raise ValidationError({"input": "Dedupe unit pair references a missing party."})

        if action == "keep_separate":
            apps.get_model("parties", "MergeVeto").objects.veto(left, right)
            return {"action": action, "result": "vetoed"}

        into, source = (left, right) if survivor_side == "left" else (right, left)
        if source.canonical().pk == into.canonical().pk:
            # A retried unit after a crash mid-batch: the merge already landed.
            return {"action": action, "result": "already_merged"}
        party_model.objects.merge(into=into, source=source)
        return {"action": action, "result": "merged"}


def _run_owner(run: Any) -> Any:
    """Return the run creator — the accountable actor for gates and approved verbs."""

    owner_id = getattr(run, "created_by_id", None)
    if owner_id is None:
        raise ValidationError({"run": "Dedupe steps require a run creator."})
    with system_context(reason="workflows_parties.dedupe.run_owner"):
        return get_user_model()._base_manager.get(pk=owner_id)
