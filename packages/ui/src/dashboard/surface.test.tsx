// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { DashboardDefinition, DashboardLoadState, DashboardRegistry, DashboardWidgetData, WidgetSpec } from "./headless";
import { BUILTIN_DASHBOARD_WIDGET_KINDS } from "./kinds";
import { DashboardSurface, visibleDashboardSnapshot } from "./surface";
import { AppRuntimeProvider } from "../runtime/runtime";
import { createRouteHref } from "../runtime/route-href";
import type { ReactElement, ReactNode } from "react";

const state = vi.hoisted(() => ({
  allowed: [false], loading: false, error: null as Error | null,
  reads: vi.fn(), refetch: vi.fn(),
  count: null as number | null,
}));
let registry: DashboardRegistry;
let loadState: DashboardLoadState;

vi.mock("../runtime/runtime", async (importOriginal) => ({
  ...await importOriginal<typeof import("../runtime/runtime")>(),
  useDashboardRegistry: () => registry,
}));
vi.mock("../page/PageHeader", () => ({ PageHeader: ({ actions }: { actions?: ReactNode }) => <header><h1>Dashboard</h1>{actions}</header> }));
vi.mock("../views/form/use-unsaved-changes-navigation-guard", () => ({
  useUnsavedChangesNavigationGuard: () => {},
}));
vi.mock("./data", () => ({
  useDashboardWidgetData: (spec: WidgetSpec): DashboardWidgetData => {
    state.reads(spec.id);
    return {
      value: 0, count: state.count, rows: [], series: [], queryFields: {}, identity: { field: "id" },
      fetching: false, error: null, live: false, updatedAt: null, refetch: state.refetch,
    };
  },
}));

const widget: WidgetSpec = {
  schemaVersion: 1, kindVersion: 1, id: "private", title: "Incoming requests", kind: "table",
  data: { shape: "rows", source: { resource: "projects.Task" } },
  visibility: { resource: "projects.Task", key: "queue__slug", value: "incoming" },
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
  state.count = null;
  loadState = { status: "absent", capabilities: { canEdit: false, canReset: false, canArchive: false } };
  registry = {
    definitions: { overview: definition }, resourceDefaults: {},
    widgetKinds: Object.fromEntries(BUILTIN_DASHBOARD_WIDGET_KINDS.map((kind) => [kind.id, kind])),
    store: {
      useDashboard: () => ({
        state: loadState, reload: unused, save: unused, reset: unused,
        createPersonal: unused, duplicate: unused, archive: unused,
      }),
      useCatalogue: () => ({ summaries: [], loading: false, error: null, refresh: () => {}, createPersonal: unused }),
      useWidgetVisibility: () => ({ allowed: state.allowed, loading: state.loading, error: state.error }),
    },
  };
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

const renderDashboard = (node: ReactElement) => render(node, {
  wrapper: ({ children }) => <AppRuntimeProvider runtime={{}}>{children}</AppRuntimeProvider>,
});

test("hides a denied widget before mounting its query and compacts the remaining layout", () => {
  renderDashboard(<DashboardSurface target={{ scope: "addon", key: "overview" }} />);
  expect(screen.queryByRole("heading", { name: "Incoming requests" })).toBeNull();
  expect(state.reads).not.toHaveBeenCalledWith("private");
  const article = screen.getByRole("heading", { name: "Shared work" }).closest("article")!;
  expect(article.style.gridColumn).toBe("1 / span 6");
});

test("an authorized empty widget stays visible and actor changes remove it", () => {
  state.allowed = [true];
  const { rerender } = renderDashboard(<DashboardSurface target={{ scope: "addon", key: "overview" }} />);
  expect(screen.getByRole("heading", { name: "Incoming requests" })).toBeTruthy();
  state.allowed = [false];
  rerender(<AppRuntimeProvider runtime={{}}><DashboardSurface target={{ scope: "addon", key: "overview" }} /></AppRuntimeProvider>);
  expect(screen.queryByRole("heading", { name: "Incoming requests" })).toBeNull();
});

test("pending visibility never starts a protected data query", () => {
  state.loading = true;
  renderDashboard(<DashboardSurface target={{ scope: "addon", key: "overview" }} />);
  expect(state.reads).not.toHaveBeenCalled();
});

test("query widgets keep refresh in their menu and never show a status footer", async () => {
  const authored: DashboardDefinition = {
    ...definition,
    widgets: [{
      ...widget, id: "authored", title: "Summary", kind: "authored", visibility: undefined,
      data: { shape: "none", binding: { dashboardKey: "overview", widgetId: "authored" } },
    }, definition.widgets[1]!],
    authored: { authored: () => <p>Authored results</p> },
  };
  renderDashboard(<DashboardSurface target={{ scope: "addon", key: "overview" }} definition={authored} />);
  const authoredCell = screen.getByRole("heading", { name: "Summary" }).closest("article")!;
  expect(within(authoredCell).queryByRole("button", { name: "Summary options" })).toBeNull();
  const dataCell = screen.getByRole("heading", { name: "Shared work" }).closest("article")!;
  expect(dataCell.querySelector("footer")).toBeNull();
  expect(within(dataCell).queryByText("Live")).toBeNull();
  expect(within(dataCell).queryByRole("button", { name: "Refresh" })).toBeNull();
  fireEvent.click(within(dataCell).getByRole("button", { name: "Shared work options" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Refresh" }));
  expect(state.refetch).toHaveBeenCalledOnce();
});

test("widget headings compose loaded totals, hints, and audience without statistic captions", () => {
  state.count = 7;
  const reading: DashboardDefinition = { ...definition, widgets: [{
    ...widget, id: "reading", title: "Open requests", visibility: undefined,
    options: { hint: "Filed this week", audience: "Reviewers" },
  }] };
  renderDashboard(<DashboardSurface target={{ scope: "addon", key: "overview" }} definition={reading} />);
  const heading = screen.getByRole("heading", { name: /Open requests/ });
  expect(heading.parentElement?.textContent).toBe("Open requests· 7· Filed this weekReviewers");
  expect(screen.queryByText("Value")).toBeNull();
  cleanup();
  const statistic: DashboardDefinition = { ...reading, widgets: [{
    ...reading.widgets[0]!, id: "statistic", title: "Decisions", kind: "stat",
    data: { shape: "value", source: { resource: "projects.Task", measure: { op: "count" } } },
  }] };
  renderDashboard(<DashboardSurface target={{ scope: "addon", key: "overview" }} definition={statistic} />);
  expect(screen.getByRole("heading", { name: /Decisions/ }).parentElement?.textContent).toContain("· 7");
  expect(screen.queryByText("Value")).toBeNull();
});

test("a full-view action composes the outward link affordance", () => {
  const linked = {
    ...widget,
    id: "linked",
    visibility: undefined,
    options: { fullViewRoute: "tasks.list" },
  };
  render(
    <AppRuntimeProvider runtime={{
      routeHref: createRouteHref([{ name: "tasks.list", path: "/tasks" }]),
    }}>
      <DashboardSurface
        target={{ scope: "addon", key: "overview" }}
        definition={{ ...definition, widgets: [linked] }}
      />
    </AppRuntimeProvider>,
  );

  const link = screen.getByRole("link", { name: "Open Incoming requests in full view" });
  expect(link.getAttribute("href")).toBe("/tasks");
  expect(link.querySelector(".glyph")).toBeTruthy();
});

test("absent dashboards use the server edit answer and declaration can turn editing off", () => {
  renderDashboard(<DashboardSurface target={{ scope: "addon", key: "overview" }} />);
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  cleanup();
  loadState = { status: "absent", capabilities: { canEdit: true, canReset: false, canArchive: false } };
  renderDashboard(<DashboardSurface target={{ scope: "addon", key: "overview" }} />);
  expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
  cleanup();
  renderDashboard(<DashboardSurface target={{ scope: "addon", key: "overview" }} definition={{ ...definition, editable: false }} />);
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
});

test("a changed declared revision replaces an older stored layout and still honors server capabilities", () => {
  loadState = {
    status: "ready", capabilities: { canEdit: false, canReset: true, canArchive: false },
    snapshot: { schemaVersion: 1, columns: 12, widgets: [{ ...widget, id: "stale", title: "Old widget", visibility: undefined }] },
    persistedId: "1", revision: 3, declarationRevision: "old", name: "Saved", description: "",
  };
  renderDashboard(<DashboardSurface target={{ scope: "addon", key: "overview" }} />);
  expect(screen.queryByRole("heading", { name: "Old widget" })).toBeNull();
  expect(screen.getByRole("heading", { name: "Shared work" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Reset" })).toBeNull();
});

test("the visible layout is the source for duplication when a declaration advances", () => {
  const stale = { schemaVersion: 1 as const, columns: 12, widgets: [{ ...widget, id: "stale" }] };
  const ready: Extract<DashboardLoadState, { status: "ready" }> = {
    status: "ready", capabilities: { canEdit: true, canReset: true, canArchive: false },
    snapshot: stale, persistedId: "1", revision: 3, declarationRevision: "old", name: "Saved",
  };
  const target = { scope: "addon" as const, key: "overview" };
  const visible = visibleDashboardSnapshot(registry, target, ready);
  expect(visible.widgets.map((item) => item.id)).toEqual(["private", "public"]);
  expect(visibleDashboardSnapshot(registry, target, { ...ready, declarationRevision: definition.revision })).toBe(stale);
});

test("a statistic uses its loaded value in the heading and body", () => {
  state.count = 5;
  const stat = { ...widget, id: "stat", kind: "stat", title: "Total", visibility: undefined,
    data: { shape: "value" as const, source: { resource: "projects.Task", measure: { op: "count" as const } } },
  };
  renderDashboard(<DashboardSurface target={{ scope: "addon", key: "overview" }}
    definition={{ ...definition, widgets: [stat] }} />);
  const article = screen.getByRole("heading", { name: "Total" }).closest("article")!;
  expect(article.querySelector("header")?.textContent).toContain("Total· 5");
  expect(article.querySelector("dd")?.textContent).toBe("0");
  expect(article.querySelector("number-flow-react")).toBeNull();
});

test("an undeclared fallback does not replace a personal saved layout", () => {
  const saved = { schemaVersion: 1 as const, columns: 12, widgets: [{ ...widget, id: "saved", title: "Saved widget", visibility: undefined }] };
  loadState = {
    status: "ready", capabilities: { canEdit: false, canReset: false, canArchive: false },
    snapshot: saved, persistedId: "1", revision: 2, declarationRevision: "", name: "Personal",
  };
  renderDashboard(<DashboardSurface target={{ scope: "personal", id: "1" }} fallbackSnapshot={{
    schemaVersion: 1, columns: 12, widgets: [{ ...widget, id: "fallback", title: "Fallback widget", visibility: undefined }],
  }} />);
  expect(screen.getByRole("heading", { name: "Saved widget" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Fallback widget" })).toBeNull();
});
