// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import type { DataResourceFieldMetadata } from "@angee/metadata";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { ResourceList } from "./ResourceList";

function scalarField(name: string, scalar: string): DataResourceFieldMetadata {
  return { name, kind: "scalar", scalar, readable: true, aggregatable: false, creatable: false, updatable: false,
    requiredOnCreate: false };
}

const resource = testDataResource("notes.Note", {
  roots: { list: "notes", detail: "notes_by_pk", aggregate: "notes_aggregate" },
  typeNames: { filter: "NoteBoolExp", order: "NoteOrderBy" },
  fields: [scalarField("title", "String"), scalarField("is_held", "Boolean"), scalarField("revision", "Int")],
  query: testResourceQuery({ fields: {
    id: testQueryField("id"),
    title: testQueryField("title"),
    is_held: testQueryField("is_held", {
      scalar: "Boolean", filter: { field: "is_held", scalar: "Boolean", values: [], operators: ["exact", "isNull"] },
    }),
    revision: testQueryField("revision", { scalar: "Int" }),
  } }),
});
const { Provider, clearClients } = createUiTestProviders({
  resources: [resource], apiUrl: "test://resource-list-filter-option-fields",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

const HELD = { is_held: true } as const;

test.each([true, false])("a filter option's fields are selected only while its filter is active (active=%s)", async (active) => {
  const getList = vi.fn(async (_request: GetListParams) => (
    { data: [{ id: "note-1", title: "Held note", revision: 3 }], total: 1 }
  ));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Provider dataProvider={{ getList }}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <ResourceList resource={resource.modelLabel} scope="local" hideCreate columns={[{ field: "title" }]}
        filterOptions={[{ id: "held", label: "Held", filter: HELD, fields: ["revision"] }]}
        {...(active ? { defaultFilter: HELD } : {})} />
    </AppRuntimeProvider></ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>);

  await screen.findByText("Held note");
  const requested = getList.mock.calls.map(([request]) => (request.meta?.fields ?? []) as readonly string[]);
  expect(requested.length).toBeGreaterThan(0);
  expect(requested.every((fields) => fields.includes("revision"))).toBe(active);
});
