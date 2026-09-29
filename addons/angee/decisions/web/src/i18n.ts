import { createNamespaceT } from "@angee/ui";

export const enDecisionsMessages = {
  "decisions.label": "Decisions",
  "decisions.kind": "Decision",
  "decisions.verdict": "Verdict",
  "decisions.resolved": "Resolved",
  "decision.revisit": "Revisit",
  "decision.revisit.title": "Revisit this decision?",
  "decision.revisit.body": "A new review retains the earlier answer in the history.",
  "decision.schemaError": "The decision form could not be read.",
};
export const useDecisionsT = createNamespaceT("decisions", enDecisionsMessages);
