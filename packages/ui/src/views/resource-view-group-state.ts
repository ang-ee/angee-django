import * as React from "react";
import type { ModelMetadata } from "@angee/metadata";

import type { ResourceViewContextValue } from "./resource-view-context";
import {
  resourceViewGroupsEqual,
  type ResourceViewGroup,
} from "./resource-view-model";
import {
  resolveResourceViewGroup,
  validResourceViewGroupStack,
} from "./resource-view-utils";

const EMPTY_GROUP_STACK = [] as const;

export interface UseResourceViewGroupStateProps {
  resourceView: ResourceViewContextValue;
  defaultGroup: ResourceViewGroup | null | undefined;
  modelMetadata: ModelMetadata | null;
  pinned?: boolean;
  clearRemovedDefault?: boolean;
}

/** Reconcile declared defaults and schema-valid groups with URL/local state. */
export function useResourceViewGroupState({
  resourceView,
  defaultGroup,
  modelMetadata,
  pinned = false,
  clearRemovedDefault = true,
}: UseResourceViewGroupStateProps): readonly ResourceViewGroup[] {
  const activeDefaultGroup = React.useMemo(
    () => defaultGroup ? resolveResourceViewGroup(defaultGroup, modelMetadata) : null,
    [defaultGroup, modelMetadata],
  );
  const validDefaultGroupStack = React.useMemo(
    () => activeDefaultGroup
      ? validResourceViewGroupStack([activeDefaultGroup], modelMetadata)
      : EMPTY_GROUP_STACK,
    [activeDefaultGroup, modelMetadata],
  );
  const validCurrentGroupStack = React.useMemo(
    () => validResourceViewGroupStack(resourceView.state.groupStack, modelMetadata),
    [modelMetadata, resourceView.state.groupStack],
  );
  const hasInvalidGroupStack = !resourceViewGroupStacksEqual(
    resourceView.state.groupStack,
    validCurrentGroupStack,
  );
  // A non-empty valid subset is a spelling/stack repair owned by this view
  // (for example camel-case URL state canonicalized to snake_case metadata).
  // No valid groups means the shared bare URL value belongs to another sibling
  // data view; render this view's default locally without writing the foreign
  // value back and starting a group-param ping-pong.
  const hasCanonicalizableGroupStack =
    hasInvalidGroupStack && validCurrentGroupStack.length > 0;
  // The previous applied default is transition memory: reading it here is
  // required to distinguish a newly-declared default from one the user cleared.
  // Converting this to render state would add a second reconciliation render and
  // can briefly expose the wrong grouping, so the reducer follow-up owns that move.
  const handledDefaultGroupRef = React.useRef<ResourceViewGroup | null>(null);
  const defaultGroupPending =
    activeDefaultGroup !== null
    && resourceView.state.group === null
    && (
      handledDefaultGroupRef.current === null
      || !resourceViewGroupsEqual(handledDefaultGroupRef.current, activeDefaultGroup)
    );
  const effectiveGroupStack = React.useMemo(() => {
    if (pinned && validDefaultGroupStack.length > 0) return validDefaultGroupStack;
    if (validCurrentGroupStack.length > 0) return validCurrentGroupStack;
    if (hasInvalidGroupStack || defaultGroupPending) return validDefaultGroupStack;
    return resourceView.state.groupStack;
  }, [
    defaultGroupPending,
    hasInvalidGroupStack,
    pinned,
    resourceView.state.groupStack,
    validCurrentGroupStack,
    validDefaultGroupStack,
  ]);

  React.useEffect(() => {
    if (!activeDefaultGroup) {
      const previousDefault = handledDefaultGroupRef.current;
      handledDefaultGroupRef.current = null;
      if (
        clearRemovedDefault
        && previousDefault
        && resourceView.state.group
        && resourceViewGroupsEqual(resourceView.state.group, previousDefault)
      ) {
        resourceView.setGroup(null);
      }
      return;
    }
    if (
      handledDefaultGroupRef.current
      && resourceViewGroupsEqual(handledDefaultGroupRef.current, activeDefaultGroup)
      && (
        !pinned
        || (
          resourceView.state.group !== null
          && resourceViewGroupsEqual(resourceView.state.group, activeDefaultGroup)
        )
      )
    ) {
      return;
    }
    const previousDefault = handledDefaultGroupRef.current;
    if (
      pinned
      || resourceView.state.group === null
      || (
        previousDefault
        && resourceViewGroupsEqual(resourceView.state.group, previousDefault)
      )
    ) {
      handledDefaultGroupRef.current = activeDefaultGroup;
      resourceView.setGroup(activeDefaultGroup);
    }
  }, [
    activeDefaultGroup,
    clearRemovedDefault,
    pinned,
    resourceView.setGroup,
    resourceView.state.group,
  ]);
  React.useEffect(() => {
    if (!hasCanonicalizableGroupStack) return;
    if (resourceViewGroupStacksEqual(
      resourceView.state.groupStack,
      effectiveGroupStack,
    )) return;
    resourceView.setGroupStack(effectiveGroupStack);
  }, [
    effectiveGroupStack,
    hasCanonicalizableGroupStack,
    resourceView.setGroupStack,
    resourceView.state.groupStack,
  ]);
  return effectiveGroupStack;
}

function resourceViewGroupStacksEqual(
  left: readonly ResourceViewGroup[],
  right: readonly ResourceViewGroup[],
): boolean {
  return left.length === right.length
    && left.every((group, index) => {
      const other = right[index];
      return other !== undefined && resourceViewGroupsEqual(group, other);
    });
}
