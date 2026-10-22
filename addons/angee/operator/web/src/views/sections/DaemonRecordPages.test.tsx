// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, ModalsHost, ToastProvider, baseIcons, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";

const routerMocks = vi.hoisted(() => ({
  params: {} as Record<string, string>,
}));

const operatorMocks = vi.hoisted(() => ({
  sourceAction: vi.fn(),
  workspaceAction: vi.fn(),
  useOperatorSnapshot: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useParams: () => routerMocks.params,
}));

// ServiceDetail reads the daemon `serviceEndpoint` through the shared authored
// query on the `operator` provider; stub just that hook from the refine owner
// so the detail frame renders without a live refine/react-query context.
vi.mock("@angee/ui", async () => ({
  ...(await vi.importActual<typeof import("@angee/ui")>("@angee/ui")),
  useRouteRecordId: () => routerMocks.params.name,
}));

vi.mock("../../i18n", () => ({
  useOperatorT: () => (key: string) => key,
}));

vi.mock("../../data/transport", () => ({
  useOperatorSnapshot: operatorMocks.useOperatorSnapshot,
  useOperatorQuery: () => ({ data: { serviceEndpoint: null }, fetching: false, error: null, refetch: vi.fn() }),
}));

vi.mock("./source-actions", () => ({
  useSourceActions: () => ({
    actions: [{ id: "fetch", label: "Fetch", primary: true, perform: operatorMocks.sourceAction }],
    busy: false,
  }),
}));

vi.mock("./logs", () => ({
  ServiceLogs: ({ name }: { name: string }) => (
    <section data-testid="service-logs">{name}</section>
  ),
}));

vi.mock("./service-actions", () => ({
  useServiceActions: () => ({ actions: [], busy: false }),
}));

vi.mock("./workspace-actions", () => ({
  useWorkspaceActions: () => ({
    actions: [{ id: "open", label: "Open", primary: true, perform: operatorMocks.workspaceAction }],
    busy: false,
  }),
}));

import { SourceDetail } from "./SourceDetail";
import { ServiceDetail } from "./ServiceDetail";
import { WorkspaceDetail } from "./WorkspaceDetail";

// The record band's verbs run through `RecordActionBar`: a mutation, confirm and toast host.
const { Provider } = createUiTestProviders();

function renderPage(page: ReactElement): ReturnType<typeof render> {
  const wrap = (node: ReactElement) => (
    <Provider>
      <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets }}>
        <ToastProvider><ModalsHost>{node}</ModalsHost></ToastProvider>
      </AppRuntimeProvider>
    </Provider>
  );
  const view = render(wrap(page));
  return { ...view, rerender: (next: ReactNode) => view.rerender(wrap(next as ReactElement)) };
}

beforeEach(() => {
  routerMocks.params = {};
  operatorMocks.sourceAction.mockClear();
  operatorMocks.workspaceAction.mockClear();
  operatorMocks.useOperatorSnapshot.mockReset();
});

afterEach(() => cleanup());

describe("operator daemon record pages", () => {
  test("SourceDetail preserves loading and not-found states", () => {
    operatorMocks.useOperatorSnapshot.mockReturnValue({
      refetch: vi.fn(),
      result: { fetching: true },
      snapshot: null,
    });

    const view = renderPage(<SourceDetail />);

    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getAllByText("sources.loading").length).toBeGreaterThan(0);

    routerMocks.params = { name: "missing" };
    operatorMocks.useOperatorSnapshot.mockReturnValue({
      refetch: vi.fn(),
      result: { fetching: false },
      snapshot: { sources: [] },
    });
    view.rerender(<SourceDetail />);

    expect(screen.getByRole("heading", {
      name: "sources.detail.notFound",
    })).toBeTruthy();
    expect(screen.getByText("missing")).toBeTruthy();
  });

  test("SourceDetail renders overview rows and binds the band verbs to the selected source", async () => {
    const source = {
      ahead: 2,
      behind: 1,
      branch: "main",
      currentRef: "abc123",
      dirty: true,
      kind: "git",
      name: "framework",
      path: "/work/framework",
      pushed: false,
      state: "running",
      upstream: "origin/main",
    };
    routerMocks.params = { name: "framework" };
    operatorMocks.useOperatorSnapshot.mockReturnValue({
      refetch: vi.fn(),
      result: { fetching: false },
      snapshot: { sources: [source] },
    });

    renderPage(<SourceDetail />);

    expect(screen.getByRole("heading", { name: "framework" })).toBeTruthy();
    expect(screen.getByText("sources.detail.overview")).toBeTruthy();
    expect(screen.getByText("main")).toBeTruthy();
    expect(screen.getByText("↑2 ↓1")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Fetch" }));
    await waitFor(() => expect(operatorMocks.sourceAction).toHaveBeenCalledWith(source));
  });

  test("ServiceDetail keeps the log tail under the record header", () => {
    routerMocks.params = { name: "web" };
    operatorMocks.useOperatorSnapshot.mockReturnValue({
      refetch: vi.fn(),
      result: { fetching: false },
      snapshot: {
        services: [
          {
            health: "ok",
            name: "web",
            runtime: "node",
            status: "running",
          },
        ],
      },
    });

    renderPage(<ServiceDetail />);

    expect(screen.getByRole("heading", { name: "web" })).toBeTruthy();
    expect(screen.getByText("services.detail.overview")).toBeTruthy();
    expect(screen.getAllByText("node")).toHaveLength(2);
    expect(screen.getByTestId("service-logs").textContent).toBe("web");
  });

  test("WorkspaceDetail runs its band verbs against the open workspace", async () => {
    const workspace = {
      name: "notes-dev",
      path: "/work/notes-dev",
      playwrightMcpUrl: "http://localhost:9001/mcp",
      processComposePort: 8000,
      template: "dev",
      ttl: "1h",
      ttlExpiresAt: "2025-01-01T01:00:00Z",
    };
    routerMocks.params = { name: "notes-dev" };
    operatorMocks.useOperatorSnapshot.mockReturnValue({
      refetch: vi.fn(),
      result: { fetching: false },
      snapshot: { workspaces: [workspace] },
    });

    renderPage(<WorkspaceDetail />);

    expect(screen.getByRole("heading", { name: "notes-dev" })).toBeTruthy();
    expect(screen.getByText("workspaces.detail.overview")).toBeTruthy();
    expect(screen.getAllByText("dev")).toHaveLength(2);
    expect(screen.getByText("/work/notes-dev")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    await waitFor(() => expect(operatorMocks.workspaceAction).toHaveBeenCalledWith(workspace));
  });
});
