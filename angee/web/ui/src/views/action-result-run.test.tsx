// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../runtime";
import { useActionResultRun } from "./action-result-run";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  toast: {
    success: vi.fn(),
    danger: vi.fn(),
  },
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
}));

vi.mock("../feedback", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useToast: () => mocks.toast,
}));

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <AppRuntimeProvider
      runtime={{ routesByResource: { "inventory.Transfer": "/inventory/transfers" } }}
    >
      {children}
    </AppRuntimeProvider>
  );
}

beforeEach(() => {
  mocks.navigate.mockClear();
  mocks.toast.success.mockClear();
  mocks.toast.danger.mockClear();
});

describe("useActionResultRun", () => {
  test("toasts success and deep-links to the created record", async () => {
    const { result } = renderHook(
      () => useActionResultRun({ linkTo: "inventory.Transfer" }),
      { wrapper },
    );

    await act(async () => {
      const outcome = await result.current(async () => ({
        ok: true,
        message: "Delivery WH/OUT/1 generated.",
        id: "tr_9",
      }));
      expect(outcome).toEqual({
        ok: true,
        message: "Delivery WH/OUT/1 generated.",
        id: "tr_9",
      });
    });

    expect(mocks.toast.success).toHaveBeenCalledWith({
      title: "Delivery WH/OUT/1 generated.",
    });
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/inventory/transfers/tr_9" });
    expect(mocks.toast.danger).not.toHaveBeenCalled();
  });

  test("an id-less success (an exhausted idempotent verb) only toasts", async () => {
    const { result } = renderHook(
      () => useActionResultRun({ linkTo: "inventory.Transfer" }),
      { wrapper },
    );

    await act(async () => {
      await result.current(async () => ({ ok: true, message: "Nothing to deliver." }));
    });

    expect(mocks.toast.success).toHaveBeenCalledWith({ title: "Nothing to deliver." });
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  test("a resource without a routed page never navigates", async () => {
    const { result } = renderHook(
      () => useActionResultRun({ linkTo: "accounting.Invoice" }),
      { wrapper },
    );

    await act(async () => {
      await result.current(async () => ({ ok: true, message: "Billed.", id: "inv_1" }));
    });

    expect(mocks.toast.success).toHaveBeenCalledWith({ title: "Billed." });
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  test("a domain failure toasts danger with its in-band non-field reasons", async () => {
    const { result } = renderHook(() => useActionResultRun(), { wrapper });

    await act(async () => {
      const outcome = await result.current(async () => ({
        ok: false,
        message: "Confirm failed.",
        validationErrors: {
          __all__: ["You are not allowed to modify this order."],
        },
      }));
      expect(outcome?.ok).toBe(false);
    });

    expect(mocks.toast.danger).toHaveBeenCalledWith({
      title: "Confirm failed.",
      description: "You are not allowed to modify this order.",
    });
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  test("a missing payload toasts the no-result title", async () => {
    const { result } = renderHook(() => useActionResultRun(), { wrapper });

    await act(async () => {
      const outcome = await result.current(async () => undefined);
      expect(outcome).toBeUndefined();
    });

    expect(mocks.toast.danger).toHaveBeenCalledWith({
      title: "The action returned no result.",
    });
  });

  test("a thrown transport error settles into a danger toast", async () => {
    const { result } = renderHook(
      () => useActionResultRun({ noResultTitle: "Verb failed." }),
      { wrapper },
    );

    await act(async () => {
      const outcome = await result.current(async () => {
        throw new Error("socket closed");
      });
      expect(outcome).toBeUndefined();
    });

    expect(mocks.toast.danger).toHaveBeenCalledWith({ title: "socket closed" });
    expect(mocks.navigate).not.toHaveBeenCalled();
  });
});
