import { createNamespaceT } from "@angee/ui";

export const enIntakeMessages: Record<string, string> = {
  "access.label": "Requester access",
  "access.hint": "An account for whoever filed this request, so they can follow it signed in.",
  "access.error": "Access decisions could not be loaded.",
  "access.approve": "Approve",
  "access.deny": "Deny",
  "access.reset": "Reset access",
  "access.resetTitle": "Reset requester access",
  "access.resetBody": "Remove the current access decision and reopen the request?",
  "access.requester": "Requester",
  "access.decision": "Current decision",
  "access.empty": "No requester access decisions yet.",
  "needs.label": "Needs",
  "needs.body": "Need",
  "needs.party": "Party",
  "needs.importance": "Importance",
  "needs.evidence": "Evidence",
  "needs.createdAt": "Captured",
  "needs.empty": "No needs have been captured for this record.",
  "capture.button": "Capture need",
  "capture.title": "Capture need",
  "capture.description": "Record an external request without changing task priority.",
  "capture.body": "Request",
  "capture.party": "Requesting party",
  "capture.importance": "Importance",
  "capture.submit": "Capture",
  "capture.submitting": "Capturing…",
  "capture.error": "Could not capture the need.",
};

export const useIntakeT = createNamespaceT("intake", enIntakeMessages);
