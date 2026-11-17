// @vitest-environment happy-dom

import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { operationDocuments } from "@angee/gql/console/actions";
import { RoutedRuntimeFixture, storySchema, testDataResource, type GraphQLWireRequest } from "@angee/storybook/testing";
import { containersFromChildren, type Occurrence, type ResourceListCalendarSpec } from "@angee/ui";
import { createRouteHref } from "@angee/ui/runtime";

import { OCCURRENCE_SOURCES } from "../sources";
import { CalendarPage } from "./CalendarPage";

// The page runs on the real runtime: refine over the GraphQL wire fake, the composed
// containers, auth and the resource routes. Only `ResourceList` is captured: its
// windowed surface is the shared owner's, and the page's part is the spec it hands it.
const captured = vi.hoisted(() => ({ spec: undefined as ResourceListCalendarSpec | undefined }));
vi.mock("@angee/ui", async (original) => ({
  ...(await original<typeof import("@angee/ui")>()),
  ResourceList: (props: { calendar?: ResourceListCalendarSpec }) => {
    captured.spec = props.calendar;
    return null;
  },
}));

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

const CALENDARS = [
  { id: "cal_alice", name: "Personal", party: { display_name: "Alice" } },
  { id: "cal_bob", name: "Personal", party: { display_name: "Bob" } },
];

const requests: GraphQLWireRequest[] = [];
let outcome = { ok: true, message: "Event rescheduled." };

function renderPage(): void {
  // The record routes and the action's invalidation resolve models through the schema's resource metadata.
  const schema = {
    ...storySchema({
      calendars: () => CALENDARS,
      reschedule_event: () => outcome,
    }, (request) => requests.push(request)).public!,
    metadata: { angee: { resources: [testDataResource("calendar.CalendarEvent"), testDataResource("calendar.Calendar")] } },
  };
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
      schemas={{ console: schema }}
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

  test("a contributed source is keyed by its container child id and built with the viewer", async () => {
    const contributed = (await spec()).sources.at(-1)!;
    expect(contributed.key).toBe("projects-calendar.due-tasks");
    expect(contributed.variables({ start: new Date(), end: new Date() })).toEqual({ assignee: "usr_viewer" });
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

  test("a refused reschedule rejects the drop, so the surface reverts", async () => {
    outcome = { ok: false, message: "This calendar is already busy at this time." };
    const onReschedule = (await spec()).onReschedule!;

    await expect(onReschedule(ROW, new Date("2026-06-16T11:00:00.000Z"), null)).rejects.toThrow(
      "This calendar is already busy at this time.",
    );
  });
});
