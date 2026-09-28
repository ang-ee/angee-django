// @vitest-environment happy-dom

import type { GetListParams } from "@refinedev/core";
import { MAX_PAGE_SIZE } from "@angee/refine";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { DataResourceMetadata, Row } from "@angee/metadata";
import { testQueryAxis } from "@angee/metadata/testing";

import { createUiTestProviders } from "../../testing";
import { ganttResources, ganttLane, ganttLanes, ganttRecord, scheduledRecord } from "../../../tests/gantt-fixtures";
import { ListView } from "../resource/ListView";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "../resource/resource-view-context";
import type { GanttViewProps } from "./GanttView";

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
  rows?: Row[]; total?: number; page?: number; pageSize?: number;
  resources?: readonly DataResourceMetadata[];
  getList?: (params: Partial<GetListParams>) => Promise<{ data: Row[]; total: number }>;
  onRowClick?: (row: Row) => void;
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
      gantt={{ start: "start", end: "end", tone: "status", rowFields: ["code"], renderRowContent: (row) => <span>Code {String(row.code)}</span> }}
      laneSource={{ field: "lane" }} onRowClick={options.onRowClick} />;
  }
  const view = render(<RouterContextProvider router={router}><Provider resources={options.resources ?? ganttResources} dataProvider={{ getList }}>
    <ResourceViewProvider resource={ganttRecord.modelLabel} scope="local" initialState={{
      view: "gantt", anchor: "2026-09-01", page: options.page ?? 1, pageSize: options.pageSize ?? 10,
    }}><Collection /></ResourceViewProvider>
  </Provider></RouterContextProvider>);
  return { ...view, getList, state: () => state, barRequests: () => getList.mock.calls.filter(([params]) => params.resource === "schedules") };
}

describe("Gantt collection over native list data", () => {
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

  test("pages by related rows and fetches every record page for those rows", async () => {
    const f = renderCollection({ page: 2, pageSize: 10, getList: async ({ resource, pagination }) => {
      if (resource === "lanes") return { data: ganttLanes, total: 21 };
      return {
        data: pagination?.currentPage === 1
          ? Array.from({ length: MAX_PAGE_SIZE }, (_, index) => ({ ...scheduledRecord, id: `schedule-${index}` }))
          : [{ ...scheduledRecord, id: "schedule-last" }],
        total: MAX_PAGE_SIZE + 1,
      };
    } });
    await waitFor(() => expect(drawing.props?.events).toHaveLength(MAX_PAGE_SIZE + 1));
    expect(f.getList).toHaveBeenCalledWith(expect.objectContaining({ resource: "lanes", pagination: { mode: "server", currentPage: 2, pageSize: 10 } }));
    expect(f.barRequests().map(([params]) => params.pagination)).toEqual([
      { mode: "server", currentPage: 1, pageSize: MAX_PAGE_SIZE },
      { mode: "server", currentPage: 2, pageSize: MAX_PAGE_SIZE },
    ]);
    expect(f.barRequests()[1]?.[0].meta?.gqlVariables).toEqual(f.barRequests()[0]?.[0].meta?.gqlVariables);
    expect(f.getList).toHaveBeenCalledTimes(3);
    expect(f.state().state.pagination.pageIndex).toBe(1);
  });

  test("pins the lane group even when another valid group is selected", async () => {
    const f = renderCollection();
    await waitFor(() => expect(f.state().state.groupStack).toEqual([{ field: "lane" }]));
    act(() => f.state().setGroup({ field: "status" }));
    await waitFor(() => expect(f.state().state.groupStack).toEqual([{ field: "lane" }]));
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
