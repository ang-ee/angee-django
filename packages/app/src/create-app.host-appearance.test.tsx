// @vitest-environment happy-dom

import { act, screen, waitFor } from "@testing-library/react";
import { useSyncExternalStore } from "react";
import { graphqlWireFetch } from "@angee/refine/testing/wire";
import {
  APPEARANCE_CACHE_KEY,
  createThemeCustomizationOptions,
  defineTheme,
  defineThemeContribution,
  resolveThemeOptions,
  useOptionalAppearance,
  type ThemeOptionsEnvelope,
} from "@angee/ui/theme";
import type { BaseMenuItem } from "@angee/ui/chrome/menu-tree";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { createApp, PassthroughChrome, type CreateAppInput } from "./create-app";
import type { HostAppearanceRead, HostAppearanceSource, ShellSettings } from "./define-addon";
import { installTestLocalStorage, TEST_SCHEMAS } from "./testing";

/** A customisable test theme whose palette starts at `brand`. */
function customisable(id: string, brand: string) {
  return defineThemeContribution({ definition: defineTheme({
    contractVersion: 1, id, labelKey: `${id}.label`, descriptionKey: `${id}.description`, revision: 1,
    tokens: { shared: {}, light: {}, dark: {} },
    options: createThemeCustomizationOptions({
      brand, accent: "#447788", neutral: "#667788", canvas: "#fafafa", surface: "#ffffff", rail: "#112233",
      success: "#16845b", warning: "#b7791f", danger: "#c2413a", info: "#3178c6",
      font: "theme", radius: "theme", density: "theme", elevation: "theme", logo: "theme",
    }),
  }) });
}
const paper = customisable("notebook.paper", "#112233");
const ink = customisable("notebook.ink", "#334455");
const fixed = defineThemeContribution({ definition: defineTheme({
  contractVersion: 1, id: "notebook.fixed", labelKey: "fixed.label", descriptionKey: "fixed.description", revision: 1,
  tokens: { shared: {}, light: { "--brand": "#0a0a0a" }, dark: {} },
}) });

/** A theme's options with one colour changed: what a source answers, or what a build states. */
function optionsOf(theme: typeof paper, patch: Record<string, string>): ThemeOptionsEnvelope {
  const options = theme.definition.options!;
  return { version: options.version, value: { ...(options.defaults as object), ...patch } };
}
const BUILD = optionsOf(paper, { brand: "#5b5bd6", accent: "#0d9488" });
const SOURCE_BRAND = "#c2410c";

/** A host appearance source answering what a test sets, recording the theme and build options it is asked about. */
function controlledSource(initial: HostAppearanceRead) {
  let current = initial;
  const listeners = new Set<() => void>();
  const asked: Array<{ theme: string | null; build: ThemeOptionsEnvelope | undefined }> = [];
  const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
  const source: HostAppearanceSource = {
    useOptions(theme, build) {
      asked.push({ theme: theme?.id ?? null, build });
      return useSyncExternalStore(subscribe, () => current);
    },
  };
  return {
    source,
    asked,
    answer(next: HostAppearanceRead) { act(() => { current = next; listeners.forEach((listener) => listener()); }); },
  };
}

/** The options the source answers for whichever theme it is asked about. */
function dressing(): HostAppearanceSource {
  return {
    useOptions(theme) {
      if (!theme?.options) return { pending: false };
      const options = theme.options;
      return { options: { version: options.version, value: { ...(options.defaults as object), brand: SOURCE_BRAND } }, pending: false };
    },
  };
}

function AppearanceProbe() {
  const appearance = useOptionalAppearance();
  return <>
    <output aria-label="Theme">{appearance?.effectiveThemeId ?? "none"}</output>
    <output aria-label="Host brand">{String((appearance?.hostOptions?.value as { brand?: string } | undefined)?.brand ?? "none")}</output>
    <output aria-label="Identity">{appearance?.resolvingIdentity === false ? "resolved" : "resolving"}</output>
  </>;
}

const SIGNED_IN_USER = {
  id: "user-1", username: "ada", firstName: "Ada", lastName: "Lovelace", email: "ada@example.test",
  isStaff: false, isActive: true, hasUsablePassword: true, roleRefs: [], permitted: [],
};

/** Schemas whose identity read answers a signed-in person holding these saved preferences. */
function signedIn(preferences: Record<string, unknown>): CreateAppInput["schemas"] {
  const user = { ...SIGNED_IN_USER, preferences };
  return {
    public: { url: "https://example.test/graphql/public/", fetch: graphqlWireFetch({ current_user: () => user }) },
    console: { url: "https://example.test/graphql/console/", fetch: graphqlWireFetch({
      current_user: () => user, real_user: () => user, viewable_people: () => [],
    }) },
  };
}

/** The notes page under an `identity` menu root that may name a theme, with an installed source when one is given. */
function hostApp({ source, search = "", root = {}, shell, appearance, schemas = TEST_SCHEMAS }: {
  source?: HostAppearanceSource;
  search?: string;
  root?: Pick<BaseMenuItem, "theme" | "themeLocked">;
  shell?: ShellSettings;
  appearance?: CreateAppInput["appearance"];
  schemas?: CreateAppInput["schemas"];
}) {
  return createApp({
    schemas,
    layouts: { public: { chrome: PassthroughChrome, requireAuth: false, schema: "public" } },
    addons: [
      { id: "notes", routes: [{ name: "notes.home", path: "/notes", layout: "public", component: AppearanceProbe }] },
      {
        id: "identity",
        icons: { "notebook-mark": () => null },
        themes: [paper, ink, fixed],
        menus: { identity: { label: "Identity", icon: "notebook-mark", route: "notes.home", ...root } },
      },
      ...(source ? [{ id: "brand", hostAppearance: source }] : []),
      ...(shell ? [{ id: "deployment", dependsOn: ["notes", "identity"], shell }] : []),
    ],
    location: { search },
    ...(appearance ? { appearance } : {}),
  });
}

async function mounted(app: ReturnType<typeof createApp>, run: () => Promise<void>): Promise<void> {
  const host = document.createElement("div");
  document.body.append(host);
  const root = app.mount(host);
  try {
    await run();
  } finally {
    act(() => root.unmount());
    host.remove();
  }
}

const root = () => document.documentElement;
const tokenOf = (theme: typeof paper, options: ThemeOptionsEnvelope | undefined, name: "--brand" | "--accent") =>
  resolveThemeOptions(theme.definition, options).tokens.light[name];

beforeEach(() => {
  installTestLocalStorage();
  history.replaceState(null, "", "/notes");
  root().removeAttribute("style");
  delete root().dataset.themeId;
  delete root().dataset.colorScheme;
});

describe("the host appearance source", () => {
  test("its options reach the host options and the root's tokens over the build's", async () => {
    const appearance = { themeId: paper.definition.id, colorScheme: "light" as const, options: BUILD };
    await mounted(hostApp({ source: dressing(), appearance }), async () => {
      await waitFor(() => expect(screen.getByLabelText("Host brand").textContent).toBe(SOURCE_BRAND));
      await waitFor(() => expect(root().style.getPropertyValue("--brand")).toBe(SOURCE_BRAND));
    });
  });

  test("a host without a source wears the build's options, exactly as before", async () => {
    const appearance = { themeId: paper.definition.id, colorScheme: "light" as const, options: BUILD };
    await mounted(hostApp({ appearance }), async () => {
      await waitFor(() => expect(screen.getByLabelText("Host brand").textContent).toBe("#5b5bd6"));
      await waitFor(() => expect(root().style.getPropertyValue("--brand")).toBe(tokenOf(paper, BUILD, "--brand")));
    });
  });

  test("a source answering no options keeps the build's", async () => {
    const { source } = controlledSource({ pending: false });
    const appearance = { themeId: paper.definition.id, colorScheme: "light" as const, options: BUILD };
    await mounted(hostApp({ source, appearance }), async () => {
      await waitFor(() => expect(root().style.getPropertyValue("--brand")).toBe(tokenOf(paper, BUILD, "--brand")));
      expect(screen.getByLabelText("Host brand").textContent).toBe("#5b5bd6");
    });
  });

  // Which theme the source is asked about: the one a person who chose none wears, locked or not.
  // The architect's ruling on which themes a kit dresses changes these rows and `sourceTheme` in createApp.
  test.each([
    { case: "the build's theme, with the build's options", appearance: { themeId: paper.definition.id, options: BUILD },
      asked: { theme: paper.definition.id, build: BUILD } },
    { case: "a selected root's own theme, without the build's options", search: "?app=identity", root: { theme: ink.definition.id },
      appearance: { themeId: paper.definition.id, options: BUILD }, asked: { theme: ink.definition.id, build: undefined } },
    { case: "a selected root's locked theme", search: "?app=identity", root: { theme: ink.definition.id, themeLocked: true },
      appearance: { themeId: paper.definition.id, options: BUILD }, asked: { theme: ink.definition.id, build: undefined } },
    { case: "the deployment's theme", shell: { theme: ink.definition.id },
      appearance: { themeId: paper.definition.id, options: BUILD }, asked: { theme: ink.definition.id, build: undefined } },
    { case: "a fixed theme", appearance: { themeId: fixed.definition.id }, asked: { theme: fixed.definition.id, build: undefined } },
    { case: "no theme", appearance: { themeId: null }, asked: { theme: null, build: undefined } },
  ])("is asked about $case", async ({ search, root: menuRoot, shell, appearance, asked }) => {
    const { source, asked: calls } = controlledSource({ pending: false });
    const app = hostApp({ source, ...(search ? { search } : {}), ...(menuRoot ? { root: menuRoot } : {}), ...(shell ? { shell } : {}), appearance });
    await mounted(app, async () => {
      await screen.findByLabelText("Theme");
      expect(calls.at(-1)).toEqual(asked);
    });
  });

  test("every app of a deployment wears the source's palette on its own theme, a locked app's too", async () => {
    const appearance = { themeId: paper.definition.id, colorScheme: "light" as const, options: BUILD };
    const shell = { apps: { reading: { rail: ["identity"], theme: ink.definition.id, themeLocked: true } } };
    // A person who saved their own theme still wears the locked app's theme there, dressed by the source.
    const schemas = signedIn({ appearance: { version: 1, themeId: paper.definition.id } });
    await mounted(hostApp({ source: dressing(), search: "?app=reading", shell, appearance, schemas }), async () => {
      await waitFor(() => expect(screen.getByLabelText("Theme").textContent).toBe(ink.definition.id));
      await waitFor(() => expect(root().style.getPropertyValue("--brand")).toBe(SOURCE_BRAND));
    });
  });

  test("a person's own theme and options win over the source's", async () => {
    const own = optionsOf(ink, { brand: "#00aa55" });
    const appearance = { themeId: paper.definition.id, colorScheme: "light" as const, options: BUILD };
    const schemas = signedIn({ appearance: { version: 1, themeId: ink.definition.id, options: own } });
    await mounted(hostApp({ source: dressing(), appearance, schemas }), async () => {
      await waitFor(() => expect(screen.getByLabelText("Theme").textContent).toBe(ink.definition.id));
      await waitFor(() => expect(root().style.getPropertyValue("--brand")).toBe("#00aa55"));
      // "Follow app default" still shows the source's options.
      expect(screen.getByLabelText("Host brand").textContent).toBe(SOURCE_BRAND);
    });
  });

  test("a pending source holds the first paint and the boot cache; its answer applies its options", async () => {
    const controlled = controlledSource({ pending: true });
    const appearance = { themeId: paper.definition.id, colorScheme: "light" as const, options: BUILD, fingerprint: "build" };
    root().style.setProperty("--brand", "#010203");
    await mounted(hostApp({ source: controlled.source, appearance }), async () => {
      // Identity has resolved: only the source's pending read holds the first paint now.
      await waitFor(() => expect(screen.getByLabelText("Identity").textContent).toBe("resolved"));
      expect(root().style.getPropertyValue("--brand")).toBe("#010203");
      expect(window.localStorage.getItem(APPEARANCE_CACHE_KEY)).toBeNull();

      const options = optionsOf(paper, { brand: SOURCE_BRAND });
      controlled.answer({ options, pending: false });
      await waitFor(() => expect(root().style.getPropertyValue("--brand")).toBe(SOURCE_BRAND));
      expect(JSON.parse(window.localStorage.getItem(APPEARANCE_CACHE_KEY)!)).toMatchObject({
        themeId: paper.definition.id, tokenLayers: { light: { "--brand": SOURCE_BRAND } },
      });
    });
  });

  test("a source that stops pending without options releases the hold onto the build's", async () => {
    const controlled = controlledSource({ pending: true });
    const appearance = { themeId: paper.definition.id, colorScheme: "light" as const, options: BUILD };
    root().style.setProperty("--brand", "#010203");
    await mounted(hostApp({ source: controlled.source, appearance }), async () => {
      await waitFor(() => expect(screen.getByLabelText("Identity").textContent).toBe("resolved"));
      expect(root().style.getPropertyValue("--brand")).toBe("#010203");
      controlled.answer({ pending: false });
      await waitFor(() => expect(root().style.getPropertyValue("--brand")).toBe(tokenOf(paper, BUILD, "--brand")));
    });
  });

  test("a build's appearance takes no pending: only the session frame holds the first paint", () => {
    // @ts-expect-error The typecheck refuses a build that would hold the first paint for the whole session.
    const build: CreateAppInput["appearance"] = { themeId: paper.definition.id, pending: true };
    expect(build).toBeDefined();
  });

  test("a source answer that changes nothing re-renders the frame without applying the appearance again", async () => {
    const options = optionsOf(paper, { brand: SOURCE_BRAND });
    const controlled = controlledSource({ options, pending: false });
    const appearance = { themeId: paper.definition.id, colorScheme: "light" as const, options: BUILD };
    await mounted(hostApp({ source: controlled.source, appearance }), async () => {
      await waitFor(() => expect(root().style.getPropertyValue("--brand")).toBe(SOURCE_BRAND));
      const applied = vi.spyOn(root().style, "setProperty");
      const asked = controlled.asked.length;
      // A new answer object holding the same options: the frame renders again, the host it passes stays the same.
      controlled.answer({ options, pending: false });
      await waitFor(() => expect(controlled.asked.length).toBeGreaterThan(asked));
      expect(applied).not.toHaveBeenCalled();
      applied.mockRestore();
    });
  });
});
