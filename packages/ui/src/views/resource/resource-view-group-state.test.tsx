// @vitest-environment happy-dom

import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { afterEach, describe, expect, test } from "vitest";
import type { ReactNode } from "react";

import {
  ResourceViewProvider,
  useResourceView,
} from "./resource-view-context";
import { useResourceViewGroupState } from "./resource-view-group-state";
import type { ResourceViewGroup } from "./resource-view-model";

const DEFAULT_GROUP: ResourceViewGroup = { field: "status" };

afterEach(cleanup);

function LocalViewProvider({ children }: { children: ReactNode }) {
  return <ResourceViewProvider scope="local">{children}</ResourceViewProvider>;
}

describe("useResourceViewGroupState", () => {
  test.each([
    [false, false],
    [false, true],
    [true, false],
    [true, true],
  ] as const)(
    "reconciles pinned=%s and clearRemovedDefault=%s",
    async (pinned, clearRemovedDefault) => {
      const { result, rerender } = renderHook(
        ({ defaultGroup }: { defaultGroup: ResourceViewGroup | null }) =>
          useResourceViewGroupState({
            resourceView: useResourceView(),
            defaultGroup,
            modelMetadata: null,
            pinned,
            clearRemovedDefault,
          }),
        {
          wrapper: LocalViewProvider,
          initialProps: {
            defaultGroup: DEFAULT_GROUP as ResourceViewGroup | null,
          },
        },
      );

      await waitFor(() => expect(result.current).toEqual([DEFAULT_GROUP]));
      rerender({ defaultGroup: null });

      await waitFor(() => {
        expect(result.current).toEqual(clearRemovedDefault ? [] : [DEFAULT_GROUP]);
      });
    },
  );
});

describe("a pinned group asks once", () => {
  // A board pins its grouping. The view's group lives in the URL, so it stays
  // null until the router commits `?group=stage` -- and the effect used to
  // re-apply on every render in that window, each pass resetting scope, writing
  // the same failed-transition value onto a fiber that already had a lane, and
  // navigating to the same target. Fifty-odd nested updates, thrown as "Maximum
  // update depth exceeded" and caught by the router, which rebuilt the list.
  // `setGroup` is rebuilt with the context value on every render in the real
  // provider, so the effect's dependency churns every pass. A fixture that reuses one
  // function identity never re-runs the effect and cannot see this bug at all --
  // the first version of this test passed with the fix removed.
  function pinnedView(group: ResourceViewGroup | null, setGroup: (next: unknown) => void) {
    return {
      state: { group, groupStack: [], view: "board" },
      setGroup: (next: unknown) => setGroup(next),
    } as never;
  }

  test("does not re-ask while the view state has not caught up", () => {
    const setGroup = vi.fn();
    const { rerender } = renderHook(
      () =>
        useResourceViewGroupState({
          resourceView: pinnedView(null, setGroup),
          defaultGroup: DEFAULT_GROUP,
          modelMetadata: null,
          pinned: true,
        }),
    );

    expect(setGroup).toHaveBeenCalledTimes(1);
    expect(setGroup).toHaveBeenCalledWith(DEFAULT_GROUP);

    // The group is still null: the router has not committed yet. Ten renders in
    // that window used to mean ten more requests.
    for (let i = 0; i < 10; i += 1) rerender();
    expect(setGroup).toHaveBeenCalledTimes(1);
  });

  test("asks again when a group that had landed is cleared", () => {
    const setGroup = vi.fn();
    let group: ResourceViewGroup | null = null;
    const { rerender } = renderHook(
      () =>
        useResourceViewGroupState({
          resourceView: pinnedView(group, setGroup),
          defaultGroup: DEFAULT_GROUP,
          modelMetadata: null,
          pinned: true,
        }),
    );
    expect(setGroup).toHaveBeenCalledTimes(1);

    // The router commits, so the group lands...
    group = DEFAULT_GROUP;
    rerender();
    expect(setGroup).toHaveBeenCalledTimes(1);

    // ...and now the user clears it. A pinned board asks for it back.
    group = null;
    rerender();
    expect(setGroup).toHaveBeenCalledTimes(2);
  });
});
