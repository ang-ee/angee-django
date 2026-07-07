// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { ReactNode } from "react";

import { ActiveDataProviderNameProvider } from "./data-provider-context";
import { OperationDocumentsProvider } from "../operation-documents";
import { useActionMutation } from "./hooks";
import type { ActionOutcome } from "../operations";

const mutationMock = vi.hoisted(() => ({
  calls: [] as Array<{
    dataProviderName: string;
    values: Record<string, unknown>;
  }>,
  response: { ok: true, message: "Done" } as Record<string, unknown>,
  invalidate: vi.fn(async () => {}),
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
        return { data: { run_probe: mutationMock.response } };
      },
    ),
    mutation: { isPending: false, error: null },
  }),
  useInvalidate: () => mutationMock.invalidate,
}));

beforeEach(() => {
  mutationMock.calls = [];
  mutationMock.response = { ok: true, message: "Done" };
  mutationMock.invalidate.mockClear();
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

    let outcome: ActionOutcome | undefined;
    await act(async () => {
      outcome = await result.current[0]("rec_1");
    });

    // The in-band outcome flows back, proving the "console" action document
    // resolved — the pre-fix "default" lookup would throw (no "default" alias in
    // the operation-documents map), the Post-button asymmetry.
    expect(outcome).toEqual({ ok: true, message: "Done" });
    // The mutation runs against the active schema's provider, not the "default"
    // alias — matching the fallback the authored hooks already use.
    expect(mutationMock.calls).toEqual([
      { dataProviderName: "console", values: { id: "rec_1" } },
    ]);
  });

  test("resolves a domain failure in-band and skips invalidation", async () => {
    mutationMock.response = {
      ok: false,
      message: "Not allowed.",
      validation_errors: { __all__: ["You are not allowed to modify this order."] },
    };
    const { result } = renderHook(
      () =>
        useActionMutation("run_probe", {
          invalidates: [{ resource: "orders", invalidates: ["list"] }],
        }),
      { wrapper: ConsoleProvider },
    );

    let outcome: ActionOutcome | undefined;
    await act(async () => {
      outcome = await result.current[0]("rec_1");
    });

    // ok=false resolves (no throw) so a settle owner can toast message +
    // in-band reasons; a failed write never refreshes caches.
    expect(outcome).toEqual({
      ok: false,
      message: "Not allowed.",
      validationErrors: { __all__: ["You are not allowed to modify this order."] },
    });
    expect(mutationMock.invalidate).not.toHaveBeenCalled();
  });

  test("carries the created record id and invalidates on success", async () => {
    mutationMock.response = { ok: true, message: "Created.", id: "tr_9" };
    const { result } = renderHook(
      () =>
        useActionMutation("run_probe", {
          invalidates: [{ resource: "orders", invalidates: ["list"] }],
        }),
      { wrapper: ConsoleProvider },
    );

    let outcome: ActionOutcome | undefined;
    await act(async () => {
      outcome = await result.current[0]("rec_1");
    });

    expect(outcome).toEqual({ ok: true, message: "Created.", id: "tr_9" });
    expect(mutationMock.invalidate).toHaveBeenCalledTimes(1);
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
