// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { ReactNode } from "react";

import { ActiveDataProviderNameProvider } from "./data-provider-context";
import { OperationDocumentsProvider } from "../operation-documents";
import { useActionMutation } from "./hooks";

const mutationMock = vi.hoisted(() => ({
  calls: [] as Array<{
    dataProviderName: string;
    values: Record<string, unknown>;
  }>,
}));

vi.mock("@refinedev/core", () => ({
  useCustom: () => ({ query: {}, result: {} }),
  useCustomMutation: () => ({
    mutateAsync: vi.fn(
      async (payload: {
        dataProviderName: string;
        values: Record<string, unknown>;
      }) => {
        mutationMock.calls.push({
          dataProviderName: payload.dataProviderName,
          values: payload.values,
        });
        return { data: { run_probe: { ok: true, message: "Done" } } };
      },
    ),
    mutation: { isPending: false, error: null },
  }),
  useInvalidate: () => vi.fn(async () => {}),
}));

beforeEach(() => {
  mutationMock.calls = [];
});

const ACTION_DOCUMENT = "mutation Probe { run_probe(id: $id) { ok message } }";

function ConsoleProvider({ children }: { children: ReactNode }) {
  // A resource page's DataProviderContext pins the active schema; the action
  // documents are keyed by that same real schema name (never a "default" alias).
  return (
    <ActiveDataProviderNameProvider name="console">
      <OperationDocumentsProvider
        documents={{ console: { actions: { run_probe: ACTION_DOCUMENT } } }}
      >
        {children}
      </OperationDocumentsProvider>
    </ActiveDataProviderNameProvider>
  );
}

describe("useActionMutation", () => {
  test("resolves the action document and data provider from the active schema", async () => {
    const { result } = renderHook(() => useActionMutation("run_probe"), {
      wrapper: ConsoleProvider,
    });

    let message: string | undefined;
    await act(async () => {
      message = await result.current[0]("rec_1");
    });

    // The success message flows back, proving the "console" action document
    // resolved — the pre-fix "default" lookup would throw (no "default" alias in
    // the operation-documents map), the Post-button asymmetry.
    expect(message).toBe("Done");
    // The mutation runs against the active schema's provider, not the "default"
    // alias — matching the fallback the authored hooks already use.
    expect(mutationMock.calls).toEqual([
      { dataProviderName: "console", values: { id: "rec_1" } },
    ]);
  });

  test("an explicit dataProviderName option still wins over the active schema", async () => {
    const { result } = renderHook(
      () => useActionMutation("run_probe", { dataProviderName: "console" }),
      {
        wrapper: ({ children }: { children: ReactNode }) => (
          <OperationDocumentsProvider
            documents={{ console: { actions: { run_probe: ACTION_DOCUMENT } } }}
          >
            {children}
          </OperationDocumentsProvider>
        ),
      },
    );

    await act(async () => {
      await result.current[0]("rec_2");
    });

    expect(mutationMock.calls).toEqual([
      { dataProviderName: "console", values: { id: "rec_2" } },
    ]);
  });
});
