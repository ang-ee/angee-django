// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ResourceQuery } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";

import { ToastProvider } from "../../feedback";
import { ListView } from "./ListView";

// A message's parts, as the message form's Parts tab lists them: grouped by role,
// with role and disposition as categorical (scalar-facet) fields.
const contract = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, message: { scalar: "ID" }, name: { scalar: "String" }, status: { scalar: "String" },
  role: { kind: "enum", values: [{ value: "BODY" }, { value: "QUOTED" }] },
  disposition: { kind: "enum", values: [{ value: "INLINE" }, { value: "ATTACHMENT" }] },
} }).contract;
for (const field of ["role", "disposition", "status"]) {
  contract.axes[field]!.server = { input: field.toUpperCase(), key: field };
  contract.axes[field]!.drill = { kind: "value", field, valueKey: field, nullMode: "isNull", valueMap: [] };
}
const resource = testDataResource("messaging.Part", {
  roots: { groups: "parts_groups", groupedRows: "parts_grouped_rows", aggregate: "parts_aggregate" },
  typeNames: { filter: "PartBoolExp", order: "PartOrderBy", groupBySpec: "PartGroupBySpec", groupedRowPage: "PartGroupedRowPage" }, query: contract,
  fields: Object.entries(contract.fields).map(([name, field]) => ({ name,
    kind: field.kind === "enum" ? "enum" : "scalar", scalar: field.scalar, values: field.values,
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false })),
});
type Where = { _and?: Where[]; message?: { _eq?: string } };
type GroupVariables = { group_by: { field: string }[]; where?: Where; limit: number };
const terms = (where: Where = {}): Where[] => [where, ...(where._and ?? []).flatMap(terms)];
const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://base-filter",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

test("a base-filtered list reads its visible group while enum facets need no request", async () => {
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const variables = meta!.gqlVariables as GroupVariables & Record<string, unknown>;
    if ("pages" in variables) return { data: { data: { rows: [] } } };
    const key = variables.group_by[0]!.field.toLowerCase();
    return { data: { parts_groups: [{ key: { [key]: "BODY" }, aggregate: { count: 2 } }], totalCount: 1 } };
  });
  const getList = vi.fn(async () => ({ data: [], total: 0 }));
  render(
    <Provider resources={[resource]} dataProvider={{ custom, getList }}>
      <OperationDocumentsProvider documents={{ console: { groups: { "messaging.Part": "query PartGroups { parts_groups { key } totalCount }" } } }}>
        <ToastProvider>
          <ListView resource={resource.modelLabel} scope="local" baseFilter={{ message: { exact: "msg_1" } }}
            columns={[{ field: "role" }, { field: "name" }, { field: "status", widget: "statusBadge" }]}
            defaultGroups={{ list: { field: "role" } }} emptyContent="No parts" />
        </ToastProvider>
      </OperationDocumentsProvider>
    </Provider>,
  );
  const requests = () => custom.mock.calls.map(([request]) => request.meta!.gqlVariables as GroupVariables & Record<string, unknown>);
  const groupRequests = () => requests().filter((variables) => Array.isArray(variables.group_by));
  const axes = () => [...new Set(groupRequests().map(({ group_by, limit }) => `${group_by[0]!.field}:${limit}`))]
    .map((request) => request.split(":")[0]).sort();
  // The visible group reads once; enum choices come from metadata even while closed.
  await waitFor(() => expect(axes()).toEqual(["ROLE"]));
  fireEvent.click(await screen.findByRole("button", { name: "Search options" }));
  await waitFor(() => expect(axes()).toEqual(["ROLE", "STATUS"]));
  for (const variables of requests()) {
    const where = variables.where as Where | undefined;
    expect(terms(where)).toContainEqual({ message: { _eq: "msg_1" } });
  }
});
