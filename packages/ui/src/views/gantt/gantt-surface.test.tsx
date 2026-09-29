// @vitest-environment happy-dom

import { act, cleanup, render, screen } from "@testing-library/react";
import { addWeeks, startOfDay, startOfWeek } from "date-fns";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, createAngeeI18nInstance } from "../../runtime";
import type { GanttProps } from "./gantt";
import GanttSurface from "./gantt-surface";

const drawing = vi.hoisted(() => ({ props: null as GanttProps | null }));
vi.mock("./gantt", () => ({ Gantt: (props: GanttProps) => { drawing.props = props; return null; } }));
vi.mock("./gantt-view", () => ({ GanttView: () => null }));
vi.mock("./gantt-nav", () => ({
  GanttNav: () => null, GanttNavToday: () => null, GanttNavPrev: () => null,
  GanttNavNext: () => null, GanttTitle: () => null, GanttScaleSwitcher: () => null,
}));
afterEach(() => { cleanup(); drawing.props = null; });

describe("Gantt drawing adapter", () => {
  test("disables drag, resize, slot selection, row selection and write callbacks", () => {
    render(<GanttSurface resources={[]} events={[]} date={new Date(2026, 8, 1)} />);
    expect(drawing.props?.interactions).toEqual({ drag: false, resize: false, selectSlot: false });
    expect(drawing.props?.rowCheckboxes).toBe(false);
    expect(drawing.props?.scheduleMode).toBe("multiple");
    expect(drawing.props?.initialCenter).toBe("anchor");
    expect(drawing.props?.onEventUpdate).toBeUndefined();
    expect(drawing.props?.onEventsChange).toBeUndefined();
    expect(drawing.props?.onSelectSlot).toBeUndefined();
    expect(drawing.props?.onSelectedRowsChange).toBeUndefined();
    expect(drawing.props?.onResourceReorder).toBeUndefined();
  });

  test("forwards controlled dates and retains readonly array identities", () => {
    const date = new Date(2026, 8, 1);
    const resources = Object.freeze([{ id: "lane-a", title: "Alpha" }]);
    const events = Object.freeze([{ id: "bar-a", resourceId: "lane-a", title: "First", start: date, end: date }]);
    const onDateChange = vi.fn();
    const view = render(<GanttSurface resources={resources} events={events} date={date} onDateChange={onDateChange} />);
    const labels = drawing.props?.i18n;
    expect(drawing.props?.resources).toBe(resources);
    expect(drawing.props?.events).toBe(events);
    expect(drawing.props?.date).toBe(date);
    act(() => drawing.props?.onDateChange?.(date));
    expect(onDateChange).toHaveBeenCalledWith(date);
    view.rerender(<GanttSurface resources={resources} events={events} date={date} onDateChange={onDateChange} />);
    expect(drawing.props?.i18n).toBe(labels);
  });

  test("custom row content replaces the title inside the fixed sidebar contract", () => {
    const resource = { id: "lane", title: "Default title" };
    render(<GanttSurface resources={[resource]} events={[]} sidebarWidth={240} minRowHeight={4}
      renderRowContent={() => <a href="/lane">Custom title</a>} />);
    expect(drawing.props?.treePanel).toMatchObject({ width: 240, minWidth: 240, maxWidth: 240, nameColumnWidth: 240, resizable: false });
    expect(drawing.props?.metrics?.minRowHeight).toBe(4);
    render(<>{drawing.props?.renderResourceLabel?.({ resource, depth: 0, isGroup: false, collapsed: false })}</>);
    expect(screen.getByRole("link", { name: "Custom title" })).toBeTruthy();
    expect(screen.queryByText("Default title")).toBeNull();
  });

  test("fits every bar and today to weeks, then releases the range on navigation", () => {
    const today = startOfDay(new Date());
    const first = addWeeks(today, -10);
    const last = addWeeks(today, 10);
    render(<GanttSurface resources={[]} events={[{ id: "bar", title: "Interval", start: first, end: last }]} fitToEvents />);
    expect(drawing.props?.range).toEqual({ start: startOfWeek(first, { weekStartsOn: 1 }), end: addWeeks(startOfWeek(last, { weekStartsOn: 1 }), 2) });
    expect(drawing.props?.initialCenter).toEqual(startOfWeek(first, { weekStartsOn: 1 }));
    expect(drawing.props?.infiniteScroll).toBe(false);
    act(() => drawing.props?.onDateChange?.(addWeeks(today, 1)));
    expect(drawing.props?.range).toBeUndefined();
    expect(drawing.props?.infiniteScroll).toBe(true);
  });

  test("includes today for past-only or empty schedules and releases fitting on scale change", () => {
    const today = startOfDay(new Date());
    render(<GanttSurface resources={[]} events={[]} fitToEvents />);
    expect(drawing.props?.range).toEqual({ start: startOfWeek(today, { weekStartsOn: 1 }), end: addWeeks(startOfWeek(today, { weekStartsOn: 1 }), 2) });
    act(() => drawing.props?.onScaleChange?.("week"));
    expect(drawing.props?.range).toBeUndefined();
  });

  test.each([0, 1, 2])("resolves native English plurals for %s events and days", (count) => {
    render(<GanttSurface resources={[]} events={[]} date={new Date(2026, 8, 1)} />);
    expect(drawing.props?.i18n?.labels?.events?.(count)).toBe(`${count} ${count === 1 ? "event" : "events"}`);
    expect(drawing.props?.i18n?.labels?.durationDays?.(count)).toBe(`${count} ${count === 1 ? "day" : "days"}`);
  });

  test("delegates locale plural categories to i18next instead of an English singular check", () => {
    const i18n = createAngeeI18nInstance({});
    i18n.addResourceBundle("cs", "ui", {
      "gantt.events_one": "{count} událost", "gantt.events_few": "{count} události", "gantt.events_other": "{count} událostí",
      "gantt.days_one": "{count} den", "gantt.days_few": "{count} dny", "gantt.days_other": "{count} dní",
    });
    void i18n.changeLanguage("cs");
    render(<AppRuntimeProvider runtime={{ i18n }}><GanttSurface resources={[]} events={[]} date={new Date(2026, 8, 1)} /></AppRuntimeProvider>);
    expect(drawing.props?.i18n?.labels?.events?.(3)).toBe("3 události");
    expect(drawing.props?.i18n?.labels?.durationDays?.(3)).toBe("3 dny");
    expect(drawing.props?.i18n?.labels?.events?.(5)).toBe("5 událostí");
  });
});
