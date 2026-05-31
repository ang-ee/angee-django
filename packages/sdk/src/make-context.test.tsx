// @vitest-environment happy-dom
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, test } from "vitest";

import { makeContext } from "./make-context";

interface Counter {
  count: number;
}

describe("makeContext", () => {
  test("the required hook returns the nearest provider value", () => {
    const Ctx = makeContext<Counter>("Counter");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <Ctx.Provider value={{ count: 7 }}>{children}</Ctx.Provider>
    );
    const { result } = renderHook(() => Ctx.use(), { wrapper });
    expect(result.current).toEqual({ count: 7 });
  });

  test("the required hook throws a named error outside a provider", () => {
    const Ctx = makeContext<Counter>("Counter");
    expect(() => renderHook(() => Ctx.use())).toThrow(/Counter/);
  });

  test("the optional hook returns null outside a provider", () => {
    const Ctx = makeContext<Counter>("Counter");
    const { result } = renderHook(() => Ctx.useMaybe());
    expect(result.current).toBeNull();
  });

  test("the optional hook returns the value inside a provider", () => {
    const Ctx = makeContext<Counter>("Counter");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <Ctx.Provider value={{ count: 3 }}>{children}</Ctx.Provider>
    );
    const { result } = renderHook(() => Ctx.useMaybe(), { wrapper });
    expect(result.current).toEqual({ count: 3 });
  });
});
