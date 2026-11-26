import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

import { createThemeCustomizationOptions, type ThemeCustomization, type ThemeTokenName } from "./runtime.mjs";

/** The surfaces text is drawn on: the page, sheets and their alternate rows, popovers and insets. */
const SURFACES = [
  "--surface-canvas", "--surface-sheet", "--surface-sheet-2", "--surface-popover", "--surface-inset",
] as const satisfies readonly ThemeTokenName[];

const TOKENS_CSS = readFileSync(fileURLToPath(new URL("../styles/tokens.css", import.meta.url)), "utf8");

/** The stock palette's tokens in a scheme as `tokens.css` authors them, each `var()` followed to its value. */
function authored(scheme: "light" | "dark"): Record<string, string> {
  const block = (selector: string) => {
    const start = TOKENS_CSS.indexOf(`\n${selector} {`);
    const body = TOKENS_CSS.slice(start, TOKENS_CSS.indexOf("\n}", start));
    return Object.fromEntries([...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(([, name, value]) => [name!, value!.trim()]));
  };
  const declared = { ...block(":root"), ...(scheme === "dark" ? block('[data-color-scheme="dark"]') : {}) };
  const follow = (value: string): string => (value.startsWith("var(") ? follow(declared[value.slice(4, -1)]!) : value);
  return Object.fromEntries(Object.entries(declared).map(([name, value]) => [name, follow(value)]));
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

describe("the stock palette's link reads at 4.5:1 on every surface", () => {
  test.each(["light", "dark"] as const)("as tokens.css authors it, uncustomised: %s", (scheme) => {
    const tokens = authored(scheme);
    for (const surface of SURFACES) {
      expect(contrast(tokens["--text-link"]!, tokens[surface]!), surface).toBeGreaterThanOrEqual(4.5);
    }
  });

  // A customisation that changes only the brand emits no surface, so its link and brand text are worked
  // out against the surfaces its defaults derive; they must read on the ones tokens.css paints.
  const light = authored("light");
  const defaults = {
    brand: light["--brand"]!, accent: light["--accent"]!, neutral: light["--n-500"]!, canvas: light["--surface-canvas"]!,
    surface: light["--surface-sheet"]!, rail: light["--surface-rail"]!, success: light["--success"]!,
    warning: light["--warning"]!, danger: light["--danger"]!, info: light["--info"]!,
    font: "theme", radius: "theme", density: "theme", elevation: "theme", logo: "theme",
  } satisfies ThemeCustomization;
  const options = createThemeCustomizationOptions(defaults);
  test.each(["#1d4ed8", "#3f7d20", "#fff59d"].flatMap((brand) => (["light", "dark"] as const).map((scheme) => ({ brand, scheme }))))(
    "with only the brand changed, to $brand: $scheme",
    ({ brand, scheme }) => {
      const emitted = options.resolve({ ...defaults, brand })[scheme] ?? {};
      expect(SURFACES.filter((surface) => surface in emitted)).toEqual([]);
      const tokens = { ...authored(scheme), ...emitted };
      for (const surface of SURFACES) {
        expect(contrast(tokens["--text-link"]!, tokens[surface]!), `link on ${surface}`).toBeGreaterThanOrEqual(4.5);
        expect(contrast(tokens["--brand-soft-text"]!, tokens[surface]!), `brand text on ${surface}`).toBeGreaterThanOrEqual(4.5);
      }
    },
  );
});
