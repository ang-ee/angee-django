// @vitest-environment happy-dom

import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";
import { useGroupedNativePages } from "./collection-source";

const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
  const variables = meta?.gqlVariables as Record<string, unknown>;
  return { data: { data: {
    group_0_rows: [{ id: "queued-2" }],
    group_0_aggregate: { aggregate: { count: 8 } },
    group_1_rows: [{ id: "done-1" }],
    group_1_aggregate: { aggregate: { count: 3 } },
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
    { key: "queued", where: { status: { _eq: "queued" } }, orderBy: undefined, page: 2, pageSize: 5 },
    { key: "done", where: { status: { _eq: "done" } }, orderBy: undefined, page: 1, pageSize: 20 },
  ];
  const { result } = renderHook(() => useGroupedNativePages({
    root: "workflow_runs",
    aggregateRoot: "workflow_runs_aggregate",
    filterType: "WorkflowRunBoolExp",
    orderType: "WorkflowRunOrderBy",
    dataProviderName: "console",
    resourceIdentifier: "workflows/WorkflowRun",
    resourceName: "workflow_runs",
  }, scopes, ["id"], undefined), { wrapper: Provider });
  await waitFor(() => expect(result.current.get("queued")?.rows).toEqual([{ id: "queued-2" }]));
  expect(result.current.get("queued")?.total).toBe(8);
  expect(result.current.get("done")?.rows).toEqual([{ id: "done-1" }]);
  expect(result.current.get("done")?.total).toBe(3);
  expect(custom).toHaveBeenCalledTimes(1);
  const variables = custom.mock.calls[0]![0].meta?.gqlVariables as Record<string, unknown>;
  expect(variables).toMatchObject({
    group_0_limit: 5, group_0_offset: 5,
    group_1_limit: 20, group_1_offset: 0,
  });
});
