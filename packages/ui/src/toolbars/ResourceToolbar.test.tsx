// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ResourceToolbar, type ResourceToolbarProps } from "./ResourceToolbar";
import { searchFixture } from "../views/resource/search/search-fixture.test-support";

const PAGER = { total: 0, page: 1, pageSize: 20 };
const status = { id: "status", label: "Status", group: { field: "status" } };
function toolbar(props: Partial<ResourceToolbarProps> = {}) {
  return render(<ResourceToolbar pager={PAGER} search={searchFixture()} onViewChange={vi.fn()} {...props} />);
}
afterEach(cleanup);

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
  expect(screen.getByRole("button", { name: "Filter" })).toBeTruthy();
  expect(screen.queryByLabelText("Previous page")).toBeNull();
  expect(screen.queryByLabelText("List view")).toBeNull();
});

test("grouping stays beside today's picker and curated labels win", () => {
  toolbar({ search: searchFixture({ groupingEnabled: true, groupStack: [status.group],
    catalog: { curatedGroups: [status], groups: [{ ...status, label: "Raw status" }] } }) });
  const filter = screen.getByRole("button", { name: "Filter" });
  const group = screen.getByLabelText("Group by");
  expect(filter.closest(".resource-toolbar-query")).toBe(group.closest(".resource-toolbar-query"));
  expect(group.textContent).toContain("Group by: Status");
  expect(screen.queryByRole("button", { name: /Remove.*group/i })).toBeNull();
});

test("custom-only groups and date granularities still use the existing editor", () => {
  const setGroupStack = vi.fn();
  toolbar({ search: searchFixture({ groupingEnabled: true, setGroupStack,
    groupStack: [{ field: "created", granularity: "month" }],
    catalog: { curatedGroups: [], groups: [{ id: "created", label: "Created", group: { field: "created" },
      type: "date", granularities: ["month", "year"] }] } }) });
  expect(screen.getByText(/Created · Month/)).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Group by"));
  expect(screen.queryByRole("button", { name: "Created" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
  expect(screen.getByLabelText("Group granularity").textContent).toContain("Month");
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(setGroupStack).not.toHaveBeenCalled();
});

test("a changed catalog replaces stale custom field and granularity drafts", () => {
  const setGroupStack = vi.fn();
  const first = searchFixture({ groupingEnabled: true, setGroupStack, catalog: { groups: [
    { id: "created", label: "Created", group: { field: "created" }, type: "date", granularities: ["day"] },
  ] } });
  const result = toolbar({ search: first });
  fireEvent.click(screen.getByLabelText("Group by"));
  fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
  result.rerender(<ResourceToolbar pager={PAGER} search={searchFixture({ groupingEnabled: true, setGroupStack,
    catalog: { groups: [{ id: "date", label: "Document date", group: { field: "date" },
      type: "date", granularities: ["month"] }] } })} />);
  expect(screen.getByLabelText("Group field").textContent).toContain("Document date");
  expect(screen.getByLabelText("Group granularity").textContent).toContain("Month");
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(setGroupStack).toHaveBeenCalledWith([{ field: "date", granularity: "month" }]);
});

test("the compact row dispatches named filters and facets by catalog identity", async () => {
  const search = searchFixture({ catalog: {
    filters: [{ id: "mine", label: "Mine", filter: { owner: { exact: "viewer" } } }],
    facets: [{ field: "status", label: "Status", source: "scalar", options: [
      { id: "bucket-open", label: "Open", value: "open", filter: { status: { exact: "open" } } },
      { id: "bucket-blank", label: "Not given", filter: { status: { isNull: true } } },
    ] }],
  } });
  toolbar({ search, filterRow: { quickFilterIds: ["mine"], facetIds: ["status"] } });
  expect(screen.queryByLabelText("Filter records")).toBeNull();
  expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Filter" }));
  expect(screen.getByRole("searchbox", { name: "Filter records" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Filter" }));
  fireEvent.click(within(screen.getByLabelText("Filters")).getByRole("button", { name: "Mine" }));
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
  toolbar({ search, filterRow: { quickFilterIds: [preset.id] } });
  const button = screen.getByRole("button", { name: preset.label });
  expect(button.getAttribute("aria-pressed")).toBe("true");
  expect(button.className).toContain("rounded-full");
  fireEvent.click(button); expect(search.toggleFavorite).toHaveBeenCalledWith(preset.id);
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  expect(search.clearQuery).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Filter and favorites" }));
  fireEvent.click(screen.getByRole("button", { name: "Unpin favorite" }));
  expect(search.pinFavorite).toHaveBeenCalledWith(preset.id, false);
});

test("chips use the active projection and dispatch to model commands", () => {
  const search = searchFixture({ catalog: { filters: [{ id: "open", label: "Open", filter: { status: { exact: "open" } } }] },
    active: [{ id: "filter:open", kind: "filter", label: "Open" }] });
  toolbar({ search });
  fireEvent.click(screen.getByRole("button", { name: "Remove Open" }));
  expect(search.toggleFilter).toHaveBeenCalledWith("open");
});

test("shared utilities stay between query controls and pager; wrapping is opt-in", () => {
  toolbar({ wrap: true, utilityActions: <button type="button">Share</button> });
  const filter = screen.getByLabelText("Filter records"), share = screen.getByRole("button", { name: "Share" });
  expect(share.parentElement?.className).toContain("resource-toolbar-utilities");
  expect(filter.compareDocumentPosition(share) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(share.compareDocumentPosition(screen.getByLabelText("Previous page")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(filter.closest("section")?.className).toContain("resource-toolbar-wrap");
});

test("providers without writable preferences keep the plain Filter trigger", () => {
  toolbar();
  expect(screen.getByRole("button", { name: "Filter" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Filter and favorites" })).toBeNull();
});

test("a shipped favorite quick filter uses the provider's id, not a saved-view object", () => {
  const search = searchFixture({ catalog: { favorites: [{ id: "view.open", preset: "view.open", label: "Open view" }] },
    active: [{ id: "favorite:view.open", kind: "favorite", label: "Open view" }] });
  toolbar({ search, filterRow: { quickFilterIds: ["view.open"] } });
  fireEvent.click(screen.getByRole("button", { name: "Open view" }));
  expect(search.toggleFavorite).toHaveBeenCalledWith("view.open");
});

test("a facet label comes from the catalog even without a clause descriptor", () => {
  toolbar({ filterRow: { facetIds: ["due_at"] }, search: searchFixture({ catalog: {
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
  fireEvent.click(screen.getByLabelText("Group by"));
  fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(search.setGroupStack).toHaveBeenCalledWith([{ field: "status" }]);
  fireEvent.click(screen.getByLabelText("Group by"));
  fireEvent.click(screen.getByLabelText("Filter and favorites"));
  expect(screen.getByRole("button", { name: "Save current search" })).toBeTruthy();
});

test("disabled grouping stays hidden even if a pinned stack has levels", () => {
  toolbar({ search: searchFixture({ groupingEnabled: false, groupStack: [status.group], catalog: { groups: [status] } }) });
  expect(screen.queryByLabelText("Group by")).toBeNull();
});
