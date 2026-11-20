// @vitest-environment happy-dom

import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { ResourceQuery, useModelMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";

import { ToastProvider } from "../../feedback";
import { ListView } from "./ListView";
import { useScalarFacets } from "../relation/scalar-facet";

// A message's parts, as the message form's Parts tab lists them: grouped by role,
// with role and disposition as categorical (scalar-facet) fields.
const contract = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, message: { scalar: "ID" }, name: { scalar: "String" },
  role: { kind: "enum", values: [{ value: "BODY" }, { value: "QUOTED" }] },
  disposition: { kind: "enum", values: [{ value: "INLINE" }, { value: "ATTACHMENT" }] },
} }).contract;
for (const field of ["role", "disposition"]) {
  contract.axes[field]!.server = { input: field.toUpperCase(), key: field };
  contract.axes[field]!.drill = { kind: "value", field, valueKey: field, nullMode: "isNull", valueMap: [] };
}
const resource = testDataResource("messaging.Part", {
  roots: { groups: "parts_groups", aggregate: "parts_aggregate" },
  typeNames: { filter: "PartBoolExp", order: "PartOrderBy" }, query: contract,
  fields: Object.entries(contract.fields).map(([name, field]) => ({ name,
    kind: field.kind === "enum" ? "enum" : "scalar", scalar: field.scalar, values: field.values,
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false })),
});
type Where = { _and?: Where[]; message?: { _eq?: string } };
type GroupVariables = { group_by: { field: string }[]; where?: Where; limit: number };
const terms = (where: Where = {}): Where[] => [where, ...(where._and ?? []).flatMap(terms)];
const { Provider, clients, clearClients } = createUiTestProviders({
  apiUrl: "test://base-filter",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

test("a base-filtered list scopes its group counts and every facet count to the base filter", async () => {
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const key = (meta!.gqlVariables as GroupVariables).group_by[0]!.field.toLowerCase();
    return { data: { parts_groups: [{ key: { [key]: "BODY" }, aggregate: { count: 2 }, rows: [] }], totalCount: 1 } };
  });
  const getList = vi.fn(async () => ({ data: [], total: 0 }));
  render(
    <Provider resources={[resource]} dataProvider={{ custom, getList }}>
      <OperationDocumentsProvider documents={{ console: { groups: { "messaging.Part": "query PartGroups { parts_groups { key } totalCount }" } } }}>
        <ToastProvider>
          <ListView resource={resource.modelLabel} scope="local" baseFilter={{ message: { exact: "msg_1" } }}
            columns={[{ field: "role" }, { field: "name" }]} defaultGroups={{ list: { field: "role" } }} emptyContent="No parts" />
        </ToastProvider>
      </OperationDocumentsProvider>
    </Provider>,
  );
  const requests = () => custom.mock.calls.map(([request]) => request.meta!.gqlVariables as GroupVariables);
  const axes = () => [...new Set(requests().map(({ group_by, limit }) => `${group_by[0]!.field}:${limit}`))]
    .map((request) => request.split(":")[0]).sort();
  // The list's role groups plus one facet per categorical field, shown as a column or not.
  await waitFor(() => expect(axes()).toEqual(["DISPOSITION", "ROLE", "ROLE"]));
  for (const variables of requests()) {
    expect(terms(variables.where)).toContainEqual({ message: { _eq: "msg_1" } });
  }
});

test("a facets read whose payload its decoder refuses notifies once, keeps the list up, and a later good payload restores its options", async () => {
  let refuse = true;
  // The list is not grouped: its every role read is the role facet's.
  const isRoleFacet = (variables: GroupVariables) => variables.group_by[0]!.field === "ROLE";
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const variables = meta!.gqlVariables as GroupVariables;
    const key = variables.group_by[0]!.field.toLowerCase();
    const groups = [{ key: { [key]: key === "role" ? "BODY" : "INLINE" }, aggregate: { count: 2 } }];
    // While refused, the role facet's read comes back without its total.
    return { data: refuse && isRoleFacet(variables) ? { parts_groups: groups } : { parts_groups: groups, totalCount: 1 } };
  });
  const getList = vi.fn(async () => ({ data: [{ id: "part_1", name: "Body text", role: "BODY", disposition: "INLINE" }], total: 1 }));
  const notify = vi.fn();
  const columns = [{ field: "role" }, { field: "name" }];
  /** The role facet's options, as a search control renders them. */
  function RoleOptions() {
    const facets = useScalarFacets(resource.modelLabel, columns, useModelMetadata(resource.modelLabel));
    return <ul aria-label="Role options">
      {facets.find((facet) => facet.field === "role")?.options.map((option) => <li key={option.id}>{option.label}</li>)}
    </ul>;
  }
  render(
    <Provider resources={[resource]} dataProvider={{ custom, getList }} notificationProvider={{ open: notify, close: vi.fn() }}>
      <OperationDocumentsProvider documents={{ console: { groups: { "messaging.Part": "query PartGroups { parts_groups { key } totalCount }" } } }}>
        <ToastProvider>
          <ListView resource={resource.modelLabel} scope="local" columns={columns} emptyContent="No parts" />
          <RoleOptions />
        </ToastProvider>
      </OperationDocumentsProvider>
    </Provider>,
  );
  const roleOptions = () => within(screen.getByRole("list", { name: "Role options" })).queryAllByRole("listitem");
  const refused = () => notify.mock.calls.filter(([params]) => params.description === "Grouped response requires a numeric totalCount");
  /** Each distinct role facet read the server refused. */
  const refusedReads = () => new Set(custom.mock.calls.map(([request]) => request.meta!.gqlVariables as GroupVariables)
    .filter(isRoleFacet).map((variables) => JSON.stringify(variables)));
  expect(await screen.findByText("Body text")).toBeTruthy();
  await waitFor(() => expect(refused().length).toBeGreaterThan(0));
  expect(refused()[0]![0]).toMatchObject({ type: "error" });
  expect(refused()).toHaveLength(refusedReads().size);
  expect(roleOptions()).toHaveLength(0);
  refuse = false;
  await act(async () => { await clients.at(-1)!.invalidateQueries(); });
  await waitFor(() => expect(roleOptions().map((option) => option.textContent)).toEqual(["Body"]));
  expect(refused()).toHaveLength(refusedReads().size);
  expect(screen.getByText("Body text")).toBeTruthy();
});
