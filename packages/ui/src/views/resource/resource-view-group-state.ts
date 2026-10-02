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
  // The previous applied default is transition memory: reading it here is
  // required to distinguish a newly-declared default from one the user cleared.
  // Undefined marks initialization, before any declaration has been reconciled.
  const handledDefaultGroupRef = React.useRef<ResourceViewGroup | null | undefined>(undefined);
  const defaultGroupPending =
    activeDefaultGroup !== null
    && resourceView.state.group === null
    && !resourceView.state.groupDefaultCleared
    && (
      handledDefaultGroupRef.current == null
      || !resourceViewGroupsEqual(handledDefaultGroupRef.current, activeDefaultGroup)
    );
  const effectiveGroupStack = React.useMemo(() => {
    if (pinned && validDefaultGroupStack.length > 0) return validDefaultGroupStack;
    if (validCurrentGroupStack.length > 0) return validCurrentGroupStack;
    if (defaultGroupPending) return validDefaultGroupStack;
    return resourceView.state.groupStack;
  }, [
    defaultGroupPending,
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
    if (resourceView.state.groupDefaultCleared && !pinned) {
      handledDefaultGroupRef.current = activeDefaultGroup;
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
      // Initializing or reasserting the same pinned group does not change the
      // effective scope (including StrictMode effect replay). A new default does.
      resourceView.setGroup(activeDefaultGroup, {
        resetScope: previousDefault !== undefined && (
          previousDefault === null
          || !resourceViewGroupsEqual(previousDefault, activeDefaultGroup)
        ),
      });
    }
  }, [
    activeDefaultGroup,
    clearRemovedDefault,
    pinned,
    resourceView.setGroup,
    resourceView.state.groupDefaultCleared,
    resourceView.state.group,
  ]);
  return effectiveGroupStack;
}
