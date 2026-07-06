// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { ReactNode } from "react";

import { ActiveDataProviderNameProvider } from "./data-provider-context";
import { useAuthoredQuery } from "./authored-hooks";

const subscriptionMock = vi.hoisted(() => ({
  calls: [] as Array<Record<string, unknown>>,
}));

vi.mock("@refinedev/core", () => ({
  useCustom: () => ({
    query: { data: undefined, isFetching: false, error: null, refetch: vi.fn() },
  }),
  useSubscription: (props: Record<string, unknown>) => {
    subscriptionMock.calls.push(props);
  },
}));

beforeEach(() => {
  subscriptionMock.calls = [];
});

const DOCUMENT = "query Rail { discuss_rail { id } }" as never;

function ConsoleProvider({ children }: { children: ReactNode }) {
  return (
    <ActiveDataProviderNameProvider name="console">
      {children}
    </ActiveDataProviderNameProvider>
  );
}

describe("useAuthoredQuery live interest", () => {
  test("declares each read model as live interest through refine useSubscription", () => {
    renderHook(
      () =>
        useAuthoredQuery(DOCUMENT, undefined, {
          models: ["messaging.Thread", "messaging.ThreadActivity"],
        }),
      { wrapper: ConsoleProvider },
    );

    // The models ride refine's own subscription seam: the provider fans them out
    // to their changes roots (asserted in provider.test.ts), the hook only
    // declares the interest.
    expect(subscriptionMock.calls).toHaveLength(1);
    expect(subscriptionMock.calls[0]).toMatchObject({
      params: { models: ["messaging.Thread", "messaging.ThreadActivity"] },
      enabled: true,
    });
  });

  test("declares no live interest for an authored read without models", () => {
    renderHook(() => useAuthoredQuery(DOCUMENT), { wrapper: ConsoleProvider });

    expect(subscriptionMock.calls[0]).toMatchObject({
      params: { models: [] },
      enabled: false,
    });
  });
});
