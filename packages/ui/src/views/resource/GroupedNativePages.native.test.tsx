// @vitest-environment happy-dom

import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";
import { useGroupedNativePages } from "./collection-source";

const defaultCustom = async ({ meta }: { meta?: Record<string, unknown> }) => {
  const variables = meta?.gqlVariables as Record<string, unknown>;
  return { data: { data: {
    rows: [{ id: "queued-2", status: "queued" }, { id: "done-1", status: "done" }],
    variables,
  } } };
};
const custom = vi.fn(defaultCustom);
const { Provider, clearClients } = createUiTestProviders({
  dataProvider: { custom },
  providerNames: ["console"],
  queryClientConfig: { defaultOptions: { queries: { retry: false } } },
});

afterEach(() => { cleanup(); clearClients(); custom.mockReset(); custom.mockImplementation(defaultCustom); });

test("grouped native pages travel in one document with independent page bounds", async () => {
  const scopes = [
    { key: "queued", value: "queued", identity: "queued", groupBy: { field: "STATUS" }, parentWhere: undefined, orderBy: undefined, page: 2, pageSize: 5, total: 8 },
    { key: "done", value: "done", identity: "done", groupBy: { field: "STATUS" }, parentWhere: undefined, orderBy: undefined, page: 1, pageSize: 20, total: 3 },
  ];
  const { result } = renderHook(() => useGroupedNativePages({
    root: "workflow_runs",
    aggregateRoot: "workflow_runs_aggregate",
    groupedRowsRoot: "workflow_runs_grouped_rows",
    groupByType: "WorkflowRunGroupBySpec",
    groupedRowPageType: "WorkflowRunGroupedRowPage",
    filterType: "WorkflowRunBoolExp",
    orderType: "WorkflowRunOrderBy",
    dataProviderName: "console",
    resourceIdentifier: "workflows/WorkflowRun",
    resourceName: "workflow_runs",
  }, scopes, ["id", "status"], undefined, (row) => row.status), { wrapper: Provider });
  await waitFor(() => expect(result.current.get("queued")?.rows).toEqual([{ id: "queued-2", status: "queued" }]));
  expect(result.current.get("queued")?.total).toBe(8);
  expect(result.current.get("done")?.rows).toEqual([{ id: "done-1", status: "done" }]);
  expect(result.current.get("done")?.total).toBe(3);
  expect(custom).toHaveBeenCalledTimes(1);
  const variables = custom.mock.calls[0]![0].meta?.gqlVariables as Record<string, unknown>;
  expect(variables).toMatchObject({
    pages: [{ key: "queued", limit: 5, offset: 5 }, { key: "done", limit: 20, offset: 0 }],
  });
});

test("a parent with 100 expanded groups reads every page in batches of at most 50", async () => {
  const scopes = Array.from({ length: 100 }, (_, index) => ({
    key: `group-${index}`, value: `group-${index}`, identity: `group-${index}`,
    groupBy: { field: "STATUS" }, page: 1, pageSize: 5, total: 1,
  }));
  custom.mockImplementation(async ({ meta }) => {
    const variables = meta?.gqlVariables as { pages: { key: string }[] };
    return { data: { data: { rows: variables.pages.map(({ key }) => ({ id: key, status: key })), variables } } };
  });
  const { result } = renderHook(() => useGroupedNativePages({
    root: "workflow_runs", aggregateRoot: "workflow_runs_aggregate",
    groupedRowsRoot: "workflow_runs_grouped_rows", groupByType: "WorkflowRunGroupBySpec",
    groupedRowPageType: "WorkflowRunGroupedRowPage", filterType: "WorkflowRunBoolExp",
    orderType: "WorkflowRunOrderBy", dataProviderName: "console",
    resourceIdentifier: "workflows/WorkflowRun", resourceName: "workflow_runs",
  }, scopes, ["id", "status"], undefined, (row) => row.status), { wrapper: Provider });
  await waitFor(() => expect(result.current.get("group-99")?.rows).toEqual([{ id: "group-99", status: "group-99" }]));
  expect(custom).toHaveBeenCalledTimes(2);
  expect(custom.mock.calls.map(([request]) => ((request.meta?.gqlVariables as { pages: unknown[] }).pages.length))).toEqual([50, 50]);
  for (const scope of scopes) {
    expect(result.current.get(scope.key)?.rows).toEqual([{ id: scope.key, status: scope.key }]);
    expect(result.current.get(scope.key)?.error).toBeNull();
  }
});
