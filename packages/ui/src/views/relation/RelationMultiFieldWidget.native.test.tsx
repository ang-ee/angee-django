// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
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

test("offers inline create from the related model's metadata unless the caller declines it", () => {
  const getList = vi.fn(async () => ({ data: [], total: 0 }));
  const { rerender } = render(<Provider dataProvider={{ getList }}>
    <RelationMultiFieldWidget value={[]} relation={relation} aria-label="Tags" />
  </Provider>);
  expect(screen.getByRole("button", { name: "New tag" })).toBeTruthy();

  rerender(<Provider dataProvider={{ getList }}>
    <RelationMultiFieldWidget controlProps={{ id: "tags", presentation: "cell" }} value={[]} relation={relation} aria-label="Tags" />
  </Provider>);
  expect(screen.getByRole("button", { name: "New tag" }).textContent).toBe("");

  rerender(<Provider dataProvider={{ getList }}>
    <RelationMultiFieldWidget value={[]} relation={relation} create={null} aria-label="Tags" />
  </Provider>);
  expect(screen.queryByRole("button", { name: "New tag" })).toBeNull();

  rerender(<Provider dataProvider={{ getList }}>
    <RelationMultiFieldWidget value={[]} relation={{ ...relation, canCreate: false }} aria-label="Tags" />
  </Provider>);
  expect(screen.queryByRole("button", { name: "New tag" })).toBeNull();
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
