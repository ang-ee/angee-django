// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
  test("keeps presets curated while a custom-only catalog exposes supported groups", () => {
    const onGroupStackChange = vi.fn();
    renderToolbar({
      view: "list",
      groupOptions: [],
      customGroupOptions: [
        { id: "partner", label: "Supplier", group: { field: "partner" } },
        { id: "currency", label: "Currency", group: { field: "currency" } },
      ],
      onGroupStackChange,
    });

    fireEvent.click(screen.getByLabelText("Filter and group"));
    expect(screen.queryByRole("button", { name: "Supplier" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
    expect(screen.getByLabelText("Group field").textContent).toContain("Supplier");
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

    fireEvent.click(screen.getByLabelText("Filter and group"));
    fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
    expect(screen.getByLabelText("Group field").textContent).toContain("Platform");
  });

  test("uses supported custom date granularities and labels their active chip", () => {
    const onGroupStackChange = vi.fn();
    renderToolbar({
      view: "list",
      groupOptions: [],
      customGroupOptions: [{
        id: "invoice-date",
        label: "Invoice date",
        group: { field: "invoice_date" },
        type: "date",
        granularities: ["month", "year"],
      }],
      groupStack: [{ field: "invoice_date", granularity: "month" }],
      onGroupStackChange,
    });

    expect(screen.getByText("Invoice date · Month")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Filter and group"));
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
    fireEvent.click(screen.getByLabelText("Filter and group"));
    fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));

    rerender(<ResourceToolbar
      pager={PAGER}
      view="list"
      groupOptions={[]}
      customGroupOptions={[{
        id: "invoice-date", label: "Invoice date", group: { field: "invoice_date" },
        type: "date", granularities: ["month"],
      }]}
      onGroupStackChange={onGroupStackChange}
      onFilterTextChange={vi.fn()}
    />);

    expect(screen.getByLabelText("Group field").textContent).toContain("Invoice date");
    expect(screen.getByLabelText("Group granularity").textContent).toContain("Month");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onGroupStackChange).toHaveBeenCalledWith([
      { field: "invoice_date", granularity: "month" },
    ]);
  });

  test("preserves group depth and ignores an exact custom duplicate", () => {
    const onGroupStackChange = vi.fn();
    const props: Partial<ResourceToolbarProps> = {
      view: "list",
      maxGroupDepth: 1,
      groupOptions: [],
      customGroupOptions: [
        { id: "partner", label: "Supplier", group: { field: "partner" } },
      ],
      groupStack: [{ field: "status" }],
      onGroupStackChange,
    };
    const { rerender } = render(<ResourceToolbar pager={PAGER}
      onFilterTextChange={vi.fn()} {...props} />);
    fireEvent.click(screen.getByLabelText("Filter and group"));
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
    fireEvent.click(screen.getByLabelText("Filter and group"));
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
