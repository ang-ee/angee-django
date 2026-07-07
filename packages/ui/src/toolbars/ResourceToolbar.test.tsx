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
  render(<ResourceToolbar pager={PAGER} onViewChange={vi.fn()} {...props} />);
}

afterEach(cleanup);

describe("ResourceToolbar under the calendar kind", () => {
  test("renders the view controls and hides filter/pager/group-by", () => {
    renderToolbar({
      view: "calendar",
      availableViews: ["list", "board", "calendar"],
      viewControls: viewControls(),
      // Group + filter options are declared but must not render under calendar.
      filterOptions: [{ id: "open", label: "Open", filter: { status: "open" } }],
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
  test("keeps filter, pager, and the list/board switcher; no view controls", () => {
    renderToolbar({
      view: "list",
      availableViews: ["list", "board"],
      filterOptions: [{ id: "open", label: "Open", filter: { status: "open" } }],
    });

    expect(screen.getByLabelText("Filter records")).toBeTruthy();
    expect(screen.getByLabelText("Previous page")).toBeTruthy();
    expect(screen.getByLabelText("List view")).toBeTruthy();
    expect(screen.getByLabelText("Board view")).toBeTruthy();

    // No calendar offered (no sources) and no view controls under list.
    expect(screen.queryByLabelText("Calendar view")).toBeNull();
    expect(screen.queryByLabelText("Previous period")).toBeNull();
  });
});
