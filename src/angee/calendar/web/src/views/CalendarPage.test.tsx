// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { AnyCalendarWindowSource, Occurrence, ResourceListCalendarSpec } from "@angee/ui";

// The page is a `ResourceList` declaration: it hands the calendar spec (sources, the
// drag verb, the quick-create seed) to `ResourceList` and owns no grid. Capture the
// spec, stand in for the calendar list read, the contributed sources and the derived
// `reschedule_event` action, so the page's own wiring is what the test exercises.
const mocks = vi.hoisted(() => ({
  toastDanger: vi.fn(),
  reschedule: vi.fn(),
  calendars: [] as { id: string; name: string }[],
  contributed: [] as { content: (context: { viewerId: string }) => unknown }[],
  calendarSpec: undefined as ResourceListCalendarSpec | undefined,
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: () => ({ data: { calendars: mocks.calendars } }),
  useActionMutation: () => [mocks.reschedule, { fetching: false, error: null }],
}));

vi.mock("@angee/ui", async () => {
  const { createRouteHref } = await import("@angee/ui/runtime/route-href");
  const routeHref = createRouteHref([{ name: "calendar.event", path: "/calendar/$id" }]);
  return {
    registerForm: (resource: string, Component: unknown) => ({ resource, Component }),
    ResourceList: (props: { calendar?: ResourceListCalendarSpec }) => {
      mocks.calendarSpec = props.calendar;
      return null;
    },
    Field: () => null,
    Form: () => null,
    Group: () => null,
    calendarWindowSource: (source: unknown) => source,
    calendarWindowBounds: (window: { start: Date; end: Date }) => ({
      window_start: window.start.toISOString(),
      window_end: window.end.toISOString(),
    }),
    errorMessage: (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback),
    useContainer: () => mocks.contributed,
    useResourceRecordHrefLookup: () => () => undefined,
    useRouteHref: () => routeHref,
    useRuntimeAuth: () => ({ user: { id: "usr_viewer" } }),
    useToast: () => ({ danger: mocks.toastDanger }),
    createNamespaceT:
      (_namespace: string, messages: Record<string, string>) => () => (key: string) => messages[key] ?? key,
  };
});

import { CalendarPage } from "./CalendarPage";

type Row = Occurrence & { calendar: string; redacted: boolean };

const ROW: Row = {
  occurrence_id: "cev_a",
  event_sqid: "cev_a",
  calendar: "cal_alice",
  title: "Design review",
  start: "2026-06-15T09:00:00.000Z",
  end: "2026-06-15T10:00:00.000Z",
  all_day: false,
  editable: true,
  redacted: false,
};

function spec(): ResourceListCalendarSpec {
  render(<CalendarPage />);
  if (!mocks.calendarSpec) throw new Error("ResourceList received no calendar spec");
  return mocks.calendarSpec;
}

function selected(source: AnyCalendarWindowSource, rows: Row[]): readonly Occurrence[] {
  return source.select({ event_occurrences: rows } as never) ?? [];
}

beforeEach(() => {
  mocks.toastDanger.mockClear();
  mocks.reschedule.mockReset();
  mocks.calendars = [{ id: "cal_alice", name: "Alice" }, { id: "cal_team", name: "Team" }];
  mocks.contributed = [];
  mocks.calendarSpec = undefined;
});
afterEach(cleanup);

describe("CalendarPage sources", () => {
  test("one labelled source per readable calendar keeps its own occurrences and opens each event", () => {
    const [alice, team] = spec().sources;
    expect([alice?.key, alice?.label, team?.key, team?.label]).toEqual(["cal_alice", "Alice", "cal_team", "Team"]);
    const rows = [ROW, { ...ROW, occurrence_id: "cev_b", event_sqid: "cev /b", calendar: "cal_team" }];
    expect(selected(alice!, rows).map((occurrence) => occurrence.occurrence_id)).toEqual(["cev_a"]);
    expect(selected(team!, rows)[0]?.to).toBe("/calendar/cev%20%2Fb");
  });

  test("busy time on a calendar the viewer does not read is unlabelled, titled Busy and opens nothing", () => {
    const busy = spec().sources[2]!;
    expect(busy.key).toBeUndefined();
    const shown = selected(busy, [ROW, { ...ROW, calendar: "cal_bob", title: "", redacted: true, editable: false }]);
    expect(shown).toHaveLength(1);
    expect(shown[0]).toMatchObject({ title: "Busy", to: undefined });
  });

  test("contributed sources follow the calendar's own, built with the viewer", () => {
    const contributed = { key: "projects.tasks" };
    mocks.contributed = [{ content: (context) => ({ ...contributed, viewer: context.viewerId }) }];
    expect(spec().sources.at(-1)).toEqual({ key: "projects.tasks", viewer: "usr_viewer" });
  });
});

describe("CalendarPage drag", () => {
  test("a drag runs reschedule_event with the new bounds and does not toast on success", async () => {
    mocks.reschedule.mockResolvedValue({ ok: true, message: "Event rescheduled." });
    const start = new Date("2026-06-16T11:00:00.000Z");
    const end = new Date("2026-06-16T12:00:00.000Z");

    await spec().onReschedule!(ROW, start, end);

    expect(mocks.reschedule).toHaveBeenCalledWith("cev_a", { start: start.toISOString(), end: end.toISOString() });
    expect(mocks.toastDanger).not.toHaveBeenCalled();
  });

  test("a refused reschedule rejects the drop (the surface reverts) and surfaces the refusal", async () => {
    mocks.reschedule.mockResolvedValue({ ok: false, message: "This calendar refuses overlapping events." });

    await expect(spec().onReschedule!(ROW, new Date("2026-06-16T11:00:00.000Z"), null)).rejects.toThrow(
      "This calendar refuses overlapping events.",
    );
    expect(mocks.toastDanger).toHaveBeenCalledTimes(1);
  });
});
