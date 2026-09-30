// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  ResourceToolbar,
  type ResourceToolbarProps,
  type ResourceToolbarViewControls,
} from "./ResourceToolbar";

const PAGER = { total: 0, page: 1, pageSize: 20 };

function viewControls(
  overrides: Partial<ResourceToolbarViewControls> = {},
): ResourceToolbarViewControls {
  return {
    mode: "month",
    modeOptions: [
      { value: "month", label: "Month" },
      { value: "week", label: "Week" },
      { value: "day", label: "Day" },
    ],
    onModeChange: vi.fn(),
    title: "June 2026",
    onPrev: vi.fn(),
    onToday: vi.fn(),
    onNext: vi.fn(),
    ...overrides,
  };
}

function renderToolbar(props: Partial<ResourceToolbarProps>): void {
  render(<ResourceToolbar pager={PAGER} onFilterTextChange={vi.fn()} onViewChange={vi.fn()} {...props} />);
}

afterEach(cleanup);

describe("ResourceToolbar under the calendar kind", () => {
  test("renders the view controls and hides filter/pager/group-by", () => {
    renderToolbar({
      view: "calendar",
      availableViews: ["list", "board", "calendar"],
      viewControls: viewControls(),
      // Group + filter options are declared but must not render under calendar.
      filterOptions: [
        { id: "open", label: "Open", filter: { status: "open" } },
      ],
      groupStack: [{ field: "status" }],
      onGroupStackChange: vi.fn(),
    });

    // The view controls (period nav + title + mode switch) are present…
    expect(screen.getByText("June 2026")).toBeTruthy();
    expect(screen.getByLabelText("Previous period")).toBeTruthy();
    expect(screen.getByLabelText("Next period")).toBeTruthy();
    expect(screen.getByText("Month")).toBeTruthy();
    expect(screen.getByText("Week")).toBeTruthy();
    expect(screen.getByText("Day")).toBeTruthy();

    // …while filter/search, the pager, and group-by are all absent.
    expect(screen.queryByLabelText("Filter records")).toBeNull();
    expect(screen.queryByLabelText("Previous page")).toBeNull();
    expect(screen.queryByText("Group by")).toBeNull();

    // The switcher offers Calendar because sources are declared.
    expect(screen.getByLabelText("Calendar view")).toBeTruthy();
  });

  test("drives mode switch and period nav", () => {
    const onModeChange = vi.fn();
    const onPrev = vi.fn();
    renderToolbar({
      view: "calendar",
      availableViews: ["list", "board", "calendar"],
      viewControls: viewControls({ onModeChange, onPrev }),
    });

    fireEvent.click(screen.getByText("Week"));
    expect(onModeChange).toHaveBeenCalledWith("week", expect.anything());

    fireEvent.click(screen.getByLabelText("Previous period"));
    expect(onPrev).toHaveBeenCalledTimes(1);
  });
});

describe("ResourceToolbar list-kind regression", () => {
  test("omits the collection switcher with one declared kind", () => {
    renderToolbar({ view: "list", availableViews: ["list"] });
    expect(screen.queryByRole("button", { name: "List view" })).toBeNull();
  });

  test("honours reduced collection chrome without dropping the filter", () => {
    renderToolbar({ view: "list", availableViews: ["list", "board"],
      chrome: { viewSwitcher: false, pager: false } });
    expect(screen.getByRole("button", { name: "Filter" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Previous page" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Board view" })).toBeNull();
  });

  test("uses a shipped preset as a declared quick filter", () => {
    const onFavoriteToggle = vi.fn();
    const preset = { id: "view.open", preset: "view.open", label: "Open records", filter: { status: { exact: "open" } } };
    renderToolbar({ view: "list", filterRow: { quickFilterIds: [preset.id] },
      favorites: [preset], activeFavoriteIds: [preset.id], onFavoriteToggle });
    const button = screen.getByRole("button", { name: "Open records" });
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(button.className).toContain("rounded-full");
    fireEvent.click(button);
    expect(onFavoriteToggle).toHaveBeenCalledWith(preset);
  });

  test("places grouping beside the filter instead of in its picker", () => {
    renderToolbar({ view: "list", groupOptions: [{ id: "status", label: "Status", group: { field: "status" } }],
      onGroupStackChange: vi.fn(), groupStack: [{ field: "status" }] });
    const filter = screen.getByRole("button", { name: "Filter" });
    const group = screen.getByRole("button", { name: "Group by" });
    expect(filter.closest(".resource-toolbar-query")).toBe(group.closest(".resource-toolbar-query"));
    expect(filter.parentElement?.className).toContain("flex-1");
    expect(filter.parentElement?.className).not.toContain("w-full");
    expect(screen.queryByRole("button", { name: /Remove.*group/i })).toBeNull();
  });

  test("names the active group from its curated shortcut first", () => {
    renderToolbar({ view: "list", groupStack: [{ field: "updatedAt" }],
      groupOptions: [{ id: "recent", label: "Updated", group: { field: "updatedAt" } }],
      customGroupOptions: [{ id: "raw", label: "Timestamp", group: { field: "updatedAt" } }] });
    expect(screen.getByRole("button", { name: "Group by" }).textContent).toContain("Group by: Updated");
  });

  test("renders a compact filter row with quick toggles, facets and conditional Clear", async () => {
    const onToggle = vi.fn();
    const onFacetChange = vi.fn();
    const onReset = vi.fn();
    renderToolbar({ view: "list", filterRow: { quickFilterIds: ["mine"], facetIds: ["status"] },
      filterOptions: [
        { id: "mine", label: "Mine", filter: { owner: { exact: "me" } } },
        { id: "status:open", label: "Open", filter: { status: { exact: "open" } } },
      ], customFilterFields: [{ id: "status", label: "Status", type: "selection", options: [{ value: "open", label: "Open" }] }],
      onFilterToggle: onToggle, onFacetChange, onQueryReset: onReset });
    expect(screen.queryByLabelText("Filter records")).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));
    expect(screen.getByRole("searchbox", { name: "Filter records" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));
    fireEvent.click(within(screen.getByLabelText("Filters")).getByRole("button", { name: "Mine" }));
    expect(onToggle).toHaveBeenCalledWith("mine");
    const facet = screen.getByRole("combobox", { name: "Status" });
    fireEvent.click(facet);
    const option = await screen.findByRole("option", { name: "Open" });
    fireEvent.pointerDown(option, { pointerType: "mouse" });
    fireEvent.click(option);
    expect(onFacetChange).toHaveBeenCalledWith("status", "status:open");
  });

  test("a facet without a descriptor uses the field vocabulary label", () => {
    renderToolbar({ view: "list", filterRow: { facetIds: ["due_at"] },
      facetLabels: { due_at: "Due date" },
      filterOptions: [{ id: "due_at:today", label: "Today", filter: { due_at: { exact: "today" } } }] });
    expect(screen.getByRole("combobox", { name: "Due date" })).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "due_at" })).toBeNull();
  });

  test("shows pinned favourites in the row and exposes rename and pin in Favorites", () => {
    const onFavoriteToggle = vi.fn();
    const onFavoritePin = vi.fn();
    renderToolbar({ view: "list", filterRow: {}, favorites: [{ id: "favorite:recent", label: "Recent", pinned: true,
      filter: { status: { exact: "recent" } } }], onFavoriteToggle, onFavoritePin, queryDirty: true, onQueryReset: vi.fn() });
    fireEvent.click(screen.getByRole("button", { name: "Recent" }));
    expect(onFavoriteToggle).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Clear" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Filter and favorites" }));
    fireEvent.click(screen.getByRole("button", { name: "Unpin favorite" }));
    expect(onFavoritePin).toHaveBeenCalledWith("favorite:recent", false);
  });

  test("keeps presets curated while a custom-only catalog exposes supported groups", () => {
    const onGroupStackChange = vi.fn();
    renderToolbar({
      view: "list",
      groupOptions: [],
      customGroupOptions: [
        { id: "partner", label: "Counterparty", group: { field: "partner" } },
        { id: "currency", label: "Currency", group: { field: "currency" } },
      ],
      onGroupStackChange,
    });

    fireEvent.click(screen.getByLabelText("Group by"));
    expect(screen.queryByRole("button", { name: "Counterparty" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
    expect(screen.getByLabelText("Group field").textContent).toContain("Counterparty");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onGroupStackChange).toHaveBeenCalledWith([{ field: "partner" }]);
  });

  test("falls back to preset options for standalone custom-group callers", () => {
    renderToolbar({
      view: "list",
      groupOptions: [
        { id: "platform", label: "Platform", group: { field: "platform" } },
      ],
      onGroupStackChange: vi.fn(),
    });

    fireEvent.click(screen.getByLabelText("Group by"));
    fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
    expect(screen.getByLabelText("Group field").textContent).toContain("Platform");
  });

  test("uses supported custom date granularities and labels their active chip", () => {
    const onGroupStackChange = vi.fn();
    renderToolbar({
      view: "list",
      groupOptions: [],
      customGroupOptions: [{
        id: "document-date",
        label: "Document date",
        group: { field: "document_date" },
        type: "date",
        granularities: ["month", "year"],
      }],
      groupStack: [{ field: "document_date", granularity: "month" }],
      onGroupStackChange,
    });

    expect(screen.getByText(/Document date · Month/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Group by"));
    fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
    expect(screen.getByLabelText("Group granularity").textContent).toContain("Month");
  });

  test("resolves stale custom field and granularity state after a catalog change", () => {
    const onGroupStackChange = vi.fn();
    const { rerender } = render(<ResourceToolbar
      pager={PAGER}
      view="list"
      groupOptions={[]}
      customGroupOptions={[{
        id: "created", label: "Created", group: { field: "created" },
        type: "date", granularities: ["day"],
      }]}
      onGroupStackChange={onGroupStackChange}
      onFilterTextChange={vi.fn()}
    />);
    fireEvent.click(screen.getByLabelText("Group by"));
    fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));

    rerender(<ResourceToolbar
      pager={PAGER}
      view="list"
      groupOptions={[]}
      customGroupOptions={[{
        id: "document-date", label: "Document date", group: { field: "document_date" },
        type: "date", granularities: ["month"],
      }]}
      onGroupStackChange={onGroupStackChange}
      onFilterTextChange={vi.fn()}
    />);

    expect(screen.getByLabelText("Group field").textContent).toContain("Document date");
    expect(screen.getByLabelText("Group granularity").textContent).toContain("Month");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onGroupStackChange).toHaveBeenCalledWith([
      { field: "document_date", granularity: "month" },
    ]);
  });

  test("preserves group depth and ignores an exact custom duplicate", () => {
    const onGroupStackChange = vi.fn();
    const props: Partial<ResourceToolbarProps> = {
      view: "list",
      maxGroupDepth: 1,
      groupOptions: [],
      customGroupOptions: [
        { id: "partner", label: "Counterparty", group: { field: "partner" } },
      ],
      groupStack: [{ field: "status" }],
      onGroupStackChange,
    };
    const { rerender } = render(<ResourceToolbar pager={PAGER}
      onFilterTextChange={vi.fn()} {...props} />);
    fireEvent.click(screen.getByLabelText("Group by"));
    fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onGroupStackChange).toHaveBeenCalledWith([{ field: "partner" }]);

    onGroupStackChange.mockClear();
    rerender(<ResourceToolbar pager={PAGER} onFilterTextChange={vi.fn()}
      {...props} groupStack={[{ field: "partner" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onGroupStackChange).not.toHaveBeenCalled();
  });

  test("places shared utilities between the query controls and pager", () => {
    renderToolbar({
      view: "list",
      utilityActions: <button type="button">Share</button>,
    });

    const filter = screen.getByLabelText("Filter records");
    const share = screen.getByRole("button", { name: "Share" });
    const pager = screen.getByLabelText("Previous page");
    expect(share.parentElement?.className).toContain("resource-toolbar-utilities");
    expect(
      filter.compareDocumentPosition(share) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      share.compareDocumentPosition(pager) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  test("a single-axis collection replaces its group through the native picker", () => {
    const onGroupStackChange = vi.fn();
    renderToolbar({
      view: "list",
      maxGroupDepth: 1,
      groupStack: [{ field: "account" }],
      groupOptions: [
        { id: "account", label: "Account", group: { field: "account" } },
        { id: "platform", label: "Platform", group: { field: "platform" } },
      ],
      onGroupStackChange,
    });
    fireEvent.click(screen.getByLabelText("Group by"));
    fireEvent.click(screen.getByText("Platform"));
    expect(onGroupStackChange).toHaveBeenCalledWith([{ field: "platform" }]);
  });
  test("opts into wrapping for narrow containers", () => {
    renderToolbar({ wrap: true });
    expect(screen.getByLabelText("Data controls").className).toContain(
      "resource-toolbar-wrap",
    );
  });
  test("keeps filter, pager, and the list/board switcher; no view controls", () => {
    renderToolbar({
      view: "list",
      availableViews: ["list", "board"],
      filterOptions: [
        { id: "open", label: "Open", filter: { status: "open" } },
      ],
    });

    expect(screen.getByLabelText("Filter records")).toBeTruthy();
    expect(screen.getByLabelText("Previous page")).toBeTruthy();
    expect(screen.getByLabelText("List view")).toBeTruthy();
    expect(screen.getByLabelText("Board view")).toBeTruthy();

    // No calendar offered (no sources) and no view controls under list.
    expect(screen.queryByLabelText("Calendar view")).toBeNull();
    expect(screen.queryByLabelText("Previous period")).toBeNull();
  });

  test("hides favorites when no authenticated preference writer is available", () => {
    renderToolbar({ view: "list" });

    fireEvent.click(screen.getByLabelText("Filter"));
    expect(screen.queryByText("Favorites")).toBeNull();
  });

  test("shows favorites when the preference adapter supplies its writer", () => {
    renderToolbar({ view: "list", onFavoriteSave: vi.fn() });

    fireEvent.click(screen.getByLabelText("Filter and favorites"));
    expect(screen.getByText("Favorites")).toBeTruthy();
  });
});
