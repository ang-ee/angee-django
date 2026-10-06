// @vitest-environment happy-dom

import { act, cleanup, screen, waitFor } from "@testing-library/react";
import { forwardRef } from "react";
import { useAppRuntime, useRuntimeBrand, type RuntimeBrand } from "@angee/ui/runtime";
import { MenuTree, type BaseMenuItem } from "@angee/ui/chrome/menu-tree";
import { defineTheme, defineThemeContribution, useOptionalAppearance } from "@angee/ui/theme";
import { afterEach, describe, expect, test } from "vitest";

import { createApp, PassthroughChrome, type CreateAppInput } from "./create-app";
import type { ShellSettings } from "./define-addon";
import { captureChrome, TEST_SCHEMAS } from "./testing";

afterEach(cleanup);

function IdentityProbe() {
  const brand = useRuntimeBrand();
  const theme = useOptionalAppearance()?.effectiveThemeId;
  const { i18n } = useAppRuntime();
  const uiCopy = i18n?.getFixedT(null, "ui");
  const addonCopy = i18n?.getFixedT(null, "notes");
  return <>
    <output aria-label="Brand">{brand ? `${brand.name}/${brand.mark}` : "unbranded"}</output>
    <output aria-label="Theme">{theme ?? "none"}</output>
    <output aria-label="Base copy">{String(uiCopy?.("modal.copy") ?? "")}</output>
    <output aria-label="Addon copy">{String(addonCopy?.("title") ?? "")}</output>
  </>;
}

const inputs = {
  schemas: TEST_SCHEMAS,
  layouts: { public: { chrome: PassthroughChrome, requireAuth: false, schema: "public" } },
  addons: [{ id: "notes", routes: [{ name: "notes.home", path: "/notes", layout: "public", component: IdentityProbe }] }],
} satisfies CreateAppInput;

const theme = (id: string) => defineThemeContribution({ definition: defineTheme({
  contractVersion: 1, id, labelKey: `${id}.label`, descriptionKey: `${id}.description`, revision: 1,
  tokens: { shared: {}, light: {}, dark: {} },
}) });
const notebook: RuntimeBrand = { name: "Notebook", mark: "notebook-mark" };
const deployment: RuntimeBrand = { name: "Deployment", mark: "notebook-mark" };

/** The notes page under an `identity` menu root, with the deployment's shell when one is given. */
function identityApp({ search = "", root = {}, shell, icons = { "notebook-mark": () => null }, appearance }: {
  search?: string;
  root?: Pick<BaseMenuItem, "brand" | "theme" | "home">;
  shell?: ShellSettings;
  icons?: Readonly<Record<string, unknown>>;
  appearance?: CreateAppInput["appearance"];
} = {}) {
  return createApp({
    ...inputs,
    addons: [
      ...inputs.addons,
      {
        id: "identity",
        i18n: { notes: { title: "My notes" } },
        icons,
        themes: [theme("notebook.paper"), theme("notebook.ink")],
        menus: [{ id: "identity", label: "Identity", icon: "notebook-mark", route: "notes.home", ...root }],
      },
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

describe("the selected app's identity", () => {
  test.each([
    { case: "no brand with nothing selected", search: "", brand: "unbranded" },
    { case: "the deployment's brand with nothing selected", search: "", shell: { brand: deployment }, brand: "Deployment/notebook-mark" },
    { case: "a selected root's own brand", search: "?app=identity", root: { brand: notebook }, brand: "Notebook/notebook-mark" },
    { case: "a selected root's label and icon over the deployment's brand", search: "?app=identity", shell: { brand: deployment },
      brand: "Identity/notebook-mark" },
  ])("publishes $case and disjoint base/addon translations", async ({ search, root, shell, brand }) => {
    history.replaceState(null, "", "/notes");
    const app = identityApp({ search, ...(root ? { root } : {}), ...(shell ? { shell } : {}) });
    await mounted(app, async () => {
      expect((await screen.findByLabelText("Brand")).textContent).toBe(brand);
      expect(screen.getByLabelText("Base copy").textContent).toBe("Copy");
      expect(screen.getByLabelText("Addon copy").textContent).toBe("My notes");
    });
  });

  test.each([
    { case: "the build default with nothing selected", search: "", theme: "notebook.ink", appearance: { themeId: "notebook.ink" } },
    { case: "the deployment's theme with nothing selected", search: "", shell: { theme: "notebook.ink" }, theme: "notebook.ink" },
    { case: "the selected root's theme over the build's", search: "?app=identity", root: { theme: "notebook.paper" },
      appearance: { themeId: "notebook.ink" }, theme: "notebook.paper" },
    { case: "the deployment's theme for a selected root naming none", search: "?app=identity", shell: { theme: "notebook.ink" },
      theme: "notebook.ink" },
    { case: "a selected app's own theme over its root's", search: "?app=reading", root: { theme: "notebook.paper" },
      shell: { apps: { reading: { rail: ["identity"], theme: "notebook.ink" } } }, theme: "notebook.ink" },
  ])("defaults the theme to $case", async ({ search, root, shell, appearance, theme: expected }) => {
    history.replaceState(null, "", "/notes");
    const app = identityApp({ search, ...(root ? { root } : {}), ...(shell ? { shell } : {}), ...(appearance ? { appearance } : {}) });
    await mounted(app, async () => {
      await waitFor(() => expect(screen.getByLabelText("Theme").textContent).toBe(expected));
    });
  });

  test("a selected root lands on its home route", async () => {
    history.replaceState(null, "", "/");
    const app = createApp({
      ...inputs,
      addons: [{
        id: "notes",
        routes: [
          { name: "notes.home", path: "/notes", layout: "public", component: IdentityProbe },
          { name: "notes.archive", path: "/notes/archive", layout: "public", component: IdentityProbe },
        ],
        menus: [{ id: "notes", home: "notes.archive", children: [
          { id: "notes.home", route: "notes.home" },
          { id: "notes.archive", route: "notes.archive" },
        ] }],
      }],
      location: { search: "?app=notes" },
    });
    expect(app.explain.selection).toMatchObject({
      app: "notes", rail: ["notes"], home: "notes.archive",
      sources: { app: "?app=notes", rail: 'menu root "notes"', home: 'menu root "notes"' },
    });
    expect(app.explain.home).toBe("/notes/archive");
    await mounted(app, async () => {
      await screen.findByLabelText("Brand");
      await waitFor(() => expect(app.router.state.location.href).toBe("/notes/archive"));
    });
  });

  test("checks every declared home, brand mark and theme, selected or not", () => {
    const routes = inputs.addons[0]!.routes;
    expect(() => createApp({ ...inputs, addons: [{ id: "notes", routes, menus: [{ id: "notes", route: "notes.home", home: "notes.missing" }] }] }))
      .toThrow('Menu root "notes" home "notes.missing" names no route.');
    expect(() => identityApp({ root: { brand: { name: "Notebook", mark: "missing" } } }))
      .toThrow('Menu root "identity" brand mark "missing" is not a registered icon.');
    expect(() => identityApp({ root: { theme: "missing.theme" } })).toThrow('Menu root "identity" theme "missing.theme" is not installed.');
    expect(() => identityApp({ shell: { brand: { name: "Deployment", mark: "missing" } } }))
      .toThrow('ANGEE_UI.shell brand mark "missing" is not a registered icon.');
    expect(() => identityApp({ shell: { theme: "missing.theme" } })).toThrow('ANGEE_UI.shell theme "missing.theme" is not installed.');
    expect(() => identityApp({ shell: { apps: { reading: { rail: ["identity"], brand: { name: "Reading", mark: "missing" } } } } }))
      .toThrow('ANGEE_UI.shell.apps.reading brand mark "missing" is not a registered icon.');
  });

  test.each([undefined, "not-a-component", { arbitrary: true }])("refuses a brand mark that is no renderable icon: %j", (icon) => {
    expect(() => identityApp({ icons: { "notebook-mark": icon }, root: { brand: notebook } }))
      .toThrow('Menu root "identity" brand mark "notebook-mark" is not a registered icon.');
  });

  test("reads a brand mark through the icon registry's normalization and forwardRef support", () => {
    const icon = forwardRef<SVGSVGElement>(() => null);
    expect(identityApp({ icons: { "notebook-mark": icon }, search: "?app=identity", root: { brand: { ...notebook, mark: " NOTEBOOK-MARK " } } })
      .explain.selection.brand?.mark)
      .toBe(" NOTEBOOK-MARK ");
  });

  test("the rail lists every root, or the selected app's, through the Refine menu projection", async () => {
    const addons = [{
      id: "navigation",
      routes: [
        { name: "notes", path: "/notes", layout: "console" },
        { name: "archive", path: "/archive", layout: "console" },
      ],
      menus: [{ id: "notes", route: "notes" }, { id: "archive", route: "archive" }],
    }];
    for (const [app, rail] of [[undefined, ["notes", "archive"]], ["notes", ["notes"]]] as const) {
      const captured = await captureChrome({ path: "/notes", addons, ...(app ? { app } : {}) });
      try {
        expect(MenuTree.from(captured.props().menus).railMenuItems().map((item) => item.id)).toEqual(rail);
      } finally {
        captured.cleanup();
      }
    }
  });
});
