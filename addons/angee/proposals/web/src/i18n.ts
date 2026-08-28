import { createNamespaceT } from "@angee/ui";

export const enProposalsMessages: Record<string, string> = {
  "common.name": "Name",
  "common.status": "Status",
  "common.state": "State",
  "common.responder": "Responder",
  "common.party": "Party",
  "common.updatedAt": "Updated",
  "round.group.target": "Solicitation target",
  "round.group.ceremony": "Ceremony",
  "round.group.receipts": "Receipts",
  "round.tabs.topics": "Topics",
  "round.tabs.proposals": "Proposal shells",
  "round.topics.empty": "No comparison topics yet.",
  "round.proposals.empty": "No proposal shells yet.",
  "round.pane.label": "Rounds",
  "round.pane.empty": "No proposal rounds on this item.",
  "round.compare.open": "Compare",
  "round.action.open": "Open round",
  "round.action.openTitle": "Open this proposal round?",
  "round.action.open.facilitatorOnly":
    "The facilitator-only policy keeps every proposal sealed from other responders.",
  "round.action.open.answers":
    "The answers policy reveals submitted topic answers to the round's responders; offer facts remain private.",
  "round.action.open.answersAndTracks":
    "The answers-and-tracks policy reveals submitted topic answers and publishes eligible proposal tracks; offer facts remain private.",
  "round.action.open.unknown":
    "The server will apply the round's selected opening policy.",
  "round.action.close": "Close round",
  "round.action.award": "Award",
  "round.action.noAward": "No award",
  "round.action.outcome": "Outcome",
  "round.action.accepted": "Accepted proposals",
  "round.action.partial": "Partially accepted proposals",
  "round.action.transfer": "Transfer facilitation",
  "round.action.facilitator": "New facilitator",
  "round.action.cancel": "Cancel round",
  "round.action.cancelTitle": "Cancel this proposal round?",
  "round.action.cancelBody":
    "This ends the round without an award. The round cannot be reopened.",
  "round.action.failed": "The round action returned no result.",
  "round.action.invalidOutcome": "Choose a round outcome.",
  "round.action.invalidFacilitator": "Choose a new facilitator.",
  "round.outcome.awarded": "Awarded",
  "round.outcome.noAward": "No award",
  "proposal.group.identity": "Response identity",
  "proposal.group.offer": "Offer facts",
  "proposal.group.receipts": "Receipts",
  "proposal.tabs.answers": "Answers",
  "proposal.tabs.reviews": "Reviews",
  "proposal.answers.empty": "No topic answers yet.",
  "proposal.reviews.mine.title": "Your review",
  "proposal.reviews.mine.description":
    "Create or update your own evaluator assessment.",
  "proposal.reviews.mine.signedOut": "Sign in to edit your review.",
  "proposal.reviews.readable.title": "Readable reviews",
  "proposal.reviews.readable.description":
    "Reviews returned by the server for this proposal.",
  "proposal.reviews.empty": "No readable reviews yet.",
  "proposal.action.submit": "Submit",
  "proposal.action.withdraw": "Withdraw",
  "proposal.action.createTrack": "Create track",
  "proposal.action.publishTrack": "Publish track",
  "proposal.action.failed": "The proposal action returned no result.",
  "comparison.title": "Compare {round}",
  "comparison.description":
    "Topics align every readable proposal; sealed or empty values both appear as an em dash.",
  "comparison.back": "Round record",
  "comparison.gridLabel": "Proposal comparison",
  "comparison.subject": "Topic or offer fact",
  "comparison.empty.title": "No readable proposals",
  "comparison.empty.description":
    "The server has not returned any proposal columns for this viewer.",
  "comparison.loading": "Loading proposal comparison…",
  "comparison.error": "The proposal comparison could not be loaded.",
  "comparison.fact.cost": "Cost and currency",
  "comparison.fact.staffing": "Staffing",
  "comparison.fact.timeframeStart": "Timeframe start",
  "comparison.fact.timeframeEnd": "Timeframe end",
  "comparison.fact.confidence": "Confidence",
  "comparison.fact.validUntil": "Valid until",
};

export const useProposalsT = createNamespaceT(
  "proposals",
  enProposalsMessages,
);
export type ProposalsT = ReturnType<typeof useProposalsT>;
