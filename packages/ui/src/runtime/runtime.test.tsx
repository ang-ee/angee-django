// @vitest-environment happy-dom
import { renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, test } from "vitest";

import {
  AppRuntimeProvider,
  useDrawers,
  useSlot,
  useT,
  useWidget,
  type AppRuntime,
} from "./runtime";

function wrapperFor(runtime: Partial<AppRuntime>) {
  return ({ children }: { children: ReactNode }) =>
    createElement(AppRuntimeProvider, { runtime, children });
}

describe("useWidget", () => {
  test("returns a registered widget by id", () => {
    const wrapper = wrapperFor({ widgets: { text: "TEXT_WIDGET" } });
    const { result } = renderHook(() => useWidget("text"), { wrapper });
    expect(result.current).toBe("TEXT_WIDGET");
  });

  test("returns undefined for an unknown widget", () => {
    const { result } = renderHook(() => useWidget("missing"));
    expect(result.current).toBeUndefined();
  });
});

describe("useSlot", () => {
  test("returns only the entries contributed to the requested slot", () => {
    const wrapper = wrapperFor({
      slots: [
        { slot: "header", id: "a" },
        { slot: "footer", id: "b" },
        { slot: "header", id: "c" },
      ],
    });
    const { result } = renderHook(() => useSlot("header"), { wrapper });
    expect(result.current.map((entry) => entry.id)).toEqual(["a", "c"]);
  });
});

describe("useDrawers", () => {
  const drawers = [
    { id: "logs", edge: "bottom" as const, title: "Logs", render: () => null },
    { id: "chat", edge: "right" as const, title: "Chat", render: () => null },
    { id: "tail", edge: "bottom" as const, title: "Tail", render: () => null },
  ];

  test("returns every drawer when no edge is given", () => {
    const wrapper = wrapperFor({ drawers });
    const { result } = renderHook(() => useDrawers(), { wrapper });
    expect(result.current.map((d) => d.id)).toEqual(["logs", "chat", "tail"]);
  });

  test("returns only the drawers contributed to the requested edge", () => {
    const wrapper = wrapperFor({ drawers });
    const { result } = renderHook(() => useDrawers("bottom"), { wrapper });
    expect(result.current.map((d) => d.id)).toEqual(["logs", "tail"]);
  });

  test("is empty when nothing is contributed", () => {
    const { result } = renderHook(() => useDrawers("right"));
    expect(result.current).toEqual([]);
  });
});

describe("useT", () => {
  test("resolves a key in its namespace and interpolates vars", () => {
    const wrapper = wrapperFor({ i18n: { notes: { greet: "Hi {name}" } } });
    const { result } = renderHook(() => useT("notes"), { wrapper });
    expect(result.current("greet", { name: "Ada" })).toBe("Hi Ada");
  });

  test("falls back to the key when the namespace lacks it", () => {
    const { result } = renderHook(() => useT("notes"));
    expect(result.current("missing")).toBe("missing");
  });
});
