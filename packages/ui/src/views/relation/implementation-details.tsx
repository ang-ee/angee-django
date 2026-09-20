import { useMemo, type ReactElement } from "react";

import { SlotOutlet } from "../../lib/slot-outlet";
import { makeContext, useModelSlot } from "../../runtime";
import type { ImplChoice } from "../resource/documents";

/** Model-owned inspection panels for a registered implementation field. */
export const IMPLEMENTATION_DETAIL_SLOT = "implementation.detail";

export interface ImplementationDetailContext {
  model: string;
  field: string;
  choice: ImplChoice;
}

const binding = makeContext<ImplementationDetailContext>("ImplementationDetailContext");

/** Read the registered choice supplied by the enclosing implementation inspector. */
export const useImplementationDetailContext = binding.use;

/** Render domain-owned panels without coupling an inspector to optional addons. */
export function ImplementationDetails({ value }: { value: ImplementationDetailContext }): ReactElement {
  const target = useMemo(() => [
    { slot: IMPLEMENTATION_DETAIL_SLOT, model: value.model },
    { slot: IMPLEMENTATION_DETAIL_SLOT, model: value.model, impl: value.choice.key },
  ], [value.model, value.choice.key]);
  const entries = useModelSlot(target);
  return (
    <binding.Provider value={value}>
      <SlotOutlet entries={entries} />
    </binding.Provider>
  );
}
