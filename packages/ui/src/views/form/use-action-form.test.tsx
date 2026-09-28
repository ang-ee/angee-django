// @vitest-environment happy-dom

import { act, cleanup, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import type { ResolverResult } from "react-hook-form";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ToastProvider } from "../../feedback";
import { useActionForm } from "./use-action-form";

function wrapper({ children }: { children: ReactNode }): React.ReactElement {
  return <ToastProvider>{children}</ToastProvider>;
}

afterEach(cleanup);

describe("useActionForm", () => {
  test("fires the submit, toasts the message, and hands off on ok", async () => {
    const submit = vi.fn().mockResolvedValue({ status: "ok", data: { id: "one" }, message: "Done." });
    const onSuccess = vi.fn();
    const { result } = renderHook(
      () => useActionForm<{ x: string }>({ submit, onSuccess, defaultValues: { x: "" } }),
      { wrapper },
    );

    let ok: boolean | undefined;
    await act(async () => {
      result.current.form.setValue("x", "1", { shouldDirty: true });
      ok = await result.current.run();
    });

    expect(ok).toBe(true);
    expect(submit).toHaveBeenCalledWith({ x: "1" });
    expect(onSuccess).toHaveBeenCalledWith(
      { x: "1" },
      { id: "one" },
    );
    expect(result.current.formError).toBeNull();
    expect(result.current.submitting).toBe(false);
    // The success message renders through the shared toast owner.
    expect(await screen.findByText("Done.")).toBeTruthy();
  });

  test("binds in-band field errors and folds unmatched keys into the form error", async () => {
    const submit = vi.fn().mockResolvedValue({
      status: "invalid",
      issues: { fieldErrors: { count: ["Too big."], _root: ["Server down."] }, formErrors: ["Fix it."] },
    });
    const { result } = renderHook(
      () => useActionForm({ submit, fieldNames: ["count"] }),
      { wrapper },
    );

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.run();
    });

    expect(ok).toBe(false);
    // Every in-band error is available for a field to bind; the form-level message
    // carries the outcome message plus the key no field claimed.
    expect(result.current.fieldErrors).toEqual({
      count: ["Too big."],
      _root: ["Server down."],
    });
    expect(result.current.formError).toBe("Fix it. _root: Server down.");
  });

  test("clearFieldError removes one field's bound messages", async () => {
    const submit = vi.fn().mockResolvedValue({
      status: "invalid",
      issues: { fieldErrors: { a: ["x"], b: ["y"] }, formErrors: [] },
    });
    const { result } = renderHook(
      () => useActionForm({ submit, fieldNames: ["a", "b"] }),
      { wrapper },
    );

    await act(async () => {
      await result.current.run();
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
      ok = await result.current.run();
    });

    expect(ok).toBe(false);
    expect(result.current.formError).toBe("boom");
    expect(result.current.fieldErrors).toEqual({});
  });

  test("uses the fallback for an invalid result without messages", async () => {
    const submit = vi.fn().mockResolvedValue({ status: "invalid", issues: { fieldErrors: {}, formErrors: [] } });
    const { result } = renderHook(
      () => useActionForm({ submit, genericErrorMessage: "Nope." }),
      { wrapper },
    );

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.run();
    });

    expect(ok).toBe(false);
    expect(result.current.formError).toBe("Nope.");
    expect(result.current.fieldErrors).toEqual({});
  });

  test("does not toast when toastSuccess is disabled", async () => {
    const submit = vi.fn().mockResolvedValue({ status: "ok", data: undefined, message: "Quiet." });
    const { result } = renderHook(
      () => useActionForm({ submit, toastSuccess: false }),
      { wrapper },
    );

    await act(async () => {
      await result.current.run();
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
      return { status: "ok", data: undefined };
    });
    const { result } = renderHook(() => useActionForm({ submit }), { wrapper });

    let firstRun!: Promise<boolean>;
    act(() => {
      firstRun = result.current.run();
    });
    await waitFor(() => expect(result.current.submitting).toBe(true));

    let second: boolean | undefined;
    await act(async () => {
      second = await result.current.run();
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

test("validates native values through the resolver without resetting dirty or touched state", async () => {
  const submit = vi.fn().mockResolvedValue({ status: "ok", data: undefined });
  const { result } = renderHook(() => useActionForm<{ title: string }>({
    defaultValues: { title: "" },
    resolver: (values): ResolverResult<{ title: string }> => values.title.trim()
      ? { values: { title: values.title.trim() }, errors: {} }
      : { values: {}, errors: { title: { type: "required", message: "Enter a title." } } },
    submit,
  }), { wrapper });
  await act(async () => { expect(await result.current.run()).toBe(false); });
  expect(submit).not.toHaveBeenCalled();
  expect(result.current.form.getFieldState("title").error?.message).toBe("Enter a title.");
  act(() => result.current.form.setValue("title", "  Draft  ", { shouldDirty: true, shouldTouch: true }));
  await act(async () => { expect(await result.current.run()).toBe(true); });
  expect(submit).toHaveBeenCalledWith({ title: "Draft" });
  expect(result.current.form.getValues("title")).toBe("  Draft  ");
  expect(result.current.form.getFieldState("title")).toMatchObject({ isDirty: true, isTouched: true });
  expect(result.current.form.formState.defaultValues).toEqual({ title: "" });
});

test.each(["invalid", "conflict"] as const)("retains the draft after %s and clears stale errors for a successful retry", async (status) => {
  const failed = status === "invalid"
    ? { status, issues: { fieldErrors: { title: ["Choose another title."] }, formErrors: [] } }
    : { status, message: "The record changed." };
  const submit = vi.fn().mockResolvedValueOnce(failed).mockResolvedValueOnce({ status: "ok", data: undefined });
  const onSuccess = vi.fn();
  const { result } = renderHook(() => useActionForm<{ title: string }>({
    defaultValues: { title: "Original" }, fieldNames: ["title"], submit, onSuccess,
  }), { wrapper });
  act(() => result.current.form.setValue("title", "Draft", { shouldDirty: true }));
  await act(async () => { expect(await result.current.run()).toBe(false); });
  expect(result.current.form.getValues("title")).toBe("Draft");
  expect(result.current.form.getFieldState("title").isDirty).toBe(true);
  expect(onSuccess).not.toHaveBeenCalled();
  expect(result.current.form.formState.errors.root?.server?.type).toBe(status === "conflict" ? "conflict" : "server");
  expect(result.current.saveConflict).toBe(status === "conflict");
  await act(async () => { expect(await result.current.run()).toBe(true); });
  expect(result.current.fieldErrors).toEqual({});
  expect(result.current.formError).toBeNull();
  expect(onSuccess).toHaveBeenCalledTimes(1);
});

test("uses the configured fallback for a failure without a message", async () => {
  const submit = vi.fn().mockRejectedValue({});
  const { result } = renderHook(() => useActionForm({ submit, genericErrorMessage: "Schedule failed." }), { wrapper });
  await act(async () => { expect(await result.current.run()).toBe(false); });
  expect(result.current.formError).toBe("Schedule failed.");
});

test("a success continuation failure stays a developer error after an accepted submission", async () => {
  const onSuccess = vi.fn(() => { throw new Error("Continuation failed"); });
  const submit = vi.fn().mockResolvedValue({ status: "ok", data: "saved", message: "Accepted" });
  const { result } = renderHook(() => useActionForm({ submit, onSuccess }), { wrapper });
  await act(async () => { await expect(result.current.run()).rejects.toThrow("Continuation failed"); });
  expect(await screen.findByText("Accepted")).toBeTruthy();
  expect(result.current.formError).toBeNull();
  expect(result.current.submitting).toBe(false);
  expect(submit).toHaveBeenCalledTimes(1);
});

test("submits the resolver's transformed type while preserving native input values", async () => {
  const onSuccess = vi.fn();
  const { result } = renderHook(() => useActionForm<{ count: string }, number, { count: number }>({
    defaultValues: { count: "12" },
    resolver: (values): ResolverResult<{ count: string }, { count: number }> => ({ values: { count: Number(values.count) }, errors: {} }),
    submit: (values) => ({ status: "ok", data: values.count }),
    onSuccess,
  }), { wrapper });
  await act(async () => { expect(await result.current.run()).toBe(true); });
  expect(onSuccess).toHaveBeenCalledWith({ count: 12 }, 12);
  expect(result.current.form.getValues()).toEqual({ count: "12" });
});

test("an outdated submit callback is a developer failure, not a retry banner", async () => {
  const submit = vi.fn().mockResolvedValue({ id: "old-result" });
  const { result } = renderHook(() => useActionForm({ submit }), { wrapper });
  await act(async () => { await expect(result.current.run()).rejects.toThrow(/FormSubmitResult contract/); });
  expect(result.current.formError).toBeNull();
  expect(result.current.submitting).toBe(false);
});
