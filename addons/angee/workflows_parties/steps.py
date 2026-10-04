"""Typed human reviews that delegate identity and duplicate writes to parties."""

from dataclasses import replace
from typing import Any

from django.apps import apps
from pydantic import BaseModel, ConfigDict, Field

from angee.base.evidence import FactAuthority
from angee.base.identity import public_id_of
from angee.decisions.contracts import (
    DecisionContext,
    DecisionFact,
    DecisionProposal,
    DecisionRecordReference,
    DecisionRequest,
)
from angee.parties.backends import ParsedAddress
from angee.workflows.reviews import DecisionStep
from angee.workflows.steps import Settlement, Step


class ProposedHandle(BaseModel):
    """An existing association whose confirmation is being reviewed."""

    model_config = ConfigDict(extra="forbid")
    party_handle_id: str = ""
    evidence: str = ""


class IdentityProposal(BaseModel):
    """Proposed native identity values; address coercion belongs to parties."""

    model_config = ConfigDict(extra="forbid")
    name: str = ""
    address: ParsedAddress = Field(default_factory=ParsedAddress)
    handle: ProposedHandle = Field(default_factory=ProposedHandle)


class IdentityInput(BaseModel):
    """One identity proposal, optional reviewer and caller-owned continuation facts."""

    model_config = ConfigDict(extra="forbid")
    party_id: str = Field(min_length=1)
    proposed: IdentityProposal
    assignee: str = ""
    context: dict[str, Any] = {}
    evidence: tuple[DecisionRecordReference, ...] = ()


class IdentityReviewConfig(BaseModel):
    """Presentation for a shared identity review, independent of its consumer."""

    model_config = ConfigDict(extra="forbid")
    party_label: str = Field(default="Party", min_length=1)
    default_address_label: str = Field(default="Primary", min_length=1, max_length=64)


class IdentityOutput(BaseModel):
    """The retained party and the result of each requested domain operation."""

    model_config = ConfigDict(extra="forbid")
    party_id: str
    context: dict[str, Any] = {}
    name_result: str | None = None
    address_result: str | None = None
    handle_result: str | None = None


class IdentityReview(DecisionStep[IdentityInput, IdentityOutput, IdentityReviewConfig]):
    """Choose fixed identity changes; free corrections are direct directory edits."""

    key = "parties_identity_review"
    label = "Review party identity"
    outcomes = {"applied": "Applied", "unchanged": "Unchanged", "rejected": "Rejected", "escalated": "Escalated"}

    def ask(self, ctx: Any) -> Settlement:
        proposed = ctx.input.proposed
        proposed = proposed.model_copy(
            update={
                "address": replace(
                    proposed.address,
                    label=proposed.address.label or ctx.config.default_address_label,
                )
            }
        )
        identity = apps.get_model("parties", "Party").objects.identity_basis(ctx.input.party_id, actor=ctx.actor)
        if not identity.party.identity_differs(identity.current, proposed.model_dump()):
            return ctx.done(IdentityOutput(party_id=ctx.input.party_id, context=ctx.input.context), outcome="unchanged")
        assignee = ctx.load(apps.get_model("iam", "User"), ctx.input.assignee) if ctx.input.assignee else ctx.actor
        actions = (
            {public_id_of(identity.party): {"fields": {"display_name": {"set": proposed.name}}}}
            if proposed.name
            else {}
        )
        return ctx.ask(
            DecisionRequest(
                kind="review-party-identity",
                records=(identity.party,),
                assignees=(assignee,),
                requester=None,
                proposal=DecisionProposal.model_validate(
                    {
                        "alternatives": (
                            {
                                "key": "apply",
                                "label": "Use the proposed identity",
                                "actions": actions,
                                "outcome": "applied",
                            },
                            {"key": "keep", "label": "Keep what is on the record", "outcome": "unchanged"},
                            {"key": "reject", "label": "Reject the proposal", "outcome": "rejected"},
                            {"key": "escalate", "label": "Escalate the review", "outcome": "escalated"},
                        )
                    }
                ),
                context=DecisionContext(
                    facts=(
                        DecisionFact(
                            pointer="/current",
                            label="Current identity",
                            value=identity.current,
                            authority=FactAuthority.SOURCE,
                            evidence=tuple(
                                DecisionRecordReference(model="parties.Address", id=row["id"])
                                for row in identity.current["addresses"]
                            )
                            + tuple(
                                DecisionRecordReference(model=model, id=row[key])
                                for row in identity.current["handles"]
                                for model, key in (("parties.PartyHandle", "id"), ("parties.Handle", "handle_id"))
                            ),
                        ),
                        DecisionFact(
                            pointer="/proposed",
                            label="Proposed identity",
                            value=proposed.model_dump(mode="json"),
                            authority=FactAuthority.UNVERIFIED,
                            evidence=ctx.input.evidence,
                        ),
                    )
                ),
            ),
            state={"proposed": proposed.model_dump(mode="json")},
        )

    def continue_with(self, ctx: Any, decisions: list[Any], outcomes: set[str]) -> Settlement:
        output = {"party_id": ctx.input.party_id, "context": ctx.input.context}
        outcome = next(iter(outcomes))
        if outcome != "applied":
            return ctx.done(output, outcome=outcome)
        owner = apps.get_model("parties", "Party").objects
        current = owner.identity_basis(ctx.input.party_id, actor=ctx.actor)
        outcome, results = owner.apply_identity(
            party_id=ctx.input.party_id,
            expected_facts_hash=current.facts_hash,
            proposed=IdentityProposal.model_validate(ctx.state["proposed"]).model_dump(),
            choices={
                "name_action": "keep",
                "address_action": "add"
                if any(value for key, value in ctx.state["proposed"]["address"].items() if key != "label")
                else "keep",
                "handle_action": "confirm" if ctx.state["proposed"]["handle"]["party_handle_id"] else "keep",
            },
            actor=ctx.actor,
        )
        name_applied = any(
            alternative.actions
            for alternative in DecisionProposal.model_validate(decisions[0].proposal).choose(decisions[0].verdict)
        )
        return ctx.done(
            {**output, **results, "name_result": "applied" if name_applied else "kept"},
            outcome="applied" if name_applied and outcome == "unchanged" else outcome,
        )


class DuplicatePair(BaseModel):
    """Immutable identities and scan evidence for one mapped review."""

    model_config = ConfigDict(extra="forbid")
    left: str
    right: str
    left_name: str
    right_name: str
    evidence: str
    evidence_refs: tuple[DecisionRecordReference, ...]


class DedupeInput(BaseModel):
    """A reviewer for every candidate in a bounded duplicate scan."""

    model_config = ConfigDict(extra="forbid")
    assignee: str = Field(min_length=1)


class DedupeConfig(BaseModel):
    """The parties owner's existing bounded batch size."""

    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=50, ge=1, le=100)


class DedupeScan(Step[DedupeInput, list[DuplicatePair], DedupeConfig]):
    """Read deterministic actor-visible candidates; map owns fan-out and collection."""

    key = "parties_dedupe_scan"
    label = "Find duplicate parties"

    def run(self, ctx: Any) -> Settlement:
        """Use the parties owner for shared handles, duplicate suppression and vetoes."""
        candidates = (
            apps.get_model("parties", "Party")
            .objects.with_actor(ctx.actor)
            .duplicate_candidates(
                limit=ctx.config.limit,
            )
        )
        return ctx.done(
            [
                DuplicatePair(
                    left=public_id_of(pair.left),
                    right=public_id_of(pair.right),
                    left_name=pair.left.display_name,
                    right_name=pair.right.display_name,
                    evidence=f"Shared handle: {pair.normalized_value}",
                    evidence_refs=tuple(
                        DecisionRecordReference(model="parties.Handle", id=public_id_of(handle))
                        for handle in pair.handles
                    ),
                )
                for pair in candidates
            ]
        )


class DuplicateReviewInput(DedupeInput):
    """One scan candidate with its reviewer, supplied by the map binding."""

    pair: DuplicatePair


class DuplicateOutput(BaseModel):
    """The parties-owned merge or separation result, or the reviewer's skip."""

    model_config = ConfigDict(extra="forbid")
    result: str


class DedupeReview(DecisionStep[DuplicateReviewInput, DuplicateOutput, None]):
    """One independent decision for each mapped pair."""

    key = "parties_dedupe_review"
    label = "Review duplicate parties"
    outcomes = {
        "merge_left": "Merged into the left party",
        "merge_right": "Merged into the right party",
        "kept_separate": "Kept separate",
        "skipped": "Skipped",
    }

    def ask(self, ctx: Any) -> Settlement:
        pair = ctx.input.pair
        left = ctx.load(apps.get_model("parties", "Party"), pair.left)
        right = ctx.load(apps.get_model("parties", "Party"), pair.right)
        return ctx.ask(
            DecisionRequest(
                kind="review-dupe-party",
                records=(left, right),
                assignees=(ctx.load(apps.get_model("iam", "User"), ctx.input.assignee),),
                requester=None,
                proposal=DecisionProposal.model_validate(
                    {
                        "alternatives": (
                            {"key": "merge_left", "label": "Merge into the left party", "outcome": "merge_left"},
                            {"key": "merge_right", "label": "Merge into the right party", "outcome": "merge_right"},
                            {"key": "keep", "label": "Keep separate", "outcome": "kept_separate"},
                            {"key": "skip", "label": "Decide later", "outcome": "skipped"},
                        )
                    }
                ),
                context=DecisionContext(
                    facts=(
                        DecisionFact(
                            pointer="/evidence",
                            label="Shared identity evidence",
                            value=pair.evidence,
                            evidence=pair.evidence_refs,
                            authority=FactAuthority.SOURCE,
                        ),
                    )
                ),
            )
        )

    def continue_with(self, ctx: Any, decisions: list[Any], outcomes: set[str]) -> Settlement:
        outcome = next(iter(outcomes))
        if outcome == "skipped":
            return ctx.done(DuplicateOutput(result="skipped"), outcome="skipped")
        pair = ctx.input.pair
        result = apps.get_model("parties", "Party").objects.apply_duplicate_pair(
            left_id=pair.left,
            right_id=pair.right,
            survivor="right" if outcome == "merge_right" else "left",
            action="keep_separate" if outcome == "kept_separate" else "merge",
            actor=ctx.actor,
        )
        return ctx.done(DuplicateOutput(result=result), outcome=outcome)
