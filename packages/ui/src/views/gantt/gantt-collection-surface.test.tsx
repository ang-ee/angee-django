// @vitest-environment happy-dom

import type { GetListParams } from "@refinedev/core";
import { MAX_PAGE_SIZE } from "@angee/refine";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { DataResourceMetadata, Row } from "@angee/metadata";
import { testQueryAxis, testQueryField } from "@angee/metadata/testing";

import { createUiTestProviders } from "../../testing";
import { ganttResources, ganttLane, ganttLanes, ganttRecord, ganttMarker, scheduledRecord } from "../../../tests/gantt-fixtures";
import type { GanttViewSpec } from "../resource/resource-view-types";
import { ListView } from "../resource/ListView";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "../resource/resource-view-context";
import type { GanttViewProps } from "./GanttView";
import { mergeGanttI18n } from "./gantt-i18n";
import { calendarDateToAnchor } from "../calendar/calendar-view-controls";

// ReUI's drawing is tested at its own boundary; all query/state/toolbar owners run here.
const drawing = vi.hoisted(() => ({ props: null as GanttViewProps | null }));
vi.mock("./GanttView", () => ({ GanttView: (props: GanttViewProps) => {
  drawing.props = props;
  return <div aria-label="Schedule chart">{props.resources.map((row) => <div key={row.id}>
    {row.title}{props.renderRowContent?.(row)}
  </div>)}</div>;
} }));

const { Provider, dataProvider, clearClients } = createUiTestProviders({
  resources: ganttResources,
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
beforeEach(() => { drawing.props = null; });
afterEach(() => { cleanup(); clearClients(); });

function renderCollection(options: {
  rows?: Row[]; total?: number; page?: number; pageSize?: number; strict?: boolean; anchor?: string;
  resources?: readonly DataResourceMetadata[];
  getList?: (params: Partial<GetListParams>) => Promise<{ data: Row[]; total: number }>;
  onRowClick?: (row: Row) => void;
  gantt?: Partial<GanttViewSpec>;
} = {}) {
  const getList = vi.fn(options.getList ?? (async ({ resource }: Partial<GetListParams>) => resource === "lanes"
    ? { data: ganttLanes, total: 21 }
    : { data: options.rows ?? [scheduledRecord], total: options.total ?? (options.rows?.length ?? 1) }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  let state: ResourceViewContextValue;
  function Collection() {
    state = useResourceView();
    return <ListView resource={ganttRecord.modelLabel}
      columns={[{ field: "name", header: "Name" }]}
      baseFilter={{ status: { exact: "active" } }}
      gantt={{ start: "start", end: "end", tone: "status", rowFields: ["code"], renderRowContent: (row) => <span>Code {String(row.code)}</span>, ...options.gantt }}
      laneSource={{ field: "lane" }} onRowClick={options.onRowClick} />;
  }
  const view = render(<RouterContextProvider router={router}><Provider resources={options.resources ?? ganttResources} dataProvider={{ getList }}>
    <ResourceViewProvider resource={ganttRecord.modelLabel} scope="local" initialState={{
      view: "gantt", anchor: options.anchor ?? "2026-09-01", page: options.page ?? 1, pageSize: options.pageSize ?? 10,
    }}><Collection /></ResourceViewProvider>
  </Provider></RouterContextProvider>, { reactStrictMode: options.strict ?? false });
  return { ...view, getList, state: () => state, barRequests: () => getList.mock.calls.filter(([params]) => params.resource === "schedules") };
}

describe("Gantt collection over native list data", () => {
  test("linked bars use the visible lane page while the lane resource owns view scope and selection", async () => {
    const getList = vi.fn(async ({ resource }: Partial<GetListParams>) => resource === "lanes"
      ? { data: ganttLanes, total: 2 }
      : { data: [scheduledRecord], total: 1 });
    const onRowClick = vi.fn();
    const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
    render(<RouterContextProvider router={router}><Provider resources={ganttResources} dataProvider={{ getList }}>
      <ResourceViewProvider resource={ganttLane.modelLabel} scope="local" initialState={{ view: "gantt", anchor: "2026-09-01" }}>
        <ListView resource={ganttLane.modelLabel} columns={[{ field: "name" }]}
          gantt={{ linked: { resource: ganttRecord.modelLabel, lane: "lane" }, start: "start", end: "end", label: "name" }}
          onRowClick={onRowClick} />
      </ResourceViewProvider>
    </Provider></RouterContextProvider>);
    await waitFor(() => expect(drawing.props?.events).toHaveLength(1));
    expect(drawing.props?.resources.map(({ id }) => id)).toEqual(["lane-a", "lane-b"]);
    expect(drawing.props?.events[0]?.resourceId).toBe("lane-a");
    expect(getList.mock.calls.find(([params]) => params.resource === "lanes")?.[0].pagination?.currentPage).toBe(1);
    expect(JSON.stringify(getList.mock.calls.find(([params]) => params.resource === "schedules")?.[0].meta?.gqlVariables?.where)).toContain("lane-a");
    act(() => drawing.props?.onEventClick?.(drawing.props!.events[0]!));
    expect(onRowClick).toHaveBeenCalledWith(ganttLanes[0]);
    act(() => drawing.props?.onSelectedRowsChange?.(["lane-b"]));
    expect(drawing.props?.selectedRows).toEqual(["lane-b"]);
  });
  test.each([false, true])("emphasizes only each lane's declared current bar (relation=%s)", async (relation) => {
    const currentLane = { ...ganttLane,
      fields: [...ganttLane.fields, { ...ganttLane.fields[0]!, name: "current", kind: relation ? "relation" as const : "scalar" as const,
        ...(relation ? { relationModelLabel: ganttRecord.modelLabel, relationObject: true } : {}) }],
      query: { ...ganttLane.query, fields: { ...ganttLane.query.fields, current: testQueryField("current", {
        scalar: "ID", kind: relation ? "relation" : "scalar",
        row: { path: relation ? "current.id" : "current", paths: [relation ? "current.id" : "current"] },
      }) } },
    };
    const f = renderCollection({ resources: [ganttRecord, currentLane], gantt: { current: "current" },
      getList: async ({ resource }) => resource === "lanes" ? {
        data: [{ ...ganttLanes[0], current: relation ? { id: "schedule-a" } : "schedule-a" }, { ...ganttLanes[1], current: null }], total: 2,
      } : { data: [scheduledRecord, { ...scheduledRecord, id: "schedule-b" }], total: 2 },
    });
    await waitFor(() => expect(drawing.props?.events).toHaveLength(2));
    expect(drawing.props?.events.map(({ id, current }) => ({ id, current }))).toEqual([
      { id: "schedule-a", current: true }, { id: "schedule-b", current: false },
    ]);
    expect(JSON.stringify(f.getList.mock.calls.find(([params]) => params.resource === "lanes")?.[0].meta?.fields)).toContain("current");
  });

  test("loads every marker page on the same lanes, using its own filter and point dates", async () => {
    const onRowClick = vi.fn();
    const f = renderCollection({ resources: [...ganttResources, ganttMarker], onRowClick,
      gantt: { markers: { resource: ganttMarker.modelLabel, lane: "lane", date: "due", filter: { status: { exact: "waiting" } } } },
      getList: async ({ resource, pagination }) => {
        if (resource === "lanes") return { data: ganttLanes, total: 2 };
        if (resource === "schedules") return { data: [scheduledRecord], total: 1 };
        return { data: pagination?.currentPage === 1
          ? Array.from({ length: MAX_PAGE_SIZE }, (_, index) => ({ id: index === 0 ? scheduledRecord.id : `checkpoint-${index}`, name: `Checkpoint ${index}`, due: "2026-09-04", lane: { id: "lane-b", name: "Beta" } }))
          : [{ id: "last", name: "Final checkpoint", due: "2026-09-05", lane: { id: "lane-a", name: "Alpha" } }], total: MAX_PAGE_SIZE + 1 };
      },
    });
    await waitFor(() => expect(drawing.props?.events).toHaveLength(MAX_PAGE_SIZE + 2));
    const marker = drawing.props!.events.find((event) => event.title === "Checkpoint 0")!;
    expect(marker.id).not.toBe(scheduledRecord.id);
    expect(marker.resourceId).toBe("lane-b");
    expect(marker.start).toEqual(marker.end);
    expect(marker.start.getHours()).toBe(0);
    expect(marker.allDay).toBe(true);
    expect(marker.readOnly).toBe(true);
    const requests = f.getList.mock.calls.filter(([params]) => params.resource === "checkpoints");
    expect(requests.map(([params]) => params.pagination?.currentPage)).toEqual([1, 2]);
    const where = JSON.stringify(requests[0]?.[0].meta?.gqlVariables);
    expect(where).toContain("lane-a");
    expect(where).toContain("lane-b");
    expect(where).toContain("waiting");
    expect(where).not.toContain("active");
    act(() => drawing.props?.onEventClick?.(marker));
    expect(onRowClick).not.toHaveBeenCalled();
    expect(dataProvider.update).not.toHaveBeenCalled();
  });

  test("omits unscheduled markers, reports invalid ones, and keeps a DateTime marker's instant", async () => {
    renderCollection({ resources: [...ganttResources, { ...ganttMarker, fields: ganttMarker.fields.map((field) => field.name === "due" ? { ...field, scalar: "DateTime" } : field) }],
      gantt: { markers: { resource: ganttMarker.modelLabel, lane: "lane", date: "due" } },
      getList: async ({ resource }) => resource === "lanes" ? { data: ganttLanes, total: 2 }
        : resource === "schedules" ? { data: [], total: 0 }
        : { data: [
          { id: "scheduled", name: "Precise checkpoint", due: "2026-09-04T14:00:00Z", lane: { id: "lane-a" } },
          { id: "absent", due: null, lane: { id: "lane-a" } },
          { id: "invalid", due: "invalid", lane: { id: "lane-a" } },
        ], total: 3 },
    });
    await screen.findByText("1 record omitted because its identity or date range is invalid.");
    expect(drawing.props?.events).toHaveLength(1);
    expect(drawing.props?.events[0]).toMatchObject({ start: new Date("2026-09-04T14:00:00Z"), end: new Date("2026-09-04T14:00:00Z"), allDay: false });
    expect(drawing.props?.resources).toHaveLength(2);
  });

  test("does not query either source for an empty lane page", async () => {
    const f = renderCollection({ resources: [...ganttResources, ganttMarker],
      gantt: { markers: { resource: ganttMarker.modelLabel, lane: "lane", date: "due" } },
      getList: async () => ({ data: [], total: 0 }),
    });
    await waitFor(() => expect(drawing.props?.loading).toBe(false));
    expect(f.getList.mock.calls.map(([params]) => params.resource)).toEqual(["lanes"]);
  });

  test("an invalid marker lane fails without an unscoped marker read", async () => {
    const f = renderCollection({ resources: [...ganttResources, ganttMarker],
      gantt: { markers: { resource: ganttMarker.modelLabel, lane: "name", date: "due" } },
    });
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(f.getList.mock.calls.some(([params]) => params.resource === "checkpoints")).toBe(false);
  });

  test("reports and retries marker read failures through the collection error owner", async () => {
    let failed = true;
    renderCollection({ resources: [...ganttResources, ganttMarker],
      gantt: { markers: { resource: ganttMarker.modelLabel, lane: "lane", date: "due" } },
      getList: async ({ resource }) => {
        if (resource === "checkpoints" && failed) throw new Error("Marker read refused");
        return { data: resource === "lanes" ? ganttLanes : resource === "schedules" ? [scheduledRecord] : [], total: resource === "lanes" ? 2 : resource === "schedules" ? 1 : 0 };
      },
    });
    await screen.findByText("Marker read refused");
    failed = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByLabelText("Schedule chart");
    expect(screen.queryByText("Marker read refused")).toBeNull();
  });
  test("fits today's initial collection window while honoring an explicit historical anchor", async () => {
    const f = renderCollection({ anchor: calendarDateToAnchor(new Date()) });
    await waitFor(() => expect(drawing.props?.events).toHaveLength(1));
    expect(drawing.props?.fitToEvents).toBe(true);
    act(() => f.state().setAnchor("2020-01-01"));
    expect(drawing.props?.fitToEvents).toBe(false);
  });
  test.each([
    ["2026-09-24", "2026-10-08", "Sep 24 - Oct 8, 2026"],
    ["2026-09-24", "2026-09-24", "Sep 24, 2026"],
    ["2026-03-07", "2026-03-09", "Mar 7 - Mar 9, 2026"],
  ])("preserves Date calendar days, including inclusive ends: %s to %s", async (start, end, label) => {
    renderCollection({ rows: [{ ...scheduledRecord, start, end }] });
    await waitFor(() => expect(drawing.props?.events).toHaveLength(1));
    const event = drawing.props!.events[0]!;
    expect(event.allDay).toBe(true);
    expect(event.start.getHours()).toBe(0);
    expect(event.end.getHours()).toBe(0);
    expect(mergeGanttI18n().functions.formatEventTime(event.start, event.end, event.allDay!)).toBe(label);
    expect(drawing.props?.defaultScale).toBe("quarter");
  });

  test("retains DateTime instants and time labels", async () => {
    const start = "2026-09-24T14:00:00Z";
    const end = "2026-09-24T16:00:00Z";
    renderCollection({ rows: [{ ...scheduledRecord, start, end }], resources: [{ ...ganttRecord,
      fields: ganttRecord.fields.map((field) => ["start", "end"].includes(field.name) ? { ...field, scalar: "DateTime" } : field),
    }, ganttLane] });
    await waitFor(() => expect(drawing.props?.events).toHaveLength(1));
    const event = drawing.props!.events[0]!;
    expect(event.allDay).toBe(false);
    expect(event.start.toISOString()).toBe("2026-09-24T14:00:00.000Z");
    expect(event.end.toISOString()).toBe("2026-09-24T16:00:00.000Z");
    expect(mergeGanttI18n().functions.formatEventTime(event.start, event.end, false)).toMatch(/\d:\d\d/);
  });

  test("projects records as read-only bars and retains a catalogue row without bars and its content slot", async () => {
    const f = renderCollection();
    await waitFor(() => expect(drawing.props?.events).toHaveLength(1));
    expect(drawing.props?.resources).toEqual([{ id: "lane-a", title: "Alpha" }, { id: "lane-b", title: "Beta" }]);
    expect(drawing.props?.events[0]).toMatchObject({ id: "schedule-a", resourceId: "lane-a", title: "First interval", readOnly: true });
    expect(screen.getByText("Code B")).toBeTruthy();
    expect(f.getList).toHaveBeenCalledWith(expect.objectContaining({ resource: "lanes", meta: expect.objectContaining({ fields: ["id", "name", "code"] }) }));
    expect(f.barRequests()[0]?.[0].meta?.fields).toEqual(expect.arrayContaining(["id", "name", "start", "end", "status", { lane: ["id", "name"] }]));
    expect(f.getList).toHaveBeenCalledTimes(2);
  });

  test.each([false, true])("pages by related rows and fetches every record page for those rows (StrictMode=%s)", async (strict) => {
    const f = renderCollection({ strict, page: 2, pageSize: 10, getList: async ({ resource, pagination }) => {
      if (resource === "lanes") return { data: ganttLanes, total: 21 };
      return {
        data: pagination?.currentPage === 1
          ? Array.from({ length: MAX_PAGE_SIZE }, (_, index) => ({ ...scheduledRecord, id: `schedule-${index}` }))
          : [{ ...scheduledRecord, id: "schedule-last" }],
        total: MAX_PAGE_SIZE + 1,
      };
    } });
    await waitFor(() => expect(drawing.props?.events).toHaveLength(MAX_PAGE_SIZE + 1));
    const catalogueRequests = f.getList.mock.calls.filter(([params]) => params.resource === "lanes");
    const cataloguePage = { mode: "server", currentPage: 2, pageSize: 10 };
    // Native Query cancels the pending catalogue read during StrictMode's
    // simulated unmount and starts it again; neither request may target page 1.
    expect(catalogueRequests.map(([params]) => params.pagination)).toEqual(strict
      ? [cataloguePage, cataloguePage]
      : [cataloguePage]);
    expect(catalogueRequests.map(([params]) => params.meta?.signal.aborted)).toEqual(strict ? [true, false] : [false]);
    expect(f.barRequests().map(([params]) => params.pagination)).toEqual([
      { mode: "server", currentPage: 1, pageSize: MAX_PAGE_SIZE },
      { mode: "server", currentPage: 2, pageSize: MAX_PAGE_SIZE },
    ]);
    expect(f.barRequests()[1]?.[0].meta?.gqlVariables).toEqual(f.barRequests()[0]?.[0].meta?.gqlVariables);
    expect(f.getList).toHaveBeenCalledTimes(strict ? 4 : 3);
    expect(f.state().state.pagination.pageIndex).toBe(1);
  });

  test("pins the lane group even when another valid group is selected", async () => {
    const f = renderCollection();
    await waitFor(() => expect(f.state().state.groupStack).toEqual([{ field: "lane" }]));
    act(() => f.state().setPage(2));
    expect(f.state().state.pagination.pageIndex).toBe(1);
    act(() => f.state().setGroup({ field: "status" }));
    await waitFor(() => expect(f.state().state.groupStack).toEqual([{ field: "lane" }]));
    expect(f.state().state.pagination.pageIndex).toBe(0);
    expect(screen.queryByRole("button", { name: /group by/i })).toBeNull();
  });

  test("shares filters, search, sorting and saved views with the resource-view owner", async () => {
    const f = renderCollection({ getList: async ({ resource, meta }) => ({
      data: resource === "lanes" ? ganttLanes : (JSON.stringify(meta?.gqlVariables) ?? "").includes("Second")
        ? [{ ...scheduledRecord, id: "schedule-second", name: "Second interval" }]
        : [scheduledRecord],
      total: resource === "lanes" ? 2 : 1,
    }) });
    await waitFor(() => expect(drawing.props?.events).toHaveLength(1));
    const search = screen.getByRole("searchbox", { name: "Filter records" });
    fireEvent.change(search, { target: { value: "First" } });
    fireEvent.keyDown(search, { key: "Enter" });
    await waitFor(() => expect(JSON.stringify(f.barRequests().at(-1)?.[0].meta?.gqlVariables)).toContain("First"));
    const filtered = JSON.stringify(f.barRequests().at(-1)?.[0].meta?.gqlVariables);
    expect(filtered).toContain("active");
    expect(filtered).toContain("lane-a");
    expect(filtered).toContain("lane-b");
    expect(f.state().state.filter).toEqual({ name: { iContains: "First" } });
    act(() => f.state().applyFavorite({
      id: "favorite:archived", label: "Saved query", view: "gantt", pageSize: 10,
      filter: { name: { iContains: "Second" } }, sort: { field: "name", dir: "desc" }, groupStack: [{ field: "status" }],
    }));
    await waitFor(() => expect(JSON.stringify(f.barRequests().at(-1)?.[0].meta?.gqlVariables)).toContain("Second"));
    expect(JSON.stringify(f.barRequests().at(-1)?.[0].meta?.gqlVariables)).not.toContain("First");
    expect(f.state().state.sorting).toEqual([{ id: "name", desc: true }]);
    expect(f.state().state.groupStack).toEqual([{ field: "lane" }]);
    expect(f.state().state.view).toBe("gantt");
    await waitFor(() => expect(drawing.props?.events.map((event) => event.title)).toEqual(["Second interval"]));
  });

  test("does not query records when the related row page is empty", async () => {
    const f = renderCollection({ getList: async () => ({ data: [], total: 0 }) });
    await waitFor(() => expect(drawing.props?.loading).toBe(false));
    expect(drawing.props?.resources).toEqual([]);
    expect(drawing.props?.events).toEqual([]);
    expect(f.barRequests()).toHaveLength(0);
    expect(f.getList).toHaveBeenCalledTimes(1);
  });

  test("reports an unusable lane drill through the shared error banner without an unscoped record read", async () => {
    const f = renderCollection({ resources: [{
      ...ganttRecord,
      query: { ...ganttRecord.query, axes: { ...ganttRecord.query.axes,
        lane: testQueryAxis("lane", { kind: "relation", identityPath: "lane.id", labelPath: "lane.name", paths: ["lane.id", "lane.name"] }),
      } },
    }, ganttLane] });
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText('Gantt row field "lane" requires a drill filter.')).toBeTruthy();
    expect(screen.queryByLabelText("Schedule chart")).toBeNull();
    expect(f.barRequests()).toHaveLength(0);
  });

  test("a malformed saved filter blocks reads until the shared query reset", async () => {
    const f = renderCollection();
    await waitFor(() => expect(drawing.props?.events).toHaveLength(1));
    const reads = f.getList.mock.calls.length;
    act(() => f.state().applyFavorite({ id: "favorite:old", label: "Obsolete", view: "gantt", filter: { removed_field: { exact: "value" } } }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByLabelText("Schedule chart")).toBeNull();
    expect(f.getList).toHaveBeenCalledTimes(reads);
    act(() => f.state().resetQuery());
    await screen.findByLabelText("Schedule chart");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("skips invalid identities and date ranges, counts them, and leaves unscheduled records uncounted", async () => {
    renderCollection({ rows: [
      scheduledRecord,
      { ...scheduledRecord, id: undefined },
      { ...scheduledRecord, id: "bad-date", start: "not-a-date" },
      { ...scheduledRecord, id: "reversed", end: "2026-08-01" },
      { ...scheduledRecord, id: "unscheduled", start: null },
    ] });
    expect(await screen.findByText("3 records omitted because their identities or date ranges are invalid.")).toBeTruthy();
    expect(drawing.props?.events.map((event) => event.id)).toEqual(["schedule-a"]);
    expect(drawing.props?.resources).toHaveLength(2);
  });

  test("uses singular copy when exactly one invalid record is skipped", async () => {
    renderCollection({ rows: [scheduledRecord, { ...scheduledRecord, id: "bad", end: "invalid" }] });
    expect(await screen.findByText("1 record omitted because its identity or date range is invalid.")).toBeTruthy();
  });

  test("controlled date navigation writes the resource-view anchor without a data read", async () => {
    const f = renderCollection();
    await waitFor(() => expect(drawing.props?.events).toHaveLength(1));
    expect(drawing.props?.date).toEqual(new Date(2026, 8, 1));
    const reads = f.getList.mock.calls.length;
    act(() => drawing.props?.onDateChange?.(new Date(2026, 9, 7)));
    expect(f.state().state.anchor).toBe("2026-10-07");
    expect(drawing.props?.date).toEqual(new Date(2026, 9, 7));
    expect(f.getList).toHaveBeenCalledTimes(reads);
  });

  test.each(["lanes", "schedules"])("shows %s errors in the shared banner and retries", async (failedResource) => {
    let failed = true;
    const f = renderCollection({ getList: async ({ resource }) => {
      if (resource === failedResource && failed) throw new Error("Schedule read refused");
      return { data: resource === "lanes" ? ganttLanes : [scheduledRecord], total: resource === "lanes" ? 2 : 1 };
    } });
    expect(await screen.findByText("Schedule read refused")).toBeTruthy();
    expect(screen.queryByLabelText("Schedule chart")).toBeNull();
    failed = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(drawing.props?.events).toHaveLength(1));
    expect(screen.queryByText("Schedule read refused")).toBeNull();
    expect(f.getList.mock.calls.filter(([params]) => params.resource === failedResource)).toHaveLength(2);
  });

  test("uses record activation without selecting or writing any records", async () => {
    const onRowClick = vi.fn();
    const f = renderCollection({ onRowClick });
    await waitFor(() => expect(drawing.props?.events).toHaveLength(1));
    const event = drawing.props?.events[0];
    expect(event).toBeDefined();
    if (!event) throw new Error("Missing fixture event");
    act(() => drawing.props?.onEventClick?.(event));
    expect(onRowClick).toHaveBeenCalledWith(scheduledRecord);
    expect(f.state().state.rowSelection).toEqual({});
    expect(dataProvider.update).not.toHaveBeenCalled();
    expect(dataProvider.create).not.toHaveBeenCalled();
  });
});
