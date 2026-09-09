// @vitest-environment happy-dom

import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource, testResourceQuery } from "@angee/metadata/testing";
import { Refine, type DataProvider } from "@angee/refine";
import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets } from "@angee/ui";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, expect, test, vi } from "vitest";

const payloads = vi.hoisted(() => [] as Array<Record<string, unknown>>);
vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAuthoredQuery: (_document: unknown, variables: Record<string, unknown>) => {
      payloads.push(variables);
      return {
        data: { workflow_step_attempts: [{
          id: "attempt-1", input_present: true, input: { one: 1 },
          output_present: true, output: { two: 2 }, checkpoint_present: false,
          error: "", stacktrace: "",
        }] },
        isFetching: false,
        error: null,
      };
    },
  };
});

import { AttemptHistory } from "./RunsPage";

const field = (name: string, scalar = "String") => ({
  name, kind: "scalar" as const, scalar, values: [], readable: true, filterable: true,
  sortable: true, aggregatable: false, groupable: false, creatable: false,
  updatable: false, requiredOnCreate: false,
  filter: { field: name, scalar, values: [], operators: ["exact"] },
});
const resource = testDataResource("workflows.StepAttempt", {
  roots: { aggregate: "workflow_step_attempts_aggregate" },
  modelName: "StepAttempt",
  typeNames: { node: "StepAttemptType", filter: "StepAttemptBoolExp", order: "StepAttemptOrderBy" },
  fields: [
    field("id", "ID"), field("step_run", "ID"), field("ordinal", "Int"), field("status"),
    field("cause"), field("result_kind"), field("outcome"), field("retry_of", "ID"),
    field("retry_index", "Int"), field("applied_at", "DateTime"),
    field("lease_revoked_at", "DateTime"), field("updated_at", "DateTime"),
  ],
  query: testResourceQuery({ fields: {
    id: { kind: "scalar", scalar: "ID", values: [], nullable: false },
    step_run: { kind: "scalar", scalar: "ID", values: [], nullable: false, filter: { field: "step_run", scalar: "ID", values: [], operators: ["exact"] } },
  } }),
});

afterEach(() => { cleanup(); payloads.length = 0; });

test("the native attempt record tabs mount only the selected payload pane", async () => {
  const row = {
    id: "attempt-1", step_run: "execution-1", ordinal: 1, status: "completed",
    cause: "automatic", result_kind: "DONE", outcome: "completed", retry_of: null,
    retry_index: 0, applied_at: "2026-09-09T00:00:00Z", lease_revoked_at: null,
    updated_at: "2026-09-09T00:00:00Z",
  };
  const returnedError = { ...row, id: "attempt-error", ordinal: 2, result_kind: "ERROR", applied_at: null };
  const returnedWait = { ...row, id: "attempt-wait", ordinal: 3, result_kind: "WAIT" };
  const provider = {
    getApiUrl: () => "test://workflows",
    getOne: vi.fn(async () => ({ data: row })),
    getList: vi.fn(async () => ({ data: [returnedError, returnedWait], total: 2 })),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const view = render(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <AttemptHistory executionId="execution-1" attemptId={null} onSelect={vi.fn()} />
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </ModelMetadataProvider></RouterContextProvider>
    </Refine>,
  );
  await waitFor(() => expect(provider.getList).toHaveBeenCalledWith(expect.objectContaining({ pagination: expect.objectContaining({ pageSize: 20 }) })));
  expect(await screen.findByText("Returned · not applied")).toBeTruthy();
  expect(screen.getByText("Error")).toBeTruthy();
  expect(screen.getByText("Wait")).toBeTruthy();
  view.rerender(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <AttemptHistory executionId="execution-1" attemptId="attempt-1" onSelect={vi.fn()} />
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </ModelMetadataProvider></RouterContextProvider>
    </Refine>,
  );
  await waitFor(() => expect(payloads.at(-1)).toEqual(expect.objectContaining({ includeInput: true, includeOutput: false })));
  fireEvent.click(await screen.findByRole("tab", { name: "Output" }));
  await waitFor(() => expect(payloads.at(-1)).toEqual(expect.objectContaining({ includeInput: false, includeOutput: true, includeCheckpoint: false, includeFailure: false })));
});
