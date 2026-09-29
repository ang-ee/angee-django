// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { DashboardDefinition, DashboardRegistry, DashboardWidgetData, WidgetSpec } from "./headless";
import { BUILTIN_DASHBOARD_WIDGET_KINDS } from "./kinds";
import { DashboardSurface } from "./surface";

const state = vi.hoisted(() => ({
  allowed: [false], loading: false, error: null as Error | null,
  reads: vi.fn(), refetch: vi.fn(),
}));
let registry: DashboardRegistry;

vi.mock("../runtime/runtime", async (importOriginal) => ({
  ...await importOriginal<typeof import("../runtime/runtime")>(),
  useDashboardRegistry: () => registry,
}));
vi.mock("../page/PageHeader", () => ({ PageHeader: () => <h1>Dashboard</h1> }));
vi.mock("../views/form/use-unsaved-changes-navigation-guard", () => ({
  useUnsavedChangesNavigationGuard: () => {},
}));
vi.mock("./data", () => ({
  useDashboardWidgetData: (spec: WidgetSpec): DashboardWidgetData => {
    state.reads(spec.id);
    return {
      value: 0, rows: [], series: [], queryFields: {}, identity: { field: "id" },
      fetching: false, error: null, live: false, updatedAt: null, refetch: state.refetch,
    };
  },
}));

const widget: WidgetSpec = {
  schemaVersion: 1, kindVersion: 1, id: "private", title: "Incoming requests", kind: "table",
  data: { shape: "rows", source: { resource: "projects.Task" } },
  visibility: { resource: "work.Queue", key: "slug", value: "incoming" },
  options: {}, x: 0, y: 0, w: 6, h: 3, isArchived: false,
};
const definition: DashboardDefinition = {
  key: "overview", title: "Overview", revision: "1",
  widgets: [widget, { ...widget, id: "public", title: "Shared work", x: 6, visibility: undefined }],
};
const unused = async (): Promise<never> => { throw new Error("Unused dashboard write"); };

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", undefined);
  state.allowed = [false];
  state.loading = false;
  state.error = null;
  registry = {
    definitions: { overview: definition }, resourceDefaults: {},
    widgetKinds: Object.fromEntries(BUILTIN_DASHBOARD_WIDGET_KINDS.map((kind) => [kind.id, kind])),
    store: {
      useDashboard: () => ({
        state: { status: "absent" }, reload: unused, save: unused, reset: unused,
        createPersonal: unused, duplicate: unused, archive: unused,
      }),
      useCatalogue: () => ({ summaries: [], loading: false, error: null, refresh: () => {}, createPersonal: unused }),
      useWidgetVisibility: () => ({ allowed: state.allowed, loading: state.loading, error: state.error }),
    },
  };
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

test("hides a denied widget before mounting its query and compacts the remaining layout", () => {
  render(<DashboardSurface target={{ scope: "addon", key: "overview" }} />);
  expect(screen.queryByRole("heading", { name: "Incoming requests" })).toBeNull();
  expect(state.reads).not.toHaveBeenCalledWith("private");
  const article = screen.getByRole("heading", { name: "Shared work" }).closest("article")!;
  expect(article.style.gridColumn).toBe("1 / span 6");
});

test("an authorized empty widget stays visible and actor changes remove it", () => {
  state.allowed = [true];
  const { rerender } = render(<DashboardSurface target={{ scope: "addon", key: "overview" }} />);
  expect(screen.getByRole("heading", { name: "Incoming requests" })).toBeTruthy();
  state.allowed = [false];
  rerender(<DashboardSurface target={{ scope: "addon", key: "overview" }} />);
  expect(screen.queryByRole("heading", { name: "Incoming requests" })).toBeNull();
});

test("pending visibility never starts a protected data query", () => {
  state.loading = true;
  render(<DashboardSurface target={{ scope: "addon", key: "overview" }} />);
  expect(state.reads).not.toHaveBeenCalled();
});

test("authored widgets omit the disconnected footer while data widgets refresh their binding", () => {
  const authored: DashboardDefinition = {
    ...definition,
    widgets: [{
      ...widget, id: "authored", title: "Summary", kind: "authored", visibility: undefined,
      data: { shape: "none", binding: { dashboardKey: "overview", widgetId: "authored" } },
    }, definition.widgets[1]!],
    authored: { authored: () => <p>Authored results</p> },
  };
  render(<DashboardSurface target={{ scope: "addon", key: "overview" }} definition={authored} />);
  const authoredCell = screen.getByRole("heading", { name: "Summary" }).closest("article")!;
  expect(within(authoredCell).queryByRole("button", { name: "Refresh" })).toBeNull();
  const dataCell = screen.getByRole("heading", { name: "Shared work" }).closest("article")!;
  fireEvent.click(within(dataCell).getByRole("button", { name: "Refresh" }));
  expect(state.refetch).toHaveBeenCalledOnce();
});
