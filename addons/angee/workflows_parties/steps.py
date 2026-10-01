"""Typed human reviews that delegate identity and duplicate writes to parties."""

from dataclasses import replace
from typing import Any, Literal

from django.apps import apps
from pydantic import BaseModel, ConfigDict, Field

from angee.base.identity import public_id_of
from angee.decisions.contracts import DecisionContext, DecisionFact, DecisionRecordReference, DecisionRequest
from angee.decisions.forms import Action
from angee.decisions.managers import ResolvedDecision
from angee.decisions.states import Verdict
from angee.parties.backends import ParsedAddress
from angee.workflows.reviews import ReviewStep
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


class IdentityBasis(BaseModel):
    """Actor-visible identity facts with the parties owner's complete-state digest."""

    model_config = ConfigDict(extra="forbid")
    party_id: str
    proposed: IdentityProposal
    current: dict[str, Any]
    facts_hash: str
    context: dict[str, Any] = {}


class IdentityOutput(BaseModel):
    """The retained party and the result of each requested domain operation."""

    model_config = ConfigDict(extra="forbid")
    party_id: str
    context: dict[str, Any] = {}
    name_result: str | None = None
    address_result: str | None = None
    handle_result: str | None = None


class ApplyIdentity(Action, key="apply_identity", label="Apply identity choices",
                    verdict=Verdict.COMPLETED, outcome="applied"):
    """Explicit field choices, leaving every unselected fact unchanged."""

    name_action: Literal["keep", "replace"] = Field(default="keep", title="Name")
    address_action: Literal["keep", "add", "replace"] = Field(default="keep", title="Address")
    handle_action: Literal["keep", "confirm", "dismiss"] = Field(default="keep", title="Contact")


class RejectIdentity(Action, key="reject_identity", label="Reject identity change",
                     verdict=Verdict.REJECTED, outcome="rejected"):
    """Retain the reason for leaving the current identity unchanged."""

    note: str = Field(min_length=1, json_schema_extra={"widget": "textarea"})


class EscalateIdentity(Action, key="escalate_identity", label="Escalate identity review",
                       verdict=Verdict.ESCALATED, outcome="escalated"):
    """Route a proposal requiring another review with its explanation."""

    note: str = Field(min_length=1, json_schema_extra={"widget": "textarea"})


class IdentityReview(ReviewStep[IdentityInput, IdentityOutput, IdentityReviewConfig, IdentityBasis]):
    """Ask and apply one proposal; stale complete identity state routes to conflict."""

    key = "parties_identity_review"
    label = "Review party identity"
    actions = (ApplyIdentity, RejectIdentity, EscalateIdentity)
    outcomes = {"unchanged": "Identity unchanged", "conflict": "Identity changed during review"}

    def ask(self, ctx: Any) -> Settlement:
        """Freeze readable facts; self-service directory review explicitly allows the requester."""
        proposal = ctx.input.proposed
        proposal = proposal.model_copy(update={"address": replace(
            proposal.address, label=proposal.address.label or ctx.config.default_address_label,
        )})
        owner = apps.get_model("parties", "Party").objects
        identity = owner.identity_basis(ctx.input.party_id, actor=ctx.actor)
        basis = IdentityBasis(
            party_id=ctx.input.party_id, proposed=proposal, current=identity.current,
            facts_hash=identity.facts_hash, context=ctx.input.context,
        )
        if not identity.party.identity_differs(identity.current, proposal.model_dump()):
            return ctx.done(IdentityOutput(party_id=basis.party_id, context=basis.context), outcome="unchanged")
        reference = DecisionRecordReference(
            model="parties.Party", id=basis.party_id, label=ctx.config.party_label, tab="identity",
        )
        references = tuple(
            DecisionRecordReference(model=model, id=row["id"])
            for key, model in (("addresses", "parties.Address"), ("handles", "parties.PartyHandle"))
            for row in identity.current[key]
        ) + tuple(DecisionRecordReference(model="parties.Handle", id=row["handle_id"])
                  for row in identity.current["handles"])
        assignee = ctx.load(apps.get_model("iam", "User"), ctx.input.assignee) if ctx.input.assignee else ctx.actor
        return ctx.ask(DecisionRequest(
            kind="review-party-identity", subject=identity.party, assignees=(assignee,),
            requester=None, actions=self.actions, basis=basis,
            context=DecisionContext(references=references, facts=(
                DecisionFact(pointer="/current", label=f"Current {ctx.config.party_label} identity",
                             value=identity.current, subject=reference, authority="source"),
                DecisionFact(pointer="/proposed", label=f"Proposed {ctx.config.party_label} identity",
                             value=proposal.model_dump(mode="json"), authority="unverified",
                             evidence=ctx.input.evidence),
            )),
        ))

    def apply(self, ctx: Any, settled: list[ResolvedDecision]) -> Settlement:
        """Apply as the recorded resolver through the complete-basis domain fence."""
        answer = settled[0]
        basis, action = answer.basis, answer.action
        assert action is not None  # This review has one seat; unanswered closures never call apply.
        output = {"party_id": basis.party_id, "context": basis.context}
        if not isinstance(action, ApplyIdentity):
            return ctx.done(output, outcome=action.outcome)
        outcome, results = apps.get_model("parties", "Party").objects.apply_identity(
            party_id=basis.party_id, expected_facts_hash=basis.facts_hash,
            proposed=basis.proposed.model_dump(), choices=action.model_dump(), actor=answer.resolver,
        )
        return ctx.done({**output, **results}, outcome=outcome)


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
        candidates = apps.get_model("parties", "Party").objects.with_actor(ctx.actor).duplicate_candidates(
            limit=ctx.config.limit,
        )
        return ctx.done([DuplicatePair(
            left=public_id_of(pair.left), right=public_id_of(pair.right),
            left_name=pair.left.display_name, right_name=pair.right.display_name,
            evidence=f"Shared handle: {pair.normalized_value}",
            evidence_refs=tuple(DecisionRecordReference(model="parties.Handle", id=public_id_of(handle))
                                for handle in pair.handles),
        ) for pair in candidates])


class DuplicateReviewInput(DedupeInput):
    """One scan candidate with its reviewer, supplied by the map binding."""

    pair: DuplicatePair


class DuplicateOutput(BaseModel):
    """The parties-owned merge or separation result, or the reviewer's skip."""

    model_config = ConfigDict(extra="forbid")
    result: str


class MergeParties(Action, key="merge_parties", label="Merge parties",
                   verdict=Verdict.COMPLETED, outcome="merged"):
    """Choose the party to retain; both identities remain on the frozen basis."""

    survivor: Literal["left", "right"] = "left"


class KeepPartiesSeparate(Action, key="keep_parties_separate", label="Keep separate",
                          verdict=Verdict.COMPLETED, outcome="kept_separate"):
    """Remember a durable veto through the parties owner."""


class SkipDuplicatePair(Action, key="skip_duplicate_pair", label="Decide later",
                       verdict=Verdict.COMPLETED, outcome="skipped"):
    """Leave the pair eligible for a later scan."""


class DedupeReview(ReviewStep[DuplicateReviewInput, DuplicateOutput, None, DuplicatePair]):
    """Review each mapped pair independently and apply only its retained answer."""

    key = "parties_dedupe_review"
    label = "Review duplicate parties"
    actions = (MergeParties, KeepPartiesSeparate, SkipDuplicatePair)

    def ask(self, ctx: Any) -> Settlement:
        """Check both records through standing access before admitting the pair."""
        pair = ctx.input.pair
        left = ctx.load(apps.get_model("parties", "Party"), pair.left)
        return ctx.ask(DecisionRequest(
            kind="review-dupe-party", subject=left,
            assignees=(ctx.load(apps.get_model("iam", "User"), ctx.input.assignee),), requester=None,
            actions=self.actions, basis=pair,
            context=DecisionContext(references=(
                DecisionRecordReference(model="parties.Party", id=pair.left, label=pair.left_name),
                DecisionRecordReference(model="parties.Party", id=pair.right, label=pair.right_name),
            ), facts=(DecisionFact(pointer="/evidence", label="Shared identity evidence",
                                   value=pair.evidence, evidence=pair.evidence_refs, authority="source"),)),
        ))

    def apply(self, ctx: Any, settled: list[ResolvedDecision]) -> Settlement:
        """Delegate merge and veto authorization and atomic writes to the parties owner."""
        answer = settled[0]
        pair, action = answer.basis, answer.action
        assert action is not None  # This review has one seat; unanswered closures never call apply.
        if isinstance(action, SkipDuplicatePair):
            return ctx.done(DuplicateOutput(result="skipped"), outcome=action.outcome)
        result = apps.get_model("parties", "Party").objects.apply_duplicate_pair(
            left_id=pair.left, right_id=pair.right,
            survivor=action.survivor if isinstance(action, MergeParties) else "left",
            action="merge" if isinstance(action, MergeParties) else "keep_separate", actor=answer.resolver,
        )
        return ctx.done(DuplicateOutput(result=result), outcome=action.outcome)
