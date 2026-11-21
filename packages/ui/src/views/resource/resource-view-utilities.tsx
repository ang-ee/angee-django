import * as React from "react";

import { ContainerOutlet } from "../../lib/container-outlet";
import { useModelMetadata } from "@angee/metadata";

import { makeContext, modelChain, useContainer, type ComposedContainerChild } from "../../runtime";
import { useRecordChromeContextMaybe, type RecordChromeContext } from "./record-chrome-context";
import type { ResourceViewFilter } from "./resource-view-model";

export interface ResourceViewUtilityContext {
  resource: string;
  filter?: ResourceViewFilter;
  fields: readonly string[];
  refresh: () => void;
  /**
   * Public ids selected by the collection owner; none while the selection is every
   * record the list matches ("Select all N"), so a utility that ignores
   * `disabledReason` acts on nothing.
   */
  selectedIds?: ReadonlySet<string>;
  /** Whether this collection exposes selection to contributed utilities. */
  selectable: boolean;
  /**
   * Why a utility acting on the selection must not run: it is every record the list
   * matches ("Select all N"), and `selectedIds` is empty. A utility passes it as its
   * control's `disabledReason`. Null on a list that offers the mode but is not in it;
   * absent on one that never offers it.
   */
  disabledReason?: string | null;
  /** Saved record enclosing this collection, for actions that explicitly target the parent. */
  record?: RecordChromeContext | null;
}

const ResourceViewUtilityContextBinding = makeContext<ResourceViewUtilityContext>(
  "ResourceViewUtilityContext",
);

/**
 * @owner ResourceToolbar
 */
export function useResourceViewUtilityContext(): ResourceViewUtilityContext {
  return ResourceViewUtilityContextBinding.use();
}

/**
 * The `resource#utilities` children for one collection's model and its MTI
 * parent. A list over its own `source` names no schema model, so it skips the
 * model lookup (`modelBacked: false`) and keeps the kind-level utilities.
 * @owner ResourceToolbar
 */
export function useResourceViewUtilities(
  resource: string,
  { modelBacked = true }: { modelBacked?: boolean } = {},
): readonly ComposedContainerChild[] {
  const canonical = useModelMetadata(modelBacked ? resource : "")?.resource.canonicalLabel;
  const models = React.useMemo(() => modelChain(canonical, resource), [canonical, resource]);
  return useContainer("resource#utilities", { models });
}

/**
 * @owner ResourceToolbar
 */
export function ResourceViewUtilities({
  value,
  modelBacked = true,
}: {
  value: ResourceViewUtilityContext;
  /** False for a list over its own `source`, which names no schema model. */
  modelBacked?: boolean;
}): React.ReactElement | null {
  const { resource, filter, fields, refresh, selectedIds, selectable, disabledReason } = value;
  const entries = useResourceViewUtilities(resource, { modelBacked });
  const record = useRecordChromeContextMaybe();
  const context = React.useMemo(
    () => ({ resource, filter, fields, refresh, selectedIds, selectable, disabledReason, record }),
    [resource, filter, fields, refresh, selectedIds, selectable, disabledReason, record],
  );
  if (entries.length === 0) return null;
  return (
    <ResourceViewUtilityContextBinding.Provider value={context}>
      <ContainerOutlet entries={entries} />
    </ResourceViewUtilityContextBinding.Provider>
  );
}
