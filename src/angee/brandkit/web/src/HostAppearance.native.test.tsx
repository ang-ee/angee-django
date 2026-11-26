// @vitest-environment happy-dom
import { onlineManager } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { defineBaseAddon } from "@angee/app";
import type { CreateAppInput } from "@angee/app/create-app";
import { installTestLocalStorage } from "@angee/app/testing";
import { operationDocuments } from "@angee/gql/console/actions";
import { ComposedRuntimeFixture, storySchema, type GraphQLWireRequest } from "@angee/storybook/testing";
import stockAddon from "@angee/theme-stock";
import { themes as stockThemes } from "@angee/theme-stock/themes";
import { ConsoleLayout } from "@angee/ui";
import { APPEARANCE_CACHE_KEY, bootstrapAppearance, defineTheme, defineThemeContribution, resolveThemeOptions } from "@angee/ui/theme";

import { HostAppearanceStory } from "./HostAppearance.stories";
import brandkit from "./index";
import { ANGEE, HARBOUR, ORCHARD, brandKitResource, colorsOf, type KitRow } from "./views/brand-kits.test-support";

const stock = stockThemes[0];
const TOKENS = ["--brand", "--surface-rail", "--surface-canvas"] as const;
const NO_TOKENS = { "--brand": "", "--surface-rail": "", "--surface-canvas": "" };
const root = () => document.documentElement;

/** The stock theme's light tokens with a kit's ten colours over its defaults, as the theme itself works them out. */
function lightTokens(kit: KitRow) {
  return resolveThemeOptions(stock, { version: stock.options!.version, value: { ...stock.options!.defaults, ...colorsOf(kit) } }).tokens.light;
}
const wornTokens = (kit: KitRow) => Object.fromEntries(TOKENS.map((name) => [name, lightTokens(kit)[name]]));
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

/** A promise the test settles: a kit read gated on it answers only then. */
function gate() {
  let open!: () => void;
  const closed = new Promise<void>((resolve) => { open = resolve; });
  return { closed, open };
}

const ORCHARD_DEFAULT = [ORCHARD, HARBOUR, { ...ANGEE, is_default: false }];
const HARBOUR_DEFAULT = [{ ...HARBOUR, is_default: true }, { ...ORCHARD, is_default: false }];

beforeEach(() => {
  installTestLocalStorage();
  root().removeAttribute("style");
  delete root().dataset.themeId;
  delete root().dataset.colorScheme;
});
afterEach(() => {
  onlineManager.setOnline(true);
  cleanup();
  vi.restoreAllMocks();
});

describe("the default kit dresses the app", () => {
  // Each kit differs from stock in all ten colours, so each of the three tokens is the kit's.
  test.each([
    { where: "the console, signed in", kits: ORCHARD_DEFAULT, kit: ORCHARD, signedIn: true, entry: "/brand-kits" },
    { where: "the console, signed in", kits: HARBOUR_DEFAULT, kit: HARBOUR, signedIn: true, entry: "/brand-kits" },
    { where: "the sign-in page, signed out", kits: ORCHARD_DEFAULT, kit: ORCHARD, signedIn: false, entry: "/login" },
    { where: "the sign-in page, signed out", kits: HARBOUR_DEFAULT, kit: HARBOUR, signedIn: false, entry: "/login" },
  ])("$where wears $kit.name's colours, read from the public schema only", async ({ kits, kit, signedIn, entry }) => {
    const requests = recorder();
    render(<HostAppearanceStory kits={kits} signedIn={signedIn} initialEntry={entry} onRequest={requests.onRequest} />);
    await waitFor(() => expect(inlineTokens()).toEqual(wornTokens(kit)));
    expect(root().dataset.themeId).toBe("angee.stock");
    expect(requests.to("default_brand_kit_colors")).toEqual(["public"]);
  });

  test.each([
    { where: "the console, signed in", signedIn: true, entry: "/brand-kits" },
    { where: "the sign-in page, signed out", signedIn: false, entry: "/login" },
  ])("with no default kit, $where wears stock exactly", async ({ signedIn, entry }) => {
    render(<HostAppearanceStory kits={[]} signedIn={signedIn} initialEntry={entry} />);
    await waitFor(() => expect(root().dataset.themeId).toBe("angee.stock"));
    expect(inlineTokens()).toEqual(NO_TOKENS);
  });

  test("the kit is read once identity resolves, and the first paint and boot cache hold until it answers", async () => {
    const requests = recorder();
    const kitRead = gate();
    // The first paint, as the head bootstrap left it (on a device's first visit, the build's appearance).
    root().style.setProperty("--brand", "#010203");
    render(<HostAppearanceStory kits={ORCHARD_DEFAULT} kitReadGate={kitRead.closed} onRequest={requests.onRequest} />);
    await waitFor(() => expect(requests.first("default_brand_kit_colors")).toBeGreaterThan(-1));
    expect(requests.first("current_user")).toBeLessThan(requests.first("default_brand_kit_colors"));
    // Identity has resolved: the console page renders under the first paint, which the hold keeps.
    await screen.findByRole("link", { name: "Open Orchard" });
    expect(root().style.getPropertyValue("--brand")).toBe("#010203");
    expect(root().dataset.themeId).toBeUndefined();
    expect(window.localStorage.getItem(APPEARANCE_CACHE_KEY)).toBeNull();

    kitRead.open();
    await waitFor(() => expect(inlineTokens()).toEqual(wornTokens(ORCHARD)));
    expect(JSON.parse(window.localStorage.getItem(APPEARANCE_CACHE_KEY)!).tokenLayers.light).toEqual(lightTokens(ORCHARD));
  });

  test("on a device's next visit the head bootstrap paints the cached kit, and the app wears nothing else while the kit is read", async () => {
    // The first visit writes the boot cache once the kit answers.
    const first = render(<HostAppearanceStory kits={ORCHARD_DEFAULT} />);
    await waitFor(() => expect(window.localStorage.getItem(APPEARANCE_CACHE_KEY)).not.toBeNull());
    first.unmount();
    root().removeAttribute("style");
    delete root().dataset.themeId;

    // The next visit: the host page's head script paints from the cache before the app mounts.
    bootstrapAppearance({
      fingerprint: "",
      host: { themeId: "angee.stock", colorScheme: "light", fingerprint: "", tokenLayers: stock.tokens },
      catalogue: { schema: 1, fingerprint: "catalogue", themes: [{ id: "angee.stock", defaultTokens: stock.tokens }] },
    }, window);
    expect(inlineTokens()).toEqual(wornTokens(ORCHARD));
    const worn: string[] = [];
    const observer = new MutationObserver(() => {
      worn.push(`${root().dataset.themeId ?? "none"} ${root().style.getPropertyValue("--brand") || "none"}`);
    });
    observer.observe(root(), { attributes: true, attributeFilter: ["data-theme-id", "style"] });
    const kitRead = gate();
    render(<HostAppearanceStory kits={ORCHARD_DEFAULT} kitReadGate={kitRead.closed} />);
    // The console renders while the read is out; the root keeps the cached kit.
    await screen.findByRole("link", { name: "Open Orchard" });
    expect(inlineTokens()).toEqual(wornTokens(ORCHARD));
    kitRead.open();
    await waitFor(() => expect(JSON.parse(window.localStorage.getItem(APPEARANCE_CACHE_KEY)!).actorId).toBe("user-1"));
    await waitFor(() => expect(worn.length).toBeGreaterThan(0));
    observer.disconnect();
    // Every state the root took is the kit's: no build frame between the head script and the answer.
    expect(new Set(worn)).toEqual(new Set([`angee.stock ${lightTokens(ORCHARD)["--brand"]}`]));
  });

  test("a failing kit read releases the first paint onto the build's appearance at its first failure", async () => {
    root().style.setProperty("--brand", "#010203");
    // The first attempt fails and every retry stays unanswered: only a release at the first failure lets go.
    render(<HostAppearanceStory kits={ORCHARD_DEFAULT} failKitRead />);
    await waitFor(() => expect(root().dataset.themeId).toBe("angee.stock"));
    expect(inlineTokens()).toEqual(NO_TOKENS);
  });

  test("a kit read paused offline releases the first paint onto the build's appearance, and the kit applies once online", async () => {
    const requests = recorder();
    root().style.setProperty("--brand", "#010203");
    render(<HostAppearanceStory kits={ORCHARD_DEFAULT} onRequest={(schema, request) => {
      requests.onRequest(schema, request);
      // Identity is on its way; every read that starts after it starts offline, so the kit read pauses.
      if (schema === "public" && request.fields.includes("current_user")) onlineManager.setOnline(false);
    }} />);
    await waitFor(() => expect(root().dataset.themeId).toBe("angee.stock"));
    expect(inlineTokens()).toEqual(NO_TOKENS);
    expect(requests.first("default_brand_kit_colors")).toBe(-1);

    onlineManager.setOnline(true);
    await waitFor(() => expect(inlineTokens()).toEqual(wornTokens(ORCHARD)));
  });

  test("a fixed theme takes no kit and sends no read", async () => {
    const fixed = defineTheme({
      contractVersion: 1, id: "example.fixed", labelKey: "fixed.label", descriptionKey: "fixed.description", revision: 1,
      tokens: { shared: {}, light: { "--brand": "#0a0a0a" }, dark: {} },
    });
    const requests = recorder();
    const themes = [defineBaseAddon({ id: "theme.fixed", themes: [defineThemeContribution({ definition: fixed })] })];
    render(<HostAppearanceStory kits={ORCHARD_DEFAULT} themeId="example.fixed" themes={themes} onRequest={requests.onRequest} />);
    await waitFor(() => expect(root().dataset.themeId).toBe("example.fixed"));
    expect(root().style.getPropertyValue("--brand")).toBe("#0a0a0a");
    expect(requests.first("default_brand_kit_colors")).toBe(-1);
  });

  test("an administrator who makes another kit the default re-themes their app at once", async () => {
    const requests = recorder();
    render(<HostAppearanceStory kits={ORCHARD_DEFAULT} initialEntry="/brand-kits/bkt_harbour" onRequest={requests.onRequest} />);
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

  test("a kit saved before the first kit read answers never shows the build's appearance in between", async () => {
    const requests = recorder();
    const kitRead = gate();
    // Every appearance the root takes, as each change lands: the theme and the brand token.
    const worn: string[] = [];
    const observer = new MutationObserver(() => {
      worn.push(`${root().dataset.themeId ?? "none"} ${root().style.getPropertyValue("--brand") || "none"}`);
    });
    observer.observe(root(), { attributes: true, attributeFilter: ["data-theme-id", "style"] });
    const storageWrites = vi.spyOn(window.localStorage, "setItem");
    const cacheWrites = () => storageWrites.mock.calls.filter(([key]) => key === APPEARANCE_CACHE_KEY);
    root().style.setProperty("--brand", "#010203");
    render(<HostAppearanceStory kits={ORCHARD_DEFAULT} initialEntry="/brand-kits/bkt_harbour" kitReadGate={kitRead.closed}
      onRequest={requests.onRequest} />);
    const isDefault = await screen.findByRole("switch", { name: "Default kit" });
    expect(requests.first("default_brand_kit_colors")).toBeGreaterThan(-1);
    fireEvent.click(isDefault);
    await waitFor(() => expect(isDefault.getAttribute("aria-checked")).toBe("true"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    // The save cancels the unanswered kit read and sends it again; the first paint still holds.
    await waitFor(() => expect(requests.to("default_brand_kit_colors")).toEqual(["public", "public"]));
    expect(requests.sent.findLastIndex(({ request }) => request.fields.includes("default_brand_kit_colors")))
      .toBeGreaterThan(requests.first("update_brand_kits_by_pk"));
    expect(root().dataset.themeId).toBeUndefined();
    expect(root().style.getPropertyValue("--brand")).toBe("#010203");
    expect(cacheWrites()).toEqual([]);

    // The save confirms on its write, whether or not this read has answered. The saved kit lands
    // when the read answers, and the app wore nothing else on the way.
    kitRead.open();
    await screen.findByText("Changes saved");
    await waitFor(() => expect(inlineTokens()).toEqual(wornTokens(HARBOUR)));
    observer.disconnect();
    expect(worn.filter((entry) => !entry.startsWith("none"))).toEqual([`angee.stock ${lightTokens(HARBOUR)["--brand"]}`]);
    expect(cacheWrites()).toHaveLength(1);
  });
});

describe("a full-product workshop", () => {
  test("the composed fixture leaves the host appearance source out, so a workshop that answers no kit read renders", async () => {
    const sent: string[] = [];
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    // A workshop as a product writes one: each schema's fake answers the session's reads and nothing of the kit's.
    const fetch = () => storySchema({ current_user: () => null }, (request) => { sent.push(...request.fields); }).public!.fetch!;
    const input: CreateAppInput = {
      addons: [stockAddon, brandkit, defineBaseAddon({
        id: "workshop-page",
        routes: [{ name: "workshop.page", path: "/page", layout: "public", component: () => <p>A product page</p> }],
      })],
      layouts: { console: { chrome: ConsoleLayout }, public: { schema: "public" } },
      schemas: {
        public: { url: "/graphql/public/", live: false, fetch: fetch() },
        console: {
          url: "/graphql/console/", live: false, fetch: fetch(),
          metadata: { angee: { resources: [brandKitResource] } }, operationDocuments,
        },
      },
      defaultSchema: "console",
      appearance: { themeId: "angee.stock", colorScheme: "light" },
    };
    render(<ComposedRuntimeFixture input={input} initialEntry="/page" />);
    await screen.findByText("A product page");
    await waitFor(() => expect(sent).toContain("current_user"));
    expect(sent).not.toContain("default_brand_kit_colors");
    expect(errors).not.toHaveBeenCalled();
  });
});
