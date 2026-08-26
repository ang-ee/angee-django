// @vitest-environment happy-dom

import { cleanup, renderHook, waitFor } from "@testing-library/react";
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
