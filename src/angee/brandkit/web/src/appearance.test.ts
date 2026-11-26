import { describe, expect, test } from "vitest";
import { themes as angeeThemes } from "@angee/theme-angee/themes";
import { themes as stockThemes } from "@angee/theme-stock/themes";
import {
  createThemeCustomizationOptions,
  defineTheme,
  parseThemeCustomization,
  resolveThemeOptions,
  type ThemeDefinition,
} from "@angee/ui/theme";

import { brandKitOptions } from "./appearance";

const stock = stockThemes[0]! as ThemeDefinition<unknown>;
const angee = angeeThemes[0]! as ThemeDefinition<unknown>;

/** A customisable theme of a deployment's own, with an authored palette and authored tokens beside it. */
const house = defineTheme({
  contractVersion: 1, id: "example.house", labelKey: "house.label", descriptionKey: "house.description", revision: 1,
  tokens: { shared: { "--r-6": "3px" }, light: { "--surface-canvas": "#f6f5ef" }, dark: { "--surface-canvas": "#161714" } },
  options: createThemeCustomizationOptions({
    brand: "#294a36", accent: "#d9e7b2", neutral: "#60685d", canvas: "#f6f5ef", surface: "#fffefa", rail: "#182c22",
    success: "#3f6b4c", warning: "#985525", danger: "#a23a2c", info: "#3e6c7c",
    font: "editorial", radius: "compact", density: "theme", elevation: "subtle", logo: "theme",
  }),
}) as ThemeDefinition<unknown>;

/** A fixed theme: it takes no options. */
const fixed = defineTheme({
  contractVersion: 1, id: "example.fixed", labelKey: "fixed.label", descriptionKey: "fixed.description", revision: 1,
  tokens: { shared: {}, light: { "--brand": "#0a0a0a" }, dark: {} },
}) as ThemeDefinition<unknown>;

/** The colours a theme's defaults state: a kit equal to them is that theme's own kit. */
function paletteOf(theme: ThemeDefinition<unknown>): Record<string, string> {
  const { font: _font, radius: _radius, density: _density, elevation: _elevation, logo: _logo, ...colors } =
    parseThemeCustomization(theme.options!.defaults);
  return colors;
}

/** The shipped Angee kit states the Angee theme's colours (`test_the_shipped_kit_states_the_angee_theme_and_no_default`). */
const SHIPPED_KIT = paletteOf(angee);

/** A kit whose ten colours differ from every theme here; `tests/test_brandkit.py` reads the same kit. */
const CLAY = {
  brand: "#c2410c", accent: "#0f766e", neutral: "#78716c", canvas: "#fafaf8", surface: "#fffbeb",
  rail: "#292524", success: "#15803d", warning: "#b45309", danger: "#b91c1c", info: "#1d4ed8",
};

/** The options a build states for the stock theme: its own colours and five choices of its own. */
const BUILD = {
  version: stock.options!.version,
  value: { ...paletteOf(stock), brand: "#4338ca", font: "inter", radius: "soft", density: "compact", elevation: "flat", logo: "brand" },
};

const valueOf = (options: ReturnType<typeof brandKitOptions>) => parseThemeCustomization(options?.value);

describe("brandKitOptions: what each kind of host wears", () => {
  test.each([
    { host: "the Angee theme with the shipped Angee kit", theme: angee, kit: SHIPPED_KIT },
    { host: "a theme with the default kit it ships, stating its own colours", theme: house, kit: paletteOf(house) },
  ])("changes nothing on $host", ({ theme, kit }) => {
    const options = brandKitOptions(theme, kit, undefined);
    expect(options).toEqual({ version: theme.options!.version, value: parseThemeCustomization(theme.options!.defaults) });
    expect(resolveThemeOptions(theme, options).tokens).toEqual(theme.tokens);
  });

  test("dresses the stock theme in the shipped Angee kit's colours, keeping stock's own choices", () => {
    const options = brandKitOptions(stock, SHIPPED_KIT, undefined);
    expect(valueOf(options)).toEqual({ ...parseThemeCustomization(stock.options!.defaults), ...SHIPPED_KIT });
    const tokens = resolveThemeOptions(stock, options).tokens;
    expect(tokens.light["--brand"]).toBe(SHIPPED_KIT.brand);
    expect(tokens.shared).toEqual({});
  });

  test.each([
    { host: "the stock theme", theme: stock },
    { host: "another customisable theme, whose authored palette the kit replaces", theme: house },
  ])("puts all ten of a kit's colours on $host and keeps its structure", ({ theme }) => {
    const options = brandKitOptions(theme, CLAY, undefined);
    const defaults = parseThemeCustomization(theme.options!.defaults);
    expect(valueOf(options)).toEqual({ ...defaults, ...CLAY });
    const tokens = resolveThemeOptions(theme, options).tokens;
    expect(tokens).not.toEqual(theme.tokens);
    expect(tokens.shared).toMatchObject(theme.tokens.shared);
  });

  // The architect's ruling on build options changes this row and the one branch in `brandKitOptions`.
  test("a build that states options keeps its font, radius, density, elevation and logo, and wears the kit's colours", () => {
    const options = brandKitOptions(stock, CLAY, BUILD);
    expect(valueOf(options)).toEqual({ ...CLAY, font: "inter", radius: "soft", density: "compact", elevation: "flat", logo: "brand" });
  });

  test.each([
    { host: "a fixed theme", theme: fixed, kit: CLAY },
    { host: "no theme", theme: null, kit: CLAY },
    { host: "no default kit", theme: stock, kit: null },
  ])("leaves the build's options to $host", ({ theme, kit }) => {
    expect(brandKitOptions(theme, kit, BUILD)).toBeUndefined();
  });

  test.each([
    { case: "a colour the parser refuses", kit: { ...CLAY, brand: "red" }, build: undefined },
    { case: "a field the theme takes no input for", kit: { ...CLAY, logo_url: "/logo.png" }, build: undefined },
    { case: "build options of another version", kit: CLAY, build: { version: 99, value: BUILD.value } },
    { case: "build options the parser refuses", kit: CLAY, build: { ...BUILD, value: { ...BUILD.value, radius: "huge" } } },
  ])("answers undefined without throwing for $case", ({ kit, build }) => {
    expect(brandKitOptions(stock, kit, build)).toBeUndefined();
  });

  test("ignores the GraphQL type name a read may carry", () => {
    expect(valueOf(brandKitOptions(stock, { __typename: "BrandKitColors", ...CLAY }, undefined))).toMatchObject(CLAY);
  });

  test("normalizes a kit's colours as the theme does", () => {
    expect(valueOf(brandKitOptions(stock, { ...CLAY, brand: "#C2410C" }, undefined)).brand).toBe("#c2410c");
  });
});
