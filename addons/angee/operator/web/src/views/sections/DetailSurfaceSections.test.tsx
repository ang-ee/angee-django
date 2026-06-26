// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

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
// query on the `operator` provider; stub just that hook (over the real `@angee/ui`)
// so the detail frame renders without a live refine/react-query context.
vi.mock("@angee/ui", async () => ({
  ...(await vi.importActual<typeof import("@angee/ui")>("@angee/ui")),
  useAuthoredQuery: () => ({
    data: { serviceEndpoint: null },
    fetching: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("../../i18n", () => ({
  useOperatorT: () => (key: string) => key,
}));

vi.mock("../../data/transport", () => ({
  useOperatorSnapshot: operatorMocks.useOperatorSnapshot,
}));

vi.mock("./source-actions", () => ({
  useSourceActions: () => ({
    actions: [
      {
        label: "Fetch",
        perform: operatorMocks.sourceAction,
        variant: "secondary",
      },
    ],
    busy: false,
  }),
}));

vi.mock("./logs", () => ({
  LogPanel: ({ title }: { title: string }) => (
    <section data-testid="workspace-logs">{title}</section>
  ),
  ServiceLogs: ({ name }: { name: string }) => (
    <section data-testid="service-logs">{name}</section>
  ),
  useDaemonLogStream: () => ({ entries: [], fetching: false }),
}));

vi.mock("./service-actions", () => ({
  useServiceActions: () => ({ actions: [], busy: false }),
}));

vi.mock("./workspace-actions", () => ({
  useWorkspaceActions: () => ({
    actions: [
      {
        label: "Open",
        perform: operatorMocks.workspaceAction,
        variant: "secondary",
      },
    ],
    busy: false,
  }),
}));

import { SourceDetail } from "./SourceDetail";
import { ServiceDetail } from "./ServiceDetail";
import { WorkspaceDetail } from "./WorkspaceDetail";

beforeEach(() => {
  routerMocks.params = {};
  operatorMocks.sourceAction.mockClear();
  operatorMocks.workspaceAction.mockClear();
  operatorMocks.useOperatorSnapshot.mockReset();
});

afterEach(() => cleanup());

describe("operator detail surfaces", () => {
  test("SourceDetail preserves loading and not-found states", () => {
    operatorMocks.useOperatorSnapshot.mockReturnValue({
      refetch: vi.fn(),
      result: { fetching: true },
      snapshot: null,
    });

    const view = render(<SourceDetail />);

    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText("operator.sources.loading")).toBeTruthy();

    routerMocks.params = { name: "missing" };
    operatorMocks.useOperatorSnapshot.mockReturnValue({
      refetch: vi.fn(),
      result: { fetching: false },
      snapshot: { sources: [] },
    });
    view.rerender(<SourceDetail />);

    expect(screen.getByRole("heading", {
      name: "operator.sources.detail.notFound",
    })).toBeTruthy();
    expect(screen.getByText("missing")).toBeTruthy();
  });

  test("SourceDetail renders overview rows and binds actions to the selected source", () => {
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

    render(<SourceDetail />);

    expect(screen.getByRole("heading", { name: "framework" })).toBeTruthy();
    expect(screen.getByText("operator.sources.detail.overview")).toBeTruthy();
    expect(screen.getByText("main")).toBeTruthy();
    expect(screen.getByText("↑2 ↓1")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Fetch" }));
    expect(operatorMocks.sourceAction).toHaveBeenCalledWith(source);
  });

  test("ServiceDetail keeps extra body content inside the shared detail frame", () => {
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

    render(<ServiceDetail />);

    expect(screen.getByRole("heading", { name: "web" })).toBeTruthy();
    expect(screen.getByText("operator.services.detail.overview")).toBeTruthy();
    expect(screen.getAllByText("node")).toHaveLength(2);
    expect(screen.getByTestId("service-logs").textContent).toBe("web");
  });

  test("WorkspaceDetail keeps actions and logs inside the shared detail frame", () => {
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

    render(<WorkspaceDetail />);

    expect(screen.getByRole("heading", { name: "notes-dev" })).toBeTruthy();
    expect(screen.getByText("operator.workspaces.detail.overview")).toBeTruthy();
    expect(screen.getAllByText("dev")).toHaveLength(2);
    expect(screen.getByText("/work/notes-dev")).toBeTruthy();
    expect(screen.getByTestId("workspace-logs").textContent).toBe(
      "operator.workspaces.detail.logs",
    );

    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(operatorMocks.workspaceAction).toHaveBeenCalledWith(workspace);
  });
});
