// @vitest-environment happy-dom

import { act, cleanup, renderHook } from "@testing-library/react";
import { useForm, type UseFormReturn } from "react-hook-form";
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

type Values = Record<string, unknown>;
const LOADED: Values = {
  title: "First", optional: "loaded", nullable: "x",
  detail: { count: 1, label: "a" }, lines: [{ id: 1, qty: 1 }], hiddenTarget: "kept",
};
/** The paths mounted controls register on every render; a path dropped from the draft has no control. */
const MOUNTED = ["title", "optional", "nullable", "detail.count", "detail.label", "lines"] as const;

function setupMounted() {
  return renderHook(() => {
    const form = useForm<Values>({ defaultValues: structuredClone(LOADED) });
    for (const name of MOUNTED) form.register(name);
    const history = useFormHistory(form);
    return { form, history, isDirty: form.formState.isDirty };
  }, { wrapper: Provider });
}

/** Whether `values` holds `path` as an own key, an `undefined` value included. */
function holds(values: unknown, path: string): boolean {
  const segments = path.split(".");
  const last = segments.pop()!;
  const parent = segments.reduce<unknown>((value, segment) => (value as Values | undefined)?.[segment], values);
  return parent !== null && typeof parent === "object" && Object.hasOwn(parent, last);
}

const dropped = Object.fromEntries(Object.entries(LOADED).filter(([name]) => name !== "hiddenTarget"));
interface FrameRow { kind: string; change: (form: UseFormReturn<Values>) => void; changed: Values; path: string; lost?: true }
const frameRows: readonly FrameRow[] = [
  { kind: "plain text", path: "title",
    change: (form) => form.setValue("title", "Second", { shouldDirty: true }), changed: { ...LOADED, title: "Second" } },
  { kind: "an omittable field left Not set", lost: true, path: "optional",
    change: (form) => form.setValue("optional", undefined, { shouldDirty: true }), changed: { ...LOADED, optional: undefined } },
  { kind: "a nullable field set to null", path: "nullable",
    change: (form) => form.setValue("nullable", null, { shouldDirty: true }), changed: { ...LOADED, nullable: null } },
  { kind: "a nested member left Not set", lost: true, path: "detail.label",
    change: (form) => form.setValue("detail.label", undefined, { shouldDirty: true }),
    changed: { ...LOADED, detail: { count: 1, label: undefined } } },
  { kind: "a whole object replaced by one without a member", lost: true, path: "detail.label",
    change: (form) => form.setValue("detail", { count: 2 }, { shouldDirty: true }), changed: { ...LOADED, detail: { count: 2 } } },
  { kind: "a lines row added", path: "lines.1",
    change: (form) => form.setValue("lines", [{ id: 1, qty: 1 }, { id: 2, qty: 5 }], { shouldDirty: true }),
    changed: { ...LOADED, lines: [{ id: 1, qty: 1 }, { id: 2, qty: 5 }] } },
  { kind: "a lines row member left Not set", path: "lines.0.qty",
    change: (form) => form.setValue("lines", [{ id: 1, qty: undefined }], { shouldDirty: true }),
    changed: { ...LOADED, lines: [{ id: 1, qty: undefined }] } },
  { kind: "a path dropped from the draft", path: "hiddenTarget",
    change: (form) => form.unregister("hiddenTarget", { keepDefaultValue: true }), changed: dropped },
];

function landsOnItsFrame({ change, changed, path }: FrameRow): void {
  const { result } = setupMounted();
  // `toStrictEqual` tells a key holding `undefined` from a missing key; `holds` names the key in question.
  const expectFrame = (frame: Values) => {
    expect(result.current.form.getValues()).toStrictEqual(frame);
    expect(holds(result.current.form.getValues(), path)).toBe(holds(frame, path));
  };
  act(() => { result.current.history.perform(() => change(result.current.form)); });
  expectFrame(changed);
  // A second, unrelated step, so the first undo lands ON the changed frame rather than on the loaded one.
  act(() => { result.current.history.perform(() => result.current.form.setValue("title", "Other", { shouldDirty: true })); });
  act(() => result.current.history.undo());
  expectFrame(changed);
  act(() => result.current.history.undo());
  expectFrame(LOADED);
  expect(result.current.isDirty).toBe(false);
  act(() => result.current.history.redo());
  expectFrame(changed);
}

test.each(frameRows.filter((row) => !row.lost))("undo and redo land on the frame $kind recorded, own keys included", landsOnItsFrame);

// KNOWN DEFECT, expected to fail until it is fixed: react-hook-form reads `undefined` as take-the-default at registration. The replay resets
// the form, every mounted field registers again, and where the frame holds `undefined` or no key the
// field takes the loaded value. These rows flip to passing when that is fixed; move them up then.
test.fails.each(frameRows.filter((row) => row.lost))("undo and redo lose $kind: react-hook-form reads `undefined` as take-the-default at registration", landsOnItsFrame);
