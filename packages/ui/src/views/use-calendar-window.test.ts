// @vitest-environment happy-dom

import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { TypedDocumentNode } from "@angee/refine";

import type { CalendarWindow, Occurrence } from "./CalendarView";
import {
  calendarWindowBounds,
  useCalendarWindow,
  type CalendarWindowSource,
} from "./use-calendar-window";

// The transport owner is `@angee/refine`'s authored-query hook; the window
// adapter only composes it. Stub it to record the variables it receives (so the
// refetch contract is observable) and to return a fixture result to map.
const authored = vi.hoisted(() => ({
  calls: [] as Array<{ variables: unknown; options: unknown }>,
  data: undefined as unknown,
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: (
    _document: unknown,
    variables: unknown,
    options: unknown,
  ) => {
    authored.calls.push({ variables, options });
    return { data: authored.data, fetching: false, error: null, refetch: () => {} };
  },
}));

interface OccurrencesResult {
  event_occurrences: Occurrence[];
}
interface WindowVariables {
  window_start: string;
  window_end: string;
}
const DOCUMENT = {} as TypedDocumentNode<OccurrencesResult, WindowVariables>;

const SOURCE: CalendarWindowSource<OccurrencesResult, WindowVariables> = {
  document: DOCUMENT,
  variables: (window) => calendarWindowBounds(window),
  select: (data) => data?.event_occurrences,
  models: ["calendar/event"],
};

const JUNE: CalendarWindow = {
  start: new Date("2026-06-01T00:00:00.000Z"),
  end: new Date("2026-07-06T00:00:00.000Z"),
};
const JULY: CalendarWindow = {
  start: new Date("2026-07-01T00:00:00.000Z"),
  end: new Date("2026-08-03T00:00:00.000Z"),
};

const OCC: Occurrence = {
  occurrence_id: "cev_a",
  event_sqid: "cev_a",
  title: "Design review",
  start: "2026-06-15T09:00:00.000Z",
  end: "2026-06-15T10:00:00.000Z",
  all_day: false,
  editable: true,
};

beforeEach(() => {
  authored.calls = [];
  authored.data = undefined;
});
afterEach(cleanup);

describe("useCalendarWindow", () => {
  test("maps the occurrence rows out of the authored result", () => {
    authored.data = { event_occurrences: [OCC] };
    const { result } = renderHook(() => useCalendarWindow(SOURCE, JUNE));

    expect(result.current.occurrences).toEqual([OCC]);
    expect(result.current.fetching).toBe(false);
    // The window rides the query variables (start inclusive, end exclusive).
    expect(authored.calls.at(-1)?.variables).toEqual({
      window_start: "2026-06-01T00:00:00.000Z",
      window_end: "2026-07-06T00:00:00.000Z",
    });
  });

  test("returns an empty array when the result has no occurrences yet", () => {
    authored.data = undefined;
    const { result } = renderHook(() => useCalendarWindow(SOURCE, JUNE));
    expect(result.current.occurrences).toEqual([]);
  });

  test("refetches with the new bounds when the window changes", () => {
    const { rerender } = renderHook(
      ({ window }: { window: CalendarWindow }) => useCalendarWindow(SOURCE, window),
      { initialProps: { window: JUNE } },
    );
    const first = authored.calls.at(-1)?.variables;

    rerender({ window: JULY });
    const second = authored.calls.at(-1)?.variables;

    expect(second).not.toEqual(first);
    expect(second).toEqual({
      window_start: "2026-07-01T00:00:00.000Z",
      window_end: "2026-08-03T00:00:00.000Z",
    });
  });

  test("passes the source's invalidation models through to the query owner", () => {
    renderHook(() => useCalendarWindow(SOURCE, JUNE));
    expect(authored.calls.at(-1)?.options).toMatchObject({ models: ["calendar/event"] });
  });
});
