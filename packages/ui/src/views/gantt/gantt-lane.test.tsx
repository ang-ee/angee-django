// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { InAppLinkProvider } from "../../lib/in-app-link";
import { GanttLane, withGanttLaneNotes } from "./gantt-lane";

afterEach(cleanup);

describe("declared Gantt lanes", () => {
  test("renders a linked title and named avatars, omitting empty secondary parts", () => {
    render(<GanttLane details={{ title: "North team", href: "/teams/north", secondary: " ",
      people: [{ id: "ada", name: "Ada Lovelace" }, { id: "empty", name: " " }] }} />);
    expect(screen.getByRole("link", { name: "North team" }).getAttribute("href")).toBe("/teams/north");
    expect(screen.getByText("Ada Lovelace")).toBeTruthy();
    expect(screen.queryByText("empty")).toBeNull();
    expect(screen.queryByTitle(" ")).toBeNull();
  });

  test("routes a plain link click, keeps modified links native, and prefers the host opener", () => {
    const onNavigate = vi.fn();
    const onOpen = vi.fn();
    const details = { title: "North team", href: "/teams/north" };
    const view = render(<InAppLinkProvider navigate={onNavigate}><GanttLane details={details} /></InAppLinkProvider>);
    const link = screen.getByRole("link", { name: "North team" });
    expect(fireEvent.click(link, { ctrlKey: true })).toBe(true);
    expect(onNavigate).not.toHaveBeenCalled();
    expect(fireEvent.click(link)).toBe(false);
    expect(onNavigate).toHaveBeenCalledWith("/teams/north");
    view.rerender(<InAppLinkProvider navigate={onNavigate}><GanttLane details={details} onOpen={onOpen} /></InAppLinkProvider>);
    fireEvent.click(link);
    expect(onOpen).toHaveBeenCalledOnce();
    expect(onNavigate).toHaveBeenCalledOnce();
  });

  test("puts the owner note only after the last scheduled bar in that lane", () => {
    const first = { id: "first", title: "First", resourceId: "north", start: new Date(2026, 8, 1), end: new Date(2026, 8, 4) };
    const last = { id: "last", title: "Last", resourceId: "north", start: new Date(2026, 8, 5), end: new Date(2026, 8, 10) };
    const other = { id: "other", title: "Other", resourceId: "south", start: new Date(2026, 8, 7), end: new Date(2026, 8, 9) };
    expect(withGanttLaneNotes([last, first, other], new Map([
      ["north", { title: "North", note: "Next: review" }],
      ["south", { title: "South", note: " " }],
    ]))).toEqual([{ ...last, note: "Next: review" }, first, other]);
  });
});
