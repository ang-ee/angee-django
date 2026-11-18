// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { createElement, type ReactNode } from "react";

import type { TypedDocumentNode } from "@angee/refine";
import {
  ModelMetadataProvider,
  schemaFieldMetadataFromDataResources,
} from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";

import type { CalendarWindow, Occurrence } from "./CalendarView";
import {
  calendarWindowBounds,
  calendarWindowSource,
  useCalendarWindow,
  type AnyCalendarWindowSource,
} from "./use-calendar-window";

// The transport owner is `@angee/refine`'s batch read; the window adapter only
// composes it. Stub it to record the scopes and options it receives (so the
// refetch and source-set contracts are observable) and to answer each scope.
interface Scope {
  key: string;
  variables: unknown;
  models?: readonly string[];
}
const batch = vi.hoisted(() => ({
  calls: [] as Array<{ scopes: Scope[]; options: { enabled?: boolean } }>,
  data: {} as Record<string, unknown>,
  refetched: [] as string[],
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQueryBatch: (scopes: Scope[], options: { enabled?: boolean }) => {
    batch.calls.push({ scopes, options });
    return new Map(scopes.map((scope) => [scope.key, {
      data: batch.data[scope.key],
      isFetching: false,
      error: null,
      refetch: () => batch.refetched.push(scope.key),
    }]));
  },
}));

interface OccurrencesResult {
  event_occurrences: Occurrence[];
}
type WindowVariables = { window_start: string; window_end: string };
const DOCUMENT = {} as TypedDocumentNode<OccurrencesResult, WindowVariables>;

function source(key: string | undefined, extra: Partial<AnyCalendarWindowSource> = {}): AnyCalendarWindowSource {
  return {
    ...calendarWindowSource({
      key,
      document: DOCUMENT,
      variables: (window) => calendarWindowBounds(window),
      select: (data) => data?.event_occurrences,
      models: ["Event"],
    }),
    ...extra,
  };
}

function Metadata({ children }: { children: ReactNode }): ReactNode {
  return createElement(ModelMetadataProvider, {
    metadata: schemaFieldMetadataFromDataResources([testDataResource("calendar.Event")]),
    children,
  });
}

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
  batch.calls = [];
  batch.data = {};
  batch.refetched = [];
});

describe("useCalendarWindow", () => {
  test("merges every source's occurrences from one batch read", () => {
    batch.data = {
      events: { event_occurrences: [OCC] },
      tasks: { event_occurrences: [{ ...OCC, occurrence_id: "task_1" }] },
    };
    const sources = [source("events"), source("tasks")];
    const { result } = renderHook(() => useCalendarWindow(sources, JUNE), { wrapper: Metadata });

    expect(result.current.occurrences.map((occurrence) => occurrence.occurrence_id)).toEqual(["cev_a", "task_1"]);
    expect(batch.calls.at(-1)?.scopes.map((scope) => scope.key)).toEqual(["events", "tasks"]);
    // The window rides each source's variables (start inclusive, end exclusive).
    expect(batch.calls.at(-1)?.scopes[0]?.variables).toEqual({
      window_start: "2026-06-01T00:00:00.000Z",
      window_end: "2026-07-06T00:00:00.000Z",
    });
  });

  test("returns an empty array while no source has answered", () => {
    const { result } = renderHook(() => useCalendarWindow([source(undefined)], JUNE), { wrapper: Metadata });
    expect(result.current.occurrences).toEqual([]);
  });

  test("keeps one occurrence array while the results are unchanged", () => {
    batch.data = { events: { event_occurrences: [OCC] } };
    const sources = [source("events")];
    const { result, rerender } = renderHook(() => useCalendarWindow(sources, JUNE), { wrapper: Metadata });
    const first = result.current.occurrences;

    rerender();

    expect(result.current.occurrences).toBe(first);
  });

  test("refetches with the new bounds when the window changes", () => {
    const sources = [source("events")];
    const { rerender } = renderHook(
      ({ window }: { window: CalendarWindow }) => useCalendarWindow(sources, window),
      { initialProps: { window: JUNE }, wrapper: Metadata },
    );
    rerender({ window: JULY });

    expect(batch.calls.at(-1)?.scopes[0]?.variables).toEqual({
      window_start: "2026-07-01T00:00:00.000Z",
      window_end: "2026-08-03T00:00:00.000Z",
    });
  });

  test("a hidden or opted-out source leaves the batch; the batch waits while disabled", () => {
    const sources = [source("events"), source("tasks"), source("lone", { enabled: false })];
    renderHook(() => useCalendarWindow(sources, JUNE, { hidden: new Set(["tasks"]), enabled: false }), {
      wrapper: Metadata,
    });

    expect(batch.calls.at(-1)?.scopes.map((scope) => scope.key)).toEqual(["events"]);
    expect(batch.calls.at(-1)?.options.enabled).toBe(false);
  });

  test("passes each source's canonical invalidation models and refetches every shown source", () => {
    const sources = [source("events"), source(undefined)];
    const { result } = renderHook(() => useCalendarWindow(sources, JUNE), { wrapper: Metadata });

    expect(batch.calls.at(-1)?.scopes.map((scope) => [scope.key, scope.models])).toEqual([
      ["events", ["calendar.Event"]],
      ["#1", ["calendar.Event"]],
    ]);
    result.current.refetch();
    expect(batch.refetched).toEqual(["events", "#1"]);
  });

  test("refuses sources that name different data providers", () => {
    const sources = [source("events"), source("tasks", { dataProviderName: "other" })];
    expect(() => renderHook(() => useCalendarWindow(sources, JUNE), { wrapper: Metadata })).toThrow(
      /share one data provider/,
    );
  });
});
