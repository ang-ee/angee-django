// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ResourceToolbar, type ResourceToolbarProps } from "./ResourceToolbar";
import { searchFixture } from "../views/resource/search/search-fixture.test-support";
import { activeItems } from "../views/resource/search/active";
import { createResourceViewState } from "../views/resource/resource-view-model";
import { ResourceViewProvider, useResourceView } from "../views/resource/resource-view-context";
import { useResourceSearch } from "../views/resource/search/use-resource-search";

const PAGER = { total: 0, page: 1, pageSize: 20 };
const status = { id: "status", label: "Status", group: { field: "status" } };
function toolbar(props: Partial<ResourceToolbarProps> = {}) {
  return render(<ResourceToolbar pager={PAGER} search={searchFixture()} onViewChange={vi.fn()} {...props} />);
}
// A roomy toolbar whose box chips fit: the box measures its narrow inert chip copies.
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    return new DOMRect(0, 0, this.closest("[inert]") ? 60 : 1024, 80);
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function groupSearch(options: Parameters<typeof searchFixture>[0]) {
  const search = searchFixture(options);
  return { ...search, active: activeItems(createResourceViewState({ groupStack: search.groupStack }), search.catalog) };
}

test("calendar keeps period controls and hides filter, grouping and pager", () => {
  const onModeChange = vi.fn(), onPrev = vi.fn();
  toolbar({ view: "calendar", availableViews: ["list", "board", "calendar"],
    search: searchFixture({ groupingEnabled: true, groupStack: [status.group], catalog: { groups: [status] } }),
    viewControls: { mode: "month", modeOptions: [{ value: "month", label: "Month" }, { value: "week", label: "Week" }],
      onModeChange, title: "June 2026", onPrev, onToday: vi.fn(), onNext: vi.fn() } });
  expect(screen.getByText("June 2026")).toBeTruthy();
  expect(screen.getByLabelText("Calendar view")).toBeTruthy();
  expect(screen.queryByLabelText("Filter records")).toBeNull();
  expect(screen.queryByLabelText("Previous page")).toBeNull();
  expect(screen.queryByLabelText("Group by")).toBeNull();
  fireEvent.click(screen.getByText("Week"));
  expect(onModeChange).toHaveBeenCalledWith("week", expect.anything());
  fireEvent.click(screen.getByLabelText("Previous period"));
  expect(onPrev).toHaveBeenCalledOnce();
});

test("reduced chrome keeps filtering and a single kind omits its switcher", () => {
  toolbar({ view: "list", availableViews: ["list"], chrome: { pager: false, viewSwitcher: false } });
  expect(screen.getByRole("button", { name: "Search options" })).toBeTruthy();
  expect(screen.queryByLabelText("Previous page")).toBeNull();
  expect(screen.queryByLabelText("List view")).toBeNull();
});

test("an undeclared list has the combined box only and curated group labels win", () => {
  toolbar({ search: groupSearch({ groupingEnabled: true, groupStack: [status.group],
    catalog: { curatedGroups: [status], groups: [{ ...status, label: "Raw status" }] } }) });
  const filter = screen.getByRole("button", { name: "Search options" });
  expect(screen.queryByRole("button", { name: "Group by" })).toBeNull();
  expect(screen.getByRole("button", { name: "Remove Status" }).parentElement?.textContent).toContain("Group by: Status");
  expect(screen.getByRole("button", { name: "Remove Status" })).toBeTruthy();
  fireEvent.click(filter);
  expect(screen.getByRole("listitem", { name: "Status" })).toBeTruthy();
});

test("custom-only groups offer granularities and prevent adding a duplicate level", () => {
  const addGroup = vi.fn();
  toolbar({ search: groupSearch({ groupingEnabled: true, addGroup,
    groupStack: [{ field: "created", granularity: "month" }],
    catalog: { curatedGroups: [], groups: [{ id: "created", label: "Created", group: { field: "created" },
      type: "date", granularities: ["month", "year"] }] } }) });
  expect(screen.getByRole("button", { name: "Remove Created · Month" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  expect(screen.queryByRole("button", { name: "Created" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "More axes…" }));
  expect(screen.getByLabelText("Group granularity").textContent).toContain("Month");
  expect(screen.getByRole("button", { name: "Add level" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Add level" }));
  expect(addGroup).not.toHaveBeenCalled();
});

test("a changed catalog replaces stale custom field and granularity drafts", () => {
  const addGroup = vi.fn();
  const first = searchFixture({ groupingEnabled: true, addGroup, catalog: { groups: [
    { id: "created", label: "Created", group: { field: "created" }, type: "date", granularities: ["day"] },
  ] } });
  const result = toolbar({ search: first });
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  fireEvent.click(screen.getByRole("button", { name: "More axes…" }));
  result.rerender(<ResourceToolbar pager={PAGER} search={searchFixture({ groupingEnabled: true, addGroup,
    catalog: { groups: [{ id: "date", label: "Document date", group: { field: "date" },
      type: "date", granularities: ["month"] }] } })} />);
  expect(screen.getByLabelText("Group axis").textContent).toContain("Document date");
  expect(screen.getByLabelText("Group granularity").textContent).toContain("Month");
  fireEvent.click(screen.getByRole("button", { name: "Add level" }));
  expect(addGroup).toHaveBeenCalledWith({ field: "date", granularity: "month" });
});

test("shortcuts dispatches named filters and facets by catalog identity", async () => {
  const search = searchFixture({ catalog: {
    filters: [{ id: "mine", label: "Mine", filter: { owner: { exact: "viewer" } } }],
    facets: [{ field: "status", label: "Status", source: "scalar", options: [
      { id: "bucket-open", label: "Open", value: "open", filter: { status: { exact: "open" } } },
      { id: "bucket-blank", label: "Not given", filter: { status: { isNull: true } } },
    ] }],
  } });
  toolbar({ search, searchDeclaration: { shortcuts: [{ kind: "toggle", id: "mine" }, { kind: "facet", field: "status" }] } });
  expect(screen.queryByLabelText("Filter records")).toBeNull();
  expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  expect(screen.getByRole("combobox", { name: "Filter records" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  fireEvent.click(within(screen.getByRole("toolbar", { name: "Search shortcuts" })).getByRole("button", { name: "Mine" }));
  expect(search.toggleFilter).toHaveBeenCalledWith("mine");
  fireEvent.click(screen.getByRole("combobox", { name: "Status" }));
  const option = await screen.findByRole("option", { name: "Not given" });
  fireEvent.pointerDown(option, { pointerType: "mouse" }); fireEvent.click(option);
  expect(search.setFacet).toHaveBeenCalledWith("status", ["bucket-blank"]);
});

test("shipped and pinned favorites retain their toggles, rename and pin controls", () => {
  const preset = { id: "favorite:open", label: "Open records", pinned: true };
  const search = searchFixture({ catalog: { favorites: [preset] }, queryDirty: true,
    active: [{ id: "favorite:favorite:open", kind: "favorite", label: preset.label }], pinFavorite: vi.fn() });
  toolbar({ search, searchDeclaration: { shortcuts: [{ kind: "toggle", id: preset.id }] } });
  const button = screen.getByRole("button", { name: preset.label });
  expect(button.getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(button); expect(search.toggleFavorite).toHaveBeenCalledWith(preset.id);
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  expect(search.clearQuery).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  fireEvent.click(screen.getByRole("button", { name: "Unpin favorite" }));
  expect(search.pinFavorite).toHaveBeenCalledWith(preset.id, false);
});

test("chips use the active projection and dispatch to model commands", () => {
  const search = searchFixture({ catalog: { filters: [{ id: "open", label: "Open", filter: { status: { exact: "open" } } }] },
    active: [{ id: "filter:open", kind: "filter", label: "Open" }] });
  toolbar({ search });
  fireEvent.click(screen.getByRole("button", { name: "Remove Open" }));
  expect(search.clear).toHaveBeenCalledWith("filter:open");
});

test.each([false, true])("one chip per group level removes only that level (collapsed %s)", (collapsed) => {
  const created = { id: "created", label: "Created", group: { field: "created" },
    type: "date" as const, granularities: ["month", "year"] };
  const catalog = searchFixture({ catalog: { groups: [status, created], curatedGroups: [status] } }).catalog;
  function Content() {
    const resourceView = useResourceView();
    const search = useResourceSearch({ resourceView, catalog, groupingEnabled: true });
    return <ResourceToolbar pager={PAGER} search={search} searchDeclaration={collapsed ? { box: "collapsed" } : undefined} />;
  }
  render(<ResourceViewProvider scope="local" initialState={{ groupStack: [
    { field: "created", granularity: "month" }, status.group, { field: "created", granularity: "year" },
  ] }}><Content /></ResourceViewProvider>);
  if (collapsed) fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  const chips = within(screen.getByRole("toolbar", { name: "Active search" }));
  expect(chips.getByRole("button", { name: "Remove Created · Month" })).toBeTruthy();
  expect(chips.getByRole("button", { name: "Remove Status" })).toBeTruthy();
  expect(chips.getByRole("button", { name: "Remove Created · Year" })).toBeTruthy();
  fireEvent.click(chips.getByRole("button", { name: "Remove Created · Month" }));
  expect(chips.queryByRole("button", { name: "Remove Created · Month" })).toBeNull();
  expect(chips.getByRole("button", { name: "Remove Status" }).parentElement?.textContent).toContain("Group by:");
  expect(chips.getByRole("button", { name: "Remove Created · Year" }).parentElement?.textContent).toContain("then:");
});

test("shared utilities stay between query controls and pager; wrapping is opt-in", () => {
  toolbar({ wrap: true, utilityActions: <button type="button">Share</button> });
  const filter = screen.getByLabelText("Filter records"), share = screen.getByRole("button", { name: "Share" });
  expect(share.parentElement?.className).toContain("resource-toolbar-utilities");
  expect(filter.compareDocumentPosition(share) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(share.compareDocumentPosition(screen.getByLabelText("Previous page")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(filter.closest("section")?.className).toContain("resource-toolbar-wrap");
});

test("providers without writable preferences keep the complete box reachable", () => {
  toolbar();
  expect(screen.getByRole("button", { name: "Search options" })).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "Search options" })).toHaveLength(1);
});

test("a shipped favorite toggle shortcut uses the provider's id, not a saved-view object", () => {
  const search = searchFixture({ catalog: { favorites: [{ id: "view.open", preset: "view.open", label: "Open view" }] },
    active: [{ id: "favorite:view.open", kind: "favorite", label: "Open view" }] });
  toolbar({ search, searchDeclaration: { shortcuts: [{ kind: "toggle", id: "view.open" }] } });
  fireEvent.click(screen.getByRole("button", { name: "Open view" }));
  expect(search.toggleFavorite).toHaveBeenCalledWith("view.open");
});

test("a facet label comes from the catalog even without a clause descriptor", () => {
  toolbar({ searchDeclaration: { shortcuts: [{ kind: "facet", field: "due_at" }] }, search: searchFixture({ catalog: {
    facets: [{ field: "due_at", label: "Due date", source: "declared", options: [
      { id: "today", label: "Today", value: "today", filter: { due_at: { exact: "today" } } },
    ] }],
  } }) });
  expect(screen.getByRole("combobox", { name: "Due date" })).toBeTruthy();
});

test("list retains its filter, pager and switcher without calendar controls", () => {
  toolbar({ view: "list", availableViews: ["list", "board"] });
  expect(screen.getByLabelText("Filter records")).toBeTruthy();
  expect(screen.getByLabelText("Previous page")).toBeTruthy();
  expect(screen.getByLabelText("List view")).toBeTruthy();
  expect(screen.getByLabelText("Board view")).toBeTruthy();
  expect(screen.queryByLabelText("Calendar view")).toBeNull();
  expect(screen.queryByLabelText("Previous period")).toBeNull();
});

test("writable preferences expose saving, and an empty curated catalog still allows custom grouping", () => {
  const search = searchFixture({ saveFavorite: vi.fn(), groupingEnabled: true,
    catalog: { groups: [status], curatedGroups: [] } });
  toolbar({ search });
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  fireEvent.click(screen.getByRole("button", { name: "More axes…" }));
  fireEvent.click(screen.getByRole("button", { name: "Add level" }));
  expect(search.addGroup).toHaveBeenCalledWith({ field: "status" });
  expect(screen.getByRole("button", { name: "Save current search" })).toBeTruthy();
});

test("disabled grouping stays hidden even if a pinned stack has levels", () => {
  toolbar({ search: searchFixture({ groupingEnabled: false, groupStack: [status.group], catalog: { groups: [status] } }) });
  expect(screen.queryByLabelText("Group by")).toBeNull();
});
