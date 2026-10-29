// @vitest-environment happy-dom

import * as React from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { DashboardStoreBinding } from "@angee/ui/dashboard/headless";
import type { RuntimeUserPreferencesState } from "@angee/ui/runtime";

const mocks = vi.hoisted(() => ({
  binding: null as DashboardStoreBinding | null,
  preferences: {} as RuntimeUserPreferencesState["preferences"],
  patchPreferences: vi.fn<RuntimeUserPreferencesState["patchPreferences"]>(),
  trash: vi.fn<(id: string, name?: string) => Promise<boolean>>(),
  navigate: vi.fn<(options: { to: string }) => Promise<void>>(),
  danger: vi.fn(),
  settle: vi.fn(),
}));

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => mocks.navigate,
}));
vi.mock("@angee/metadata", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/metadata")>()),
  useModelMetadata: () => undefined,
}));
vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  Page: ({ children }: { children: React.ReactNode }) => children,
  PageToolbar: ({ start, end }: { start: React.ReactNode; end: React.ReactNode }) => <>{start}{end}</>,
  DashboardSurface: ({ toolbar }: { toolbar: React.ReactNode }) => toolbar,
  RecordChrome: () => null,
  MutationDialog: () => null,
  useBreadcrumbLeafLabel: () => {},
  useDashboardRegistry: () => ({ store: { useDashboard: () => mocks.binding } }),
  useRouteParam: () => "dashboard-1",
  useRouteHref: () => (name: string) => name === "dashboards.detail" ? "/dashboards/dashboard-1" : "/dashboards",
  useRuntimeUserPreferences: () => ({
    available: true, preferences: mocks.preferences, patchPreferences: mocks.patchPreferences,
  }),
  useTrashRecord: () => ({ available: true, busy: false, trash: mocks.trash }),
  useActionResultRun: () => mocks.settle,
  useToast: () => ({ danger: mocks.danger }),
}));

import { HOME_PATH_PREFERENCE_KEY, ROUTE_SHORTCUTS_PREFERENCE_KEY } from "@angee/ui";
import { PersonalDashboardPage } from "./DashboardPage";

let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.clearAllMocks();
  mocks.trash.mockReset().mockResolvedValue(true);
  mocks.patchPreferences.mockReset().mockResolvedValue(undefined);
  mocks.navigate.mockReset().mockResolvedValue(undefined);
  mocks.preferences = {
    [HOME_PATH_PREFERENCE_KEY]: "/dashboards/dashboard-1",
    [ROUTE_SHORTCUTS_PREFERENCE_KEY]: [
      { id: "dashboard:dashboard-1", label: "My dashboard", path: "/dashboards/dashboard-1" },
      { id: "other", label: "Other dashboard", path: "/dashboards/other" },
    ],
  };
  mocks.binding = {
    state: {
      status: "ready", persistedId: "dashboard-1", revision: 1, declarationRevision: "",
      name: "My dashboard", snapshot: { schemaVersion: 1, columns: 12, widgets: [] },
      capabilities: { canEdit: true, canReset: true, canTrash: true },
    },
    reload: vi.fn(), save: vi.fn(), reset: vi.fn(), createPersonal: vi.fn(), duplicate: vi.fn(),
  };
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await React.act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function trashDashboard(): Promise<HTMLButtonElement> {
  await React.act(async () => root.render(<PersonalDashboardPage />));
  const menu = host.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"]')!;
  await React.act(async () => menu.click());
  const trash = Array.from(document.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'))
    .find((item) => item.textContent?.includes("Move to trash"))!;
  expect(trash).toBeDefined();
  await React.act(async () => trash.click());
  return menu;
}

describe("dashboard trash follow-ups", () => {
  test("cleans preferences and leaves feedback to the shared trash verb", async () => {
    const menu = await trashDashboard();
    await vi.waitFor(() => expect(mocks.navigate).toHaveBeenCalledWith({ to: "/dashboards" }));
    expect(mocks.trash).toHaveBeenCalledExactlyOnceWith("dashboard-1", "My dashboard");
    const apply = mocks.patchPreferences.mock.calls[0]![0];
    expect(apply(mocks.preferences)).toEqual({
      [HOME_PATH_PREFERENCE_KEY]: null,
      [ROUTE_SHORTCUTS_PREFERENCE_KEY]: [{ id: "other", label: "Other dashboard", path: "/dashboards/other" }],
    });
    expect(mocks.settle).not.toHaveBeenCalled();
    expect(mocks.danger).not.toHaveBeenCalled();
    expect(menu.disabled).toBe(false);
  });

  test("reports failed preference cleanup and still leaves the trashed dashboard", async () => {
    mocks.patchPreferences.mockRejectedValueOnce(new Error("Preferences could not be saved."));
    const menu = await trashDashboard();
    await vi.waitFor(() => expect(mocks.danger).toHaveBeenCalledExactlyOnceWith({
      title: "Preferences could not be saved.",
    }));
    expect(mocks.navigate).toHaveBeenCalledExactlyOnceWith({ to: "/dashboards" });
    expect(mocks.settle).not.toHaveBeenCalled();
    expect(menu.disabled).toBe(false);
  });

  test("does not clean up or navigate when the shared trash verb is cancelled or fails", async () => {
    // Both settled outcomes resolve false; the shared hook owns their feedback difference.
    mocks.trash.mockResolvedValueOnce(false);
    const menu = await trashDashboard();
    expect(mocks.trash).toHaveBeenCalledOnce();
    expect(mocks.patchPreferences).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
    expect(mocks.danger).not.toHaveBeenCalled();
    expect(mocks.settle).not.toHaveBeenCalled();
    expect(menu.disabled).toBe(false);
  });

  test("settles a rejected trash prompt without starting follow-ups", async () => {
    mocks.trash.mockRejectedValueOnce(new Error("Confirmation unavailable."));
    const menu = await trashDashboard();
    expect(mocks.danger).toHaveBeenCalledExactlyOnceWith({ title: "Confirmation unavailable." });
    expect(mocks.patchPreferences).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
    expect(menu.disabled).toBe(false);
  });
});
