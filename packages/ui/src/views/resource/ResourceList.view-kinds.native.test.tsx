// @vitest-environment happy-dom

import type { ComponentProps } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider, containersFromChildren, type ComposedContainers, type ContainerRule, type ResourceCardContent, type ResourceViewKindContent } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { ResourceList } from "./ResourceList";
import { useResourceView } from "./resource-view-context";
import { RESOURCE_CONTAINERS } from "./resource-view-kinds";

const resource = testDataResource("notes.Note", {
  recordRepresentation: "title",
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

function composed(
  rules: (Omit<ContainerRule, "exempt" | "rank"> & { rank?: number })[] = [],
  children: Parameters<typeof containersFromChildren>[1] = {},
): ComposedContainers {
  const containers = containersFromChildren(RESOURCE_CONTAINERS, {
    "notes.Note#views": { "nexus.graph": { content: graph, sequence: 15 } },
    "tasks.Task#views": { "nexus.timeline": { content: { ...graph, label: "Timeline" } } },
    ...children,
  });
  return { ...containers, rules: rules.length ? { "resource#views": rules.map((rule, index) => ({ rank: index, ...rule, exempt: [] })) } : {} };
}

function renderCollection(containers: ComposedContainers, props: Partial<ComponentProps<typeof ResourceList>> = {}) {
  const getList = vi.fn(async () => ({ data: [{ id: "note-1", title: "First note" }, ...(props.defaultView === "gallery"
    ? [{ id: "note-2", title: "Second note" }] : [])], total: props.defaultView === "gallery" ? 2 : 1 }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Provider dataProvider={{ getList }}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider>
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets, containers }}>
        <ResourceList resource={resource.modelLabel} scope="local" hideCreate columns={[{ field: "title" }]} {...props} />
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
  // The gallery needs a card to draw, and this list declares none.
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

test("a page card or the model's registered card offers the gallery beside list and board", async () => {
  renderCollection(composed(), { renderCard: (row) => <span>{String(row.title)}</span> });
  await screen.findByText("First note");
  expect(await switcherLabels()).toEqual(["List view", "Graph", "Board view", "Grid view", "Dashboard view"]);
  cleanup();
  renderCollection(composed([], { "notes.Note#card": { "skin.note": { content: noteCard } } }));
  await screen.findByText("First note");
  expect(await switcherLabels()).toContain("Grid view");
});

test("the gallery kind pages the records as cards with one renderer and the shared selection", async () => {
  renderCollection(composed(), {
    defaultView: "gallery",
    renderCard: (row, context) => <span>{String(row.title)} · {context.kind}</span>,
  });
  expect(await screen.findByText("First note · gallery")).toBeTruthy();
  expect(screen.getByText("Second note · gallery")).toBeTruthy();
  expect(document.querySelectorAll("[data-slot='gallery-card']")).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Grid view" }).getAttribute("aria-pressed")).toBe("true");
  const checkbox = screen.getByRole("checkbox", { name: "Select First note" });
  fireEvent.click(checkbox);
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "Select First note" }).getAttribute("aria-checked")).toBe("true"));
});

test("a page opening on the gallery keeps it with no card declared, drawing the list's column card", async () => {
  renderCollection(composed(), { defaultView: "gallery" });
  expect(await screen.findByText("Second note")).toBeTruthy();
  const cards = document.querySelectorAll("[data-slot='gallery-card']");
  expect(cards).toHaveLength(2);
  expect(cards[0]?.getAttribute("data-frame")).toBe("inset");
});

const noteCard: ResourceCardContent = {
  render: (row, context) => <span>Card of {String(row.title)} on the {context.kind}</span>,
  frame: "flush",
};

test("a model's registered card draws its gallery and board with no page code; the page's own card wins", async () => {
  const cards = composed([], { "notes.Note#card": { "skin.note": { content: noteCard, requiredFields: ["title"] } } });
  renderCollection(cards, { defaultView: "gallery" });
  expect(await screen.findByText("Card of First note on the gallery")).toBeTruthy();
  expect(document.querySelector("[data-slot='gallery-card']")?.getAttribute("data-frame")).toBe("flush");
  fireEvent.click(screen.getByRole("button", { name: "Board view" }));
  expect(await screen.findByText("Card of First note on the board")).toBeTruthy();
  expect(document.querySelector("[data-slot='board-card']")?.getAttribute("data-frame")).toBe("flush");
  cleanup();
  renderCollection(cards, { defaultView: "gallery", renderCard: (row) => <span>Page card of {String(row.title)}</span> });
  expect(await screen.findByText("Page card of First note")).toBeTruthy();
  expect(screen.queryByText(/^Card of/)).toBeNull();
});
