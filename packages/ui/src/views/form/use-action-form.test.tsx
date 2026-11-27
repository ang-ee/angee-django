// @vitest-environment happy-dom

import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import type { Mode, ResolverResult } from "react-hook-form";
import { describe, expect, test, vi } from "vitest";

import { ToastProvider } from "../../feedback";
import { useActionForm } from "./use-action-form";

function wrapper({ children }: { children: ReactNode }): React.ReactElement {
  return <ToastProvider>{children}</ToastProvider>;
}

describe("useActionForm", () => {
  test("fires the submit, toasts the message, and hands off on ok", async () => {
    const submit = vi.fn().mockResolvedValue({ status: "ok", data: { id: "one" }, message: "Done." });
    const onSuccess = vi.fn();
    const { result } = renderHook(
      () => useActionForm<{ x: string }>({ submit, onSuccess, defaultValues: { x: "" }, shownFieldNames: [] }),
      { wrapper },
    );

    let ok: boolean | undefined;
    await act(async () => {
      result.current.form.setValue("x", "1", { shouldDirty: true });
      ok = await result.current.run();
    });

    expect(ok).toBe(true);
    expect(submit).toHaveBeenCalledWith({ x: "1" }, { errorNotification: false });
    expect(onSuccess).toHaveBeenCalledWith(
      { x: "1" },
      { id: "one" },
    );
    expect(result.current.formErrors).toEqual([]);
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
      () => useActionForm({ submit, shownFieldNames: ["count"] }),
      { wrapper },
    );

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.run();
    });

    expect(ok).toBe(false);
    // Every in-band error is available for a field to bind; the form-level messages are
    // the refusal's form-level reason, then the key no field claimed.
    expect(result.current.fieldErrors).toEqual({
      count: ["Too big."],
      _root: ["Server down."],
    });
    expect(result.current.formErrors).toEqual(["Fix it.", "_root: Server down."]);
  });

  test("fields that follow the values are read with the form's values when the answer lands: a reason of a field not shown then folds into the form error", async () => {
    const submit = vi.fn().mockResolvedValue({
      status: "invalid", issues: { fieldErrors: { token: ["Expired."] }, formErrors: [] },
    });
    const shownFieldNames = (values: { mode: string }) => values.mode === "note" ? ["mode", "token"] : ["mode"];
    const { result } = renderHook(
      () => useActionForm<{ mode: string }>({ submit, defaultValues: { mode: "email" }, shownFieldNames }),
      { wrapper },
    );

    await act(async () => { await result.current.run(); });
    expect(result.current.formErrors).toEqual(["token: Expired."]);

    act(() => result.current.form.setValue("mode", "note"));
    await act(async () => { await result.current.run(); });
    expect(result.current.formErrors).toEqual([]);
    expect(result.current.fieldErrors).toEqual({ token: ["Expired."] });
  });

  /** A submit refused under `token` once `answer` is called. */
  function heldTokenRefusal() {
    let answer!: () => void;
    const gate = new Promise<void>((resolve) => { answer = resolve; });
    const submit = vi.fn().mockImplementation(async () => {
      await gate;
      return { status: "invalid", issues: { fieldErrors: { token: ["Expired."] }, formErrors: [] } };
    });
    return { submit, answer: () => answer() };
  }

  test.each<readonly [string, readonly string[], readonly string[], readonly string[]]>([
    ["a field gone while the submit was out is left to the form error", ["token"], [], ["token: Expired."]],
    ["a field drawn while the submit was out claims its reason", [], ["token"], []],
  ])("a plain list is read when the answer lands: %s", async (_name, atSubmit, atAnswer, formErrors) => {
    const { submit, answer } = heldTokenRefusal();
    const { result, rerender } = renderHook(
      ({ shownFieldNames }: { shownFieldNames: readonly string[] }) => useActionForm({ submit, shownFieldNames }),
      { wrapper, initialProps: { shownFieldNames: atSubmit } },
    );

    let run!: Promise<boolean>;
    act(() => { run = result.current.run(); });
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    rerender({ shownFieldNames: atAnswer });
    await act(async () => {
      answer();
      await run;
    });

    expect(result.current.formErrors).toEqual(formErrors);
    expect(result.current.fieldErrors).toEqual({ token: ["Expired."] });
  });

  test("the line naming the refused fields is read with the list, when the answer lands", async () => {
    const { submit, answer } = heldTokenRefusal();
    const { result, rerender } = renderHook(
      ({ line }: { line: string }) => useActionForm({ submit, shownFieldNames: ["token"], fieldSummary: () => line }),
      { wrapper, initialProps: { line: "Check the fields shown at the submit." } },
    );

    let run!: Promise<boolean>;
    act(() => { run = result.current.run(); });
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    rerender({ line: "Check the fields shown now." });
    await act(async () => {
      answer();
      await run;
    });

    expect(result.current.formErrors).toEqual(["Check the fields shown now."]);
  });

  test("clearFieldError removes one field's bound messages", async () => {
    const submit = vi.fn().mockResolvedValue({
      status: "invalid",
      issues: { fieldErrors: { a: ["x"], b: ["y"] }, formErrors: [] },
    });
    const { result } = renderHook(
      () => useActionForm({ submit, shownFieldNames: ["a", "b"] }),
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
      () => useActionForm({ submit, genericErrorMessage: "Nope.", shownFieldNames: [] }),
      { wrapper },
    );

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.run();
    });

    expect(ok).toBe(false);
    expect(result.current.formErrors).toEqual(["boom"]);
    expect(result.current.fieldErrors).toEqual({});
    // The form renders the failure, so the submission was told to keep refine's toast off.
    expect(submit).toHaveBeenCalledExactlyOnceWith({}, { errorNotification: false });
  });

  test("uses the fallback for an invalid result without messages", async () => {
    const submit = vi.fn().mockResolvedValue({ status: "invalid", issues: { fieldErrors: {}, formErrors: [] } });
    const { result } = renderHook(
      () => useActionForm({ submit, genericErrorMessage: "Nope.", shownFieldNames: [] }),
      { wrapper },
    );

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.run();
    });

    expect(ok).toBe(false);
    expect(result.current.formErrors).toEqual(["Nope."]);
    expect(result.current.fieldErrors).toEqual({});
  });

  test("does not toast when toastSuccess is disabled", async () => {
    const submit = vi.fn().mockResolvedValue({ status: "ok", data: undefined, message: "Quiet." });
    const { result } = renderHook(
      () => useActionForm({ submit, toastSuccess: false, shownFieldNames: [] }),
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
    const { result } = renderHook(() => useActionForm({ submit, shownFieldNames: [] }), { wrapper });

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
    defaultValues: { title: "" }, shownFieldNames: [],
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
  expect(submit).toHaveBeenCalledWith({ title: "Draft" }, { errorNotification: false });
  expect(result.current.form.getValues("title")).toBe("  Draft  ");
  expect(result.current.form.getFieldState("title")).toMatchObject({ isDirty: true, isTouched: true });
  expect(result.current.form.formState.defaultValues).toEqual({ title: "" });
});

test.each([false, true])("shows editor errors together with field validation (resolver: %s)", async (withResolver) => {
  const submit = vi.fn().mockResolvedValue({ status: "ok", data: undefined });
  const { result } = renderHook(() => useActionForm<{ title: string; payload: string }>({
    defaultValues: { title: "", payload: "retained" }, shownFieldNames: [],
    resolver: withResolver ? (values): ResolverResult<{ title: string; payload: string }> => values.title
      ? { values, errors: {} }
      : { values: {}, errors: { title: { type: "required", message: "Enter a title." } } }
      : undefined,
    submit,
  }), { wrapper });
  result.current.form.register("title", { required: "Enter a title." });
  const editorValidation = vi.fn((): string | undefined => "Complete the draft.");
  const unregister = result.current.form.registerFieldValidation("payload", editorValidation);

  await act(async () => { expect(await result.current.run()).toBe(false); });
  expect(submit).not.toHaveBeenCalled();
  expect(editorValidation).toHaveBeenCalledTimes(1);
  expect(editorValidation).toHaveBeenCalledWith("retained", { title: "", payload: "retained" });
  expect(result.current.form.getFieldState("title").error?.message).toBe("Enter a title.");
  expect(result.current.form.getFieldState("payload").error?.message).toBe("Complete the draft.");

  act(() => result.current.form.setValue("title", "Ready"));
  editorValidation.mockReturnValue(undefined);
  await act(async () => { expect(await result.current.run()).toBe(true); });
  expect(submit).toHaveBeenCalledWith({ title: "Ready", payload: "retained" }, { errorNotification: false });
  expect(result.current.form.formState.errors).toEqual({});
  unregister();
});

test.each(["invalid", "conflict"] as const)("retains the draft after %s and clears stale errors for a successful retry", async (status) => {
  const failed = status === "invalid"
    ? { status, issues: { fieldErrors: { title: ["Choose another title."] }, formErrors: [] } }
    : { status, message: "The record changed." };
  const submit = vi.fn().mockResolvedValueOnce(failed).mockResolvedValueOnce({ status: "ok", data: undefined });
  const onSuccess = vi.fn();
  const { result } = renderHook(() => useActionForm<{ title: string }>({
    defaultValues: { title: "Original" }, shownFieldNames: ["title"], submit, onSuccess,
  }), { wrapper });
  act(() => result.current.form.setValue("title", "Draft", { shouldDirty: true }));
  await act(async () => { expect(await result.current.run()).toBe(false); });
  expect(result.current.form.getValues("title")).toBe("Draft");
  expect(result.current.form.getFieldState("title").isDirty).toBe(true);
  expect(onSuccess).not.toHaveBeenCalled();
  // A refusal its field shows is not repeated as the form-level fallback; a conflict names the form.
  expect(result.current.form.formState.errors.root?.server?.type).toBe(status === "conflict" ? "conflict" : undefined);
  if (status === "invalid") expect(result.current.fieldErrors).toEqual({ title: ["Choose another title."] });
  expect(result.current.saveConflict).toBe(status === "conflict");
  await act(async () => { expect(await result.current.run()).toBe(true); });
  expect(result.current.fieldErrors).toEqual({});
  expect(result.current.formErrors).toEqual([]);
  expect(onSuccess).toHaveBeenCalledTimes(1);
});

test("uses the configured fallback for a failure without a message", async () => {
  const submit = vi.fn().mockRejectedValue({});
  const { result } = renderHook(() => useActionForm({ submit, genericErrorMessage: "Schedule failed.", shownFieldNames: [] }), { wrapper });
  await act(async () => { expect(await result.current.run()).toBe(false); });
  expect(result.current.formErrors).toEqual(["Schedule failed."]);
});

test("a success continuation failure stays a developer error after an accepted submission", async () => {
  const onSuccess = vi.fn(() => { throw new Error("Continuation failed"); });
  const submit = vi.fn().mockResolvedValue({ status: "ok", data: "saved", message: "Accepted" });
  const { result } = renderHook(() => useActionForm({ submit, onSuccess, shownFieldNames: [] }), { wrapper });
  await act(async () => { await expect(result.current.run()).rejects.toThrow("Continuation failed"); });
  expect(await screen.findByText("Accepted")).toBeTruthy();
  expect(result.current.formErrors).toEqual([]);
  expect(result.current.submitting).toBe(false);
  expect(submit).toHaveBeenCalledTimes(1);
});

test("submits the resolver's transformed type while preserving native input values", async () => {
  const onSuccess = vi.fn();
  const { result } = renderHook(() => useActionForm<{ count: string }, number, { count: number }>({
    defaultValues: { count: "12" }, shownFieldNames: [],
    resolver: (values): ResolverResult<{ count: string }, { count: number }> => ({ values: { count: Number(values.count) }, errors: {} }),
    submit: (values) => ({ status: "ok", data: values.count }),
    onSuccess,
  }), { wrapper });
  await act(async () => { expect(await result.current.run()).toBe(true); });
  expect(onSuccess).toHaveBeenCalledWith({ count: 12 }, 12);
  expect(result.current.form.getValues()).toEqual({ count: "12" });
});

describe("when the form checks a value before its first submit", () => {
  const resolver = (values: { title: string }): ResolverResult<{ title: string }> => values.title.trim()
    ? { values, errors: {} }
    : { values: {}, errors: { title: { type: "required", message: "Enter a title." } } };
  function TitleForm({ mode, withResolver = true }: { mode?: Mode; withResolver?: boolean }): React.ReactElement {
    const action = useActionForm<{ title: string }>({
      defaultValues: { title: "Draft" }, resolver: withResolver ? resolver : undefined, mode, shownFieldNames: ["title"],
      submit: () => ({ status: "ok", data: undefined }),
    });
    return <form onSubmit={(event) => { event.preventDefault(); void action.run(); }}>
      <input aria-label="Title" {...action.form.register("title")} />
      <output aria-label="Title message">{action.form.formState.errors.title?.message ?? ""}</output>
      <button type="button" onClick={() => action.form.setError("title", { type: "server", message: "Choose another title." })}>
        Show error
      </button>
      <button type="submit">Save</button>
    </form>;
  }
  const message = () => screen.getByLabelText("Title message").textContent;
  // The change's validation runs after the event; a macrotask lets it settle before asserting.
  const settle = () => act(() => new Promise((resolve) => setTimeout(resolve, 20)));

  test("guard: a caller that passes no mode checks nothing before the first submit", async () => {
    render(<TitleForm />, { wrapper });
    fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "" } });
    await settle();
    expect(message()).toBe("");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(message()).toBe("Enter a title."));
  });

  test("guard: a caller that passes no mode and no resolver keeps an error it set through an edit until the first submit", async () => {
    render(<TitleForm withResolver={false} />, { wrapper });
    const title = await screen.findByLabelText("Title");
    fireEvent.click(screen.getByRole("button", { name: "Show error" }));
    await waitFor(() => expect(message()).toBe("Choose another title."));
    fireEvent.change(title, { target: { value: "Other" } });
    await settle();
    expect(message()).toBe("Choose another title.");
  });

  test("a caller's react-hook-form mode decides it: with onChange, a failing value shows its message as it changes", async () => {
    render(<TitleForm mode="onChange" />, { wrapper });
    fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "" } });
    await waitFor(() => expect(message()).toBe("Enter a title."));

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Ready" } });
    await waitFor(() => expect(message()).toBe(""));
  });
});

test("an outdated submit callback is a developer failure, not a retry banner", async () => {
  const submit = vi.fn().mockResolvedValue({ id: "old-result" });
  const { result } = renderHook(() => useActionForm({ submit, shownFieldNames: [] }), { wrapper });
  await act(async () => { await expect(result.current.run()).rejects.toThrow(/FormSubmitResult contract/); });
  expect(result.current.formErrors).toEqual([]);
  expect(result.current.submitting).toBe(false);
});

test("the form states the fields it shows: an omitted list does not compile, and [] sends every field reason to the banner", async () => {
  const submit = vi.fn().mockResolvedValue({
    status: "invalid", issues: { fieldErrors: { title: ["Choose another title."], "rows.0.qty": ["Too many."] }, formErrors: [] },
  });
  // @ts-expect-error `shownFieldNames` is required: a form that shows no field a reason can sit under says `[]`.
  renderHook(() => useActionForm({ submit }), { wrapper }).unmount();
  const { result } = renderHook(() => useActionForm({ submit, shownFieldNames: [] }), { wrapper });
  await act(async () => { expect(await result.current.run()).toBe(false); });
  // Each reason is still set at its path, and none is claimed: the banner says each as `path: reason`.
  expect(result.current.fieldErrors).toEqual({ title: ["Choose another title."], "rows.0.qty": ["Too many."] });
  expect(result.current.formErrors).toEqual(["title: Choose another title.", "rows.0.qty: Too many."]);
});
