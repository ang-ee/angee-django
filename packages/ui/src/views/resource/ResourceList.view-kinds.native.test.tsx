// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider, containersFromChildren, type ComposedContainers, type ContainerRule, type ResourceViewKindContent } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { ResourceList } from "./ResourceList";
import { useResourceView } from "./resource-view-context";
import { RESOURCE_CONTAINERS } from "./resource-view-kinds";

const resource = testDataResource("notes.Note", {
  roots: { list: "notes", detail: "notes_by_pk", aggregate: "notes_aggregate" },
  typeNames: { filter: "NoteBoolExp", order: "NoteOrderBy" },
  fields: [{ name: "title", kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
  query: testResourceQuery({ fields: { id: testQueryField("id"), title: testQueryField("title") } }),
});
const { Provider, clearClients } = createUiTestProviders({
  resources: [resource], apiUrl: "test://resource-view-kinds",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

/** A contributed kind's body reads the collection's state through `useResourceView()`. */
function GraphBody({ resource: model }: { resource: string }) {
  const { state } = useResourceView();
  return <p>Graph of {model} as {state.view}</p>;
}

const graph: ResourceViewKindContent = {
  label: "Graph", icon: "network", render: GraphBody,
  capabilities: { grouping: false, pagination: false, columns: false, filter: true },
};

function composed(rules: (Omit<ContainerRule, "exempt" | "rank"> & { rank?: number })[] = []): ComposedContainers {
  const containers = containersFromChildren(RESOURCE_CONTAINERS, {
    "notes.Note#views": { "nexus.graph": { content: graph, sequence: 15 } },
    "tasks.Task#views": { "nexus.timeline": { content: { ...graph, label: "Timeline" } } },
  });
  return { ...containers, rules: rules.length ? { "resource#views": rules.map((rule, index) => ({ rank: index, ...rule, exempt: [] })) } : {} };
}

function renderCollection(containers: ComposedContainers) {
  const getList = vi.fn(async () => ({ data: [{ id: "note-1", title: "First note" }], total: 1 }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Provider dataProvider={{ getList }}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider>
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets, containers }}>
        <ResourceList resource={resource.modelLabel} scope="local" hideCreate columns={[{ field: "title" }]} />
      </AppRuntimeProvider>
    </ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>);
}

const switcherLabels = async (): Promise<string[]> => {
  const switcher = await screen.findByRole("group", { name: "View switcher" });
  return within(switcher).getAllByRole("button").map((button) => button.getAttribute("aria-label") ?? "");
};

test("the switcher offers the model's contributed kinds among the framework's, in container order", async () => {
  renderCollection(composed());
  await screen.findByText("First note");
  // Calendar and Gantt need page-declared sources; the dashboard needs the aggregate root, which this model has.
  expect(await switcherLabels()).toEqual(["List view", "Graph", "Board view", "Dashboard view"]);
  expect(screen.queryByRole("button", { name: "Timeline" })).toBeNull();
});

test("a contributed kind renders its body under the shared toolbar", async () => {
  renderCollection(composed());
  fireEvent.click(await screen.findByRole("button", { name: "Graph" }));
  expect(await screen.findByText("Graph of notes.Note as nexus.graph")).toBeTruthy();
  expect(screen.queryByText("First note")).toBeNull();
  expect(screen.getByRole("button", { name: "Graph" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "List view" }));
  expect(await screen.findByText("First note")).toBeTruthy();
});

test("layers narrow the offered kinds: only, except and hide, each where its condition holds", async () => {
  renderCollection(composed([{ layer: "notes", only: ["list", "nexus.graph"] }]));
  await waitFor(async () => expect(await switcherLabels()).toEqual(["List view", "Graph"]));
  cleanup();
  renderCollection(composed([{ layer: "notes", except: ["board", "dashboard"] }, { layer: "product", hide: ["nexus.graph"] }]));
  await screen.findByText("First note");
  // A single remaining kind needs no switcher.
  expect(screen.queryByRole("group", { name: "View switcher" })).toBeNull();
  cleanup();
  renderCollection(composed([{ layer: "notes", when: { route: "notes.board" }, only: ["list"] }]));
  await waitFor(async () => expect(await switcherLabels()).toEqual(["List view", "Graph", "Board view", "Dashboard view"]));
});
