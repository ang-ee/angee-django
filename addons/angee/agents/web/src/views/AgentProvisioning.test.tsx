// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { enAgentsMessages } from "../i18n";
import { AgentProvisioning } from "./AgentProvisioning";

const mocks = vi.hoisted(() => ({
  refetch: vi.fn(),
  record: {
    id: "agent-1",
    lifecycle: "DRAFT",
    runtimeStatus: "ERROR",
    lastError: "operator POST workspaces: HTTP 409: workspace demo-agent conflicts: already exists",
    workspace: "",
    service: "",
    workspaceTemplate: { path: "workspaces/agent-default" },
    serviceTemplate: { id: "service-template-1" },
  },
  workspaceStatus: null as {
    error?: string | null;
    innerError?: string | null;
    sources: Array<Record<string, unknown>>;
  } | null,
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useNamespaceT:
      (_namespace: string, messages: Record<string, string>) =>
      (key: string): string =>
        messages[key] ?? key,
    useResourceRecord: () => ({
      fetching: false,
      record: mocks.record,
      refetch: mocks.refetch,
    }),
  };
});

vi.mock("@angee/base", () => ({
  Card: ({ children }: { children: ReactNode }) => <article>{children}</article>,
  CardContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  CardHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
  CardTitle: ({ children }: { children: ReactNode }) => <h3>{children}</h3>,
}));

vi.mock("@angee/operator/runtime", () => ({
  OperatorTransportProvider: ({ children }: { children: ReactNode }) => children,
  ServiceRow: ({ name }: { name: string }) => (
    <section data-testid="service-row">{name}</section>
  ),
  ServiceLogs: ({ name, title }: { name: string; title?: ReactNode }) => (
    <section data-testid="service-logs" data-name={name}>{title}</section>
  ),
  StateTag: ({ state }: { state: string }) => <span>{state}</span>,
  WorkspaceRow: ({ name }: { name: string }) => (
    <section data-testid="workspace-row">{name}</section>
  ),
  useOperatorSnapshot: () => ({
    result: {},
    snapshot: null,
  }),
  useOperatorSubscription: () => ({
    data: mocks.workspaceStatus
      ? { onWorkspaceStatusChange: mocks.workspaceStatus }
      : undefined,
  }),
}));

beforeEach(() => {
  // A failed provision that rolled back: lifecycle reset to DRAFT, run state ERROR.
  mocks.record.lifecycle = "DRAFT";
  mocks.record.runtimeStatus = "ERROR";
  mocks.record.lastError =
    "operator POST workspaces: HTTP 409: workspace demo-agent conflicts: already exists";
  mocks.record.workspace = "";
  mocks.record.service = "";
  mocks.workspaceStatus = null;
});

afterEach(cleanup);

describe("AgentProvisioning", () => {
  test("shows the saved provisioning error even without recorded runtime names", () => {
    const intro = enAgentsMessages["agents.provisioning.intro"] ?? "";

    render(<AgentProvisioning agentId="agent-1" pane="service" />);

    expect(screen.queryByRole("heading", { name: "Service" })).toBeNull();
    expect(screen.getByText(String(mocks.record.lastError))).toBeTruthy();
    expect(screen.getByText(intro)).toBeTruthy();
  });

  test("renders the service row first and service logs underneath", () => {
    mocks.record.lifecycle = "READY";
    mocks.record.runtimeStatus = "RUNNING";
    mocks.record.lastError = "";
    mocks.record.service = "agent-demo-agent";
    const logsTitle = enAgentsMessages["agents.provisioning.serviceLogs"] ?? "Service logs";

    render(<AgentProvisioning agentId="agent-1" pane="service" />);

    expect(screen.getByTestId("service-row").textContent).toBe("agent-demo-agent");
    expect(screen.queryByText("Service actions")).toBeNull();
    expect(screen.getByText(logsTitle)).toBeTruthy();
  });

  test("renders the workspace row and source git status without workspace logs", () => {
    mocks.record.lifecycle = "READY";
    mocks.record.runtimeStatus = "RUNNING";
    mocks.record.lastError = "";
    mocks.record.workspace = "agent-demo-workspace";
    mocks.workspaceStatus = {
      sources: [
        {
          slot: "main",
          source: "notes",
          state: "ready",
          branch: "workspace/demo",
          dirty: false,
          ahead: 2,
          behind: 1,
          path: "/workspace",
        },
      ],
    };
    const logsTitle = enAgentsMessages["agents.provisioning.workspaceLogs"] ?? "Workspace logs";

    render(<AgentProvisioning agentId="agent-1" pane="workspace" />);

    expect(screen.getByTestId("workspace-row").textContent).toBe("agent-demo-workspace");
    expect(screen.queryByText(logsTitle)).toBeNull();
    expect(screen.getByText(enAgentsMessages["agents.provisioning.workspaceSources"] ?? "Sources")).toBeTruthy();
    expect(screen.getByText("notes")).toBeTruthy();
    expect(screen.getByText("+2 / -1")).toBeTruthy();
  });
});
