// @vitest-environment happy-dom

import {
  cleanup,
  render,
  screen } from "@testing-library/react";
import type { ReactElement,
  ReactNode } from "react";
import { afterEach,
  beforeEach,
  describe,
  expect,
  test,
  vi } from "vitest";
import {
  ModelMetadataProvider,
} from "@angee/resources";
import type {
  SchemaFieldMetadata,
} from "@angee/resources";

import { enAgentsMessages } from "../i18n";
import { AgentProvisioning } from "./AgentProvisioning";

const mocks = vi.hoisted(() => ({
  refetch: vi.fn(),
  record: {
    id: "agent-1",
    lifecycle: "DRAFT",
    runtime_status: "ERROR",
    last_error: "operator POST workspaces: HTTP 409: workspace demo-agent conflicts: already exists",
    workspace: "",
    service: "",
    workspace_template: { path: "workspaces/agent-default" },
    runtime_class: "CLAUDE_CODE",
  },
  workspaceStatus: null as {
    error?: string | null;
    innerError?: string | null;
    sources: Array<Record<string, unknown>>;
  } | null,
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useNamespaceT:
      (_namespace: string, messages: Record<string, string>) =>
      (key: string): string =>
        messages[key] ?? key,
    Card: ({ children }: { children: ReactNode }) => <article>{children}</article>,
    CardContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    CardHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
    CardTitle: ({ children }: { children: ReactNode }) => <h3>{children}</h3>,
  };
});

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useOne: () => ({
      result: mocks.record,
      query: {
        isFetching: false,
        error: null,
        refetch: mocks.refetch,
      },
    }),
  };
});

vi.mock("@angee/operator/runtime", () => ({
  OperatorTransportProvider: ({ children }: { children: ReactNode }) => children,
  ServiceRow: ({ name }: { name: string }) => (
    <section data-testid="service-row">{name}</section>
  ),
  ServiceLogs: ({ name, title }: { name: string; title?: ReactNode }) => (
    <section data-testid="service-logs" data-name={name}>{title}</section>
  ),
  WorkspaceRow: ({ name }: { name: string }) => (
    <section data-testid="workspace-row">{name}</section>
  ),
  WorkspaceSources: ({
    sources,
    title,
  }: {
    sources: Array<Record<string, unknown>>;
    title?: ReactNode;
  }) => (
    <section data-testid="workspace-sources">
      <h3>{title}</h3>
      {sources.map((source) => (
        <p key={`${source.slot}:${source.source}`}>
          <span>{String(source.source)}</span>
          <span>{`+${source.ahead ?? 0} / -${source.behind ?? 0}`}</span>
        </p>
      ))}
    </section>
  ),
  useOperatorSnapshot: () => ({
    result: {},
    snapshot: null,
  }),
  useWorkspaceStatus: () => ({
    status: mocks.workspaceStatus,
    error: null,
    fetching: false,
  }),
}));

beforeEach(() => {
  // A failed provision that rolled back: lifecycle reset to DRAFT, run state ERROR.
  mocks.record.lifecycle = "DRAFT";
  mocks.record.runtime_status = "ERROR";
  mocks.record.last_error =
    "operator POST workspaces: HTTP 409: workspace demo-agent conflicts: already exists";
  mocks.record.workspace = "";
  mocks.record.service = "";
  mocks.workspaceStatus = null;
});

afterEach(cleanup);

describe("AgentProvisioning", () => {
  test("shows the saved provisioning error even without recorded runtime names", () => {
    const intro = enAgentsMessages["agents.provisioning.intro"] ?? "";

    renderProvisioning(<AgentProvisioning agentId="agent-1" pane="service" />);

    expect(screen.queryByRole("heading", { name: "Service" })).toBeNull();
    expect(screen.getByText(String(mocks.record.last_error))).toBeTruthy();
    expect(screen.getByText(intro)).toBeTruthy();
  });

  test("renders the service row first and service logs underneath", () => {
    mocks.record.lifecycle = "READY";
    mocks.record.runtime_status = "RUNNING";
    mocks.record.last_error = "";
    mocks.record.service = "agent-demo-agent";
    const logsTitle = enAgentsMessages["agents.provisioning.serviceLogs"] ?? "Service logs";

    renderProvisioning(<AgentProvisioning agentId="agent-1" pane="service" />);

    expect(screen.getByTestId("service-row").textContent).toBe("agent-demo-agent");
    expect(screen.queryByText("Service actions")).toBeNull();
    expect(screen.getByText(logsTitle)).toBeTruthy();
  });

  test("renders the workspace row and source git status without workspace logs", () => {
    mocks.record.lifecycle = "READY";
    mocks.record.runtime_status = "RUNNING";
    mocks.record.last_error = "";
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

    renderProvisioning(<AgentProvisioning agentId="agent-1" pane="workspace" />);

    expect(screen.getByTestId("workspace-row").textContent).toBe("agent-demo-workspace");
    expect(screen.queryByText(logsTitle)).toBeNull();
    expect(screen.getByText(enAgentsMessages["agents.provisioning.workspaceSources"] ?? "Sources")).toBeTruthy();
    expect(screen.getByText("notes")).toBeTruthy();
    expect(screen.getByText("+2 / -1")).toBeTruthy();
  });
});

const AGENT_METADATA: SchemaFieldMetadata = {
  types: {
    AgentType: {
      typeName: "AgentType",
      fields: {},
      rootFields: {
        detail: "agent",
        list: "agents",
      },
      resource: {
        schemaName: "console",
        modelLabel: "agents.Agent",
        appLabel: "agents",
        modelName: "Agent",
        publicIdField: "id",
        roots: { detail: "agent", list: "agents" },
        typeNames: {
          node: "AgentType",
          filter: "AgentFilter",
          order: "AgentOrder",
        },
        capabilities: ["detail", "list"],
        filterFields: [],
        orderFields: [],
        aggregateFields: [],
        groupByFields: [],
        relationAxes: [],
      },
    },
  },
};

function renderProvisioning(children: ReactElement): ReturnType<typeof render> {
  return render(
    <ModelMetadataProvider metadata={AGENT_METADATA}>
      {children}
    </ModelMetadataProvider>,
  );
}
