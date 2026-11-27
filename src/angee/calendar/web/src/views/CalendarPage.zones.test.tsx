// @vitest-environment happy-dom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { operationDocuments } from "@angee/gql/console/actions";
import { RoutedRuntimeFixture, storySchema, testDataResource, type GraphQLWireRequest } from "@angee/storybook/testing";
import {
  containersFromChildren, formatDate, formatDateStorage, formatTime, type ListColumn, type Occurrence,
  type ResourceListCalendarSpec,
} from "@angee/ui";
import { createRouteHref } from "@angee/ui/runtime";

import { OCCURRENCE_SOURCES } from "../sources";
import { CalendarPage } from "./CalendarPage";

// The page runs on the real runtime: refine over the GraphQL wire fake, the composed
// containers, auth and the resource routes. Only `ResourceList` is captured: its
// windowed surface is the shared owner's, and the page's part is the spec it hands it.
const captured = vi.hoisted(() => ({
  spec: undefined as ResourceListCalendarSpec | undefined,
  columns: undefined as readonly ListColumn[] | undefined,
}));
vi.mock("@angee/ui", async (original) => ({
  ...(await original<typeof import("@angee/ui")>()),
  ResourceList: (props: { calendar?: ResourceListCalendarSpec; columns?: readonly ListColumn[] }) => {
    captured.spec = props.calendar;
    captured.columns = props.columns;
    return null;
  },
}));

type Row = Occurrence & { calendar: string; redacted: boolean; effective_timezone: string };

const ROW: Row = {
  occurrence_id: "cev_a",
  event_sqid: "cev_a",
  calendar: "cal_alice",
  title: "Design review",
  start: "2026-06-15T09:00:00.000Z",
  end: "2026-06-15T10:00:00.000Z",
  all_day: false,
  effective_timezone: "America/Chicago",
  editable: true,
  redacted: false,
};

const CALENDARS = [
  { id: "cal_alice", name: "Personal", party: { display_name: "Alice" } },
  { id: "cal_bob", name: "Personal", party: { display_name: "Bob" } },
];

const requests: GraphQLWireRequest[] = [];
let outcome = { ok: true, message: "Event rescheduled." };

function renderPage(): void {
  // The record routes and the action's invalidation resolve models through the schema's resource metadata.
  const schemas = storySchema({
    calendars: () => CALENDARS,
    reschedule_event: () => outcome,
  }, { schema: "console", onRequest: (request) => requests.push(request),
    resources: [testDataResource("calendar.CalendarEvent"), testDataResource("calendar.Calendar")] });
  const containers = containersFromChildren([{ address: OCCURRENCE_SOURCES }], {
    [OCCURRENCE_SOURCES]: {
      "projects-calendar.due-tasks": {
        content: ({ viewerId }: { viewerId: string }) => ({
          label: "My due tasks",
          document: {} as never,
          variables: () => ({ assignee: viewerId }),
          select: () => [],
        }),
      },
    },
  });
  render(
    <RoutedRuntimeFixture
      activeSchema="console"
      schemas={schemas}
      collectionPath="/calendar"
      resourceName="calendar.CalendarEvent"
      resourceLabel="Calendar"
      operationDocuments={{ console: operationDocuments }}
      runtime={{
        containers,
        auth: { user: { id: "usr_viewer", name: "Viewer" }, status: "authenticated", hasRole: () => false },
        routeHref: createRouteHref([{ name: "calendar.events.record", path: "/calendar/$id" }]),
        routesByResource: {
          "calendar.CalendarEvent": { collection: "calendar.events", record: { name: "calendar.events.record", param: "id" } },
        },
      }}
    >
      <CalendarPage />
    </RoutedRuntimeFixture>,
  );
}

async function spec(): Promise<ResourceListCalendarSpec> {
  renderPage();
  await waitFor(() => expect(captured.spec?.sources.length).toBe(4));
  return captured.spec!;
}

function selected(source: ResourceListCalendarSpec["sources"][number], rows: Row[]): readonly Occurrence[] {
  return source.select({ event_occurrences: rows } as never) ?? [];
}

beforeEach(() => {
  requests.length = 0;
  outcome = { ok: true, message: "Event rescheduled." };
  captured.spec = undefined;
  captured.columns = undefined;
});
afterEach(cleanup);

describe("CalendarPage sources", () => {
  test("one source per listed calendar, labelled with its owning party, keeps that calendar's occurrences", async () => {
    const [alice, bob] = (await spec()).sources;
    expect([alice?.key, alice?.label, bob?.key, bob?.label]).toEqual([
      "cal_alice", "Personal · Alice", "cal_bob", "Personal · Bob",
    ]);
    const rows = [ROW, { ...ROW, occurrence_id: "cev_b", event_sqid: "cev /b", calendar: "cal_bob" }];
    expect(selected(alice!, rows).map((occurrence) => occurrence.occurrence_id)).toEqual(["cev_a"]);
    expect(selected(bob!, rows)[0]?.to).toBe("/calendar/cev%20%2Fb");
  });

  test("busy time comes from the server's redacted flag: titled Busy and inert", async () => {
    const [alice] = (await spec()).sources;
    const [busy] = selected(alice!, [{ ...ROW, title: "", redacted: true, editable: false }]);
    expect(busy).toMatchObject({ title: "Busy", to: undefined });
  });

  // The suite runs in the runner's zone and under each zone the shared Vitest defaults pin: the
  // projection reads each all-day occurrence's days in its event's own zone, so every browser shows
  // them on the same days (a Chicago Nov 20 is no Nov 19 in Los Angeles), and a timed one stays an
  // instant, shown in the viewer's zone.
  test("an all-day occurrence is the days it spans in its event's own zone; a timed one its instants", async () => {
    const [alice] = (await spec()).sources;
    const allDay = { ...ROW, all_day: true, editable: false };
    const [chicago, tokyo, timed] = selected(alice!, [
      { ...allDay, occurrence_id: "chicago", start: "2026-11-20T06:00:00+00:00", end: "2026-11-21T06:00:00+00:00" },
      {
        ...allDay, occurrence_id: "tokyo", effective_timezone: "Asia/Tokyo",
        start: "2026-11-19T15:00:00+00:00", end: "2026-11-21T15:00:00+00:00",
      },
      ROW,
    ]);

    expect([chicago?.start, chicago?.end]).toEqual(["2026-11-20", "2026-11-21"]);
    expect([tokyo?.start, tokyo?.end]).toEqual(["2026-11-20", "2026-11-22"]);
    expect([timed?.start, timed?.end]).toEqual([ROW.start, ROW.end]);
  });

  // A zone newer than this browser's zone data would throw from Intl inside the query's select.
  test("an all-day occurrence in a zone this browser does not know shows on the viewer's days", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const [alice] = (await spec()).sources;
    const start = "2026-11-20T06:00:00+00:00";
    const end = "2026-11-21T06:00:00+00:00";

    const [unknown] = selected(alice!, [
      { ...ROW, all_day: true, editable: false, effective_timezone: "Mars/Olympus_Mons", start, end },
    ]);

    expect([unknown?.start, unknown?.end]).toEqual([formatDateStorage(new Date(start)), formatDateStorage(new Date(end))]);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("Mars/Olympus_Mons"));
    warn.mockRestore();
  });

  test("a contributed source is keyed by its container child id and built with the viewer", async () => {
    const contributed = (await spec()).sources.at(-1)!;
    expect(contributed.key).toBe("projects-calendar.due-tasks");
    expect(contributed.variables({ start: new Date(), end: new Date() })).toEqual({ assignee: "usr_viewer" });
  });
});

describe("CalendarPage events list", () => {
  async function bounds(): Promise<(field: "start" | "end") => ListColumn> {
    await spec();
    return (field) => captured.columns!.find((column) => column.field === field)!;
  }

  // CAL-F4: the list's plain DateTime cell read a Chicago all-day Nov 20 as Nov 19 in Los Angeles.
  test("an all-day event's bounds read as its days in its own zone, with no clock time", async () => {
    const bound = await bounds();
    // A list row, as the events list reads it: the event's midnights in Chicago.
    const row: Record<string, unknown> = {
      ...ROW, all_day: true, start: "2026-11-20T06:00:00+00:00", end: "2026-11-21T06:00:00+00:00",
    };

    // The cell reads its siblings, so the list selects them.
    expect(bound("start").selectionPaths).toEqual(["start", "all_day", "effective_timezone"]);
    render(<>{bound("start").render!(row)}{bound("end").render!(row)}</>);

    const [start, end] = screen.getAllByRole("time");
    expect([start?.getAttribute("datetime"), end?.getAttribute("datetime")]).toEqual(["2026-11-20", "2026-11-21"]);
    expect([start?.textContent, end?.textContent]).toEqual(["Nov 20, 2026", "Nov 21, 2026"]);
  });

  // 03:30 UTC falls on another day or clock time in each zone the suite runs in.
  test("a timed event's bound reads as its date and clock time in the viewer's zone", async () => {
    const bound = await bounds();
    const start = "2026-11-20T03:30:00+00:00";
    // The event's own zone is no runner's: a timed bound never reads in it.
    const row: Record<string, unknown> = { ...ROW, start, effective_timezone: "Pacific/Kiritimati" };
    const viewer = Intl.DateTimeFormat().resolvedOptions().timeZone;

    render(<>{bound("start").render!(row)}</>);

    const timed = screen.getByRole("time");
    expect(timed.getAttribute("datetime")).toBe(new Date(start).toISOString());
    expect(timed.textContent).toContain(formatDate(start, { density: "full", timeZone: viewer }));
    expect(timed.textContent).toContain(formatTime(start, { timeZone: viewer }));
    expect(timed.textContent).not.toContain(formatTime(start, { timeZone: "Pacific/Kiritimati" }));
  });
});

describe("CalendarPage quick-create", () => {
  test("a timed selection seeds the event's instants", async () => {
    const createDefaults = (await spec()).createDefaults!;
    const start = "2026-06-17T14:00:00.000Z";
    const end = "2026-06-17T15:00:00.000Z";

    expect(createDefaults(new Date(start), new Date(end), false)).toEqual({ start, end, all_day: false });
  });

  // Whichever zone the browser is in (the suite runs in each pinned zone), the page sends the days
  // FullCalendar selected and never that zone's midnight as an instant (Tokyo's Nov 20 is 15:00 UTC
  // the day before, which the server refuses on a Chicago calendar).
  test("a month-view day seeds the chosen days, which the event places at midnight in its own zone", async () => {
    const createDefaults = (await spec()).createDefaults!;
    // FullCalendar selects whole days at the browser's local midnights.
    const start = new Date(2026, 10, 20);
    const end = new Date(2026, 10, 21);

    expect(createDefaults(start, end, true)).toEqual({ start: "2026-11-20", end: "2026-11-21", all_day: true });
  });
});

describe("CalendarPage drag", () => {
  test("a drag runs reschedule_event with the new bounds", async () => {
    const onReschedule = (await spec()).onReschedule!;
    const start = new Date("2026-06-16T11:00:00.000Z");
    const end = new Date("2026-06-16T12:00:00.000Z");

    await onReschedule(ROW, start, end);

    expect(requests.find(({ fields }) => fields.includes("reschedule_event"))?.variables).toEqual({
      id: "cev_a", start: start.toISOString(), end: end.toISOString(),
    });
  });

  // FullCalendar drops an all-day event on the viewer's local midnight of the day it shows, which the
  // grid shows in the event's own zone. The page sends that day; the event places it at midnight in
  // its zone. The browser's midnight as an instant (Tokyo's Nov 22 is 09:00 Nov 21 in Chicago) would
  // land a day early, and a snap to the nearest midnight cannot tell.
  test("an all-day drag sends the days it lands on, never the browser's midnight", async () => {
    const onReschedule = (await spec()).onReschedule!;

    await onReschedule({ ...ROW, all_day: true }, new Date(2026, 10, 22), new Date(2026, 10, 23));

    expect(requests.find(({ fields }) => fields.includes("reschedule_event"))?.variables).toEqual({
      id: "cev_a", start: "2026-11-22", end: "2026-11-23",
    });
  });

  // `CalendarEvent.reschedule` makes an all-day move without a later end one day long, so the page sends
  // the day it lands on as both bounds and keeps no copy of that rule.
  test("an all-day drop without an end sends the day it lands on, which the event makes one day long", async () => {
    const onReschedule = (await spec()).onReschedule!;

    await onReschedule({ ...ROW, all_day: true }, new Date(2026, 10, 22), null);

    expect(requests.find(({ fields }) => fields.includes("reschedule_event"))?.variables).toEqual({
      id: "cev_a", start: "2026-11-22", end: "2026-11-22",
    });
  });

  test("a refused reschedule rejects the drop, so the surface reverts", async () => {
    outcome = { ok: false, message: "This calendar is already busy at this time." };
    const onReschedule = (await spec()).onReschedule!;

    await expect(onReschedule(ROW, new Date("2026-06-16T11:00:00.000Z"), null)).rejects.toThrow(
      "This calendar is already busy at this time.",
    );
  });
});
