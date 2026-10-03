import * as React from "react";

import { ContainerOutlet } from "../../lib/container-outlet";
import { makeContext, useContainer, type ComposedContainerChild } from "../../runtime";
import { useRecordChromeContextMaybe, type RecordChromeContext } from "./record-chrome-context";
import type { ResourceViewFilter } from "./resource-view-model";

export interface ResourceViewUtilityContext {
  resource: string;
  filter?: ResourceViewFilter;
  fields: readonly string[];
  refresh: () => void;
  /** Public ids selected by the collection owner. */
  selectedIds?: ReadonlySet<string>;
  /** Whether this collection exposes selection to contributed utilities. */
  selectable: boolean;
  /** Saved record enclosing this collection, for actions that explicitly target the parent. */
  record?: RecordChromeContext | null;
}

const ResourceViewUtilityContextBinding = makeContext<ResourceViewUtilityContext>(
  "ResourceViewUtilityContext",
);

export function useResourceViewUtilityContext(): ResourceViewUtilityContext {
  return ResourceViewUtilityContextBinding.use();
}

/** The `resource#utilities` children for one collection's model. */
export function useResourceViewUtilities(resource: string): readonly ComposedContainerChild[] {
  const models = React.useMemo(() => [resource], [resource]);
  return useContainer("resource#utilities", { models });
}

export function ResourceViewUtilities({
  value,
}: {
  value: ResourceViewUtilityContext;
}): React.ReactElement | null {
  const { resource, filter, fields, refresh, selectedIds, selectable } = value;
  const entries = useResourceViewUtilities(resource);
  const record = useRecordChromeContextMaybe();
  const context = React.useMemo(
    () => ({ resource, filter, fields, refresh, selectedIds, selectable, record }),
    [resource, filter, fields, refresh, selectedIds, selectable, record],
  );
  if (entries.length === 0) return null;
  return (
    <ResourceViewUtilityContextBinding.Provider value={context}>
      <ContainerOutlet entries={entries} />
    </ResourceViewUtilityContextBinding.Provider>
  );
}
