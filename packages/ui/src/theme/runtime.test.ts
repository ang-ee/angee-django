import { describe, expect, test } from "vitest";

import {
  defineTheme,
  THEME_TOKEN_NAMES,
  type ThemeDefinition,
  type ThemeTokenName,
} from "./runtime.mjs";

const EXPOSED_TOKENS = [
  "--fw-regular", "--fw-medium", "--fw-semibold", "--fw-bold",
  "--fs-15", "--lh-15", "--fs-18", "--lh-18", "--fs-22", "--lh-22",
  "--chart-1", "--chart-2", "--chart-3", "--chart-4", "--chart-5", "--chart-6", "--chart-7", "--chart-8",
  "--chart-other", "--chart-surface",
  "--dur-fast", "--dur-base", "--dur-slow", "--ease",
] as const satisfies readonly ThemeTokenName[];

const accepted = [
  ["typography weight", "--fw-semibold", "400"],
  ["font size", "--fs-15", "15px"],
  ["line height", "--lh-18", "26px"],
  ["motion duration", "--dur-fast", "120ms"],
  ["motion easing", "--ease", "cubic-bezier(0.2, 0.6, 0.2, 1)"],
  ["motion easing y overshoot", "--ease", "cubic-bezier(0, -0.4, 1, 1.4)"],
] as const satisfies readonly (readonly [string, ThemeTokenName, string])[];

const rejected = [
  ["typography weight", "--fw-semibold", "901"],
  ["font size", "--fs-15", "41px"],
  ["line height", "--lh-18", "11px"],
  ["motion duration", "--dur-fast", "1001ms"],
  ["motion easing", "--ease", "cubic-bezier(0.2, nope, 0.2, 1)"],
  ["motion easing x control", "--ease", "cubic-bezier(-0.1, 0.6, 1.1, 1)"],
] as const satisfies readonly (readonly [string, ThemeTokenName, string])[];

function themeWithToken(name: ThemeTokenName, value: string): ThemeDefinition {
  return {
    contractVersion: 1,
    id: "example.token-bounds",
    labelKey: "token-bounds.label",
    descriptionKey: "token-bounds.description",
    revision: 1,
    tokens: { shared: { [name]: value }, light: {}, dark: {} },
  };
}

describe("theme token layers", () => {
  test("publishes the typography, chart, and motion token groups", () => {
    const published = THEME_TOKEN_NAMES.filter((name) =>
      name === "--ease"
      || ["--fw-", "--fs-", "--lh-", "--chart-", "--dur-"].some((prefix) => name.startsWith(prefix)),
    );
    expect(new Set(published)).toEqual(new Set(EXPOSED_TOKENS));
  });

  test.each(accepted)("accepts a bounded %s", (_kind, name, value) => {
    expect(() => defineTheme(themeWithToken(name, value))).not.toThrow();
  });

  test.each(rejected)("rejects an invalid %s", (_kind, name, value) => {
    expect(() => defineTheme(themeWithToken(name, value))).toThrow(TypeError);
  });
});
