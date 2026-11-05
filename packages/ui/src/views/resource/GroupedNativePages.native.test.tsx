// @vitest-environment happy-dom

import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";
import { useGroupedNativePages } from "./collection-source";

const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
  const variables = meta?.gqlVariables as Record<string, unknown>;
  return { data: { data: {
    rows: [{ id: "queued-2", status: "queued" }, { id: "done-1", status: "done" }],
    variables,
  } } };
});
const { Provider, clearClients } = createUiTestProviders({
  dataProvider: { custom },
  providerNames: ["console"],
  queryClientConfig: { defaultOptions: { queries: { retry: false } } },
});

afterEach(() => { cleanup(); clearClients(); custom.mockClear(); });

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
