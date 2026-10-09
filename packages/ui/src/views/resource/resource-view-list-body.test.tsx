// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { schemaFieldMetadataFromDataResources, type ModelMetadata } from "@angee/metadata";
import { testDataResource, testQueryField } from "@angee/metadata/testing";
import { getCoreRowModel, useReactTable, flexRender } from "@tanstack/react-table";
import { afterEach, expect, test, vi } from "vitest";
import { AppRuntimeProvider, createRouteHref } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import type { WidgetRenderProps } from "../../widgets";

import {
  buildColumns,
  cellContent,
  groupMeasuresFromColumns,
  hasuraMeasuresFromGroupMeasures,
  ListCellContent,
  RowActionsHeader,
} from "./resource-view-list-body";

vi.mock("../../i18n", () => ({
  useUiT: () => (key: string) => key,
}));

afterEach(cleanup);

test.each([undefined, "text", "statusBadge"])("conditional cells hide ordinary values and %s widgets", (widget) => {
  const column = { field: "status", widget, showWhen: (row: { visible: boolean }) => row.visible };
  const { container, rerender } = render(<ListCellContent column={column} row={{ status: "ACTIVE", visible: false }} />);
  expect(container.textContent).toBe("");
  rerender(<ListCellContent column={column} row={{ status: "ACTIVE", visible: true }} />);
  expect(container.textContent).not.toBe("");
});

test("conditional cells do not invoke a hidden custom renderer", () => {
  const renderValue = vi.fn(() => "Visible value");
  const { rerender } = render(<ListCellContent column={{ field: "value", showWhen: () => false, render: renderValue }} row={{ value: 0 }} />);
  expect(renderValue).not.toHaveBeenCalled();
  rerender(<ListCellContent column={{ field: "value", showWhen: () => true, render: renderValue }} row={{ value: 0 }} />);
  expect(screen.getByText("Visible value")).toBeTruthy();
});

test("renders a visually hidden list-column header", () => {
  const [column] = buildColumns(
    [
      {
        field: "actions",
        header: "Actions",
        headerVisuallyHidden: true,
        sortable: false,
      },
    ],
    {},
  );
  function Header() {
    const table = useReactTable({ data: [], columns: [column!], getCoreRowModel: getCoreRowModel() });
    const header = table.getHeaderGroups()[0]!.headers[0]!;
    return <>{flexRender(header.column.columnDef.header, header.getContext())}</>;
  }
  render(<Header />);

  expect(screen.getByText("Actions").classList.contains("sr-only")).toBe(true);
});

test("renders the framework row-actions header as visually hidden copy", () => {
  render(
    <table>
      <thead>
        <tr>
          <RowActionsHeader />
        </tr>
      </thead>
    </table>,
  );

  expect(screen.getByText("list.actions").classList.contains("sr-only")).toBe(true);
});

test("projects count columns into aggregate measures", () => {
  expect(
    groupMeasuresFromColumns([
      { field: "id", header: "Files", aggregate: "count" },
      { field: "size_bytes", header: "Size", aggregate: "sum" },
    ]),
  ).toEqual([
    { op: "count", field: "id", columnId: "id", label: "Files", unit: "" },
    {
      op: "sum",
      field: "size_bytes",
      columnId: "size_bytes",
      label: "Size",
      unit: "",
    },
  ]);
});

test("resolves a column measure once to its server aggregate input", () => {
  const metadata = {
    resource: {
      aggregateMeasures: [{ op: "sum", field: "word_count", input: "WORD_COUNT" }],
    },
  } as unknown as ModelMetadata;
  const measures = groupMeasuresFromColumns([
    { field: "word_count", header: "Words", aggregate: "sum" },
  ]);

  expect(hasuraMeasuresFromGroupMeasures(measures, metadata)).toEqual([{
    op: "sum",
    field: "WORD_COUNT",
    input: "WORD_COUNT",
    columnId: "word_count",
    label: "Words",
    unit: "",
  }]);
});

test("routes boolean cell copy through the UI translator", () => {
  const t = (key: string) => ({ "list.yes": "Sí", "list.no": "No" })[key] ?? key;

  expect(cellContent({ field: "enabled" }, { id: "1", enabled: true }, t)).toBe("Sí");
  expect(cellContent({ field: "enabled" }, { id: "2", enabled: false }, t)).toBe("No");
});

test("renders a metadata-declared date scalar without relying on its name", () => {
  const metadata = modelMetadata("published", "DateTime");

  const { container } = render(
    <>{cellContent(
      { field: "published" },
      { id: "1", published: "2026-08-22T10:00:00Z" },
      (key) => key,
      metadata,
    )}</>,
  );

  expect(container.querySelector("time")?.getAttribute("datetime")).toBe(
    "2026-08-22T10:00:00.000Z",
  );
});

test("does not probe a date-looking field declared as a string", () => {
  const metadata = modelMetadata("published_at", "String");

  const { container } = render(
    <>{cellContent(
      { field: "published_at" },
      { id: "1", published_at: "2026-08-22T10:00:00Z" },
      (key) => key,
      metadata,
    )}</>,
  );

  expect(container.querySelector("time")).toBeNull();
  expect(screen.getByText("2026-08-22T10:00:00Z")).toBeTruthy();
});

test("model-backed cells keep a Float named duration numeric", () => {
  const { container } = render(<ListCellContent
    column={{ field: "duration" }} row={{ duration: 2 }}
    metadata={modelMetadata("duration", "Float")} />);
  expect(container.textContent).toBe("2");
  expect(container.querySelector("time")).toBeNull();
});

test("renders metadata enum labels in the normal list-cell path", () => {
  const resource = testDataResource("tests.Row", {
    fields: [{
      name: "status", kind: "enum", values: [{ value: "PENDING", description: "Needs approval" }],
      readable: true, filterable: false, sortable: false, aggregatable: false,
      groupable: false, creatable: false, updatable: false, requiredOnCreate: false,
    }],
  });
  const metadata = schemaFieldMetadataFromDataResources([resource]).labels[resource.modelLabel]!;
  render(<ListCellContent column={{ field: "status" }} row={{ status: "PENDING" }} metadata={metadata} />);
  expect(screen.getByText("Needs approval")).toBeTruthy();
});

test("passes a column's status display to its cell widget", () => {
  const renderStatus = vi.fn(({ field }: WidgetRenderProps) => <span>{field?.statusDisplay}</span>);
  render(
    <AppRuntimeProvider runtime={{ widgets: {
      "test.status": { read: renderStatus, cell: renderStatus },
    } }}>
      <ListCellContent
        column={{ field: "status", statusDisplay: "dot", widget: "test.status" }}
        row={{ status: "ACTIVE" }}
      />
    </AppRuntimeProvider>,
  );

  expect(screen.getByText("dot")).toBeTruthy();
});

test("to-many relation cells link each retained record label without another read", () => {
  const user = testDataResource("iam.User", { recordRepresentation: "display_name" });
  const { Provider, clearClients } = createUiTestProviders({ resources: [user] });
  const getOne = vi.fn();
  const runtime = {
    routeHref: createRouteHref([{ name: "iam.users", path: "/iam/users" }, { name: "iam.users.record", path: "/iam/users/$id" }]),
    routesByResource: { "iam.User": { collection: "iam.users", record: { name: "iam.users.record", param: "id" } } },
  };
  try {
    render(<Provider dataProvider={{ getOne }}><AppRuntimeProvider runtime={runtime}>
      <ListCellContent
        column={{ field: "assignees", relationList: {
          model: "iam.User", identityPath: "id", labelPath: "display_name",
        } }}
        row={{ assignees: [
          { id: "usr_1", display_name: "Ada" },
          { id: "usr_2", display_name: "Lin" },
        ] }}
      />
    </AppRuntimeProvider></Provider>);
    expect(screen.getByRole("link", { name: "Ada" }).getAttribute("href")).toBe("/iam/users/usr_1");
    expect(screen.getByRole("link", { name: "Lin" }).getAttribute("href")).toBe("/iam/users/usr_2");
    expect(getOne).not.toHaveBeenCalled();
  } finally {
    cleanup();
    clearClients();
  }
});

test("a to-many relation whose backend widget has no cell still renders the linked chips", () => {
  const tag = testDataResource("tags.Tag", { recordRepresentation: "name" });
  const { Provider, clearClients } = createUiTestProviders({ resources: [tag] });
  const runtime = {
    widgets: { "angee.tags.tags": { read: () => <span>form widget</span> } },
    routeHref: createRouteHref([{ name: "tags", path: "/tags" }, { name: "tags.record", path: "/tags/$id" }]),
    routesByResource: { "tags.Tag": { collection: "tags", record: { name: "tags.record", param: "id" } } },
  };
  try {
    render(<Provider><AppRuntimeProvider runtime={runtime}>
      <ListCellContent
        column={{ field: "tags", widget: "angee.tags.tags", relationList: {
          model: "tags.Tag", identityPath: "id", labelPath: "name",
        } }}
        row={{ tags: [{ id: "tag_1", name: "Urgent" }] }}
      />
    </AppRuntimeProvider></Provider>);
    expect(screen.getByRole("link", { name: "Urgent" }).getAttribute("href")).toBe("/tags/tag_1");
    expect(screen.queryByText("form widget")).toBeNull();
  } finally {
    cleanup();
    clearClients();
  }
});

test("renders query enum labels from wire values and preserves declared row aliases", () => {
  const queryField = testQueryField("wire_status", {
    kind: "enum",
    values: [{ value: "PENDING", description: "Needs approval" }],
    filter: { field: "status", scalar: "Enum", values: [], operators: ["exact"], valueMap: [{ from: "PENDING", to: "pending" }] },
  });
  expect(cellContent({ field: "status", queryField }, { wire_status: "PENDING" }, (key) => key))
    .toBe("Needs approval");
});

test("renders query relation labels with the declared identity fallback", () => {
  const queryField = testQueryField("channel.public_key", {
    kind: "relation", scalar: "ID",
    relation: { model: "messaging.Channel", identityPath: "channel.public_key", labelPath: "channel.display_name" },
  });
  expect(cellContent({ field: "channel", queryField }, { channel: { public_key: "chan_1", display_name: "Inbox" } }, (key) => key))
    .toBe("Inbox");
  expect(cellContent({ field: "channel", queryField }, { channel: { public_key: "chan_1", id: "private-id", display_name: null } }, (key) => key))
    .toBe("chan_1");
  expect(cellContent({ field: "channel", queryField }, { channel: null }, (key) => key)).toBe("—");
  expect(cellContent({ field: "caption" }, { caption: "" }, (key) => key)).toBe("—");
});

test("uses query scalar metadata for dates and translated boolean aliases", () => {
  const dateField = testQueryField("observed", { scalar: "DateTime" });
  const { container } = render(<>{cellContent(
    { field: "recorded", queryField: dateField }, { observed: "2026-08-22T10:00:00Z" }, (key) => key,
  )}</>);
  expect(container.querySelector("time")?.getAttribute("datetime")).toBe("2026-08-22T10:00:00.000Z");
  const t = (key: string) => ({ "list.yes": "Sí", "list.no": "No" })[key] ?? key;
  expect(cellContent({ field: "enabled", queryField: testQueryField("is_enabled", { scalar: "Boolean" }) }, { is_enabled: true }, t)).toBe("Sí");
  expect(cellContent({ field: "recorded", queryField: testQueryField("observed") }, { observed: "2026-08-22T10:00:00Z" }, t))
    .toBe("2026-08-22T10:00:00Z");
});

function modelMetadata(name: string, scalar: string): ModelMetadata {
  const resource = testDataResource("tests.Row", {
    fields: [{
      name, kind: "scalar", scalar, readable: true, filterable: false,
      sortable: false, aggregatable: false, groupable: false, creatable: false,
      updatable: false, requiredOnCreate: false,
    }],
  });
  return schemaFieldMetadataFromDataResources([resource]).labels[resource.modelLabel]!;
}
