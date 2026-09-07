// @vitest-environment happy-dom

import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { Refine, type DataProvider, type GetListParams } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { ResourceQuery, ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources, type DataResourceFieldMetadata, type DataResourceMetadata, type Row } from "@angee/metadata";
import { testDataResource, testQueryField } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";

import { ToastProvider } from "../../feedback";
import { BoardView } from "./BoardView";
import { FlatListBody } from "./resource-view-list-body";
import { ResourceViewProvider, useResourceView } from "./resource-view-context";
import { useClientResourceViewSurface, useResourceViewSurface, type ResourceViewSurface } from "./resource-view-surface";
import { requestedFieldPaths } from "./resource-view-codecs";
import type { ResourceViewGroup } from "./resource-view-model";

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => vi.fn(),
}));

const columns = ["title", "sender_name", "thread_title", "channel_vendor_name", "status", "sent_at"]
  .map((field) => ({ field, header: field }));
const scalarField = (name: string): DataResourceFieldMetadata => ({
  name, kind: "scalar", scalar: "String", readable: true, aggregatable: false,
  creatable: false, updatable: false, requiredOnCreate: false,
});
const query = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" },
  ...Object.fromEntries(columns.map(({ field }) => [field, { scalar: "String" }])),
  channel: { kind: "relation", identityPath: "channel.id", labelPath: "channel.display_name" },
  sent_at: { scalar: "DateTime" },
  status: { kind: "enum", values: [{ value: "NEW", description: "New" }, { value: "SYNCED", description: "Synced" }] },
} }).contract;
query.fields.channel!.relation = { model: "integrate.Integration", identityPath: "channel.id", labelPath: "channel.display_name" };
query.axes.channel!.server = { input: "CHANNEL", key: "channel_id", labelInput: "CHANNEL__DISPLAY_NAME", labelKey: "channel__display_name" };
query.axes.channel!.drill = { kind: "identity", field: "channel", valueKey: "channel_id", nullMode: "isNull", valueMap: [] };
// The inbox declares only scalar display columns. Group selections come from its query.
const message = testDataResource("messaging.Message", {
  roots: { aggregate: "messages_aggregate" }, typeNames: { filter: "MessageBoolExp", order: "MessageOrderBy" }, query,
  fields: [scalarField("id"), ...columns.map(({ field }) => ({ ...scalarField(field),
    ...(field === "status" ? { kind: "enum" as const, values: query.fields.status!.values } : {}),
    ...(field === "sent_at" ? { scalar: "DateTime" } : {}),
  })), { ...scalarField("channel"), kind: "relation", scalar: null,
    relationObject: true, relationModelLabel: "integrate.Integration" }],
});
const integration = testDataResource("integrate.Integration", {
  fields: [scalarField("id"), scalarField("display_name")], recordRepresentation: "display_name",
  query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, display_name: { scalar: "String" } } }).contract,
});
const channelNames = ["CATC", "B.V.", "2026-09"];
const rows: Row[] = Array.from({ length: 6 }, (_, index) => ({
  id: `message-${index}`, title: `Message ${index}`, sender_name: `Sender ${index % 3}`,
  thread_title: `Thread ${index % 2}`, channel_vendor_name: "Slack", status: index % 2 ? "SYNCED" : "NEW",
  sent_at: index % 2 ? "2026-09-01T00:00:00Z" : "2026-08-01T00:00:00Z",
  channel: { id: `channel-${index % 3}`, display_name: channelNames[index % 3] },
}));
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); });

function fixture({
  view = "board", source = "state", data = rows, resource = message, target = integration, groups, displayColumns = columns,
}: {
  view?: "board" | "list";
  source?: "state" | "default";
  data?: Row[];
  resource?: DataResourceMetadata;
  target?: DataResourceMetadata;
  groups?: readonly ResourceViewGroup[];
  displayColumns?: typeof columns;
} = {}) {
  const schema = schemaFieldMetadataFromDataResources([resource, target]);
  const model = schema.labels[resource.modelLabel]!;
  const groupStack = groups ?? [{ field: "channel" }];
  const getList = vi.fn(async (_params: GetListParams) => ({ data, total: data.length }));
  const provider = { getApiUrl: () => "test://inbox", getList, getOne: vi.fn(),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as DataProvider;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(client);
  let surface!: ResourceViewSurface<Row>;
  let resourceViewState!: ReturnType<typeof useResourceView>;
  const useSurface = resource.rowModel === "client" ? useClientResourceViewSurface : useResourceViewSurface;
  function Surface() {
    const resourceView = useResourceView();
    resourceViewState = resourceView;
    surface = useSurface({ resource: resource.modelLabel, modelMetadata: model,
      columns: displayColumns, resourceView, groupStack });
    return view === "board" ? <BoardView columns={displayColumns} groups={surface.groupedRows}
      resourceView={resourceView} selectedIds={surface.selectedIds} interactive={false}
      emptyContent="No messages" fetching={surface.list.fetching} />
      : <FlatListBody {...surface} columns={displayColumns} resourceView={resourceView} groupStack={groupStack}
        onPageSelectionChange={surface.setPageSelection} fetching={surface.list.fetching}
        interactive={false} selectable={false} emptyContent="No messages" />;
  }
  render(
    <Refine resources={[...refineResourcesFromDataResources([resource, target])]} dataProvider={{ default: provider, console: provider }}
      options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>
      <ModelMetadataProvider metadata={schema}><ToastProvider>
        <ResourceViewProvider resource={resource.modelLabel} scope="local" initialState={{ view, pageSize: 20,
          // Default groups are a surface declaration; the URL state can be empty.
          groupStack: source === "default" ? [] : groupStack }}><Surface /></ResourceViewProvider>
      </ToastProvider></ModelMetadataProvider>
    </Refine>,
  );
  return { get surface() { return surface; }, get view() { return resourceViewState; }, getList, model, schema, groupStack };
}

test("semantic JSON columns select their scalar root and custom record identities drive native rows", async () => {
  const resource = { ...message, fields: [scalarField("public_key"), { ...scalarField("metadata"), scalar: "JSON" }],
    query: { ...query, identity: { field: "public_key" }, fields: {
      public_key: testQueryField("public_key", { scalar: "ID", filter: null }),
      "metadata.mailbox": testQueryField("metadata.mailbox", { filter: null, row: { path: "metadata.mailbox", paths: ["metadata"] } }),
    }, axes: {} },
  };
  const f = fixture({ resource, view: "list", groups: [], displayColumns: [{ field: "metadata.mailbox", header: "Mailbox" }],
    data: [{ public_key: "record-unique", metadata: { mailbox: "inbox" } }] });
  expect(await screen.findByText("inbox")).toBeTruthy();
  expect(f.surface.table.getRowModel().rows[0]?.id).toBe("record-unique");
  act(() => f.surface.table.getRowModel().rows[0]!.toggleSelected());
  expect(f.view.state.rowSelection).toEqual({ "record-unique": true });
  expect(f.getList.mock.calls[0]?.[0].meta?.fields).toEqual(["public_key", "metadata"]);
  expect(f.surface.requestedFields).toEqual(["public_key", "metadata"]);
});

test.each(["state", "default"] as const)("inbox %s grouping renders one board lane per channel without a channel column", async (source) => {
  const f = fixture({ source });
  await waitFor(() => expect(f.surface.list.error).toBeNull());
  for (const name of channelNames) {
    const lane = await screen.findByRole("region", { name });
    expect(within(lane).getAllByRole("article")).toHaveLength(2);
  }
  expect(f.surface.groupedRows).toHaveLength(3);
  expect(f.surface.table.getState().grouping).toEqual(["channel"]);
  expect(f.surface.visibleFields.map(({ id }) => id)).toEqual(columns.map(({ field }) => field));
  expect(f.surface.requestedFields).toEqual(["id", ...columns.map(({ field }) => field), "channel.id", "channel.display_name"]);
  const request = f.getList.mock.calls.at(-1)?.[0];
  expect(request?.meta?.gqlQuery).toBeTruthy();

});

test("the same relation projection labels flat grouped headers", async () => {
  const f = fixture({ view: "list" });
  await waitFor(() => expect(f.surface.rowModels).toHaveLength(3));
  for (const name of channelNames) {
    expect(await screen.findByRole("button", { name: `${name} 2` })).toBeTruthy();
  }
  expect(screen.getAllByRole("columnheader")).toHaveLength(6);
});

test("distinct relation identities keep separate lanes when names match, while null remains empty", async () => {
  const f = fixture({ data: [
    { id: "1", title: "First", channel: { id: "first", display_name: "Shared name" } },
    { id: "2", title: "Second", channel: { id: "second", display_name: "Shared name" } },
    { id: "3", title: "Missing", channel: null },
  ] });
  await waitFor(() => expect(f.surface.groupedRows).toHaveLength(3));
  expect(await screen.findAllByRole("region", { name: "Shared name" })).toHaveLength(2);
  expect(screen.getByRole("region", { name: "No value" })).toBeTruthy();
  expect(f.surface.groupedRows.slice(0, 2).map(({ key }) => key)).toEqual(["channel:first", "channel:second"]);
});

test("a selected scalar identity and an aliased label dimension use their resource field paths", async () => {
  const resource = { ...message, fields: [...(message.fields ?? []), scalarField("channel_id")],
    query: { ...query, axes: { ...query.axes, channel: { ...query.axes.channel!,
      identityPath: "channel_id", paths: ["channel_id", "channel.display_name"],
      server: { ...query.axes.channel!.server!, labelKey: "channelLabel" },
    } } } };
  const f = fixture({ resource, data: [{ id: "1", title: "Message", channel_id: "identity",
    channel: { id: "other", display_name: "Axis label" } }] });
  expect(await screen.findByRole("region", { name: "Axis label" })).toBeTruthy();
  expect(f.surface.groupedRows[0]?.key).toBe("channel:identity");
  expect(f.surface.requestedFields).toContain("channel_id");
  expect(f.surface.requestedFields).toContain("channel.display_name");
  expect(f.surface.requestedFields).not.toContain("channelLabel");
});

test("client relation grouping without server dimensions uses the target's public id and representation", async () => {
  const resource = { ...message, rowModel: "client" as const,
    query: { ...query, axes: { ...query.axes, channel: { ...query.axes.channel!, server: undefined,
      identityPath: "channel.public_id", paths: ["channel.public_id", "channel.display_name"],
    } } } };
  const target = { ...integration, query: { ...integration.query, identity: { field: "public_id" } },
    fields: [scalarField("public_id"), scalarField("display_name")] };
  const f = fixture({ resource, target, data: [{ id: "1", title: "Message",
    channel: { public_id: "public-channel", display_name: "Representation" } }] });
  expect(await screen.findByRole("region", { name: "Representation" })).toBeTruthy();
  expect(f.surface.requestedFields).toContain("channel.public_id");
  expect(f.surface.requestedFields).toContain("channel.display_name");
  expect(f.surface.groupedRows[0]?.key).toBe("channel:public-channel");
});

test.each([
  { group: { field: "channel_vendor_name" }, names: ["Slack"] },
  { group: { field: "status" }, names: ["New", "Synced"] },
  { group: { field: "sent_at", granularity: "month" as const }, names: ["August 2026", "September 2026"] },
])("scalar/enum/date grouping keeps its existing keys and labels: $group.field", async ({ group, names }) => {
  const resource = { ...message, fields: (message.fields ?? []).map((field) => field.name === "status"
    ? { ...field, kind: "enum" as const, values: [{ value: "NEW", description: "New" }, { value: "SYNCED", description: "Synced" }] } : field) };
  const f = fixture({ resource, groups: [group] });
  for (const name of names) expect(await screen.findByRole("region", { name })).toBeTruthy();
  expect(f.surface.table.getState().grouping).toEqual([group.granularity ? `${group.field}:${group.granularity}` : group.field]);
  expect(requestedFieldPaths(columns, undefined, f.model, undefined, [group])).toEqual(["id", ...columns.map(({ field }) => field)]);
});


test("client board grouping follows native local filtering after rows are already loaded", async () => {
  const f = fixture({ resource: { ...message, rowModel: "client" } });
  await waitFor(() => expect(f.surface.groupedRows).toHaveLength(3));
  act(() => f.view.setFilter({ title: { exact: "Message 0" } }));
  await waitFor(() => expect(f.surface.groupedRows).toHaveLength(1));
  expect(screen.getByRole("region", { name: "CATC" })).toBeTruthy();
  expect(screen.getAllByRole("article")).toHaveLength(1);
  expect(f.surface.groupedRows[0]?.rows.map(({ id }) => id)).toEqual(["message-0"]);
});


test("client filters and sorts on a hidden declared field select its value once", async () => {
  const hidden = ResourceQuery.forRows({ fields: { hidden_rank: { scalar: "Float" } } }).contract;
  const resource = { ...message, rowModel: "client" as const,
    query: { ...query, fields: { ...query.fields, ...hidden.fields }, axes: { ...query.axes, ...hidden.axes } },
    fields: [...message.fields ?? [], { ...scalarField("hidden_rank"), scalar: "Float" }],
  };
  const f = fixture({ resource, groups: [], data: rows.map((row, index) => ({ ...row, hidden_rank: index })) });
  await waitFor(() => expect(f.surface.rows).toHaveLength(6));
  expect(f.surface.requestedFields).toContain("hidden_rank");
  const reads = f.getList.mock.calls.length;
  act(() => f.view.setSorting([{ id: "hidden_rank", desc: true }]));
  await waitFor(() => expect(f.surface.rows[0]?.id).toBe("message-5"));
  act(() => f.view.setFilter({ hidden_rank: { gte: 4 } }));
  await waitFor(() => expect(f.surface.rows.map(({ id }) => id)).toEqual(["message-5", "message-4"]));
  expect(f.getList).toHaveBeenCalledTimes(reads);
  expect(f.surface.visibleFields.map(({ id }) => id)).toEqual(columns.map(({ field }) => field));
});


test("native client date sorting compares instants across timezone spellings", async () => {
  const f = fixture({ resource: { ...message, rowModel: "client" }, groups: [], data: [
    { ...rows[0], id: "later", sent_at: "2026-09-07T10:30:00Z" },
    { ...rows[1], id: "earlier", sent_at: "2026-09-07T12:00:00+02:00" },
  ] });
  await waitFor(() => expect(f.surface.rows).toHaveLength(2));
  act(() => f.view.setSorting([{ id: "sent_at", desc: false }]));
  await waitFor(() => expect(f.surface.rows.map(({ id }) => id)).toEqual(["earlier", "later"]));
});
