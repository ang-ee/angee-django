import type { ReactElement } from "react";
import { makeContext, ContainerOutlet, useContainer } from "@angee/ui";
import type { Decision } from "./documents.console";

const binding = makeContext<{ readonly decision: Decision }>("DecisionProvider");
export const DecisionProvider = binding.Provider;
export const useDecision = binding.use;

/** Waiting owners link their origin without owning the question's presentation. */
export function DecisionOriginOutlet(): ReactElement {
  return <ContainerOutlet entries={useContainer("decisions#origin")} />;
}
