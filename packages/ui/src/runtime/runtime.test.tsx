// @vitest-environment happy-dom
import { renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, test } from "vitest";

import {
  AppRuntimeProvider,
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
