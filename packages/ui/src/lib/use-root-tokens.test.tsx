// @vitest-environment happy-dom

import { act, cleanup, render } from "@testing-library/react";
import { memo, useMemo, type ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../runtime/runtime";
import { AppearanceProvider } from "../theme/appearance";
import { defineTheme } from "../theme/runtime.mjs";
import { useMotionTokens, useRootTokens } from "./use-root-tokens";

const theme = defineTheme({
  contractVersion: 1, id: "example.tokens", labelKey: "tokens.label", descriptionKey: "tokens.description", revision: 1,
  tokens: { shared: {}, light: {}, dark: {} },
  options: {
    version: 1, defaults: { focus: "#112233", radius: "6px" },
    parse: (value) => value as { focus: string; radius: string },
    resolve: (value) => ({ shared: { "--border-focus": value.focus, "--r-6": value.radius } }),
  },
});
const runtime = { themes: [{ definition: theme }] };
const NAMES = ["--border-focus"] as const;

function Themed({ focus, radius, children }: { focus: string; radius: string; children: ReactNode }) {
  const host = useMemo(() => ({ themeId: "example.tokens", colorScheme: "light" as const, options: { version: 1, value: { focus, radius } } }), [focus, radius]);
  return <AppRuntimeProvider runtime={runtime}><AppearanceProvider host={host}>{children}</AppearanceProvider></AppRuntimeProvider>;
}

/** Let renders settle: until `count` holds over one timer turn, at most 20 turns. */
async function settle(count: () => number): Promise<void> {
  let last = -1;
  for (let turn = 0; turn < 20 && last !== count(); turn++) {
    last = count();
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
  }
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  for (const name of ["--border-focus", "--r-6", "--dur-base", "--ease"]) document.documentElement.style.removeProperty(name);
});

describe("useRootTokens", () => {
  test("settles when the names are written inline", async () => {
    let renders = 0;
    function Inline() {
      renders += 1;
      // The cap: past 50 renders the names come from a constant, so a reader that loops stops there.
      useRootTokens(renders > 50 ? NAMES : ["--border-focus"]);
      return null;
    }
    render(<Themed focus="#112233" radius="6px"><Inline /></Themed>);
    await settle(() => renders);
    expect(renders).toBeLessThan(10);
  });

  test("reads the root no more when its parent renders again with the names written inline", async () => {
    const reads = vi.spyOn(globalThis, "getComputedStyle");
    const rootReads = () => reads.mock.calls.filter(([element]) => element === document.documentElement).length;
    let renders = 0;
    function Inline() {
      renders += 1;
      useRootTokens(["--border-focus"]);
      return null;
    }
    const { rerender } = render(<Themed focus="#112233" radius="6px"><Inline /></Themed>);
    await settle(() => renders);
    const settledReads = rootReads();
    const settledRenders = renders;
    for (let again = 0; again < 3; again++) rerender(<Themed focus="#112233" radius="6px"><Inline /></Themed>);
    await settle(() => renders);
    expect(renders).toBeGreaterThan(settledRenders);
    expect(rootReads()).toBe(settledReads);
  });

  test("keeps its record while an appearance change moves none of its names, so no consumer of it renders again", async () => {
    const seen: string[] = [];
    let consumerRenders = 0;
    const Consumer = memo(function Consumer({ tokens }: { tokens: Readonly<Record<string, string>> }) {
      consumerRenders += 1;
      seen.push(tokens["--border-focus"] ?? "");
      return null;
    });
    function Reader() {
      return <Consumer tokens={useRootTokens(NAMES)} />;
    }
    const { rerender } = render(<Themed focus="#112233" radius="6px"><Reader /></Themed>);
    await settle(() => consumerRenders);
    expect(seen.at(-1)).toBe("#112233");
    const settled = consumerRenders;
    rerender(<Themed focus="#112233" radius="8px"><Reader /></Themed>);
    await settle(() => consumerRenders);
    expect(document.documentElement.style.getPropertyValue("--r-6")).toBe("8px");
    expect(consumerRenders).toBe(settled);
    rerender(<Themed focus="#e7b008" radius="8px"><Reader /></Themed>);
    await settle(() => consumerRenders);
    expect(consumerRenders).toBe(settled + 1);
    expect(seen.at(-1)).toBe("#e7b008");
  });

  test("reads the root at mount without an appearance provider", () => {
    document.documentElement.style.setProperty("--border-focus", "#123456");
    let read = "";
    function Reader() {
      read = useRootTokens(NAMES)["--border-focus"];
      return null;
    }
    render(<Reader />);
    expect(read).toBe("#123456");
  });
});

describe("useMotionTokens", () => {
  test("reads the root's timing, with the stock duration for one that does not parse and the stock easing for none", () => {
    let timing: { duration: number; easing: string } | null = null;
    function Reader() {
      timing = useMotionTokens();
      return null;
    }
    document.documentElement.style.setProperty("--dur-base", "0.2s");
    document.documentElement.style.setProperty("--ease", "linear");
    render(<Reader />);
    expect(timing).toEqual({ duration: 200, easing: "linear" });
    cleanup();
    document.documentElement.style.setProperty("--dur-base", "abc");
    document.documentElement.style.removeProperty("--ease");
    render(<Reader />);
    expect(timing).toEqual({ duration: 180, easing: "cubic-bezier(0.2, 0.6, 0.2, 1)" });
  });
});
