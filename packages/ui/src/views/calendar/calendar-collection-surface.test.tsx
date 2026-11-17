// @vitest-environment happy-dom

import * as React from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import type { CalendarViewProps, Occurrence } from "./CalendarView";
import type { CalendarViewSpec } from "../resource/resource-view-types";
import type { AnyCalendarWindowSource } from "./use-calendar-window";
import { InAppLinkProvider, type InAppNavigator } from "../../lib/in-app-link";

// Capture the CalendarView props (so the interaction callbacks can be driven
// without FullCalendar) and stand in for the windowed-fetch owner so settled
// gating and error/retry are observable.
const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  calendarProps: undefined as CalendarViewProps | undefined,
  windowCalls: [] as Array<{ enabled: boolean | undefined; key?: string }>,
  resultFor: undefined as ((source: AnyCalendarWindowSource) => {
    occurrences: readonly Occurrence[]; fetching: boolean; error: Error | null; refetch: () => void;
  }) | undefined,
  result: {
    occurrences: [] as readonly Occurrence[],
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
}));

vi.mock("./CalendarView", () => ({
  CalendarView: (props: CalendarViewProps) => {
    mocks.calendarProps = props;
    return <div data-testid="calendar-view" />;
  },
}));

vi.mock("./use-calendar-window", () => ({
  useCalendarWindow: (source: AnyCalendarWindowSource) => {
    mocks.windowCalls.push({ enabled: source.enabled, key: source.key });
    return mocks.resultFor?.(source) ?? mocks.result;
  },
}));

import { CalendarCollectionSurface, calendarSourceToggles } from "./calendar-collection-surface";
import { ResourceViewProvider, useResourceView } from "../resource/resource-view-context";

const SOURCE = { document: {}, variables: () => ({}), select: () => [] } as
  unknown as AnyCalendarWindowSource;

function surfaceProps(spec: Partial<CalendarViewSpec> = {}): CalendarViewSpec {
  return { sources: [SOURCE], ...spec };
}

function Harness({ spec }: { spec: CalendarViewSpec }): React.ReactElement {
  const resourceView = useResourceView();
  return (
    <CalendarCollectionSurface
      resource="calendar.Event"
      resourceView={resourceView}
      calendar={spec}
      availableViews={["list", "board", "calendar"]}
    />
  );
}

function renderSurface(spec: CalendarViewSpec, navigateInApp?: InAppNavigator): void {
  const content = (
    <ResourceViewProvider scope="local" resource="calendar.Event" initialState={{ view: "calendar", anchor: "2026-06-15" }}>
      <Harness spec={spec} />
    </ResourceViewProvider>
  );
  render(
    navigateInApp ? <InAppLinkProvider navigate={navigateInApp}>{content}</InAppLinkProvider> : content,
  );
}

const OCC_WITH_ROUTE: Occurrence = {
  occurrence_id: "cev_a",
  event_sqid: "cev_a",
  title: "Design review",
  start: "2026-06-15T09:00:00.000Z",
  end: "2026-06-15T10:00:00.000Z",
  all_day: false,
  editable: true,
  to: "/calendar/cev_a",
};
const OCC_NO_ROUTE: Occurrence = { ...OCC_WITH_ROUTE, occurrence_id: "cev_b", to: undefined };

beforeEach(() => {
  mocks.navigate.mockClear();
  mocks.calendarProps = undefined;
  mocks.windowCalls = [];
  mocks.resultFor = undefined;
  mocks.result = {
    occurrences: [],
    fetching: false,
    error: null,
    refetch: vi.fn(),
  };
});

describe("CalendarCollectionSurface", () => {
  test("one toggle per labelled source hides its occurrences and pauses its fetch", () => {
    const events = { ...SOURCE, key: "events", label: "Events" } as AnyCalendarWindowSource;
    const agenda = { ...SOURCE, key: "agenda", label: "Agenda" } as AnyCalendarWindowSource;
    mocks.resultFor = (source) => ({
      ...mocks.result,
      occurrences: [{ ...OCC_NO_ROUTE, occurrence_id: source.key ?? "", title: source.key ?? "" }],
    });
    renderSurface(surfaceProps({ sources: [events, agenda] }));
    const toggles = screen.getByRole("group", { name: "Shown sources" });
    expect(within(toggles).getByRole("button", { name: "Agenda" }).getAttribute("aria-pressed")).toBe("true");
    expect(mocks.calendarProps?.occurrences.map((occurrence) => occurrence.occurrence_id)).toEqual(["events", "agenda"]);
    mocks.windowCalls = [];
    fireEvent.click(within(toggles).getByRole("button", { name: "Agenda" }));
    expect(mocks.calendarProps?.occurrences.map((occurrence) => occurrence.occurrence_id)).toEqual(["events"]);
    expect(mocks.windowCalls.filter((call) => call.key === "agenda").every((call) => call.enabled === false)).toBe(true);
    expect(within(toggles).getByRole("button", { name: "Agenda" }).getAttribute("aria-pressed")).toBe("false");
  });

  test("a hidden source named by the view state starts hidden", () => {
    const events = { ...SOURCE, key: "events", label: "Events" } as AnyCalendarWindowSource;
    const agenda = { ...SOURCE, key: "agenda", label: "Agenda" } as AnyCalendarWindowSource;
    render(
      <ResourceViewProvider scope="local" resource="calendar.Event"
        initialState={{ view: "calendar", anchor: "2026-06-15", hiddenSources: ["agenda"] }}>
        <Harness spec={surfaceProps({ sources: [events, agenda] })} />
      </ResourceViewProvider>,
    );
    expect(screen.getByRole("button", { name: "Agenda" }).getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("button", { name: "Events" }).getAttribute("aria-pressed")).toBe("true");
  });

  test("source toggles need unique keys a URL carries; one source gets none", () => {
    const keyed = (key?: string, label?: string) => ({ ...SOURCE, key, label }) as AnyCalendarWindowSource;
    expect(calendarSourceToggles([keyed("events", "Events"), keyed("tasks")])).toEqual([{ value: "events", label: "Events" }]);
    expect(calendarSourceToggles([keyed("events", "Events")])).toEqual([]);
    expect(() => calendarSourceToggles([keyed("a"), keyed("a")])).toThrow(/share the key "a"/);
    expect(() => calendarSourceToggles([keyed(undefined, "Orphan"), keyed("b")])).toThrow(/needs a key/);
    expect(() => calendarSourceToggles([keyed("two words"), keyed("b")])).toThrow(/may hold only/);
  });

  test("content occurrences use the inherited navigation owner", () => {
    const navigateInApp = vi.fn();
    renderSurface(surfaceProps(), navigateInApp);
    mocks.calendarProps?.onEventClick?.(OCC_WITH_ROUTE);
    expect(navigateInApp).toHaveBeenCalledExactlyOnceWith("/calendar/cev_a");
    expect(mocks.navigate).not.toHaveBeenCalled();
  });
  test("gates the fetch until the grid reports its window (settled)", () => {
    renderSurface(surfaceProps());
    // First render: the seed window is degenerate, so the source is not enabled.
    expect(mocks.windowCalls.every((call) => call.enabled === false)).toBe(true);

    // The grid reports its padded window; the surface settles and enables the fetch.
    mocks.windowCalls = [];
    act(() => {
      mocks.calendarProps?.onRangeChange?.({
        start: new Date("2026-05-25T00:00:00.000Z"),
        end: new Date("2026-07-06T00:00:00.000Z"),
      });
    });
    expect(mocks.windowCalls.at(-1)?.enabled).toBe(true);
  });

  test("navigates only for an occurrence carrying a source-declared route", () => {
    renderSurface(surfaceProps());
    mocks.calendarProps?.onEventClick?.(OCC_WITH_ROUTE);
    expect(mocks.navigate).toHaveBeenCalledWith({ href: "/calendar/cev_a" });

    mocks.navigate.mockClear();
    mocks.calendarProps?.onEventClick?.(OCC_NO_ROUTE);
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  test("forwards the reschedule and quick-create seams to the grid", () => {
    const onReschedule = vi.fn();
    const onSelectRange = vi.fn();
    renderSurface(surfaceProps({ onReschedule, onSelectRange }));

    expect(mocks.calendarProps?.onEventDrop).toBe(onReschedule);
    mocks.calendarProps?.onSelectRange?.(
      new Date("2026-06-17T14:00:00.000Z"),
      new Date("2026-06-17T15:00:00.000Z"),
    );
    expect(onSelectRange).toHaveBeenCalledTimes(1);
  });

  test("shows the error banner with a retry that refetches", () => {
    const refetch = vi.fn();
    mocks.result = {
      occurrences: [],
      fetching: false,
      error: new Error("boom"),
      refetch,
    };
    renderSurface(surfaceProps());

    fireEvent.click(screen.getByText("Retry"));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
