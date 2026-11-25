// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { defineBaseAddon } from "@angee/app";
import { installTestLocalStorage } from "@angee/app/testing";
import type { GraphQLWireRequest } from "@angee/storybook/testing";
import { themes as stockThemes } from "@angee/theme-stock/themes";
import { APPEARANCE_CACHE_KEY, defineTheme, defineThemeContribution, resolveThemeOptions, type ThemeDefinition } from "@angee/ui/theme";

import { brandKitOptions } from "./appearance";
import { AppAppearanceStory } from "./HostAppearance.stories";
import { ANGEE, HARBOUR, ORCHARD, colorsOf, type KitRow } from "./views/brand-kits.test-support";

const stock = stockThemes[0]! as ThemeDefinition<unknown>;
const TOKENS = ["--brand", "--surface-rail", "--surface-canvas"] as const;
const root = () => document.documentElement;

/** What the stock theme wears in light with a kit's colours, as the theme itself works them out. */
function wornTokens(kit: KitRow) {
  const light = resolveThemeOptions(stock, brandKitOptions(stock, colorsOf(kit), undefined)).tokens.light;
  return Object.fromEntries(TOKENS.map((name) => [name, light[name]]));
}
const inlineTokens = () => Object.fromEntries(TOKENS.map((name) => [name, root().style.getPropertyValue(name)]));

/** Every request the app sends, with its schema, in order. */
function recorder() {
  const sent: Array<{ schema: "public" | "console"; request: GraphQLWireRequest }> = [];
  return {
    sent,
    onRequest: (schema: "public" | "console", request: GraphQLWireRequest) => { sent.push({ schema, request }); },
    first: (field: string) => sent.findIndex(({ request }) => request.fields.includes(field)),
    to: (field: string) => sent.filter(({ request }) => request.fields.includes(field)).map(({ schema }) => schema),
  };
}

const ORCHARD_DEFAULT = [ORCHARD, HARBOUR, { ...ANGEE, is_default: false }];
const HARBOUR_DEFAULT = [{ ...HARBOUR, is_default: true }, { ...ORCHARD, is_default: false }];

beforeEach(() => {
  installTestLocalStorage();
  root().removeAttribute("style");
  delete root().dataset.themeId;
  delete root().dataset.colorScheme;
});
afterEach(() => { cleanup(); });

describe("the default kit dresses the app", () => {
  // Each kit differs from stock in all ten colours, so each of the three tokens is the kit's.
  test.each([
    { where: "the console, signed in", kits: ORCHARD_DEFAULT, kit: ORCHARD, signedIn: true, entry: "/brand-kits" },
    { where: "the console, signed in", kits: HARBOUR_DEFAULT, kit: HARBOUR, signedIn: true, entry: "/brand-kits" },
    { where: "the sign-in page, signed out", kits: ORCHARD_DEFAULT, kit: ORCHARD, signedIn: false, entry: "/login" },
    { where: "the sign-in page, signed out", kits: HARBOUR_DEFAULT, kit: HARBOUR, signedIn: false, entry: "/login" },
  ])("$where wears $kit.name's colours, read from the public schema only", async ({ kits, kit, signedIn, entry }) => {
    const requests = recorder();
    render(<AppAppearanceStory kits={kits} signedIn={signedIn} initialEntry={entry} onRequest={requests.onRequest} />);
    await waitFor(() => expect(inlineTokens()).toEqual(wornTokens(kit)));
    expect(root().dataset.themeId).toBe("angee.stock");
    expect(requests.to("default_brand_kit_colors")).toEqual(["public"]);
  });

  test.each([
    { where: "the console, signed in", signedIn: true, entry: "/brand-kits" },
    { where: "the sign-in page, signed out", signedIn: false, entry: "/login" },
  ])("with no default kit, $where wears stock exactly", async ({ signedIn, entry }) => {
    render(<AppAppearanceStory kits={[]} signedIn={signedIn} initialEntry={entry} />);
    await waitFor(() => expect(root().dataset.themeId).toBe("angee.stock"));
    expect(inlineTokens()).toEqual({ "--brand": "", "--surface-rail": "", "--surface-canvas": "" });
  });

  test("the kit is read once identity resolves, and the first paint and boot cache hold until it answers", async () => {
    const requests = recorder();
    let answer!: () => void;
    const gate = new Promise<void>((resolve) => { answer = resolve; });
    // The first paint, as the head bootstrap left it.
    root().style.setProperty("--brand", "#010203");
    render(<AppAppearanceStory kits={ORCHARD_DEFAULT} kitReadGate={gate} onRequest={requests.onRequest} />);
    await waitFor(() => expect(requests.first("default_brand_kit_colors")).toBeGreaterThan(-1));
    expect(requests.first("current_user")).toBeLessThan(requests.first("default_brand_kit_colors"));
    // Identity has resolved: the console page renders, and the app still wears its first paint.
    await screen.findByRole("link", { name: "Open Orchard" });
    expect(root().style.getPropertyValue("--brand")).toBe("#010203");
    expect(root().dataset.themeId).toBeUndefined();
    expect(window.localStorage.getItem(APPEARANCE_CACHE_KEY)).toBeNull();

    answer();
    await waitFor(() => expect(inlineTokens()).toEqual(wornTokens(ORCHARD)));
    expect(window.localStorage.getItem(APPEARANCE_CACHE_KEY)).not.toBeNull();
  });

  test("a failing kit read releases the first paint onto the build's appearance at its first failure", async () => {
    root().style.setProperty("--brand", "#010203");
    render(<AppAppearanceStory kits={ORCHARD_DEFAULT} failKitRead />);
    // The read retries for seconds; the hold ends at the first failure, well within this wait.
    await waitFor(() => expect(root().dataset.themeId).toBe("angee.stock"), { timeout: 900 });
    expect(inlineTokens()).toEqual({ "--brand": "", "--surface-rail": "", "--surface-canvas": "" });
  });

  test("a fixed theme takes no kit and sends no read", async () => {
    const fixed = defineTheme({
      contractVersion: 1, id: "example.fixed", labelKey: "fixed.label", descriptionKey: "fixed.description", revision: 1,
      tokens: { shared: {}, light: { "--brand": "#0a0a0a" }, dark: {} },
    });
    const requests = recorder();
    const themes = [defineBaseAddon({ id: "theme.fixed", themes: [defineThemeContribution({ definition: fixed })] })];
    render(<AppAppearanceStory kits={ORCHARD_DEFAULT} themeId="example.fixed" themes={themes} onRequest={requests.onRequest} />);
    await waitFor(() => expect(root().dataset.themeId).toBe("example.fixed"));
    expect(root().style.getPropertyValue("--brand")).toBe("#0a0a0a");
    expect(requests.first("default_brand_kit_colors")).toBe(-1);
  });

  test("an administrator who makes another kit the default re-themes their app at once", async () => {
    const requests = recorder();
    render(<AppAppearanceStory kits={ORCHARD_DEFAULT} initialEntry="/brand-kits/bkt_harbour" onRequest={requests.onRequest} />);
    await waitFor(() => expect(inlineTokens()).toEqual(wornTokens(ORCHARD)));
    const isDefault = await screen.findByRole("switch", { name: "Default kit" });
    fireEvent.click(isDefault);
    await waitFor(() => expect(isDefault.getAttribute("aria-checked")).toBe("true"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Changes saved");
    await waitFor(() => expect(inlineTokens()).toEqual(wornTokens(HARBOUR)));
    const saved = requests.first("update_brand_kits_by_pk");
    expect(saved).toBeGreaterThan(-1);
    expect(requests.sent.slice(saved + 1).some(({ schema, request }) =>
      schema === "public" && request.fields.includes("default_brand_kit_colors"))).toBe(true);
  });
});
