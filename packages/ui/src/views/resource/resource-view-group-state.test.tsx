// @vitest-environment happy-dom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
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

describe("a pinned board re-applies its default group after a clear", () => {
  // The reconciliation cases above cover applying and removing a *declared*
  // default. This is the distinct product contract of a pinned board: its
  // grouping is fixed, so clearing the group is undone and the default lands
  // again. (The route-scope render churn that motivated the interim guard needs
  // an async router to reproduce -- the board integration suites own that; the
  // observable outcome asserted here is that the group settles back to the
  // pinned default rather than staying cleared.)
  test("clearing the group re-applies the pinned default", async () => {
    const { result } = renderHook(
      () => {
        const view = useResourceView();
        useResourceViewGroupState({
          resourceView: view,
          defaultGroup: DEFAULT_GROUP,
          modelMetadata: null,
          pinned: true,
        });
        return view;
      },
      { wrapper: LocalViewProvider },
    );

    await waitFor(() =>
      expect(result.current.state.group?.field).toBe(DEFAULT_GROUP.field),
    );

    act(() => result.current.setGroup(null));
    await waitFor(() =>
      expect(result.current.state.group?.field).toBe(DEFAULT_GROUP.field),
    );
  });
});
