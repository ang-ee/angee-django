import type { ComponentType, ReactElement } from "react";
import { makeContext, SlotOutlet, useSlot, type SlotContribution } from "@angee/ui";

import type { Decision } from "./documents.console";

/** Exactly one consumer presentation may claim a decision kind. */
const DECISION_CONTENT_SLOT = "decisions.content";
/** Waiting owners contribute their own links without adding dependencies here. */
export const DECISION_ORIGIN_SLOT = "decisions.origin";

type ReadonlyTree<T> = T extends object ? { readonly [Key in keyof T]: ReadonlyTree<T[Key]> } : T;
type PayloadField = "basis" | "context" | "form_schema" | "resolution";
// Generated JSON scalars already use readonly JsonValue; do not recursively remap that recursive type.
type DecisionSnapshot = ReadonlyTree<Omit<Decision, PayloadField>> & Readonly<Pick<Decision, PayloadField>>;

export interface DecisionContentProps {
  readonly decision: DecisionSnapshot;
  /** Consumers parse their own retained payloads before reading them. */
  readonly basis: unknown;
  readonly context: unknown;
}

const binding = makeContext<DecisionContentProps>("DecisionContentProvider");
export const DecisionContentProvider = binding.Provider;
export const useDecisionContent = binding.use;

/** Native slot identity makes duplicate kind claims fail during addon composition. */
export function decisionContent(kind: string, Component: ComponentType<DecisionContentProps>): SlotContribution {
  return { slot: DECISION_CONTENT_SLOT, id: kind, content: <ConsumerContent Component={Component} /> };
}

function ConsumerContent({ Component }: { Component: ComponentType<DecisionContentProps> }): ReactElement {
  return <Component {...useDecisionContent()} />;
}

/** Mount inside the page's React Hook Form provider so content can edit the form. */
export function DecisionContentOutlet(): ReactElement {
  const { decision } = useDecisionContent();
  return <SlotOutlet entries={useDecisionContentEntries(decision.kind)} />;
}

/** The selected kind's registered presentation replaces generic fact display. */
export function useDecisionContentEntries(kind: string): readonly SlotContribution[] {
  return useSlot(DECISION_CONTENT_SLOT).filter((entry) => entry.id === kind);
}

/** An unfilled origin slot renders nothing. Contributions use useDecisionContent. */
export function DecisionOriginOutlet(): ReactElement {
  return <SlotOutlet entries={useSlot(DECISION_ORIGIN_SLOT)} />;
}
