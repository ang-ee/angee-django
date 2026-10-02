import { createNamespaceT } from "@angee/ui";

export const enWorkflowsPartiesMessages = {
  "review.unavailable": "Review details unavailable",
  "review.unavailableDescription": "The retained review details could not be read.",
  "identity.title": "Party identity",
  "identity.current": "Current",
  "identity.proposed": "Proposed",
  "identity.name": "Name",
  "identity.address": "Address",
  "identity.contact": "Contact",
  "identity.contacts": "Current contacts",
  "identity.contactEvidence": "Contact evidence",
  "identity.notRecorded": "Not recorded",
  "identity.notProvided": "Not provided",
  "identity.confirmed": "Confirmed",
  "identity.dismissed": "Dismissed",
  "identity.suggested": "Suggested",
  "duplicate.title": "Possible duplicate parties",
  "duplicate.left": "Left party",
  "duplicate.right": "Right party",
  "duplicate.name": "Name",
  "duplicate.evidence": "Shared contact",
};

export const useWorkflowsPartiesT = createNamespaceT("workflows-parties", enWorkflowsPartiesMessages);
