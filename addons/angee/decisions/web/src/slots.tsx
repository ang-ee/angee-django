import type { ComponentType, ReactElement, ReactNode } from "react";
import { makeContext, ContainerOutlet, useContainer, type ComposedContainerChild, type ContainerChild } from "@angee/ui";

import type { Decision } from "./documents.console";


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

/**
 * A decision kind's presentation, for an addon's `decisions#content` child.
 * The container holds one child per kind, so a second claim fails composition.
 */
export function decisionContent(kind: string, Component: ComponentType<DecisionContentProps>): ContainerChild<ReactNode> {
  return { key: kind, content: <ConsumerContent Component={Component} /> };
}

function ConsumerContent({ Component }: { Component: ComponentType<DecisionContentProps> }): ReactElement {
  return <Component {...useDecisionContent()} />;
}

/** Mount inside the page's React Hook Form provider so content can edit the form. */
export function DecisionContentOutlet(): ReactElement {
  const { decision } = useDecisionContent();
  return <ContainerOutlet entries={useDecisionContentEntries(decision.kind)} />;
}

/** The selected kind's registered presentation replaces generic fact display. */
export function useDecisionContentEntries(kind: string): readonly ComposedContainerChild[] {
  return useContainer("decisions#content").filter((entry) => entry.key === kind);
}

/** An empty `decisions#origin` renders nothing. Children read the decision with useDecisionContent. */
export function DecisionOriginOutlet(): ReactElement {
  return <ContainerOutlet entries={useContainer("decisions#origin")} />;
}
