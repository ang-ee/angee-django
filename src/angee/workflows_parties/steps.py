"""Typed human reviews that delegate identity and same_as writes to parties."""

from dataclasses import replace
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, Field

from angee.base.models.identity import public_id_of
from angee.decisions.contracts import (
    DecisionContext,
    DecisionFact,
    DecisionProposal,
    DecisionRecordReference,
    DecisionRequest,
)
from angee.parties.backends import ParsedAddress
from angee.provenance.evidence import FactAuthority
from angee.workflows.decision_steps import DecisionStep
from angee.workflows.steps import Settlement, Step


class ProposedHandle(BaseModel):
    """An existing association whose confirmation is being reviewed."""

    model_config = ConfigDict(extra="forbid")
    statement_id: str = ""
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
        party_id = public_id_of(identity.party)
        records = [identity.party]
        alternatives = []
        if proposed.name and proposed.name != identity.current["name"]:
            alternatives.append(
                {
                    "key": "name",
                    "label": "Use the proposed name",
                    "outcome": "applied",
                    "actions": {party_id: {"fields": {"display_name": {}}}},
                }
            )
        if any(getattr(proposed.address, name) for name in apps.get_model("parties", "Address").objects.components):
            alternatives.append({"key": "address", "label": "Add the proposed address", "outcome": "applied"})
        if proposed.handle.statement_id:
            statement = identity.party.statements.for_reader(ctx.actor).from_public_id(proposed.handle.statement_id)
            if statement is None:
                raise ValidationError("The proposed handle assertion is not readable on this Party.")
            records.append(statement.handle)
            alternatives.append({"key": "handle", "label": "Confirm the proposed handle", "outcome": "applied"})
        alternatives.extend(
            [
                {"key": "keep", "label": "Keep what is on the record", "outcome": "unchanged"},
                {"key": "reject", "label": "Reject the proposal", "outcome": "rejected"},
                {"key": "escalate", "label": "Escalate the review", "outcome": "escalated"},
            ]
        )
        return ctx.ask(
            DecisionRequest(
                kind="review-party-identity",
                records=tuple(records),
                assignees=(assignee,),
                requester=None,
                proposal=DecisionProposal.model_validate({"multiple": True, "alternatives": alternatives}),
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
                                for model, key in (("parties.Handle", "handle_id"),)
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
            state={"proposed": proposed.model_dump(mode="json"), "facts_hash": identity.facts_hash},
        )

    def apply(self, ctx: Any, decision: Any) -> str:
        chosen = set(decision.verdict or ())
        changes = chosen & {"name", "address", "handle"}
        other = chosen - changes
        if other and (changes or len(other) != 1):
            raise ValidationError("Choose identity changes together, or one keep, reject or escalate alternative.")
        if not changes:
            ctx.identity_results = {}
            return {"keep": "unchanged", "reject": "rejected", "escalate": "escalated"}[next(iter(other))]
        party = ctx.load(apps.get_model("parties", "Party"), ctx.input.party_id)
        addresses = apps.get_model("parties", "Address").objects.with_actor(ctx.actor).filter(party=party)
        existing_addresses = set(addresses.values_list("pk", flat=True)) if "address" in changes else set()
        outcome, ctx.identity_results = party.apply_identity(
            expected_facts_hash=ctx.state["facts_hash"],
            proposed=IdentityProposal.model_validate(ctx.state["proposed"]).model_dump(),
            choices={
                "name_action": "replace" if "name" in changes else "keep",
                "address_action": "add" if "address" in changes else "keep",
                "handle_action": "confirm" if "handle" in changes else "keep",
            },
        )
        if outcome == "conflict":
            raise ValidationError("The party identity changed during review; reload and review its current facts.")
        ctx.record(party, operation="changed")
        if "address" in changes:
            for address in addresses.exclude(pk__in=existing_addresses):
                ctx.record(address, operation="created")
        return outcome

    def continue_with(self, ctx: Any, decision: Any, outcome: str) -> Settlement:
        return ctx.done(
            {"party_id": ctx.input.party_id, "context": ctx.input.context, **ctx.identity_results}, outcome=outcome
        )


class SameAsPair(BaseModel):
    """Immutable identities and scan evidence for one mapped review."""

    model_config = ConfigDict(extra="forbid")
    left: str
    right: str
    left_name: str
    right_name: str
    evidence: str
    evidence_refs: tuple[DecisionRecordReference, ...]


class DedupeInput(BaseModel):
    """A reviewer for every candidate in a bounded same_as scan."""

    model_config = ConfigDict(extra="forbid")
    assignee: str = Field(min_length=1)


class DedupeConfig(BaseModel):
    """The parties owner's existing bounded batch size."""

    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=50, ge=1, le=100)


class DedupeScan(Step[DedupeInput, list[SameAsPair], DedupeConfig]):
    """Read deterministic actor-visible candidates; map owns fan-out and collection."""

    key = "parties_dedupe_scan"
    label = "Find same identity statements"

    def run(self, ctx: Any) -> Settlement:
        """Read pending same_as statements through the parties owner."""
        candidates = (
            apps.get_model("parties", "Party")
            .objects.with_actor(ctx.actor)
            .same_as_pairs(
                limit=ctx.config.limit,
            )
        )
        return ctx.done(
            [
                SameAsPair(
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


class SameAsReviewInput(DedupeInput):
    """One scan candidate with its reviewer, supplied by the map binding."""

    pair: SameAsPair


class SameAsOutput(BaseModel):
    """The parties-owned merge or separation result, or the reviewer's skip."""

    model_config = ConfigDict(extra="forbid")
    result: str


class DedupeReview(DecisionStep[SameAsReviewInput, SameAsOutput, None]):
    """One independent decision for each mapped pair."""

    key = "parties_dedupe_review"
    label = "Review same identity statements"
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

    def continue_with(self, ctx: Any, decision: Any, outcome: str) -> Settlement:
        if outcome == "skipped":
            return ctx.done(SameAsOutput(result="skipped"), outcome="skipped")
        pair = ctx.input.pair
        result = apps.get_model("parties", "Party").objects.apply_same_as_pair(
            left_id=pair.left,
            right_id=pair.right,
            survivor="right" if outcome == "merge_right" else "left",
            action="keep_separate" if outcome == "kept_separate" else "merge",
            actor=ctx.actor,
        )
        return ctx.done(SameAsOutput(result=result), outcome=outcome)
