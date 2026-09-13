import * as React from "react";

import { SlotOutlet } from "../../lib/slot-outlet";
import { makeContext, useSlot, type SlotContribution } from "../../runtime";
import { useRecordChromeContextMaybe, type RecordChromeContext } from "./record-chrome-context";
import type { ResourceViewFilter } from "./resource-view-model";

export const RESOURCE_VIEW_ACTIONS_SLOT = "resource-view.actions";

export interface ResourceViewActionContext {
  resource: string;
  filter?: ResourceViewFilter;
  fields: readonly string[];
  refresh: () => void;
  /** Public ids selected by the collection owner. */
  selectedIds?: ReadonlySet<string>;
  /** Saved record enclosing this collection; nested actions target it. */
  record?: RecordChromeContext | null;
}

const ResourceViewActionContextBinding = makeContext<ResourceViewActionContext>(
  "ResourceViewActionContext",
);

export function useResourceViewActionContext(): ResourceViewActionContext {
  return ResourceViewActionContextBinding.use();
}

export function useResourceViewActions(resource: string): readonly SlotContribution[] {
  const entries = useSlot(RESOURCE_VIEW_ACTIONS_SLOT);
  return React.useMemo(() => {
    const result = entries.filter((entry) => entry.model === undefined || entry.model === resource);
    const ids = new Set<string>();
    for (const entry of result) {
      if (ids.has(entry.id)) {
        throw new Error(`Resource view action "${entry.id}" is contributed more than once for "${resource}".`);
      }
      ids.add(entry.id);
    }
    return result;
  }, [entries, resource]);
}

export function ResourceViewActions({
  value,
}: {
  value: ResourceViewActionContext;
}): React.ReactElement | null {
  const entries = useResourceViewActions(value.resource);
  const record = useRecordChromeContextMaybe();
  if (entries.length === 0) return null;
  return (
    <ResourceViewActionContextBinding.Provider value={{ ...value, record }}>
      <SlotOutlet entries={entries} />
    </ResourceViewActionContextBinding.Provider>
  );
}

export function resourceViewActionsSlot(model?: string): {
  slot: typeof RESOURCE_VIEW_ACTIONS_SLOT;
  model?: string;
} {
  return {
    slot: RESOURCE_VIEW_ACTIONS_SLOT,
    ...(model ? { model } : {}),
  };
}
