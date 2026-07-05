// @vitest-environment happy-dom

import { act, cleanup, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ToastProvider } from "../feedback";
import { useActionForm } from "./use-action-form";

function wrapper({ children }: { children: ReactNode }): React.ReactElement {
  return <ToastProvider>{children}</ToastProvider>;
}

afterEach(cleanup);

describe("useActionForm", () => {
  test("fires the submit, toasts the message, and hands off on ok", async () => {
    const submit = vi.fn().mockResolvedValue({ ok: true, message: "Done." });
    const onSuccess = vi.fn();
    const { result } = renderHook(
      () => useActionForm<{ x: string }>({ submit, onSuccess }),
      { wrapper },
    );

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.run({ x: "1" });
    });

    expect(ok).toBe(true);
    expect(submit).toHaveBeenCalledWith({ x: "1" });
    expect(onSuccess).toHaveBeenCalledWith(
      { x: "1" },
      { ok: true, message: "Done." },
    );
    expect(result.current.formError).toBeNull();
    expect(result.current.submitting).toBe(false);
    // The success message renders through the shared toast owner.
    expect(await screen.findByText("Done.")).toBeTruthy();
  });

  test("binds in-band field errors and folds unmatched keys into the form error", async () => {
    const submit = vi.fn().mockResolvedValue({
      ok: false,
      message: "Fix it.",
      validationErrors: { amount: ["Too big."], _root: ["Server down."] },
    });
    const { result } = renderHook(
      () => useActionForm({ submit, fieldNames: ["amount"] }),
      { wrapper },
    );

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.run({});
    });

    expect(ok).toBe(false);
    // Every in-band error is available for a field to bind; the form-level message
    // carries the outcome message plus the key no field claimed.
    expect(result.current.fieldErrors).toEqual({
      amount: ["Too big."],
      _root: ["Server down."],
    });
    expect(result.current.formError).toBe("Fix it. Server down.");
  });

  test("clearFieldError removes one field's bound messages", async () => {
    const submit = vi.fn().mockResolvedValue({
      ok: false,
      message: "",
      validationErrors: { a: ["x"], b: ["y"] },
    });
    const { result } = renderHook(
      () => useActionForm({ submit, fieldNames: ["a", "b"] }),
      { wrapper },
    );

    await act(async () => {
      await result.current.run({});
    });
    act(() => result.current.clearFieldError("a"));

    expect(result.current.fieldErrors).toEqual({ b: ["y"] });
  });

  test("surfaces a thrown failure as the form error and clears field errors", async () => {
    const submit = vi.fn().mockRejectedValue(new Error("boom"));
    const { result } = renderHook(
      () => useActionForm({ submit, genericErrorMessage: "Nope." }),
      { wrapper },
    );

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.run({});
    });

    expect(ok).toBe(false);
    expect(result.current.formError).toBe("boom");
    expect(result.current.fieldErrors).toEqual({});
  });

  test("does not toast when toastSuccess is disabled", async () => {
    const submit = vi.fn().mockResolvedValue({ ok: true, message: "Quiet." });
    const { result } = renderHook(
      () => useActionForm({ submit, toastSuccess: false }),
      { wrapper },
    );

    await act(async () => {
      await result.current.run({});
    });

    expect(screen.queryByText("Quiet.")).toBeNull();
  });

  test("guards re-entry while a submit is in flight", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const submit = vi.fn().mockImplementation(async () => {
      await gate;
      return { ok: true, message: "" };
    });
    const { result } = renderHook(() => useActionForm({ submit }), { wrapper });

    let firstRun!: Promise<boolean>;
    act(() => {
      firstRun = result.current.run({});
    });
    await waitFor(() => expect(result.current.submitting).toBe(true));

    let second: boolean | undefined;
    await act(async () => {
      second = await result.current.run({});
    });
    // The in-flight guard rejects the re-entry without re-firing the action.
    expect(second).toBe(false);
    expect(submit).toHaveBeenCalledTimes(1);

    await act(async () => {
      release();
      await firstRun;
    });
    expect(result.current.submitting).toBe(false);
  });
});
