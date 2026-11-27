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
  isShadowList,
  readAppearancePayload,
  resolveAppearance,
  resolveThemeOptions,
  THEME_ELEVATION_PRESETS,
  type AppearanceBootstrapPayload,
  THEME_TOKEN_NAMES,
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
  // One row per shadow form the contract accepts.
  ["elevation of no shadow", "--elevation-xs", "0 0 #0000"],
  ["elevation in a three-digit hex", "--elevation-xs", "0 1px 2px #000"],
  ["elevation in a six-digit hex", "--elevation-sm", "0 1px 2px #000000"],
  ["two-layer elevation in rem and em", "--elevation-sm", "0 0.0625rem 0.125rem #0000000f, 0 1em 1em #0000000a"],
  ["elevation in transparent", "--elevation-md", "0 0 2px transparent"],
  ["elevation in currentcolor", "--elevation-md", "0 0 2px currentcolor"],
  ["elevation in rgb() with an alpha", "--elevation-lg", "0 4px 12px rgb(0 0 0 / 0.2)"],
  ["elevation in rgb() with a percentage alpha", "--elevation-lg", "0 4px 12px rgb(0 0 0 / 20%)"],
  ["elevation in rgba() with commas", "--elevation-lg", "0 4px 12px rgba(0, 0, 0, 0.1)"],
  ["elevation in rgb() from a colour", "--elevation-sm", "0 1px 2px rgb(from #111827 r g b / 0.06)"],
  ["elevation in rgb() from a hex", "--elevation-sm", "0 1px 2px rgb(from #11141a r g b / 0.06)"],
  ["elevation in color-mix()", "--elevation-md", "0 2px 4px color-mix(in oklch, #000000 20%, transparent)"],
  ["elevation with the colour first", "--elevation-md", "#0000001f 0 2px 4px"],
  ["inset elevation", "--elevation-popover", "inset 0 1px 2px rgb(0 0 0 / 0.2)"],
  ["elevation inset last", "--elevation-popover", "0 1px 2px -1px #000 inset"],
  ["elevation in upper-case units", "--elevation-xs", "0 1PX 1PX #0000000a"],
  ["ring of a derived brand", "--ring", "0 0 0 3px #fff59d4d"],
  ["ring in rgb()", "--ring", "0 0 0 3px rgb(231 176 8 / 30%)"],
  ["ring mixed from a colour", "--ring", "0 0 0 3px color-mix(in oklab, #5b5bd6 30%, transparent)"],
  ["danger ring in srgb", "--ring-danger", "0 0 0 3px color-mix(in srgb, #dc2626 30%, transparent)"],
] as const satisfies readonly (readonly [string, ThemeTokenName, string])[];

const rejected = [
  ["typography weight", "--fw-semibold", "901"],
  ["font size", "--fs-15", "41px"],
  ["line height", "--lh-18", "11px"],
  ["motion duration", "--dur-fast", "1001ms"],
  ["motion easing", "--ease", "cubic-bezier(0.2, nope, 0.2, 1)"],
  ["motion easing x control", "--ease", "cubic-bezier(-0.1, 0.6, 1.1, 1)"],
  // An elevation is composed into one box-shadow with the focus ring: a layer that is not a shadow
  // voids the ring too.
  ["elevation of none", "--elevation-xs", "none"],
  ["elevation of NONE", "--elevation-popover", "NONE"],
  ["elevation with a none layer", "--elevation-sm", "0 1px 2px #0000000f, none"],
  ["elevation of a CSS-wide keyword", "--elevation-lg", "initial"],
  ["elevation of 0", "--elevation-xs", "0"],
  ["elevation of transparent", "--elevation-xs", "transparent"],
  ["elevation of a colour alone", "--elevation-sm", "red"],
  ["elevation with a named colour", "--elevation-sm", "0 1px 2px black"],
  ["elevation of unitless lengths", "--elevation-md", "1 1 #0000"],
  ["elevation of a negative blur", "--elevation-md", "0 0 -1px #000"],
  ["elevation of a colour between lengths", "--elevation-lg", "0 #000 0"],
  ["elevation of inset alone", "--elevation-popover", "inset"],
  ["elevation inset twice", "--elevation-popover", "inset 0 1px #000 inset"],
  ["elevation of none with lengths", "--elevation-xs", "none 0 0"],
  ["elevation with a trailing comma", "--elevation-sm", "0 0 #0000,"],
  ["elevation of a bare var()", "--elevation-sm", "var(--elevation-md)"],
  ["elevation with a no-break space between its lengths", "--elevation-xs", "0\u00a00 #0000"],
  ["elevation that starts with a no-break space", "--elevation-xs", "\u00a00 0 #0000"],
  ["ring with a no-break space before its colour", "--ring", "0 0 0 3px\u00a0#5b5bd64d"],
  ["elevation in rgb() from a var()", "--elevation-sm", "0 1px 2px rgb(from var(--n-900) r g b / 0.06)"],
  ["ring mixed from a var()", "--ring", "0 0 0 3px color-mix(in oklab, var(--brand) 30%, transparent)"],
  // Outside the forms, whatever a browser would make of it.
  ["elevation with an rgb() alpha missing its slash", "--elevation-sm", "0 1px 2px rgb(0 0 0 0.1)"],
  ["elevation in hsl()", "--elevation-sm", "0 0 hsl(0 0 0 0)"],
  ["elevation in an rgb() of a word", "--elevation-sm", "0 0 rgb(nope)"],
  ["elevation in an rgb() of a var()", "--elevation-sm", "0 0 rgb(var(--nope))"],
  ["elevation with calc() of a colour", "--elevation-md", "0 0 calc(1px + red) #000"],
  ["elevation with calc() of a var()", "--elevation-md", "0 0 calc(var(--nope)) #000"],
  ["elevation closing a parenthesis it never opened", "--elevation-md", "0 0 calc(1px)), none)"],
  ["elevation closing an rgb() twice", "--elevation-md", "0 0 rgb(0 0 0)), none)"],
  ["elevation with calc()", "--elevation-md", "0 calc(1px + 1px) 4px #00000014"],
  ["elevation in vw", "--elevation-lg", "0 1vw 2px #000"],
  ["ring in a named colour", "--ring", "0 0 0 3px black"],
  ["danger ring of none", "--ring-danger", "none"],
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

  test("the flat elevation of a customization resolves through the contract to shadows, never none", () => {
    const options = createThemeCustomizationOptions({
      brand: "#2563eb", accent: "#0d9488", neutral: "#6b7280", canvas: "#f7f8fa", surface: "#ffffff",
      rail: "#11141a", success: "#16a34a", warning: "#d97706", danger: "#dc2626", info: "#0284c7",
      font: "theme", radius: "theme", density: "theme", elevation: "theme", logo: "theme",
    });
    const definition = defineTheme({ ...themeWithToken("--brand", "#2563eb"), options });
    const { tokens } = resolveThemeOptions(definition, { version: options.version, value: { ...options.defaults, elevation: "flat" } });
    for (const scheme of ["light", "dark"] as const) {
      expect(tokens[scheme]["--elevation-xs"]).toBe("0 0 #0000");
      expect(tokens[scheme]["--elevation-sm"]).toBe("0 0 #0000");
    }
  });
});

// Brand kits the repository's tests use, by name: Angee (`brandkit/resources/install/010_brandkit.brandkit.yaml`),
// Orchard, Harbour, Sandstone and Pale (`brandkit/web/src/views/brand-kits.test-support.ts`), Clay
// (`brandkit/web/src/appearance.test.ts`, `tests/test_brandkit.py`). No near-black kit is among them:
// the black, white and mid-grey brands `tests/test_brandkit.py` holds the mail palette to stand in.
const ORCHARD = { brand: "#3f7d20", accent: "#c2410c", neutral: "#6b665c", canvas: "#f7f5ee", surface: "#fffdf7", rail: "#1f2a1a", success: "#15803d", warning: "#b45309", danger: "#b91c1c", info: "#0e7490" };
const KITS = {
  Angee: { brand: "#e7b008", accent: "#6366f1", neutral: "#71717a", canvas: "#fafaf9", surface: "#ffffff", rail: "#09090b", success: "#22c55e", warning: "#eab308", danger: "#dc2626", info: "#4f46e5" },
  Orchard: ORCHARD,
  Harbour: { brand: "#1d4ed8", accent: "#0891b2", neutral: "#64748b", canvas: "#f1f5f9", surface: "#fbfdff", rail: "#0b1f3a", success: "#047857", warning: "#a16207", danger: "#be123c", info: "#4338ca" },
  Sandstone: { brand: "#fde047", accent: "#0d9488", neutral: "#71717a", canvas: "#ffffff", surface: "#8a8580", rail: "#292524", success: "#16a34a", warning: "#d97706", danger: "#dc2626", info: "#2563eb" },
  Pale: { ...ORCHARD, brand: "#fff59d" },
  Clay: { brand: "#c2410c", accent: "#0f766e", neutral: "#78716c", canvas: "#fafaf8", surface: "#fffbeb", rail: "#292524", success: "#15803d", warning: "#b45309", danger: "#b91c1c", info: "#1d4ed8" },
  "Black brand": { ...ORCHARD, brand: "#000000", danger: "#000000" },
  "White brand": { ...ORCHARD, brand: "#ffffff", danger: "#ffffff" },
  "Mid-grey brand": { ...ORCHARD, brand: "#777777" },
};

describe("what the runtime derives for a kit or a person passes the runtime's own contract", () => {
  const options = createThemeCustomizationOptions({
    brand: "#2563eb", accent: "#0d9488", neutral: "#6b7280", canvas: "#f7f8fa", surface: "#ffffff",
    rail: "#11141a", success: "#16a34a", warning: "#d97706", danger: "#ef4444", info: "#0284c7",
    font: "theme", radius: "theme", density: "theme", elevation: "theme", logo: "theme",
  });
  const definition = defineTheme({ ...themeWithToken("--brand", "#2563eb"), options });
  test.each(Object.entries(KITS))("the %s kit, as a brand kit and as a person's customization in every elevation", (_kit, colors) => {
    const choices = [{ ...options.defaults, ...colors }, ...THEME_ELEVATION_PRESETS.map((elevation) => ({ ...options.defaults, brand: colors.brand, danger: colors.danger, elevation }))];
    for (const value of choices) {
      // resolveThemeOptions checks every resolved token against the contract and throws on one outside it.
      const { tokens } = resolveThemeOptions(definition, { version: options.version, value });
      for (const scheme of ["light", "dark"] as const) {
        for (const name of ["--ring", "--ring-danger"] as const) expect(isShadowList(tokens[scheme][name] ?? "")).toBe(true);
        for (const name of ["--elevation-xs", "--elevation-sm", "--elevation-md", "--elevation-lg", "--elevation-popover"] as const) {
          if (value.elevation !== "theme") expect(isShadowList(tokens[scheme][name] ?? "")).toBe(true);
        }
      }
    }
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
