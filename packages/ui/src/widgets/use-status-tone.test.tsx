// @vitest-environment happy-dom

import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { AppRuntimeProvider } from "../runtime/runtime";
import type { StatusToneMap } from "./status-tones";
import { useStatusTone } from "./use-status-tone";

afterEach(cleanup);

describe("useStatusTone", () => {
  test("uses defaults without a runtime provider", () => {
    const { result } = renderHook(useStatusTone);
    expect(result.current("READY")).toBe("success");
    expect(result.current("reviewed")).toBe("brand");
    expect(result.current(null)).toBe("neutral");
  });

  test("reads the current app's vocabulary and preserves explicit options", () => {
    let statusTones: StatusToneMap = { reviewed: "accent" };
    const { result, rerender } = renderHook(useStatusTone, {
      wrapper: ({ children }) => (
        <AppRuntimeProvider runtime={{ statusTones }}>{children}</AppRuntimeProvider>
      ),
    });
    const resolveTone = result.current;
    expect(resolveTone(" REVIEWED ")).toBe("accent");
    expect(resolveTone("REVIEWED", { REVIEWED: "danger" })).toBe("danger");
    expect(resolveTone("bespoke", undefined, { unknownTone: "info" })).toBe("info");
    expect(resolveTone(null, undefined, { emptyTone: "warning" })).toBe("warning");
    rerender();
    expect(result.current).toBe(resolveTone);

    statusTones = { reviewed: "info" };
    rerender();
    expect(result.current("REVIEWED")).toBe("info");
  });
});
