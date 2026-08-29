// @vitest-environment happy-dom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { CalendarView, type Occurrence } from "./CalendarView";

// Stand in for FullCalendar's drag engine: capture the options the surface hands
// it and expose the imperative `getApi` the controlled effects drive, so the
// tests can simulate its interaction callbacks without its heavy DOM.
const fcMock = vi.hoisted(() => ({
  lastProps: undefined as Record<string, unknown> | undefined,
  changeViewCalls: [] as string[],
  gotoDateCalls: [] as Date[],
}));

vi.mock("@fullcalendar/react", async () => {
  const React = await import("react");
  const Mock = React.forwardRef(
    (props: Record<string, unknown>, ref: React.Ref<unknown>) => {
      fcMock.lastProps = props;
      React.useImperativeHandle(ref, () => ({
        getApi: () => ({
          view: {
            activeStart: new Date("2026-06-01T00:00:00.000Z"),
            activeEnd: new Date("2026-07-06T00:00:00.000Z"),
          },
          changeView: (id: string) => fcMock.changeViewCalls.push(id),
          gotoDate: (date: Date) => fcMock.gotoDateCalls.push(date),
        }),
      }));
      const events = (props.events as Array<{ id: string; title: string }>) ?? [];
      return React.createElement(
        "div",
        { "data-testid": "fc", "data-view": props.initialView as string },
        events.map((event) => React.createElement("div", { key: event.id }, event.title)),
      );
    },
  );
  return { default: Mock };
});
vi.mock("@fullcalendar/daygrid", () => ({ default: { name: "dayGrid" } }));
vi.mock("@fullcalendar/timegrid", () => ({ default: { name: "timeGrid" } }));
vi.mock("@fullcalendar/interaction", () => ({ default: { name: "interaction" } }));

interface FcEvent {
  id: string;
  title: string;
  editable: boolean;
}
interface EventDropArg {
  event: { start: Date | null; end: Date | null; extendedProps: { occurrence: Occurrence } };
  revert: () => void;
}
interface SurfaceProps {
  initialView: string;
  events: FcEvent[];
  datesSet?: (arg: { start: Date; end: Date }) => void;
  select?: (arg: { start: Date; end: Date }) => void;
  eventDrop?: (arg: EventDropArg) => void;
  eventResize?: (arg: EventDropArg) => void;
  eventClick?: (arg: { event: { extendedProps: { occurrence: Occurrence } } }) => void;
}

function surface(): SurfaceProps {
  if (!fcMock.lastProps) throw new Error("FullCalendar surface not mounted");
  return fcMock.lastProps as unknown as SurfaceProps;
}

const EDITABLE: Occurrence = {
  occurrence_id: "cev_a",
  event_sqid: "cev_a",
  title: "Design review",
  start: "2026-06-15T09:00:00.000Z",
  end: "2026-06-15T10:00:00.000Z",
  all_day: false,
  editable: true,
};
const RECURRING: Occurrence = {
  occurrence_id: "cev_b:20260615T090000Z",
  event_sqid: "cev_b",
  title: "Team standup",
  start: "2026-06-15T09:00:00.000Z",
  end: "2026-06-15T09:15:00.000Z",
  all_day: false,
  editable: false,
};
const RANGE = {
  start: new Date("2026-06-01T00:00:00.000Z"),
  end: new Date("2026-07-06T00:00:00.000Z"),
};

beforeEach(() => {
  fcMock.lastProps = undefined;
  fcMock.changeViewCalls = [];
  fcMock.gotoDateCalls = [];
});
afterEach(cleanup);

describe("CalendarView", () => {
  test("renders the occurrence array in month view", async () => {
    render(<CalendarView view="month" range={RANGE} occurrences={[EDITABLE, RECURRING]} />);
    // The FullCalendar bundle is code-split behind Suspense — await the boundary.
    expect(await screen.findByText("Design review")).toBeTruthy();
    expect(screen.getByText("Team standup")).toBeTruthy();
    expect(surface().initialView).toBe("dayGridMonth");
  });

  test("maps week and day modes to the FullCalendar timegrid views", async () => {
    render(<CalendarView view="week" range={RANGE} occurrences={[]} />);
    await screen.findByTestId("fc");
    expect(surface().initialView).toBe("timeGridWeek");
    cleanup();
    render(<CalendarView view="day" range={RANGE} occurrences={[]} />);
    await screen.findByTestId("fc");
    expect(surface().initialView).toBe("timeGridDay");
  });

  test("switches FullCalendar when the controlled view changes", async () => {
    const { rerender } = render(
      <CalendarView view="month" range={RANGE} occurrences={[]} />,
    );
    await screen.findByTestId("fc");
    rerender(<CalendarView view="week" range={RANGE} occurrences={[]} />);
    expect(fcMock.changeViewCalls).toContain("timeGridWeek");
  });

  test("a drag on an editable occurrence fires onEventDrop with the new bounds", async () => {
    const onEventDrop = vi.fn();
    render(
      <CalendarView
        view="week"
        range={RANGE}
        occurrences={[EDITABLE]}
        onEventDrop={onEventDrop}
      />,
    );
    await screen.findByTestId("fc");

    const revert = vi.fn();
    const start = new Date("2026-06-16T11:00:00.000Z");
    const end = new Date("2026-06-16T12:00:00.000Z");
    surface().eventDrop?.({
      event: { start, end, extendedProps: { occurrence: EDITABLE } },
      revert,
    });

    expect(onEventDrop).toHaveBeenCalledWith(EDITABLE, start, end);
    expect(revert).not.toHaveBeenCalled();
  });

  test("a resize on an editable occurrence reschedules through onEventDrop", async () => {
    const onEventDrop = vi.fn();
    render(
      <CalendarView
        view="week"
        range={RANGE}
        occurrences={[EDITABLE]}
        onEventDrop={onEventDrop}
      />,
    );
    await screen.findByTestId("fc");

    // A resize rides the same seam as a drop — new bounds, same handler shape.
    const revert = vi.fn();
    const start = new Date("2026-06-15T09:00:00.000Z");
    const end = new Date("2026-06-15T11:00:00.000Z");
    surface().eventResize?.({
      event: { start, end, extendedProps: { occurrence: EDITABLE } },
      revert,
    });

    expect(onEventDrop).toHaveBeenCalledWith(EDITABLE, start, end);
    expect(revert).not.toHaveBeenCalled();
  });

  test("reverts the optimistic change when the reschedule handler rejects", async () => {
    const onEventDrop = vi.fn().mockRejectedValue(new Error("server rejected"));
    render(
      <CalendarView
        view="week"
        range={RANGE}
        occurrences={[EDITABLE]}
        onEventDrop={onEventDrop}
      />,
    );
    await screen.findByTestId("fc");

    const revert = vi.fn();
    surface().eventDrop?.({
      event: {
        start: new Date("2026-06-16T11:00:00.000Z"),
        end: new Date("2026-06-16T12:00:00.000Z"),
        extendedProps: { occurrence: EDITABLE },
      },
      revert,
    });

    // FullCalendar already moved the event; a rejected write reverts it.
    await waitFor(() => expect(revert).toHaveBeenCalledTimes(1));
    expect(onEventDrop).toHaveBeenCalledTimes(1);
  });

  test("keeps the new slot when the reschedule handler resolves", async () => {
    const onEventDrop = vi.fn().mockResolvedValue(undefined);
    render(
      <CalendarView
        view="week"
        range={RANGE}
        occurrences={[EDITABLE]}
        onEventDrop={onEventDrop}
      />,
    );
    await screen.findByTestId("fc");

    const revert = vi.fn();
    surface().eventDrop?.({
      event: {
        start: new Date("2026-06-16T11:00:00.000Z"),
        end: new Date("2026-06-16T12:00:00.000Z"),
        extendedProps: { occurrence: EDITABLE },
      },
      revert,
    });

    await waitFor(() => expect(onEventDrop).toHaveBeenCalledTimes(1));
    expect(revert).not.toHaveBeenCalled();
  });

  test("a non-editable occurrence is not draggable and never reschedules", async () => {
    const onEventDrop = vi.fn();
    render(
      <CalendarView
        view="week"
        range={RANGE}
        occurrences={[RECURRING]}
        onEventDrop={onEventDrop}
      />,
    );
    await screen.findByTestId("fc");

    // The FullCalendar event carries the per-event gate that withholds the drag.
    const event = surface().events.find((candidate) => candidate.id === RECURRING.occurrence_id);
    expect(event?.editable).toBe(false);

    // And the handler reverts rather than firing, even if a drop reaches it.
    const revert = vi.fn();
    surface().eventDrop?.({
      event: {
        start: new Date("2026-06-16T11:00:00.000Z"),
        end: new Date("2026-06-16T12:00:00.000Z"),
        extendedProps: { occurrence: RECURRING },
      },
      revert,
    });
    expect(onEventDrop).not.toHaveBeenCalled();
    expect(revert).toHaveBeenCalledTimes(1);
  });

  test("a range select fires onSelectRange", async () => {
    const onSelectRange = vi.fn();
    render(
      <CalendarView
        view="week"
        range={RANGE}
        occurrences={[]}
        onSelectRange={onSelectRange}
      />,
    );
    await screen.findByTestId("fc");

    const start = new Date("2026-06-17T14:00:00.000Z");
    const end = new Date("2026-06-17T15:00:00.000Z");
    surface().select?.({ start, end });
    expect(onSelectRange).toHaveBeenCalledWith(start, end);
  });

  test("an event click fires onEventClick with the occurrence", async () => {
    const onEventClick = vi.fn();
    render(
      <CalendarView
        view="month"
        range={RANGE}
        occurrences={[EDITABLE]}
        onEventClick={onEventClick}
      />,
    );
    await screen.findByTestId("fc");

    surface().eventClick?.({ event: { extendedProps: { occurrence: EDITABLE } } });
    expect(onEventClick).toHaveBeenCalledWith(EDITABLE);
  });

  test("navigation fires onRangeChange once per distinct window", async () => {
    const onRangeChange = vi.fn();
    render(
      <CalendarView
        view="month"
        range={RANGE}
        occurrences={[]}
        onRangeChange={onRangeChange}
      />,
    );
    await screen.findByTestId("fc");

    const next = {
      start: new Date("2026-07-01T00:00:00.000Z"),
      end: new Date("2026-08-01T00:00:00.000Z"),
    };
    surface().datesSet?.(next);
    expect(onRangeChange).toHaveBeenCalledWith(next);

    onRangeChange.mockClear();
    surface().datesSet?.(next);
    expect(onRangeChange).not.toHaveBeenCalled();
  });
});
