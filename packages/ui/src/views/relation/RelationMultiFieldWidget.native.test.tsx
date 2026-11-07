// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";

import { createUiTestProviders } from "../../testing";
import { AppRuntimeProvider, createRouteHref } from "../../runtime";
import { RelationMultiFieldWidget } from "./RelationMultiFieldWidget";

const tag = testDataResource("tags.Tag", {
  recordRepresentation: "name",
  fields: [{ name: "name", kind: "scalar", scalar: "String", readable: true, aggregatable: false,
    creatable: true, updatable: true, requiredOnCreate: true }],
});
const relation = { resource: "tags.Tag", labelField: "name", canCreate: true };
const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://relation-lists", resources: [tag],
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

async function typeQuery(query: string): Promise<void> {
  fireEvent.input(screen.getByRole("combobox", { name: "Tags" }), { target: { value: query }, inputType: "insertText" });
  await screen.findByRole("listbox");
}

test("one chips field offers create for an unmatched query, from the related model's metadata", async () => {
  const getList = vi.fn(async () => ({ data: [{ id: "tag-1", name: "Urgent" }], total: 1 }));
  render(<Provider dataProvider={{ getList }}>
    <RelationMultiFieldWidget value={[]} relation={relation} aria-label="Tags" />
  </Provider>);
  // No create button beside the field: create is the search's last option.
  expect(screen.queryByRole("button", { name: "New tag" })).toBeNull();
  await typeQuery("urg");
  expect(await screen.findByRole("option", { name: "Urgent" })).toBeTruthy();
  // A tag needs only its name: "Create" makes it at once, "Create and edit…" opens its form.
  expect(screen.getByRole("option", { name: "Create “urg”" })).toBeTruthy();
  expect(screen.getByRole("option", { name: "Create and edit…" })).toBeTruthy();
  await typeQuery("Follow up");
  expect(await screen.findByRole("option", { name: "Create “Follow up”" })).toBeTruthy();
  // The search is debounced, as the to-one picker's is.
  await waitFor(() => expect(screen.queryByRole("option", { name: "Urgent" })).toBeNull());
  // An exact match is picked, not created.
  await typeQuery("urgent");
  expect(await screen.findByRole("option", { name: "Urgent" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: /Create/ })).toBeNull();
});

test("a cell keeps an icon create; a declined or uncreatable relation offers none", async () => {
  const getList = vi.fn(async () => ({ data: [], total: 0 }));
  const { unmount } = render(<Provider dataProvider={{ getList }}>
    <RelationMultiFieldWidget controlProps={{ id: "tags", presentation: "cell" }} value={[]} relation={relation} aria-label="Tags" />
  </Provider>);
  expect(screen.getByRole("button", { name: "New tag" }).textContent).toBe("");
  unmount();

  for (const props of [{ create: null }, { relation: { ...relation, canCreate: false } }]) {
    const view = render(<Provider dataProvider={{ getList }}>
      <RelationMultiFieldWidget value={[]} relation={relation} aria-label="Tags" {...props} />
    </Provider>);
    await typeQuery("Follow up");
    expect(await screen.findByText("No options")).toBeTruthy();
    expect(screen.queryByRole("option", { name: /Create/ })).toBeNull();
    view.unmount();
  }
});

test("read-only related records are linked chips with their loaded labels and no option read", () => {
  const getList = vi.fn();
  const getOne = vi.fn();
  const runtime = {
    routeHref: createRouteHref([{ name: "tags", path: "/tags" }, { name: "tags.record", path: "/tags/$id" }]),
    routesByResource: { "tags.Tag": { collection: "tags", record: { name: "tags.record", param: "id" } } },
  };
  render(<Provider dataProvider={{ getList, getOne }}><AppRuntimeProvider runtime={runtime}>
    <RelationMultiFieldWidget readOnly value={[{ id: "tag-1", name: "Urgent" }, { id: "tag-2", name: "Billing" }]}
      relation={relation} aria-label="Tags" />
  </AppRuntimeProvider></Provider>);

  expect(screen.getByRole("link", { name: "Urgent" }).getAttribute("href")).toBe("/tags/tag-1");
  expect(screen.getByRole("link", { name: "Billing" }).getAttribute("href")).toBe("/tags/tag-2");
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(getList).not.toHaveBeenCalled();
  expect(getOne).not.toHaveBeenCalled();
});

test("ids seeded without their records are labelled from one bounded read", async () => {
  const getList = vi.fn(async ({ filters }: { filters?: { field?: string; operator?: string; value?: unknown }[] }) => {
    const ids = filters?.find((filter) => filter.field === "id" && filter.operator === "in")?.value;
    return Array.isArray(ids)
      ? { data: ids.map((id) => ({ id, name: id === "tag-3" ? "Seeded" : "Other" })), total: ids.length }
      : { data: [], total: 0 };
  });
  render(<Provider dataProvider={{ getList }}>
    <RelationMultiFieldWidget value={["tag-3"]} relation={relation} create={null} aria-label="Tags" />
  </Provider>);

  expect(await screen.findByText("Seeded")).toBeTruthy();
  expect(screen.queryByText("tag-3")).toBeNull();
  const idReads = getList.mock.calls.filter(([params]) => params.filters?.some((filter) => filter.field === "id"));
  expect(idReads).toHaveLength(1);
  expect(idReads[0]![0].filters).toEqual([{ field: "id", operator: "in", value: ["tag-3"] }]);
});

test("with every record picked and nothing typed the field opens no empty list; a pick closes it", async () => {
  const getList = vi.fn(async () => ({ data: [{ id: "tag-1", name: "Urgent" }, { id: "tag-2", name: "Billing" }], total: 2 }));
  const change = vi.fn();
  const { rerender } = render(<Provider dataProvider={{ getList }}>
    <RelationMultiFieldWidget value={["tag-1"]} onChange={change} relation={relation} aria-label="Tags" />
  </Provider>);
  await typeQuery("bill");
  fireEvent.click(await screen.findByRole("option", { name: "Billing" }));
  expect(change).toHaveBeenCalledWith(["tag-1", "tag-2"]);
  // The pick closes the list, so it reopens beneath the box's new edge.
  await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());

  rerender(<Provider dataProvider={{ getList }}>
    <RelationMultiFieldWidget value={["tag-1", "tag-2"]} onChange={change} relation={relation} aria-label="Tags" />
  </Provider>);
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Tags" }), { key: "ArrowDown" });
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(screen.queryByText("No options")).toBeNull();
  // Typing still offers to create.
  await typeQuery("Follow up");
  expect(await screen.findByRole("option", { name: "Create “Follow up”" })).toBeTruthy();
});

test("Create “name” makes the record at once and adds it as a chip", async () => {
  const getList = vi.fn(async () => ({ data: [], total: 0 }));
  const create = vi.fn(async ({ variables }: { variables: Record<string, unknown> }) => ({ data: { id: "tag-new", ...variables } }));
  const change = vi.fn();
  render(<Provider dataProvider={{ getList, create }}>
    <RelationMultiFieldWidget value={["tag-1"]} onChange={change} relation={relation} aria-label="Tags" />
  </Provider>);
  await typeQuery("Follow up");
  fireEvent.click(await screen.findByRole("option", { name: "Create “Follow up”" }));
  await waitFor(() => expect(change).toHaveBeenCalledWith(["tag-1", "tag-new"]));
  expect(create.mock.calls[0]?.[0]).toMatchObject({ variables: { name: "Follow up" } });
  expect(screen.queryByRole("dialog")).toBeNull();
});
