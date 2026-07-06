// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { ReactNode } from "react";

import { ActiveDataProviderNameProvider } from "./data-provider-context";
import { useAuthoredQuery } from "./authored-hooks";

// Keep refine's real useSubscription — it reads the live context and no-ops when
// no live provider is mounted (the app's "no live" path). Only stub the data read
// so the hook renders without a full <Refine> data stack.
vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useCustom: () => ({
      query: { data: undefined, isFetching: false, error: null, refetch: vi.fn() },
    }),
  };
});

function ConsoleProvider({ children }: { children: ReactNode }) {
  return (
    <ActiveDataProviderNameProvider name="console">
      {children}
    </ActiveDataProviderNameProvider>
  );
}

describe("useAuthoredQuery without a live provider", () => {
  test("declaring models is a no-op when no live provider is mounted", () => {
    const { result } = renderHook(
      () =>
        useAuthoredQuery(
          "query Rail { discuss_rail { id } }" as never,
          undefined,
          { models: ["messaging.Thread"] },
        ),
      { wrapper: ConsoleProvider },
    );

    expect(result.current.error).toBeNull();
    expect(result.current.data).toBeUndefined();
  });
});
