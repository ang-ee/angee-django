// @vitest-environment happy-dom

import * as React from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { DashboardStore, DashboardStoreBinding, DashboardSaveResult } from "@angee/ui/dashboard/headless";

const mocks = vi.hoisted(() => ({
  store: null as DashboardStore | null,
  save: vi.fn<DashboardStoreBinding["save"]>(),
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  useDashboardRegistry: () => ({ store: mocks.store }),
  useResourceViewUtilityContext: () => ({ resource: "example.Item", filter: {} }),
}));

import { CaptureDashboardAction } from "./CaptureDashboardAction";

const target = { scope: "personal", id: "dashboard-1" } as const;
const capabilities = { canEdit: true, canReset: true, canArchive: true };
const snapshot = { schemaVersion: 1, columns: 12, widgets: [] } as const;
const saved: DashboardSaveResult = {
  persistedId: target.id, revision: 2, snapshot: { ...snapshot, widgets: [] }, capabilities,
};

let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  mocks.save.mockReset().mockResolvedValue(saved);
  const write = vi.fn(async () => saved);
  mocks.store = {
    useCatalogue: () => ({
      summaries: [{ id: target.id, target, title: "My dashboard", resources: [],
        revision: 1, customized: false, available: true, capabilities }],
      loading: false, error: null, refresh: vi.fn(), createPersonal: write,
    }),
    useDashboard: () => ({
      state: { status: "ready", capabilities, persistedId: target.id, revision: 1,
        name: "My dashboard", declarationRevision: "", snapshot: { ...snapshot, widgets: [] } },
      reload: vi.fn(async () => {}), save: mocks.save, reset: vi.fn(async () => {}),
      createPersonal: write, duplicate: write, archive: write,
    }),
  };
});
afterEach(async () => {
  await React.act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("CaptureDashboardAction", () => {
  test("captures through a native action item and reports pending state on the toolbar", async () => {
    let finish!: (result: DashboardSaveResult) => void;
    mocks.save.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await React.act(async () => root.render(<CaptureDashboardAction />));
    const trigger = host.querySelector<HTMLButtonElement>("button")!;
    await React.act(async () => trigger.click());
    const item = document.querySelector<HTMLButtonElement>('[role="menuitem"]')!;
    expect(item.textContent).toContain("My dashboard");
    expect(item.tagName).toBe("BUTTON");
    await React.act(async () => item.click());
    await vi.waitFor(() => expect(trigger.getAttribute("aria-busy")).toBe("true"));
    expect(trigger.disabled).toBe(true);
    expect(mocks.save).toHaveBeenCalledWith(expect.objectContaining({
      target, expectedRevision: 1,
      snapshot: expect.objectContaining({ widgets: [expect.objectContaining({
        data: { shape: "value", source: { resource: "example.Item", filter: {}, measure: { op: "count" } } },
      })] }),
    }));
    await React.act(async () => { finish(saved); });
    await vi.waitFor(() => expect(trigger.getAttribute("aria-busy")).toBeNull());
    expect(trigger.disabled).toBe(false);
  });

  test("retains a failed capture for the reopened menu", async () => {
    mocks.save.mockRejectedValueOnce(new Error("Dashboard save failed."));
    await React.act(async () => root.render(<CaptureDashboardAction />));
    const trigger = host.querySelector<HTMLButtonElement>("button")!;
    await React.act(async () => trigger.click());
    await React.act(async () => document.querySelector<HTMLButtonElement>('[role="menuitem"]')!.click());
    await vi.waitFor(() => expect(trigger.getAttribute("aria-busy")).toBeNull());
    await React.act(async () => trigger.click());
    expect(document.body.textContent).toContain("Dashboard save failed.");
  });
});
