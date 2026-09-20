// @vitest-environment happy-dom

import { cleanup, render, screen, within } from "@testing-library/react";
import { testQueryField } from "@angee/metadata/testing";
import { afterEach, describe, expect, test } from "vitest";
import * as v from "valibot";

import { DashboardSnapshotSchema, parseDashboardSnapshot, type DashboardWidgetData, type WidgetSpec } from "./headless";
import { BUILTIN_DASHBOARD_WIDGET_KINDS } from "./kinds";

const TableWidget = BUILTIN_DASHBOARD_WIDGET_KINDS.find(({ id }) => id === "table")!.Component;
const nestedFields = {
  "step_run.run.id": testQueryField("step_run.run.id"),
  "step_run.run.workflow.name": testQueryField("step_run.run.workflow.name"),
  "step_run.step.name": testQueryField("step_run.step.name"),
  "step_run.status": testQueryField("step_run.status", {
    kind: "enum", values: [{ value: "pending", description: "Pending review" }],
  }),
};
const spec: WidgetSpec = {
  schemaVersion: 1,
  id: "review-rows",
  kind: "table",
  kindVersion: 1,
  title: "Reviews",
  data: {
    shape: "rows",
    source: {
      resource: "workflows.Decision",
      fields: Object.keys(nestedFields),
    },
  },
  options: {},
  x: 0,
  y: 0,
  w: 6,
  h: 4,
  isArchived: false,
};
const data: DashboardWidgetData = {
  value: null,
  series: [],
  rows: [],
  queryFields: nestedFields,
  identity: { field: "public_key" },
  fetching: false,
  error: null,
  live: false,
  updatedAt: null,
  refetch: () => {},
};

afterEach(() => cleanup());

describe("dashboard table columns", () => {
  test("renders declared nested paths in order, with labels and nullable relations", () => {
    render(<TableWidget
      spec={{
        ...spec,
        options: {
          columns: [
            { path: "step_run.run.id", label: "Run" },
            { path: "step_run.run.workflow.name", label: "Workflow" },
            { path: "step_run.step.name" },
            { path: "step_run.status", label: "Status" },
          ],
        },
      }}
      data={{
        ...data,
        rows: [
          {
            public_key: "decision-1",
            unselected: "Not a column",
            step_run: {
              status: "pending",
              status_label: "Ignored companion guess",
              step: { name: "Confirm details" },
              run: { id: "run-1", workflow: { name: "Invoice intake" } },
            },
          },
          { public_key: "decision-2", step_run: null },
        ],
      }}
    />);

    expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent))
      .toEqual(["Run", "Workflow", "Step Run Step Name", "Status"]);
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]!).getAllByRole("cell").map((cell) => cell.textContent))
      .toEqual(["run-1", "Invoice intake", "Confirm details", "Pending review"]);
    expect(within(rows[2]!).getAllByRole("cell").map((cell) => cell.textContent))
      .toEqual(["", "", "", ""]);
  });

  test("defaults to selected logical fields in declaration order, excluding native identity", () => {
    const queryFields = {
      public_key: testQueryField("public_key", { scalar: "ID" }),
      title: testQueryField("title"),
      status: testQueryField("wire_status", { kind: "enum", values: [{ value: "PENDING", description: "Needs review" }] }),
    };
    render(<TableWidget
      spec={{ ...spec, data: { shape: "rows", source: { resource: "workflows.Decision", fields: ["public_key", "status", "title"] } } }}
      data={{ ...data, queryFields, rows: [{ public_key: "decision-1", title: "Review", wire_status: "PENDING", extra: "Not selected" }] }}
    />);

    expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Status", "Title"]);
    expect(screen.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["Needs review", "Review"]);
  });

  test("uses declared row projections and relation labels without normalizing wire values", () => {
    const queryFields = {
      count: testQueryField("message_count", { scalar: "Int" }),
      channel: testQueryField("channel.public_key", {
        kind: "relation",
        scalar: "ID",
        relation: { model: "messaging.Channel", identityPath: "channel.public_key", labelPath: "channel.display_name" },
      }),
      "channel.display_name": testQueryField("channel.display_name"),
      status: testQueryField("wire_status", {
        kind: "enum",
        values: [{ value: "PENDING", description: "Awaiting review" }],
        filter: {
          field: "status", scalar: "Enum", values: [], operators: ["exact"],
          valueMap: [{ from: "PENDING", to: "pending" }],
        },
      }),
    };
    render(<TableWidget
      spec={{
        ...spec,
        data: { shape: "rows", source: { resource: "messaging.Message", fields: Object.keys(queryFields) } },
        options: { columns: [{ path: "count" }, { path: "channel" }, { path: "status" }] },
      }}
      data={{
        ...data,
        queryFields,
        rows: [
          { id: "message-1", message_count: 0, channel: { public_key: "chan_1", display_name: "Inbox" }, wire_status: "PENDING" },
          { id: "message-2", message_count: 7, channel: { public_key: "chan_2", id: "wrong-id", display_name: null }, wire_status: "PENDING" },
          { id: "message-3", message_count: null, channel: null, wire_status: null },
        ],
      }}
    />);

    expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Count", "Channel", "Status"]);
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]!).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["0", "Inbox", "Awaiting review"]);
    expect(within(rows[2]!).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["7", "chan_2", "Awaiting review"]);
    expect(within(rows[3]!).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["", "", ""]);
  });

  test.each([
    { columns: [], path: [] },
    { columns: [{ path: "" }], path: [0, "path"] },
    { columns: [{ path: "title", label: null }], path: [0, "label"] },
    { columns: [{ path: "title", label: "" }], path: [0, "label"] },
    { columns: [{ path: "title", extra: true }], path: [0, "extra"] },
    { columns: [{ path: "title" }, { path: "title" }], path: [1, "path"] },
    { columns: [{ path: "not_selected" }], path: [0, "path"] },
  ])("preserves the exact nested issue path for invalid columns: %j", ({ columns, path }) => {
    const result = v.safeParse(DashboardSnapshotSchema, {
      schemaVersion: 1,
      columns: 12,
      widgets: [{ ...spec, options: { columns } }],
    });
    expect(result.success).toBe(false);
    expect(result.issues?.[0].path?.map(({ key }) => key)).toEqual(["widgets", 0, "options", "columns", ...path]);
  });

  test.each([
    { shape: "value", source: { resource: "workflows.Decision" } },
    { shape: "series", source: { resource: "workflows.Decision" } },
    { shape: "none", binding: { dashboardKey: "review", widgetId: "action" } },
  ])("rejects declared columns on non-row widgets: %j", (widgetData) => {
    expect(() => parseDashboardSnapshot({
      schemaVersion: 1,
      columns: 12,
      widgets: [{ ...spec, data: widgetData, options: { columns: null } }],
    })).toThrow("options.columns is only valid for row widgets");
  });

  test("renders malformed columns as a widget error without throwing", () => {
    render(<TableWidget spec={{ ...spec, options: { columns: false } }} data={data} />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  test("keeps record row elements when rows reorder and declares accessible table headers", () => {
    const widget = { ...spec, data: { shape: "rows" as const, source: { resource: "workflows.Decision", fields: ["title"] } } };
    const queryFields = { title: testQueryField("title") };
    const first = { public_key: "decision-1", title: "First" };
    const second = { public_key: "decision-2", title: "Second" };
    const { rerender } = render(<>
      <h3 id="reviews-title">Pending reviews</h3>
      <TableWidget spec={widget} data={{ ...data, queryFields, rows: [first, second] }} titleId="reviews-title" />
    </>);
    const firstRow = screen.getByText("First").closest("tr");
    expect(screen.getByRole("table", { name: "Pending reviews" }).getAttribute("aria-labelledby")).toBe("reviews-title");
    expect(screen.getByRole("columnheader").getAttribute("scope")).toBe("col");

    rerender(<>
      <h3 id="reviews-title">Pending reviews</h3>
      <TableWidget spec={widget} data={{ ...data, queryFields, rows: [second, first] }} titleId="reviews-title" />
    </>);
    expect(screen.getByText("First").closest("tr")).toBe(firstRow);
    expect(screen.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["Second", "First"]);
  });

  test("preserves archived widget options without applying active row column validation", () => {
    const snapshot = parseDashboardSnapshot({
      schemaVersion: 1,
      columns: 12,
      widgets: [{ ...spec, isArchived: true, options: { columns: false } }],
    });
    expect(snapshot.widgets[0]!.options.columns).toBe(false);
  });
});
