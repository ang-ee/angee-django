// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listProps: null as Record<string, unknown> | null,
  choice: { key: "wait", category: "Flow", defaults: {}, config_schema: null },
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: () => ({
    isFetching: false,
    data: { workflow_step_operations: [{
      key: "wait", label: "Wait", effect: "NONE", effect_description: "Pauses execution",
      idempotent: true, subject_declaration: "", outcomes: [{ key: "done", label: "Done" }],
      input_schema: { type: "object" }, output_schema: { type: "string" },
      input_contract: { root_node_id: 1, raw_schema: { type: "object" }, nodes: [{ id: 1, kind: "object", json_type: "object", title: "Input", description: null, nullable: false }], edges: [] },
      output_contract: { root_node_id: 1, raw_schema: { type: "string" }, nodes: [{ id: 1, kind: "scalar", json_type: "string", title: "Output", description: null, nullable: false }], edges: [] },
    }] },
  }),
}));

vi.mock("@angee/ui", () => ({
  useImplementationDetailContext: () => ({ model: "workflows.Step", field: "step_class", choice: mocks.choice }),
  useRouteHref: () => (name: string, params?: { id?: string }) => `/${name}/${params?.id ?? ""}`,
  Code: ({ children }: { children: unknown }) => <code>{String(children)}</code>,
  CodeBlock: ({ children }: { children: unknown }) => <pre>{String(children)}</pre>,
  ControlBandProvider: ({ children }: { children: unknown }) => <>{children as never}</>,
  DetailSection: ({ title, rows }: { title: string; rows: readonly (readonly [string, unknown])[] }) => <section><h2>{title}</h2>{rows.map(([label, value]) => <div key={label}>{label}{value as never}</div>)}</section>,
  LoadingPanel: ({ message }: { message: string }) => <div>{message}</div>,
  TextLink: ({ children, href }: { children: unknown; href: string }) => <a href={href}>{children as never}</a>,
  ListView: (props: Record<string, unknown>) => { mocks.listProps = props; return <div>usage-list</div>; },
}));

vi.mock("../i18n", () => ({ useWorkflowsT: () => (key: string) => key }));
vi.mock("../documents.console", () => ({ WorkflowStepOperationsDocument: "WorkflowStepOperations" }));

import { WorkflowImplementationDetails } from "./WorkflowImplementationDetails";

describe("WorkflowImplementationDetails", () => {
  beforeEach(() => { mocks.listProps = null; });

  test("shows declared contracts and scopes configured usage to the implementation key", () => {
    render(<WorkflowImplementationDetails />);
    expect(screen.getByText("stepTypes.contracts")).toBeTruthy();
    expect(screen.getByText("stepTypes.behavior")).toBeTruthy();
    expect(screen.getByText("usage-list")).toBeTruthy();
    expect(mocks.listProps).toMatchObject({
      resource: "workflows.Step",
      baseFilter: { step_class: { exact: "wait" } },
      selectable: false,
    });
  });
});
