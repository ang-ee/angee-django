// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
  gridMounts: 0,
  windowCalls: [] as Array<{ enabled: boolean | undefined; hidden: string[]; keys: Array<string | undefined> }>,
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
    React.useEffect(() => {
      mocks.gridMounts += 1;
    }, []);
    return <div data-testid="calendar-view" />;
  },
}));

vi.mock("./use-calendar-window", () => ({
  useCalendarWindow: (
    sources: readonly AnyCalendarWindowSource[],
    _window: unknown,
    options: { hidden?: ReadonlySet<string>; enabled?: boolean } = {},
  ) => {
    mocks.windowCalls.push({
      enabled: options.enabled, hidden: [...(options.hidden ?? [])], keys: sources.map((source) => source.key),
    });
    if (!mocks.resultFor) return mocks.result;
    const shown = sources.filter((source) => source.key === undefined || !options.hidden?.has(source.key));
    return { ...mocks.result, occurrences: shown.flatMap((source) => mocks.resultFor!(source).occurrences) };
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
  mocks.gridMounts = 0;
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
    expect(mocks.windowCalls.at(-1)?.hidden).toEqual(["agenda"]);
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

  test("a hidden key without a toggle is ignored; hiding every source says so and offers them back", () => {
    const events = { ...SOURCE, key: "events", label: "Events" } as AnyCalendarWindowSource;
    const agenda = { ...SOURCE, key: "agenda", label: "Agenda" } as AnyCalendarWindowSource;
    const lone = { ...SOURCE, key: "lone", label: "Lone" } as AnyCalendarWindowSource;
    const renderWith = (sources: AnyCalendarWindowSource[], hiddenSources: string[]) => render(
      <ResourceViewProvider scope="local" resource="calendar.Event"
        initialState={{ view: "calendar", anchor: "2026-06-15", hiddenSources }}>
        <Harness spec={surfaceProps({ sources })} />
      </ResourceViewProvider>,
    );
    mocks.resultFor = (source) => ({
      ...mocks.result,
      occurrences: [{ ...OCC_NO_ROUTE, occurrence_id: source.key ?? "", title: source.key ?? "" }],
    });
    // A lone source has no toggle, so a URL naming it cannot hide it.
    renderWith([lone], ["lone"]);
    expect(mocks.calendarProps?.occurrences.map((occurrence) => occurrence.occurrence_id)).toEqual(["lone"]);
    expect(screen.queryByText("Every source is hidden, so the calendar shows nothing.")).toBeNull();
    cleanup();
    renderWith([events, agenda], ["events", "agenda"]);
    expect(screen.getByText("Every source is hidden, so the calendar shows nothing.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(screen.getByRole("button", { name: "Agenda" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByText("Every source is hidden, so the calendar shows nothing.")).toBeNull();
  });

  test("the grid stays mounted while the source set grows and shrinks", () => {
    const events = { ...SOURCE, key: "events", label: "Events" } as AnyCalendarWindowSource;
    const agenda = { ...SOURCE, key: "agenda", label: "Agenda" } as AnyCalendarWindowSource;
    const view = render(
      <ResourceViewProvider scope="local" resource="calendar.Event" initialState={{ view: "calendar", anchor: "2026-06-15" }}>
        <Harness spec={surfaceProps({ sources: [events] })} />
      </ResourceViewProvider>,
    );
    for (const sources of [[events, agenda], [events]]) {
      view.rerender(
        <ResourceViewProvider scope="local" resource="calendar.Event" initialState={{ view: "calendar", anchor: "2026-06-15" }}>
          <Harness spec={surfaceProps({ sources })} />
        </ResourceViewProvider>,
      );
    }
    expect(mocks.gridMounts).toBe(1);
    expect(mocks.windowCalls.at(-1)?.keys).toEqual(["events"]);
  });

  test("a source label may name an addon message key", () => {
    const keyed = { ...SOURCE, key: "tasks", label: { namespace: "tasks", key: "source.mine", fallback: "My tasks" } };
    const other = { ...SOURCE, key: "events", label: "Events" } as AnyCalendarWindowSource;
    expect(calendarSourceToggles([keyed as AnyCalendarWindowSource, other])[0]).toEqual({ value: "tasks", label: "My tasks" });
    const i18n = { getFixedT: () => (key: string) => `translated ${key}` };
    expect(calendarSourceToggles([keyed as AnyCalendarWindowSource, other], i18n)[0]?.label).toBe("translated source.mine");
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
