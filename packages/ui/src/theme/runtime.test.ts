// @vitest-environment happy-dom

import { Storage as TestStorage } from "happy-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  APPEARANCE_CACHE_KEY,
  APPEARANCE_CACHE_LIMIT,
  APPEARANCE_CACHE_SCHEMA,
  APPEARANCE_PAYLOAD_ATTRIBUTE,
  bootstrapAppearance,
  createThemeCustomizationOptions,
  defineTheme,
  readAppearancePayload,
  resolveAppearance,
  resolveThemeOptions,
  serializableThemeMetadata,
  type AppearanceBootstrapPayload,
  THEME_TOKEN_NAMES,
  type ThemeCustomization,
  type ThemeCustomizationConfiguration,
  type ThemeDefinition,
  type ThemeTokenName,
} from "./runtime.mjs";

const EXPOSED_TOKENS = [
  "--fw-regular", "--fw-medium", "--fw-semibold", "--fw-bold",
  "--fs-11", "--lh-11", "--fs-12", "--lh-12", "--fs-13", "--lh-13", "--fs-14", "--lh-14",
  "--fs-15", "--lh-15", "--fs-16", "--lh-16", "--fs-18", "--lh-18", "--fs-22", "--lh-22",
  "--fs-28", "--lh-28", "--fs-34", "--lh-34",
  "--chart-1", "--chart-2", "--chart-3", "--chart-4", "--chart-5", "--chart-6", "--chart-7", "--chart-8",
  "--chart-other", "--chart-surface",
  "--dur-fast", "--dur-base", "--dur-slow", "--ease",
] as const satisfies readonly ThemeTokenName[];

const accepted = [
  ["display font family", "--font-family-display", '"Fraunces", Georgia, serif'],
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


const bootstrapPayload: AppearanceBootstrapPayload = {
  fingerprint: "host-fingerprint",
  host: {
    themeId: "example.token-bounds",
    colorScheme: "light",
    fingerprint: "host-fingerprint",
    tokenLayers: { shared: { "--brand": "#112233" }, light: {}, dark: {} },
  },
  catalogue: {
    schema: 1,
    fingerprint: "catalogue-fingerprint",
    themes: [{ id: "example.token-bounds" }],
  },
};

function cacheAppearance(overrides: Record<string, unknown>) {
  window.localStorage.setItem(APPEARANCE_CACHE_KEY, JSON.stringify({
    schema: APPEARANCE_CACHE_SCHEMA,
    fingerprint: bootstrapPayload.fingerprint,
    actorId: "actor-one",
    themeId: "example.token-bounds",
    colorSchemePreference: "dark",
    tokenLayers: { shared: { "--brand": "#abcdef" }, light: {}, dark: {} },
    ...overrides,
  }));
}

describe("first-paint appearance", () => {
  beforeEach(() => {
    Object.defineProperty(window, "localStorage", { configurable: true, value: new TestStorage() });
    document.documentElement.removeAttribute("style");
    delete document.documentElement.dataset.themeId;
    delete document.documentElement.dataset.colorScheme;
    delete document.documentElement.dataset.theme;
  });
  afterEach(() => { vi.restoreAllMocks(); });

  test.each(accepted)("uses the theme contract for cached %s", (_kind, name, value) => {
    cacheAppearance({ tokenLayers: themeWithToken(name, value).tokens });
    bootstrapAppearance(bootstrapPayload, window);
    expect(document.documentElement.style.getPropertyValue(name)).toBe(value);
    expect(document.documentElement.dataset.colorScheme).toBe("dark");
  });

  test.each(rejected)("falls back to host defaults for cached %s", (_kind, name, value) => {
    cacheAppearance({ tokenLayers: themeWithToken(name, value).tokens });
    bootstrapAppearance(bootstrapPayload, window);
    expect(document.documentElement.style.getPropertyValue(name)).toBe("");
    expect(document.documentElement.style.getPropertyValue("--brand")).toBe("#112233");
    expect(document.documentElement.dataset.colorScheme).toBe("light");
  });

  test.each<readonly [string, Record<string, unknown>]>([
    ["schema", { schema: APPEARANCE_CACHE_SCHEMA + 1 }],
    ["old single-scheme cache", { schema: 1, tokens: { "--brand": "#abcdef" } }],
    ["fingerprint", { fingerprint: "other-host" }],
    ["uninstalled theme", { themeId: "example.missing" }],
    ["unknown token", { tokenLayers: { shared: { "--private-token": "15px" }, light: {}, dark: {} } }],
    ["unsafe inactive scheme", { tokenLayers: { shared: {}, light: { "--brand": "url(https://example.test/image)" }, dark: {} } }],
    ["missing scheme", { tokenLayers: { shared: {}, dark: {} } }],
    ["oversized cache", { padding: "x".repeat(APPEARANCE_CACHE_LIMIT) }],
  ])("rejects an invalid cache %s", (_name, overrides) => {
    cacheAppearance(overrides);
    bootstrapAppearance(bootstrapPayload, window);
    expect(document.documentElement.dataset.colorScheme).toBe("light");
    expect(document.documentElement.style.getPropertyValue("--brand")).toBe("#112233");
  });

  test("uses the host when browser storage is unavailable", () => {
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    bootstrapAppearance(bootstrapPayload, window);
    expect(document.documentElement.style.getPropertyValue("--brand")).toBe("#112233");
  });
});

describe("the appearance a document wears", () => {
  const host = {
    themeId: "house.theme", colorScheme: "light" as const, fingerprint: "payload",
    options: { version: 1, value: { brand: "#101010" } },
    // The host's own options have already been resolved into these layers.
    tokenLayers: { shared: { "--r-6": "9px" }, light: { "--brand": "#101010" }, dark: { "--brand": "#f0f0f0" } },
  };
  const payload: AppearanceBootstrapPayload = {
    fingerprint: "payload",
    host,
    catalogue: {
      schema: 1, fingerprint: "catalogue",
      themes: [
        { id: "house.theme", defaultTokens: { shared: {}, light: { "--brand": "#aaaaaa" }, dark: {} } },
        { id: "other.theme", defaultTokens: { shared: { "--r-6": "2px" }, light: { "--brand": "#00aa00" }, dark: { "--brand": "#00ff00" } } },
      ],
    },
  };

  test("is the host's theme, its forced color scheme and its own token layers", () => {
    // The visitor prefers dark, and a host that pins light is never asked.
    expect(resolveAppearance(payload, { prefersDark: () => { throw new Error("not asked"); } })).toEqual({
      themeId: "house.theme", colorScheme: "light", tokens: { "--r-6": "9px", "--brand": "#101010" },
    });
  });

  test("follows the visitor only when the host follows the system", () => {
    const system = { ...payload, host: { ...host, colorScheme: "system" as const } };
    expect(resolveAppearance(system, { prefersDark: () => true }).colorScheme).toBe("dark");
    expect(resolveAppearance(system, { prefersDark: () => true }).tokens["--brand"]).toBe("#f0f0f0");
    expect(resolveAppearance(system, { prefersDark: () => false }).colorScheme).toBe("light");
    expect(resolveAppearance(system).colorScheme).toBe("light");
  });

  test("wears a named installed theme with its own tokens, under the host's color scheme", () => {
    expect(resolveAppearance(payload, { themeId: "other.theme" })).toEqual({
      themeId: "other.theme", colorScheme: "light", tokens: { "--r-6": "2px", "--brand": "#00aa00" },
    });
    // Naming the host's theme wears it as the host does, options included.
    expect(resolveAppearance(payload, { themeId: "house.theme" }).tokens["--brand"]).toBe("#101010");
  });

  test("leaves the host's theme when the one named is not installed, and wears none when told so", () => {
    expect(resolveAppearance(payload, { themeId: "gone.theme" })).toEqual(resolveAppearance(payload));
    expect(resolveAppearance(payload, { themeId: null })).toEqual({ themeId: null, colorScheme: "light", tokens: {} });
  });

  test("a remembered preference replaces the color scheme and the token layers", () => {
    expect(resolveAppearance(payload, {
      themeId: "other.theme", colorSchemePreference: "dark",
      tokenLayers: { shared: {}, light: {}, dark: { "--brand": "#abcdef" } },
    })).toEqual({ themeId: "other.theme", colorScheme: "dark", tokens: { "--brand": "#abcdef" } });
  });

  test("is read from the data block a document carries it in", () => {
    document.head.innerHTML = `<script type="application/json" ${APPEARANCE_PAYLOAD_ATTRIBUTE}>${JSON.stringify(payload).replaceAll("<", "\\u003c")}</script>`;
    expect(readAppearancePayload()).toEqual(payload);
    document.head.innerHTML = "";
    expect(readAppearancePayload()).toBeNull();
  });
});

test("theme envelopes accept only their declared current option version", () => {
  const definition = defineTheme({
    ...themeWithToken("--brand", "#112233"),
    options: { version: 2, defaults: {}, parse: (value: unknown) => value, resolve: () => ({}) },
  });
  expect(() => resolveThemeOptions(definition, { version: 1, value: {} })).toThrow("options version 1 is unsupported");
  expect(resolveThemeOptions(definition, { version: 2, value: {} }).version).toBe(2);
});

describe("a customisable theme that declines the host appearance source", () => {
  const defaults = {
    brand: "#315c52", accent: "#8b5cf6", neutral: "#777064", canvas: "#f8f6f1", surface: "#ffffff", rail: "#18211e",
    success: "#16845b", warning: "#b7791f", danger: "#c2413a", info: "#3178c6",
    font: "theme", radius: "theme", density: "theme", elevation: "theme", logo: "theme",
  } satisfies ThemeCustomization;
  const customisable = (configuration: ThemeCustomizationConfiguration) => defineTheme({
    ...themeWithToken("--r-6", "4px"),
    options: createThemeCustomizationOptions(defaults, configuration),
  });
  const silent = customisable({ version: 2 });
  const declining = customisable({ version: 2, acceptsHostAppearanceSource: false });

  test("states it on its options definition, and one that says nothing accepts", () => {
    expect(declining.options?.acceptsHostAppearanceSource).toBe(false);
    expect(silent.options?.acceptsHostAppearanceSource).toBe(true);
  });

  test("keeps its customisation contract: options version, defaults, catalogue entry and what a saved envelope resolves to", () => {
    const saved = { version: 2, value: { ...defaults, brand: "#c2410c", rail: "#292524", radius: "soft" } };
    expect([declining.options?.version, declining.options?.defaults]).toEqual([2, silent.options?.defaults]);
    expect(serializableThemeMetadata(declining)).toEqual(serializableThemeMetadata(silent));
    expect(resolveThemeOptions(declining, saved)).toEqual(resolveThemeOptions(silent, saved));
  });

  test.each([["a string", "no"], ["a number", 0], ["null", null]])("is refused as %s, on any options definition", (_kind, value) => {
    const stated = value as unknown as boolean;
    expect(() => customisable({ version: 2, acceptsHostAppearanceSource: stated })).toThrow("acceptsHostAppearanceSource must be a boolean");
    expect(() => defineTheme({
      ...themeWithToken("--r-6", "4px"),
      options: { version: 1, defaults: {}, acceptsHostAppearanceSource: stated, parse: (option: unknown) => option, resolve: () => ({}) },
    })).toThrow("acceptsHostAppearanceSource must be a boolean");
  });
});
