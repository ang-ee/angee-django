import { createNamespaceT } from "@angee/ui";

export const enWorkflowsPartiesMessages: Record<string, string> = {
  "activity.label": "Workflow activity",
  "decision.reviewTitle": "Review party details",
  "identityReview.unavailable": "Party identity evidence unavailable",
  "identityReview.unavailableDescription": "Open the processing details to inspect this decision's retained evidence.",
  "identityReview.kind": "Party identity",
  "identityReview.description": "Compare the current Party record with the identity proposed from retained source evidence.",
  "identityReview.unknownParty": "Unknown party",
  "identityReview.openParty": "Open party",
  "identityReview.openEvidence": "Open evidence",
  "identityReview.current": "Current party",
  "identityReview.proposed": "Proposed from source",
  "identityReview.name": "Name",
  "identityReview.address": "Address",
  "identityReview.contact": "Contact",
  "identityReview.contactStatus": "Contact status",
  "identityReview.contactEvidence": "Contact evidence",
  "identityReview.contacts": "Current contacts",
  "identityReview.noContacts": "No contacts recorded",
  "identityReview.notRecorded": "Not recorded",
  "identityReview.notProvided": "Not provided",
  "identityReview.status.confirmed": "Confirmed",
  "identityReview.status.dismissed": "Dismissed",
  "identityReview.status.suggested": "Suggested",
  "identityReview.history": "Approved changes update the Party record. Source evidence and this decision remain in workflow history.",
  "dedupe.run": "Run dedupe",
  "dedupe.running": "Starting…",
  "dedupe.description": "Scan for duplicates and review the proposed merges as one batch.",
  "dedupe.failed": "Could not start the dedupe run.",
};

export const useWorkflowsPartiesT = createNamespaceT("workflows-parties", enWorkflowsPartiesMessages);
