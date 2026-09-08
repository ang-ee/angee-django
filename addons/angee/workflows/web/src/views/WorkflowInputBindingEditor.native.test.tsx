// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine, type DataProvider } from "@angee/refine";
import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";
import { afterEach, describe, expect, test, vi } from "vitest";

const query = {
  current: { data: undefined as unknown, error: null as Error | null },
  transport: null as null | ((variables: unknown) => Promise<unknown>),
};
vi.mock("../documents.console", async (importOriginal) => ({
  ...await importOriginal<typeof import("../documents.console")>(),
  WorkflowInputSourcesDocument: {
    kind: "Document",
    definitions: [{ kind: "OperationDefinition", operation: "query", selectionSet: { kind: "SelectionSet", selections: [{ kind: "Field", name: { kind: "Name", value: "workflow_input_sources" } }] } }],
  } as never,
}));

import { WorkflowInputBindingEditor } from "./WorkflowInputBindingEditor";
import { inputPreviewRequest, WorkflowInputPreviewProvider } from "./workflow-input-preview";
import type { WorkflowDefinitionValues } from "./workflow-definition-state";

afterEach(cleanup);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function QueryProviders({ children }: { children: React.ReactNode; }) {
  const provider = React.useMemo(() => ({
    getApiUrl: () => "test://workflow-input",
    getList: vi.fn(), getOne: vi.fn(), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
    custom: vi.fn(async ({ meta }: { meta?: { gqlVariables?: unknown; }; }) => {
      if (query.transport) return { data: await query.transport(meta?.gqlVariables) };
      if (query.current.error) throw query.current.error;
      return { data: query.current.data };
    }),
  } as unknown as DataProvider), []);
  return <Refine dataProvider={provider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>{children}</Refine>;
}

function renderEditor(value: Record<string, unknown> | null, onStructuralChange = vi.fn()) {
  render(<QueryProviders><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowInputPreviewProvider value={{ prepare: () => ({ workflow: "workflow_1", expectedRevision: 1, edit: {}, target: { id: "target" } }), stale: vi.fn() }}><WorkflowInputBindingEditor
    nodeKey="target" value={value} readOnly={false} messages={[]} onChange={() => undefined}
    onCommit={() => undefined} onStructuralChange={onStructuralChange} onFocused={() => undefined}
  /></WorkflowInputPreviewProvider></AppRuntimeProvider></QueryProviders>);
  return onStructuralChange;
}

describe("WorkflowInputBindingEditor", () => {
  test.beforeEach(() => {
    query.current = { data: undefined, error: null };
    query.transport = null;
  });
  test("opening Input leaves Automatic absent until the user chooses a mode", () => {
    const change = renderEditor(null);
    expect(screen.getByText("Automatic input")).toBeTruthy();
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Map input" }));
    expect(change).toHaveBeenCalledWith({});
  });

  test("mapping focuses the first mode and a saved source uses its current name and navigation", async () => {
    const goToSource = vi.fn();
    const { rerender } = render(
      <QueryProviders><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <WorkflowInputBindingEditor
          nodeKey="target"
          value={null}
          readOnly={false}
          messages={[]}
          onChange={() => undefined}
          onCommit={() => undefined}
          onStructuralChange={() => undefined}
          onFocused={() => undefined}
        />
      </AppRuntimeProvider></QueryProviders>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Map input" }));
    rerender(
      <QueryProviders><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <WorkflowInputBindingEditor
          nodeKey="target"
          value={{}}
          readOnly={false}
          messages={[]}
          onChange={() => undefined}
          onCommit={() => undefined}
          onStructuralChange={() => undefined}
          onFocused={() => undefined}
        />
      </AppRuntimeProvider></QueryProviders>,
    );
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Literal value" })));
    rerender(
      <QueryProviders><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <WorkflowInputBindingEditor
          nodeKey="target"
          value={{ kind: "step_output", step_key: "wait", path: [] }}
          readOnly={false}
          messages={[]}
          sourceLabel={() => "Wait checkpoint"}
          onGoToSource={goToSource}
          onChange={() => undefined}
          onCommit={() => undefined}
          onStructuralChange={() => undefined}
          onFocused={() => undefined}
        />
      </AppRuntimeProvider></QueryProviders>,
    );
    expect(screen.getByText("Wait checkpoint")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Go to source" }));
    expect(goToSource).toHaveBeenCalledWith("wait");
  });

  test("Literal mode stays incomplete until null or a value is explicitly chosen", () => {
    const change = renderEditor({});
    fireEvent.click(screen.getByRole("button", { name: "Literal value" }));
    expect(change).toHaveBeenCalledWith({ kind: "constant" });
  });

  test("literal controls keep visible labels and the null label toggles its shared checkbox", () => {
    const change = renderEditor({ kind: "constant", value: "kept" });
    expect(screen.getAllByText("Literal value").length).toBeGreaterThan(1);
    fireEvent.click(screen.getByText("Use null"));
    expect(change).toHaveBeenCalledWith({ kind: "constant", value: null });
  });

  test("sibling literal labels target only their own checkbox", () => {
    const change = renderEditor({
      kind: "array",
      items: [
        { kind: "constant", value: "first" },
        { kind: "constant", value: "second" },
      ],
    });
    const labels = screen.getAllByText("Use null");
    expect(labels).toHaveLength(2);
    fireEvent.click(labels[1]!);
    expect(change).toHaveBeenCalledWith({
      kind: "array",
      items: [
        { kind: "constant", value: "first" },
        { kind: "constant", value: null },
      ],
    });
  });

  test("Literal value records explicit null and object keys remain literal", () => {
    const change = renderEditor({ kind: "object", fields: { "a.b[]/kind": { kind: "constant", value: null } } });
    expect(screen.getByText("a.b[]/kind")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]!);
    expect(change).toHaveBeenCalledWith({ kind: "object", fields: {} });
  });

  test("object and array construction label fields, retain focus, and change nested type atomically", async () => {
    const changes = vi.fn();
    function Harness() {
      const [value, setValue] = React.useState<Record<string, unknown>>({ kind: "object", fields: {} });
      return <QueryProviders><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowInputBindingEditor
        nodeKey="target" value={value} readOnly={false} messages={[]}
        onChange={(next) => setValue(next as Record<string, unknown>)} onCommit={() => undefined}
        onStructuralChange={(next) => { changes(next); setValue(next as Record<string, unknown>); }}
        onFocused={() => undefined}
      /></AppRuntimeProvider></QueryProviders>;
    }
    render(<Harness />);
    const field = screen.getByRole("combobox", { name: "Field key" });
    fireEvent.change(field, { target: { value: "a.b[]/kind" } });
    fireEvent.click(screen.getByRole("button", { name: "Add field" }));
    const literalChoices = screen.getAllByRole("button", { name: "Literal value" });
    await waitFor(() => expect(document.activeElement).toBe(literalChoices.at(-1)));
    changes.mockClear();
    fireEvent.click(literalChoices.at(-1)!);
    const type = await screen.findByRole("combobox", { name: "Change" });
    expect(document.activeElement).toBe(type);
    expect(changes).toHaveBeenCalledTimes(1);
    changes.mockClear();
    fireEvent.click(type);
    const arrayOption = await screen.findByRole("option", { name: "Array" });
    fireEvent.pointerDown(arrayOption);
    fireEvent.pointerUp(arrayOption);
    fireEvent.click(arrayOption);
    expect(changes).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(await screen.findByRole("combobox", { name: "Change" }));
    fireEvent.click(screen.getByRole("button", { name: "Add item" }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getAllByRole("button", { name: "Literal value" }).at(-1)));
  });

  test("nested references use readable source identity and navigate", () => {
    const goToSource = vi.fn();
    render(<QueryProviders><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowInputBindingEditor
      nodeKey="target" value={{ kind: "object", fields: { nested: { kind: "step_output", step_key: "wait", path: [] } } }}
      readOnly={false} messages={[]} sourceLabel={() => "Wait checkpoint"} onGoToSource={goToSource}
      onChange={() => undefined} onCommit={() => undefined} onStructuralChange={() => undefined} onFocused={() => undefined}
    /></AppRuntimeProvider></QueryProviders>);
    expect(screen.getByText("Wait checkpoint")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Go to source" }));
    expect(goToSource).toHaveBeenCalledWith("wait");
  });

  test("a nested reference diagnostic with a literal key focuses Change", () => {
    const focused = vi.fn();
    render(<QueryProviders><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowInputBindingEditor
      nodeKey="target" value={{ kind: "object", fields: { "a.b[]/kind": { kind: "step_output", step_key: "source", path: ["x.y"] } } }}
      readOnly={false} messages={[]} detailPath={["fields", "a.b[]/kind", "path"]}
      onChange={() => undefined} onCommit={() => undefined} onStructuralChange={() => undefined} onFocused={focused}
    /></AppRuntimeProvider></QueryProviders>);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Change" }));
    expect(focused).toHaveBeenCalledOnce();
  });

  test("stages a reference and requires an explicit safe array index", async () => {
    query.current = { data: { workflow_input_sources: { status: "SUCCESS", revision: 1, current_revision: null, diagnostics: [], sources: [{ kind: "workflow_input", id: null, client_key: null, step_key: null, label: "Workflow input", contract: { raw_schema: {}, root_node_id: 0, nodes: [{ id: 0, kind: "array", json_type: null, title: null, description: null, nullable: false }, { id: 1, kind: "scalar", json_type: "string", title: "Entry", description: null, nullable: false }], edges: [{ parent_node_id: 0, child_node_id: 1, kind: "item", key: null }] } }] } }, error: null };
    const change = renderEditor({});
    fireEvent.click(screen.getByRole("button", { name: "Reference" }));
    fireEvent.click(await screen.findByText("Array item"));
    const index = screen.getByRole("textbox", { name: "Array index" });
    fireEvent.change(index, { target: { value: "-1.5" } });
    expect((index as HTMLInputElement).value).toBe("-1.5");
    expect((screen.getByRole("button", { name: "Use value" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(index, { target: { value: "2" } });
    fireEvent.click(screen.getByText("Item 3"));
    fireEvent.click(screen.getByRole("button", { name: "Use value" }));
    expect(change).toHaveBeenLastCalledWith({ kind: "workflow_input", path: [2] });
  });

  test("query errors retry and Cancel never changes the binding", async () => {
    query.current = { data: undefined, error: new Error("Unavailable") };
    const change = renderEditor({});
    fireEvent.click(screen.getByRole("button", { name: "Reference" }));
    expect(await screen.findByText("Input sources are unavailable. Try again.")).toBeTruthy();
    expect(screen.queryByText("Unavailable")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(change).not.toHaveBeenCalled();
  });

  test.each(["SUCCESS", "STALE"] as const)("a late A %s response cannot enter the B picker", async (lateStatus) => {
    const requests = new Map<string, ReturnType<typeof deferred<unknown>>>();
    query.transport = async (variables) => {
      const target = (variables as { target: { client_key: string; }; }).target.client_key;
      const request = deferred<unknown>();
      requests.set(target, request);
      return request.promise;
    };
    const changed = vi.fn();
    const stale = vi.fn();
    const Wrapper = ({ nodeKey }: { nodeKey: string; }) => (
      <QueryProviders><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <WorkflowInputPreviewProvider value={{
          prepare: (target) => ({ workflow: "workflow_1", expectedRevision: 1, edit: {}, target: { client_key: target } }),
          stale,
        }}>
          <WorkflowInputBindingEditor nodeKey={nodeKey} value={{}} readOnly={false} messages={[]}
            onChange={() => undefined} onCommit={() => undefined} onStructuralChange={changed} onFocused={() => undefined} />
        </WorkflowInputPreviewProvider>
      </AppRuntimeProvider></QueryProviders>
    );
    const view = render(<Wrapper nodeKey="target-a" />);
    fireEvent.click(screen.getByRole("button", { name: "Reference" }));
    await waitFor(() => expect(requests.has("target-a")).toBe(true));
    view.rerender(<Wrapper nodeKey="target-b" />);
    await waitFor(() => expect(requests.has("target-b")).toBe(true));

    const response = (status: "SUCCESS" | "STALE", key: string, label: string) => ({
      workflow_input_sources: {
        status,
        revision: status === "SUCCESS" ? 1 : null,
        current_revision: status === "STALE" ? 2 : null,
        diagnostics: [],
        sources: status === "SUCCESS" ? [{
          kind: "step_output", id: `node-${key}`, client_key: null, step_key: key, label,
          contract: { raw_schema: {}, root_node_id: 0, nodes: [{ id: 0, kind: "scalar", json_type: "string", title: null, description: null, nullable: false }], edges: [] },
        }] : [],
      },
    });
    await act(async () => { requests.get("target-b")!.resolve(response("SUCCESS", "source-b", "B source")); });
    expect(await screen.findByText("B source")).toBeTruthy();
    await act(async () => { requests.get("target-a")!.resolve(response(lateStatus, "source-a", "A source")); });
    expect(screen.queryByText("A source")).toBeNull();
    expect(screen.getByText("B source")).toBeTruthy();
    expect(stale).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("B source"));
    fireEvent.click(screen.getByRole("button", { name: "Use value" }));
    expect(changed).toHaveBeenLastCalledWith({ kind: "step_output", step_key: "source-b", path: [] });
  });
});

test("prospective sources reuse the acknowledged baseline and exact client identity", () => {
  const node = { id: "", clientKey: "target", key: "target", name: "Target", step_class: "wait", config: {}, config_errors: {}, input_binding: null, join_rule: "ALL_SUCCESS", is_entry: true, position: {} };
  const baseline = { id: "workflow_1", definition: { revision: 7, nodes: {}, edges: {}, readiness: [] } } as unknown as WorkflowDefinitionValues;
  const current = structuredClone(baseline);
  current.definition.nodes.target = { ...node, input_binding: { kind: "constant", value: null } };
  expect(inputPreviewRequest("workflow_1", baseline, current, "target")).toEqual({
    workflow: "workflow_1", expectedRevision: 7, target: { client_key: "target" },
    edit: expect.objectContaining({ node_creates: [expect.objectContaining({ client_key: "target", fields: expect.objectContaining({ input_binding: { kind: "constant", value: null } }) })] }),
  });
});
