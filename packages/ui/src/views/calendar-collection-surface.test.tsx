// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { CalendarViewProps, Occurrence } from "./CalendarView";
import type { CalendarViewSpec } from "./resource-view-types";
import type { AnyCalendarWindowSource } from "./use-calendar-window";

// Capture the CalendarView props (so the interaction callbacks can be driven
// without FullCalendar) and stand in for the windowed-fetch owner so settled
// gating and error/retry are observable.
const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  calendarProps: undefined as CalendarViewProps | undefined,
  windowCalls: [] as Array<{ enabled: boolean | undefined }>,
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
    mocks.windowCalls.push({ enabled: source.enabled });
    return mocks.result;
  },
}));

import { CalendarCollectionSurface } from "./calendar-collection-surface";
import { ResourceViewProvider, useResourceView } from "./resource-view-context";

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

function renderSurface(spec: CalendarViewSpec): void {
  render(
    <ResourceViewProvider
      scope="local"
      resource="calendar.Event"
      initialState={{ view: "calendar", anchor: "2026-06-15" }}
    >
      <Harness spec={spec} />
    </ResourceViewProvider>,
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
  mocks.result = {
    occurrences: [],
    fetching: false,
    error: null,
    refetch: vi.fn(),
  };
});
afterEach(cleanup);

describe("CalendarCollectionSurface", () => {
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
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/calendar/cev_a" });

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
