// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import { Gantt } from "./gantt";
import { GanttView } from "./gantt-view";
import GanttSurface from "./gantt-surface";

afterEach(cleanup);

const resources = [{ id: "project", title: "Project" }, { id: "empty", title: "Unscheduled" }];
const events = [
  { id: "first", resourceId: "project", title: "First phase", start: new Date(2026, 8, 22), end: new Date(2026, 10, 2), allDay: true },
  { id: "second", resourceId: "project", title: "Second phase", start: new Date(2026, 9, 7), end: new Date(2026, 10, 9), allDay: true },
  { id: "third", resourceId: "project", title: "Third phase", start: new Date(2026, 9, 8), end: new Date(2026, 10, 3), allDay: true },
];

describe("Gantt row and control geometry", () => {
  test("packs overlapping bars inside matching, non-shrinking tree and timeline rows", () => {
    const view = render(<GanttSurface resources={resources} events={events} date={new Date(2026, 9, 1)} defaultScale="quarter" />);
    const row = view.container.querySelector<HTMLElement>('[data-gantt-resource="project"]')!;
    const treeRow = view.container.querySelector<HTMLElement>('[data-slot="gantt-row-group"][data-gantt-row-id="project"]')!;
    const segments = [...row.querySelectorAll<HTMLElement>("[data-lane]")];
    expect(segments.map((segment) => segment.dataset.lane)).toEqual(["0", "1", "2"]);
    expect(segments.every((segment) => segment.dataset.laneCount === "3")).toBe(true);
    expect(parseFloat(row.style.height)).toBeGreaterThan(3.5);
    expect(treeRow.style.height).toBe(row.style.height);
    expect(row.classList.contains("shrink-0")).toBe(true);
    expect(treeRow.classList.contains("shrink-0")).toBe(true);
    expect(row.parentElement?.classList.contains("shrink-0")).toBe(true);
    for (const segment of segments) {
      expect(parseFloat(segment.style.top)).toBeGreaterThanOrEqual(0);
      expect(parseFloat(segment.style.top) + parseFloat(segment.style.height)).toBeLessThan(parseFloat(row.style.height));
    }
    expect(view.container.querySelector<HTMLElement>('[data-gantt-resource="empty"]')?.style.height).toBe("3.5rem");
    expect(view.container.querySelector<HTMLElement>('[data-slot="gantt-tree-pane"]')?.style.width).toBe("224px");
    expect(view.container.querySelector<HTMLElement>('[data-slot="gantt-tree-cell"]')?.style.width).toBe("224px");
    expect(screen.getByRole("button", { name: /First phase, Sep 22 - Nov 1, 2026/ })).toBeTruthy();
  });

  test("reuses a lane after a non-overlapping bar ends", () => {
    const view = render(<GanttSurface resources={resources} events={[
      { ...events[0]!, end: new Date(2026, 9, 7) }, events[1]!,
    ]} date={new Date(2026, 9, 1)} defaultScale="quarter" />);
    expect([...view.container.querySelectorAll<HTMLElement>("[data-lane]")].map((segment) => segment.dataset.lane)).toEqual(["0", "0"]);
  });

  test("reserves space for zoom outside the scrolling panes and keeps buttons functional", () => {
    const view = render(<GanttSurface resources={resources} events={events} date={new Date(2026, 9, 1)} defaultScale="quarter" />);
    const zoom = view.container.querySelector<HTMLElement>('[data-slot="gantt-zoom"]')!;
    const row = view.container.querySelector<HTMLElement>("[data-gantt-resource]")!;
    expect(zoom.closest('[data-slot="gantt-timeline-pane"]')).toBeNull();
    expect(zoom.parentElement?.dataset.slot).toBe("gantt-view");
    expect(zoom.classList.contains("absolute")).toBe(false);
    const width = row.style.minWidth;
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(row.style.minWidth).not.toBe(width);
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(row.style.minWidth).toBe(width);
  });

  test("the headless range renders bars beyond the anchor period and updates with new bounds", () => {
    const view = render(<Gantt resources={resources} events={events} defaultScale="quarter" date={new Date(2026, 0, 1)}
      range={{ start: new Date(2026, 8, 21), end: new Date(2026, 10, 23) }}><GanttView /></Gantt>);
    expect(view.container.querySelectorAll("[data-lane]")).toHaveLength(3);
    view.rerender(<Gantt resources={resources} events={events} defaultScale="quarter" date={new Date(2026, 0, 1)}
      range={{ start: new Date(2026, 11, 1), end: new Date(2027, 0, 1) }}><GanttView /></Gantt>);
    expect(view.container.querySelectorAll("[data-lane]")).toHaveLength(0);
  });
});
