// @vitest-environment happy-dom

import { act, cleanup, screen, waitFor } from "@testing-library/react";
import { useAppRuntime, useRuntimeBrand } from "@angee/ui/runtime";
import { MenuTree } from "@angee/ui/chrome/menu-tree";
import { afterEach, describe, expect, test } from "vitest";

import { createApp, PassthroughChrome, type CreateAppInput } from "./create-app";
import { captureChrome, TEST_SCHEMAS } from "./testing";

afterEach(cleanup);

function IdentityProbe() {
  const brand = useRuntimeBrand();
  const { i18n } = useAppRuntime();
  const uiCopy = i18n?.getFixedT(null, "ui");
  const addonCopy = i18n?.getFixedT(null, "notes");
  return <>
    <output aria-label="Brand">{brand ? `${brand.name}/${brand.mark}` : "unbranded"}</output>
    <output aria-label="Base copy">{String(uiCopy?.("modal.copy") ?? "")}</output>
    <output aria-label="Addon copy">{String(addonCopy?.("title") ?? "")}</output>
  </>;
}

const inputs = {
  schemas: TEST_SCHEMAS,
  layouts: { public: { chrome: PassthroughChrome, requireAuth: false, schema: "public" } },
  addons: [{ id: "notes", routes: [{ name: "notes.home", path: "/notes", layout: "public", component: IdentityProbe }] }],
} satisfies CreateAppInput;

describe("composed identity and host home", () => {
  test.each(["/notes?tab=archive", "notes.home"])("resolves the host home %s", async (home) => {
    history.replaceState(null, "", "/");
    const app = createApp({ ...inputs, home });
    const host = document.createElement("div");
    document.body.append(host);
    const root = app.mount(host);
    try {
      await screen.findByLabelText("Brand");
      await waitFor(() => expect(app.router.state.location.href)
        .toBe(home.startsWith("/") ? home : "/notes"));
    } finally {
      act(() => root.unmount());
      host.remove();
    }
  });

  test("accepts an absolute home without interpreting it as a route name", () => {
    expect(() => createApp({ ...inputs, home: "/not-in-the-route-registry" })).not.toThrow();
  });

  test("fails composition for an unknown home route name", () => {
    expect(() => createApp({ ...inputs, home: "notes.missing" }))
      .toThrow('Unknown route name "notes.missing"');
  });

  test.each([false, true])("publishes brand=%s and disjoint base/addon translations", async (branded) => {
    history.replaceState(null, "", "/notes");
    const app = createApp({
      ...inputs,
      addons: [...inputs.addons, {
        id: "identity", i18n: { notes: { title: "My notes" } },
        ...(branded ? { brand: { name: "Notebook", mark: "notebook-mark" }, icons: { "notebook-mark": () => null } } : {}),
      }],
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = app.mount(host);
    try {
      expect((await screen.findByLabelText("Brand")).textContent)
        .toBe(branded ? "Notebook/notebook-mark" : "unbranded");
      expect(screen.getByLabelText("Base copy").textContent).toBe("Copy");
      expect(screen.getByLabelText("Addon copy").textContent).toBe("My notes");
    } finally {
      act(() => root.unmount());
      host.remove();
    }
  });

  test("preserves explicit app-root selection through Refine menu projection", async () => {
    const captured = await captureChrome({
      path: "/notes",
      addons: [{
        id: "navigation",
        routes: [
          { name: "notes", path: "/notes", layout: "console" },
          { name: "archive", path: "/archive", layout: "console" },
        ],
        menus: [
          { id: "notes", route: "notes", appRoot: true },
          { id: "archive", route: "archive" },
        ],
      }],
    });
    try {
      expect(MenuTree.from(captured.props().menus).appRoots().map((item) => item.id)).toEqual(["notes"]);
    } finally {
      captured.cleanup();
    }
  });

  test("rejects a non-root appRoot during app composition", () => {
    expect(() => createApp({ ...inputs, addons: [{
      id: "invalid-navigation", routes: inputs.addons.flatMap((addon) => addon.routes),
      menus: [{ id: "root", children: [{ route: "notes.home", appRoot: true }] }],
    }] })).toThrow(/notes.home.*appRoot.*non-root/);
  });
});
