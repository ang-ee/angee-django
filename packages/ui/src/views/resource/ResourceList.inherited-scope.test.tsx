// @vitest-environment happy-dom

import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ResourceList } from "./ResourceList";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";

const captured = new Map<string, ResourceViewContextValue>();
const router = vi.hoisted(() => ({ navigate: vi.fn() }));

vi.mock("@tanstack/react-router", () => ({
  useSearch: () => ({ sort: "name:asc", page: 3 }),
  useNavigate: () => router.navigate,
}));

// Keep the real ResourceList/provider composition; observe the state delivered
// to each body without involving its data transport or record form.
vi.mock("./resource-list/body", () => ({
  ResourceListBody: ({ resource }: { resource: string }) => {
    captured.set(resource, useResourceView());
    return resource === "catalog.Catalog"
      ? <ResourceList resource="catalog.Entry" scope="local" columns={[]} order={{ min_qty: "asc" }} />
      : null;
  },
}));

afterEach(() => {
  cleanup();
  captured.clear();
  vi.clearAllMocks();
});

test("an explicitly local nested resource owns sorting and reset without changing its parent's route", () => {
  render(<ResourceList resource="catalog.Catalog" columns={[]} />);
  const parent = captured.get("catalog.Catalog")!;
  const child = () => captured.get("catalog.Entry")!;

  expect(parent.state.sorting).toEqual([{ id: "name", desc: false }]);
  expect(parent.state.pagination.pageIndex).toBe(2);
  expect(child().state.sorting).toEqual([{ id: "min_qty", desc: false }]);
  expect(child().state.pagination.pageIndex).toBe(0);
  expect(child().resource).toBe("catalog.Entry");

  act(() => {
    child().setSorting([{ id: "min_qty", desc: true }]);
    child().setFilter({ compute: { exact: "fixed" } });
    child().toggleSelectedId("entry-1", true);
  });
  expect(child().state.sorting).toEqual([{ id: "min_qty", desc: true }]);
  expect(child().state.filter).toEqual({ compute: { exact: "fixed" } });
  expect(child().state.rowSelection).toEqual({ "entry-1": true });

  act(() => child().resetQuery());
  expect(child().state.sorting).toEqual([]);
  expect(child().state.filter).toEqual({});
  expect(child().state.rowSelection).toEqual({});
  expect(captured.get("catalog.Catalog")).toBe(parent);
  expect(router.navigate).not.toHaveBeenCalled();
});

test.each(["catalog.Entry", "catalog.Other", undefined])("inherits an existing provider across resources (%s)", (resource) => {
  render(
    <ResourceViewProvider resource={resource} scope="local" initialState={{ pageSize: 7, sorting: [{ id: "updated_at", desc: true }] }}>
      <ResourceList resource="catalog.Entry" scope="inherit" columns={[]} order={{ min_qty: "asc" }} />
    </ResourceViewProvider>,
  );
  expect(captured.get("catalog.Entry")!.state.pagination.pageSize).toBe(7);
  expect(captured.get("catalog.Entry")!.state.sorting).toEqual([{ id: "updated_at", desc: true }]);
});

test("a nested list of another resource does not inherit the record's collection state", () => {
  // The shape of a record tab: a Round page owns the route's state and sorts by
  // `submission_deadline`; its Topic panel declares no scope. A sort field is
  // resource-specific, so inheriting hands the panel a field Topic cannot sort
  // and its query dies. An explicit `inherit` still shares — see the test above.
  render(
    <ResourceViewProvider
      resource="catalog.Catalog"
      scope="local"
      initialState={{ pageSize: 7, sorting: [{ id: "updated_at", desc: true }] }}
    >
      <ResourceList resource="catalog.Other" columns={[]} order={{ min_qty: "asc" }} />
    </ResourceViewProvider>,
  );

  const nested = captured.get("catalog.Other")!;
  expect(nested.resource).toBe("catalog.Other");
  expect(nested.state.sorting).toEqual([{ id: "min_qty", desc: false }]);
  expect(nested.state.pagination.pageSize).not.toBe(7);
  expect(router.navigate).not.toHaveBeenCalled();
});

test("a nested list of the same resource still inherits the ambient view state by default", () => {
  render(
    <ResourceViewProvider
      resource="catalog.Catalog"
      scope="local"
      initialState={{ pageSize: 7, sorting: [{ id: "updated_at", desc: true }] }}
    >
      <ResourceList resource="catalog.Catalog" columns={[]} order={{ name: "asc" }} />
    </ResourceViewProvider>,
  );

  const nested = captured.get("catalog.Catalog")!;
  expect(nested.state.pagination.pageSize).toBe(7);
  expect(nested.state.sorting).toEqual([{ id: "updated_at", desc: true }]);
});

test("a named list under a provider bound to no resource inherits it by default", () => {
  // An owner bound to no resource is still shared: `GroupedSurface.native.test.tsx`
  // relies on a named list taking an unbound ambient view's state, invalid sort included.
  render(
    <ResourceViewProvider scope="local" initialState={{ pageSize: 7, sorting: [{ id: "updated_at", desc: true }] }}>
      <ResourceList resource="catalog.Entry" columns={[]} order={{ min_qty: "asc" }} />
    </ResourceViewProvider>,
  );

  const nested = captured.get("catalog.Entry")!;
  expect(nested.state.pagination.pageSize).toBe(7);
  expect(nested.state.sorting).toEqual([{ id: "updated_at", desc: true }]);
});
