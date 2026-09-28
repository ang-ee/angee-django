// @vitest-environment happy-dom

import { act, cleanup, renderHook } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { afterEach, expect, test, vi } from "vitest";

import { createUiTestProviders } from "../../testing";
import { useFormHistory } from "./use-form-history";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const { Provider } = createUiTestProviders();

function setup(readOnly = false, limit = 100) {
  return renderHook(() => {
    const form = useForm({ defaultValues: { title: "First", detail: { count: 1 } } });
    const history = useFormHistory(form, { readOnly, limit });
    return { form, history, isDirty: form.formState.isDirty };
  }, { wrapper: Provider });
}

test("undo and redo restore whole snapshots against the native dirty baseline", () => {
  const { result } = setup();
  act(() => result.current.history.perform(() => {
    result.current.form.setValue("title", "Second", { shouldDirty: true });
    result.current.form.setValue("detail.count", 2, { shouldDirty: true });
  }));
  expect(result.current.history.canUndo).toBe(true);
  expect(result.current.isDirty).toBe(true);
  act(() => result.current.history.undo());
  expect(result.current.form.getValues()).toEqual({ title: "First", detail: { count: 1 } });
  expect(result.current.isDirty).toBe(false);
  expect(result.current.history.canRedo).toBe(true);
  act(() => result.current.history.redo());
  expect(result.current.form.getValues()).toEqual({ title: "Second", detail: { count: 2 } });
  expect(result.current.isDirty).toBe(true);
});

test("groups typing, ignores unrelated commits, and finishes active edits before undo", () => {
  const { result } = setup();
  act(() => {
    result.current.history.start("title");
    result.current.form.setValue("title", "S");
    result.current.history.start("title");
    result.current.form.setValue("title", "Second");
    result.current.history.commit("detail");
    result.current.history.undo();
  });
  expect(result.current.form.getValues("title")).toBe("First");
  expect(result.current.history.canUndo).toBe(false);
  act(() => result.current.history.redo());
  expect(result.current.form.getValues("title")).toBe("Second");
});

test("new edits discard redo but unchanged operations preserve it", () => {
  const { result } = setup();
  act(() => {
    result.current.history.perform(() => result.current.form.setValue("title", "Second"));
    result.current.history.undo();
    result.current.history.perform(() => result.current.form.setValue("title", "First"));
  });
  expect(result.current.history.canRedo).toBe(true);
  act(() => {
    result.current.history.start("title");
    result.current.form.setValue("title", "Third");
    result.current.history.commit("title");
  });
  expect(result.current.history.canRedo).toBe(false);
  act(() => result.current.history.undo());
  expect(result.current.form.getValues("title")).toBe("First");
});

test("continued typing after undo stays grouped and prevents stale redo", () => {
  const { result } = setup();
  act(() => result.current.history.start("title"));
  expect(result.current.history.canUndo).toBe(false);
  act(() => result.current.form.setValue("title", "Second"));
  expect(result.current.history.canUndo).toBe(true);
  act(() => result.current.history.undo());
  expect(result.current.history.canRedo).toBe(true);
  act(() => result.current.form.setValue("title", "Third"));
  expect(result.current.history.canUndo).toBe(true);
  expect(result.current.history.canRedo).toBe(false);
  act(() => {
    result.current.history.commit("title");
    result.current.history.redo();
  });
  expect(result.current.form.getValues("title")).toBe("Third");
  act(() => result.current.history.undo());
  expect(result.current.form.getValues("title")).toBe("First");
});

test("reset and a new focus group keep undo availability live", () => {
  const { result } = setup();
  act(() => {
    result.current.history.start("title");
    result.current.form.setValue("title", "Second");
  });
  expect(result.current.history.canUndo).toBe(true);
  act(() => {
    result.current.history.reset();
    result.current.history.start("title");
  });
  expect(result.current.history.canUndo).toBe(false);
  act(() => result.current.form.setValue("title", "Third"));
  expect(result.current.history.canUndo).toBe(true);
  act(() => result.current.history.undo());
  expect(result.current.form.getValues("title")).toBe("Second");
});

test("a synchronous action preserves the focused group for later typing", () => {
  const { result } = setup();
  act(() => {
    result.current.history.start("title");
    result.current.form.setValue("title", "Second");
    result.current.history.perform(() => result.current.form.setValue("detail.count", 2));
    result.current.form.setValue("title", "Third");
    result.current.history.commit("title");
    result.current.history.undo();
  });
  expect(result.current.form.getValues()).toEqual({ title: "Second", detail: { count: 2 } });
});

test("reset clears pending history without changing accepted values or defaults", () => {
  const { result } = setup();
  act(() => {
    result.current.history.start("title");
    result.current.form.setValue("title", "Loaded");
    result.current.form.reset(result.current.form.getValues());
    result.current.history.reset();
    result.current.history.commit("title");
    result.current.history.undo();
  });
  expect(result.current.form.getValues("title")).toBe("Loaded");
  expect(result.current.isDirty).toBe(false);
  expect(result.current.history.canUndo).toBe(false);
  expect(result.current.history.canRedo).toBe(false);
});

test("read-only history does not execute edits", () => {
  const { result } = setup(true);
  const action = vi.fn();
  act(() => {
    expect(result.current.history.perform(action)).toBe(false);
    result.current.history.start("title");
    result.current.history.undo();
    result.current.history.redo();
  });
  expect(action).not.toHaveBeenCalled();
  expect(result.current.history.canUndo).toBe(false);
});

test("both stacks retain only the configured number of recent edits", () => {
  const { result } = setup(false, 2);
  act(() => {
    for (const value of [2, 3, 4]) expect(result.current.history.perform(() => {
      result.current.form.setValue("detail.count", value);
    })).toBe(true);
    result.current.history.undo();
    result.current.history.undo();
    result.current.history.undo();
  });
  expect(result.current.form.getValues("detail.count")).toBe(2);
  expect(result.current.history.canUndo).toBe(false);
  act(() => { result.current.history.redo(); result.current.history.redo(); result.current.history.redo(); });
  expect(result.current.form.getValues("detail.count")).toBe(4);
  expect(result.current.history.canRedo).toBe(false);
});

test("a native value change captures once and renders or replay do not recapture it", () => {
  const { result, rerender } = setup();
  const clone = vi.spyOn(globalThis, "structuredClone");
  act(() => {
    result.current.history.start("title");
    result.current.form.setValue("title", "Second");
    result.current.history.commit("title");
  });
  expect(clone).toHaveBeenCalledTimes(1);
  rerender();
  act(() => { result.current.history.undo(); result.current.history.redo(); });
  expect(clone).toHaveBeenCalledTimes(1);
});

test("partially completed actions remain undoable when an action throws", () => {
  const { result } = setup();
  act(() => {
    expect(() => result.current.history.perform(() => {
      result.current.form.setValue("detail.count", 3);
      throw new Error("Interrupted");
    })).toThrow("Interrupted");
  });
  act(() => result.current.history.undo());
  expect(result.current.form.getValues("detail.count")).toBe(1);
});
