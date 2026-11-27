import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

import { createThemeCustomizationOptions, type ThemeCustomization, type ThemeTokenName } from "./runtime.mjs";

/** The surfaces text is drawn on: the page, sheets and their alternate rows, popovers and insets. */
const SURFACES = [
  "--surface-canvas", "--surface-sheet", "--surface-sheet-2", "--surface-popover", "--surface-inset",
] as const satisfies readonly ThemeTokenName[];

const SCHEMES = ["light", "dark"] as const;

const TOKENS_CSS = readFileSync(fileURLToPath(new URL("../styles/tokens.css", import.meta.url)), "utf8");

/** The stock palette's tokens in a scheme as `tokens.css` authors them, each `var()` followed to its value. */
function authored(scheme: "light" | "dark"): Record<string, string> {
  const block = (selector: string) => {
    const start = TOKENS_CSS.indexOf(`\n${selector} {`);
    const body = TOKENS_CSS.slice(start, TOKENS_CSS.indexOf("\n}", start));
    return Object.fromEntries([...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(([, name, value]) => [name!, value!.trim()]));
  };
  const declared: Record<string, string> = { ...block(":root"), ...(scheme === "dark" ? block('[data-color-scheme="dark"]') : {}) };
  const follow = (value: string): string => {
    const reference = /^var\((--[\w-]+)\)$/.exec(value);
    return reference ? follow(declared[reference[1]!] ?? `an undeclared ${reference[1]}`) : value;
  };
  return Object.fromEntries(Object.entries(declared).map(([name, value]) => [name, follow(value)]));
}

/** A token's colour, which must be `#rrggbb` to be measured: a stylesheet restructured past this reader fails here. */
function colorOf(tokens: Partial<Record<string, string>>, name: string): string {
  const value = tokens[name];
  expect(value, `${name} is ${value}, not a #rrggbb colour this test can measure`).toMatch(/^#[0-9a-f]{6}$/i);
  return value!;
}

/** The WCAG 2 contrast ratio of two `#rrggbb` colours, worked out here rather than by the runtime under test. */
function contrast(left: string, right: string): number {
  const luminance = (color: string) => {
    const [red, green, blue] = [1, 3, 5].map((start) => {
      const channel = Number.parseInt(color.slice(start, start + 2), 16) / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    }) as [number, number, number];
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
  };
  const [lighter, darker] = [luminance(left), luminance(right)].sort((first, second) => second - first) as [number, number];
  return (lighter + 0.05) / (darker + 0.05);
}

/** `count` colours from a fixed seed (a Park–Miller sequence), the same on every run. */
function seededColors(seed: number, count: number): string[] {
  let state = seed;
  const next = () => (state = (state * 16807) % 2147483647) % 256;
  return Array.from({ length: count }, () => `#${[next(), next(), next()].map((channel) => channel.toString(16).padStart(2, "0")).join("")}`);
}

// The stock theme's customisation defaults, read from the palette tokens.css authors.
const light = authored("light");
const defaults = {
  brand: colorOf(light, "--brand"), accent: colorOf(light, "--accent"), neutral: colorOf(light, "--n-500"),
  canvas: colorOf(light, "--surface-canvas"), surface: colorOf(light, "--surface-sheet"), rail: colorOf(light, "--surface-rail"),
  success: colorOf(light, "--success"), warning: colorOf(light, "--warning"), danger: colorOf(light, "--danger"), info: colorOf(light, "--info"),
  font: "theme", radius: "theme", density: "theme", elevation: "theme", logo: "theme",
} satisfies ThemeCustomization;
const options = createThemeCustomizationOptions(defaults);

describe("the stock palette's link reads at 4.5:1 on every surface tokens.css paints", () => {
  test.each(SCHEMES)("uncustomised: %s", (scheme) => {
    const tokens = authored(scheme);
    for (const surface of SURFACES) {
      expect(contrast(colorOf(tokens, "--text-link"), colorOf(tokens, surface)), surface).toBeGreaterThanOrEqual(4.5);
    }
  });

  // A brand alone emits no surface, and the stock theme states none in its definition, so its link and
  // brand text are worked out against the surfaces its defaults derive; they must read on the real ones.
  test.each(SCHEMES)("with the brand alone changed, over a seeded sample of brands: %s", (scheme) => {
    const painted = authored(scheme);
    const under: string[] = [];
    for (const brand of seededColors(11, 400)) {
      const emitted = options.resolve({ ...defaults, brand })[scheme] ?? {};
      expect(SURFACES.filter((surface) => surface in emitted)).toEqual([]);
      for (const text of ["--text-link", "--brand-soft-text"] as const) {
        for (const surface of SURFACES) {
          const ratio = contrast(colorOf(emitted, text), colorOf(painted, surface));
          if (ratio < 4.5) under.push(`${brand} ${text} on ${surface}: ${ratio.toFixed(3)}`);
        }
      }
    }
    expect(under).toEqual([]);
  });
});

describe("where the surfaces pull opposite ways and no colour reads on all of them", () => {
  // A white canvas beside dark grey sheets: a link dark enough for the canvas is lost on the sheets.
  const value = { ...defaults, brand: "#3355ff", canvas: "#ffffff", surface: "#3a3a3a" };

  test("the link is the colour that reads best where it reads worst, at least 3:1 on every surface", () => {
    const tokens = options.resolve(value).light ?? {};
    const worst = (color: string) => Math.min(...SURFACES.map((surface) => contrast(color, colorOf(tokens, surface))));
    expect(Math.max(worst("#11141a"), worst("#ffffff"))).toBeLessThan(4.5);
    const link = colorOf(tokens, "--text-link");
    expect(worst(link)).toBeGreaterThanOrEqual(Math.max(worst(value.brand), worst("#11141a"), worst("#ffffff")));
    expect(worst(link)).toBeGreaterThanOrEqual(3);
  });
});
