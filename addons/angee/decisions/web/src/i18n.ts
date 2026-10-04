import { createNamespaceT } from "@angee/ui";

export const enDecisionsMessages = {
  "inbox.scope": "Decisions",
  "inbox.assigned": "Assigned to me",
  "inbox.canAct": "I can answer",
  "inbox.requested": "Requested by me",
  "inbox.state": "Decision state",
  "inbox.open": "Open",
  "inbox.answered": "Answered",
  "inbox.empty": "No decisions in this view.",
  "inbox.kind": "Kind",
  "inbox.requester": "Requester",
  "inbox.verdict": "Verdict",
  "decision.invalidProposal": "This proposal is invalid.",
  "decision.submit": "Confirm",
  "decision.conflict": "This decision has changed. Reload the page to review the current question.",
  "decision.unavailable": "This decision is unavailable.",
  "context.title": "Context",
  "context.facts": "Facts",
  "context.references": "References",
  "context.evidence": "Evidence",
  "context.invalid": "Context is unavailable.",
  "context.source": "Source",
  "context.correction": "Correction",
  "context.unverified": "Unverified",
};

export const useDecisionsT = createNamespaceT("decisions", enDecisionsMessages);
