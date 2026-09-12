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
  // Converting this to render state would add a second reconciliation render and
  // can briefly expose the wrong grouping, so the reducer follow-up owns that move.
  const handledDefaultGroupRef = React.useRef<ResourceViewGroup | null>(null);
  // Whether the handled default has actually reached the view state; see the
  // effect below for why the two are not the same question.
  const appliedDefaultGroupRef = React.useRef(false);
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
      appliedDefaultGroupRef.current = false;
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
    const handled =
      handledDefaultGroupRef.current !== null
      && resourceViewGroupsEqual(handledDefaultGroupRef.current, activeDefaultGroup);
    const groupIsDefault =
      resourceView.state.group !== null
      && resourceViewGroupsEqual(resourceView.state.group, activeDefaultGroup);
    // Once the group has actually landed in the URL, remember it: that is what
    // separates "the router has not committed ?group=stage yet" from "the user
    // cleared the group", which look identical from `state.group === null`.
    if (groupIsDefault) appliedDefaultGroupRef.current = true;

    // A pinned board used to re-apply on every render until the URL caught up,
    // because the early return required `state.group` to equal the default and
    // that is URL state. Each pass called `setGroup`, which resets scope, writes
    // the same failed-transition value onto a fiber that already has a lane, and
    // navigates to the same target -- fifty-odd nested updates before the URL
    // settled, thrown as "Maximum update depth exceeded" and caught by the
    // router, which rebuilt the list.
    //
    // Having asked for it once is enough. The request is still re-made when the
    // user clears a group that had landed, which is the case the pinned branch
    // existed for.
    if (handled && (!pinned || groupIsDefault || !appliedDefaultGroupRef.current)) {
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
      appliedDefaultGroupRef.current = false;
      resourceView.setGroup(activeDefaultGroup);
    }
  }, [
    activeDefaultGroup,
    clearRemovedDefault,
    pinned,
    resourceView.setGroup,
    resourceView.state.group,
  ]);
  return effectiveGroupStack;
}
