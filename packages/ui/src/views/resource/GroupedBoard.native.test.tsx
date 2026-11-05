// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { ResourceQuery, type DataResourceFieldMetadata, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";

import { ToastProvider } from "../../feedback";
import { ListView, type ListViewNavigationScope, type ResourceListSnapshot } from "./ListView";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";

const query = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, title: { scalar: "String" }, status: { scalar: "String" },
  channel: { kind: "relation", identityPath: "channel.id", labelPath: "channel.display_name" },
} }).contract;
query.fields.channel!.filter!.field = "channel.id";
query.fields.channel!.relation = { model: "integrate.Integration", identityPath: "channel.id", labelPath: "channel.display_name" };
query.axes.channel!.server = { input: "CHANNEL", key: "channel_id", labelInput: "CHANNEL__DISPLAY_NAME", labelKey: "channel_name" };
query.axes.channel!.drill = { kind: "identity", field: "channel", valueKey: "channel_id", nullMode: "isNull", valueMap: [] };
query.axes.status!.server = { input: "STATUS", key: "status" };
query.axes.status!.drill = { kind: "value", field: "status", valueKey: "status", nullMode: "isNull", valueMap: [] };
const scalarField = (name: string): DataResourceFieldMetadata => ({
  name, kind: "scalar", scalar: name === "id" ? "ID" : "String", readable: true,
  aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
});
const resource = testDataResource("messaging.Message", {
  roots: { groups: "messages_groups", aggregate: "messages_aggregate", delete: undefined },
  typeNames: { filter: "MessageBoolExp", order: "MessageOrderBy" }, query,
  recordRepresentation: "title", capabilities: ["list"],
  fields: [scalarField("id"), scalarField("title"), scalarField("status"), {
    ...scalarField("channel"), kind: "relation", scalar: null, relationObject: true,
    relationModelLabel: "integrate.Integration", nullable: true,
  }],
});
const channels = [
  { id: "mail", label: "Mail", count: 67 },
  { id: "signal", label: "Signal", count: 2 },
  { id: "archived", label: "Archived channel", count: 1 },
  { id: "shared-first", label: "Shared name", count: 1 },
  { id: "shared-second", label: "Shared name", count: 1 },
  { id: null, label: null, count: 1 },
];
type MessageRow = Row & { id?: string; public_key?: string; title: string; status: string; channel: { id: string; display_name: string | null } | null };
// The first 50 records all belong to Mail. A board grouping the list page can
// never discover Signal, the older channel, either duplicate label, or NULL.
const rows: MessageRow[] = channels.flatMap((channel) => Array.from({ length: channel.count }, (_, index) => ({
  id: `${channel.id ?? "unassigned"}-${index + 1}`,
  title: `${channel.id ?? "unassigned"} message ${index + 1}${channel.id === "signal" ? " needle" : ""}`,
  status: "active", channel: channel.id === null ? null : { id: channel.id, display_name: channel.label },
})));
type Where = {
  _and?: Where[];
  channel?: { id: { _eq?: string; _is_null?: boolean } };
  title?: { _ilike: string };
  status?: { _eq: string };
};
type GroupVariables = { where?: Where; group_by: { field: string }[]; limit: number; offset: number };
type LeafRequest = { where?: Where; orderBy?: unknown; limit: number; offset: number; document: unknown };
const terms = (where: Where = {}): Where[] => [where, ...(where._and ?? []).flatMap(terms)];
function leafRequests(calls: { meta?: Record<string, unknown> }[][]): LeafRequest[] {
  return calls.flatMap(([request]) => {
    const variables = request?.meta?.gqlVariables as Record<string, unknown> | undefined;
    if (!variables) return [];
    return Object.keys(variables).filter((key) => /^group_\d+_limit$/.test(key)).map((key) => {
      const prefix = key.slice(0, -"limit".length);
      return {
        where: variables[`${prefix}where`] as Where | undefined,
        orderBy: variables[`${prefix}order_by`],
        limit: variables[key] as number,
        offset: variables[`${prefix}offset`] as number,
        document: request?.meta?.gqlQuery,
      };
    });
  });
}
const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://grouped-board",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

function fixture({ pageSize = 50, error, baseFilter, viewKind = "board", nested = false, customIdentity = false, refreshAction = false, noGroupKeyLabel = false }: {
  pageSize?: number;
  error?: "root" | "leaf";
  baseFilter?: Record<string, unknown>;
  viewKind?: "board" | "list";
  nested?: boolean;
  customIdentity?: boolean;
  refreshAction?: boolean;
  noGroupKeyLabel?: boolean;
} = {}) {
  const baseResource = customIdentity ? { ...resource,
    fields: [scalarField("public_key"), ...resource.fields!.filter((field) => field.name !== "id")],
    query: { ...query, identity: { field: "public_key" }, fields: { ...query.fields,
      ...ResourceQuery.forRows({ fields: { public_key: { scalar: "ID" } } }).contract.fields,
    } },
  } : resource;
  const activeResource = noGroupKeyLabel ? { ...baseResource, query: {
    ...baseResource.query, axes: { ...baseResource.query.axes, channel: {
      ...baseResource.query.axes.channel!, server: { input: "CHANNEL", key: "channel_id" },
    } },
  } } : baseResource;
  const activeRows: MessageRow[] = customIdentity ? rows.map(({ id, ...row }) => ({ ...row, public_key: id })) : rows;
  let refreshedTitle: string | undefined;
  const selectRows = (where: Where | undefined, offset: number, limit: number) => {
    const clauses = terms(where);
    const identity = clauses.find((term) => term.channel)?.channel?.id;
    if (error === "leaf" && identity?._eq === "mail") throw new Error("Mail records unavailable");
    const needle = clauses.find((term) => term.title)?.title?._ilike.replaceAll("%", "");
    const status = clauses.find((term) => term.status)?.status?._eq;
    const filtered = activeRows.filter((row) =>
      (!identity || (identity._is_null ? row.channel === null : row.channel?.id === identity._eq))
      && (!needle || row.title.includes(needle)) && (!status || row.status === status));
    return { rows: filtered.slice(offset, offset + limit).map((row) => refreshedTitle && row.id === "mail-1"
      ? { ...row, title: refreshedTitle } : row), total: filtered.length };
  };
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    if (error === "root") throw new Error("Channel groups unavailable");
    const leaves = leafRequests([[{ meta }]]);
    if (leaves.length > 0) {
      const data: Record<string, unknown> = {};
      leaves.forEach((leaf, index) => {
        const page = selectRows(leaf.where, leaf.offset, leaf.limit);
        data[`group_${index}_rows`] = page.rows;
        data[`group_${index}_aggregate`] = { aggregate: { count: page.total } };
      });
      return { data: { data } };
    }
    const groupVariables = meta!.gqlVariables as GroupVariables;
    if (groupVariables.group_by[0]?.field === "STATUS") {
      const channelId = terms(groupVariables.where).find((term) => term.channel)?.channel?.id;
      const channel = channels.find((channel) => channel.id === (channelId?._is_null ? null : channelId?._eq));
      return { data: { messages_groups: [{ key: { status: "active" }, aggregate: { count: channel!.count } }], totalCount: 1 } };
    }
    const filtered = terms(groupVariables.where).some((term) => term.title)
      ? channels.filter((channel) => channel.id === "signal") : channels;
    return { data: {
      messages_groups: filtered.slice(groupVariables.offset, groupVariables.offset + groupVariables.limit).map((channel) => ({
        key: { channel_id: channel.id, ...(!noGroupKeyLabel ? { channel_name: channel.label } : {}) }, aggregate: { count: channel.count },
      })), totalCount: filtered.length,
    } };
  });
  let view!: ResourceViewContextValue;
  const rowHref = vi.fn((row: MessageRow, _scope?: ListViewNavigationScope) => `/?record=${row.public_key ?? row.id}`);
  const onListStateChange = vi.fn((_state: ResourceListSnapshot<MessageRow>) => undefined);
  function Inbox() {
    view = useResourceView();
    return <ListView<MessageRow> resource={resource.modelLabel} columns={[{ field: "title", header: "Title" }]}
      baseFilter={baseFilter} rowHref={rowHref} onListStateChange={onListStateChange} emptyContent="No messages"
      cardActions={refreshAction ? (_row, { refresh }) => <button onClick={refresh}>Refresh cards</button> : undefined} />;
  }
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/", component: () =>
    <ResourceViewProvider resource={resource.modelLabel} scope="local" initialState={{
      view: viewKind, pageSize, groupStack: [{ field: "channel" }, ...(nested ? [{ field: "status" }] : [])], sorting: [{ id: "title", desc: true }],
    }}><Inbox /></ResourceViewProvider> });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Provider resources={[activeResource]} dataProvider={{ custom }}>
    <OperationDocumentsProvider documents={{ console: { groups: { "messaging.Message":
      "query MessageGroups { messages_groups { key { channel_id channel_name } aggregate { count } } totalCount }",
    } } }}><ToastProvider><RouterProvider router={router} /></ToastProvider></OperationDocumentsProvider>
  </Provider>);
  return { get view() { return view; }, custom,
    groupRequests: () => custom.mock.calls.map(([request]) => request.meta?.gqlVariables as GroupVariables)
      .filter((variables) => Array.isArray(variables?.group_by)),
    leafRequests: () => leafRequests(custom.mock.calls), rowHref, onListStateChange,
    changeTitle: (title: string) => { refreshedTitle = title; } };
}

test("server board discovers all channel buckets before fetching bounded card pages, including NULL and duplicate labels", async () => {
  const f = fixture();
  const mail = await screen.findByRole("region", { name: "Mail" });
  await waitFor(() => expect(within(mail).getAllByRole("article")).toHaveLength(20));
  expect(await screen.findByRole("region", { name: "Signal" })).toBeTruthy();
  expect(await screen.findByRole("region", { name: "Archived channel" })).toBeTruthy();
  const shared = await screen.findAllByRole("region", { name: "Shared name" });
  expect(shared).toHaveLength(2);
  expect(await within(shared[0]!).findByText("shared-first message 1")).toBeTruthy();
  expect(await within(shared[1]!).findByText("shared-second message 1")).toBeTruthy();
  const unassigned = await screen.findByRole("region", { name: "No channel" });
  expect(await within(unassigned).findByText("unassigned message 1")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Groups 1-6 / 6 groups" })).toBeTruthy();
  expect(within(mail).getByRole("button", { name: "Mail records 1-20 / 67" })).toBeTruthy();
  expect(f.custom).toHaveBeenCalled();
  expect(f.leafRequests().length).toBeGreaterThan(0);
  expect(f.leafRequests().every((request) => terms(request.where).some((term) => term.channel))).toBe(true);

  // Labels never become identity: collapsing one namesake leaves the other open.
  fireEvent.click(within(shared[0]!).getByRole("button", { name: "Shared name" }));
  await waitFor(() => expect(within(shared[0]!).queryByRole("article")).toBeNull());
  expect(within(shared[1]!).getByText("shared-second message 1")).toBeTruthy();
});

test("a relation group without a key label uses its batched leaf label, including while collapsed", async () => {
  const f = fixture({ noGroupKeyLabel: true });
  const mail = await screen.findByRole("region", { name: "Mail" });
  expect(await screen.findByRole("region", { name: "Signal" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "mail" })).toBeNull();
  const leaf = f.leafRequests();
  expect(leaf.length).toBeGreaterThan(1);
  expect(JSON.stringify(leaf[0]?.document)).toContain("display_name");
  expect(JSON.stringify(leaf[0]?.document)).toContain("channel");
  fireEvent.click(within(mail).getByRole("button", { name: "Mail" }));
  await waitFor(() => expect(within(mail).queryByRole("article")).toBeNull());
  expect(screen.getByRole("region", { name: "Mail" })).toBeTruthy();
  expect(f.leafRequests().some((request) => request.limit === 1)).toBe(true);
});

test("a grouped company header and readable supplier cell use labels selected in the same leaf document", async () => {
  const invoiceQuery = ResourceQuery.forRows({ fields: {
    id: { scalar: "ID" }, number: { scalar: "String" },
    company: { kind: "relation", identityPath: "company.id", labelPath: "company.display_name" },
    partner: { kind: "relation", identityPath: "partner.id", labelPath: "partner.display_name" },
  } }).contract;
  invoiceQuery.fields.company!.relation = { model: "arp.Company", identityPath: "company.id", labelPath: "company.display_name" };
  invoiceQuery.fields.partner!.relation = { model: "parties.Party", identityPath: "partner.id", labelPath: "partner.display_name" };
  invoiceQuery.axes.company!.server = { input: "COMPANY", key: "company_id" };
  invoiceQuery.axes.company!.drill = { kind: "identity", field: "company", valueKey: "company_id", nullMode: "isNull", valueMap: [] };
  const invoice = testDataResource("accounting.Invoice", {
    roots: { groups: "invoices_groups", aggregate: "invoices_aggregate" },
    typeNames: { filter: "InvoiceBoolExp", order: "InvoiceOrderBy" }, query: invoiceQuery,
    fields: [scalarField("id"), scalarField("number"), ...["company", "partner"].map((name) => ({
      ...scalarField(name), kind: "relation" as const, scalar: null, relationObject: true,
      relationModelLabel: name === "company" ? "arp.Company" : "parties.Party",
    }))],
  });
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const variables = meta!.gqlVariables as Record<string, unknown>;
    if ("group_by" in variables) return { data: { invoices_groups: [
      { key: { company_id: "pty_acme" }, aggregate: { count: 1 } },
      { key: { company_id: "pty_beta" }, aggregate: { count: 1 } },
    ], totalCount: 2 } };
    const data: Record<string, unknown> = {};
    for (const key of Object.keys(variables).filter((name) => /^group_\d+_limit$/.test(name))) {
      const alias = key.slice(0, -"limit".length);
      const acme = JSON.stringify(variables[`${alias}where`]).includes("pty_acme");
      data[`${alias}rows`] = [{ id: acme ? "bill-1" : "bill-2", number: "",
        company: { id: acme ? "pty_acme" : "pty_beta", display_name: acme ? "Acme" : "Beta" },
        partner: { id: acme ? "pty_supplier" : "pty_other", display_name: acme ? "Supplier Inc" : "Other Supplier" },
      }];
      data[`${alias}aggregate`] = { aggregate: { count: 1 } };
    }
    return { data: { data } };
  });
  render(<Provider resources={[invoice]} dataProvider={{ custom }}>
    <OperationDocumentsProvider documents={{ console: { groups: { "accounting.Invoice":
      "query InvoiceGroups { invoices_groups { key { company_id } aggregate { count } } totalCount }",
    } } }}><ToastProvider><ResourceViewProvider resource="accounting.Invoice" scope="local" initialState={{
      view: "list", groupStack: [{ field: "company" }], columnVisibility: { company: false },
    }}><ListView resource="accounting.Invoice" columns={[
      { field: "number", header: "Number" }, { field: "company", header: "Company" },
      { field: "partner", header: "Supplier" },
    ]} /></ResourceViewProvider></ToastProvider></OperationDocumentsProvider>
  </Provider>);
  expect(await screen.findByRole("button", { name: "Acme" })).toBeTruthy();
  expect(await screen.findByRole("button", { name: "Beta" })).toBeTruthy();
  expect(await screen.findByText("Supplier Inc")).toBeTruthy();
  expect(screen.getByText("Other Supplier")).toBeTruthy();
  expect(screen.getAllByText("—")).toHaveLength(2);
  const leafCalls = custom.mock.calls.filter(([request]) =>
    Object.keys(request.meta?.gqlVariables as Record<string, unknown>).some((name) => /^group_\d+_limit$/.test(name)));
  expect(leafCalls).toHaveLength(1);
  const leafDocument = JSON.stringify(leafCalls[0]![0].meta?.gqlQuery);
  expect(leafDocument).toContain("company");
  expect(leafDocument).toContain("partner");
  expect(leafDocument).toContain("display_name");
});

test("the root pager pages channel groups, so older channels remain reachable independently of record totals", async () => {
  const f = fixture({ pageSize: 2 });
  expect(await screen.findByRole("region", { name: "Signal" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Archived channel" })).toBeNull();
  expect(screen.getByRole("button", { name: "Groups 1-2 / 6 groups" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByRole("region", { name: "Archived channel" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Mail" })).toBeNull();
  expect(screen.getByRole("button", { name: "Groups 3-4 / 6 groups" })).toBeTruthy();
  expect(f.groupRequests().at(-1)).toMatchObject({ offset: 2, limit: 2 });
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByRole("region", { name: "No channel" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Groups 5-6 / 6 groups" })).toBeTruthy();
});

test("each channel pages its own records and links retain that leaf filter, order and page", async () => {
  const f = fixture();
  const mail = await screen.findByRole("region", { name: "Mail" });
  expect(await within(mail).findByRole("link", { name: "mail message 1" })).toBeTruthy();
  const signal = await screen.findByRole("region", { name: "Signal" });
  expect(await within(signal).findByText("signal message 1 needle")).toBeTruthy();
  fireEvent.click(within(mail).getByRole("button", { name: "Next Mail records" }));
  const secondPageCard = await within(mail).findByRole("link", { name: "mail message 21" });
  expect(within(mail).queryByText("mail message 1")).toBeNull();
  expect(within(signal).getByText("signal message 1 needle")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Groups 1-6 / 6 groups" })).toBeTruthy();
  const request = f.leafRequests().findLast((request) => request.offset === 20 &&
    terms(request.where).some((term) => term.channel?.id._eq === "mail"));
  expect(request).toMatchObject({ offset: 20, limit: 20,
    where: { channel: { id: { _eq: "mail" } } }, orderBy: { title: "desc" } });
  const navigation = f.rowHref.mock.calls.findLast(([row]) => row.id === "mail-21")?.[1];
  const scope = { filter: { channel: { exact: "mail" } }, order: { title: "DESC" }, page: 2, pageSize: 20 };
  expect(navigation).toMatchObject(scope);
  expect(secondPageCard.getAttribute("href")).toBe("/?record=mail-21");
  fireEvent.click(secondPageCard);
  expect(f.onListStateChange.mock.calls.at(-1)?.[0]).toMatchObject({
    page: 2, pageSize: 20, total: 67, navigationScope: scope,
    rows: expect.arrayContaining([expect.objectContaining({ id: "mail-21" })]),
  });
});

test("base and toolbar filters constrain both channel discovery and every channel's record request", async () => {
  const f = fixture({ baseFilter: { status: { exact: "active" } } });
  expect(await screen.findByRole("region", { name: "Mail" })).toBeTruthy();
  const previousLeafCount = f.leafRequests().length;
  act(() => f.view.setFilter({ title: { iContains: "needle" } }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "Mail" })).toBeNull());
  const signal = await screen.findByRole("region", { name: "Signal" });
  expect(await within(signal).findByText("signal message 1 needle")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Groups 1-1 / 1 groups" })).toBeTruthy();
  const rootTerms = terms(f.groupRequests().at(-1)?.where);
  expect(rootTerms).toEqual(expect.arrayContaining([
    expect.objectContaining({ status: { _eq: "active" } }), expect.objectContaining({ title: { _ilike: "%needle%" } }),
  ]));
  const filteredLeaves = f.leafRequests().slice(previousLeafCount);
  expect(filteredLeaves.length).toBeGreaterThan(0);
  for (const leaf of filteredLeaves) {
    expect(terms(leaf.where)).toEqual(expect.arrayContaining([
      expect.objectContaining({ status: { _eq: "active" } }), expect.objectContaining({ title: { _ilike: "%needle%" } }),
      expect.objectContaining({ channel: { id: { _eq: "signal" } } }),
    ]));
  }
});

test.each(["board", "list"] as const)("a grouped %s card or row publishes navigation using the declared public identity", async (viewKind) => {
  const f = fixture({ viewKind, customIdentity: true });
  const link = await screen.findByRole("link", { name: viewKind === "list" ? "Open mail message 1" : "mail message 1" });
  expect(link.getAttribute("href")).toBe("/?record=mail-1");
  fireEvent.click(link);
  expect(f.onListStateChange.mock.calls.at(-1)?.[0]).toMatchObject({
    total: 67, page: 1, pageSize: 20,
    navigationScope: { filter: { channel: { exact: "mail" } }, order: { title: "DESC" }, page: 1, pageSize: 20 },
    rows: expect.arrayContaining([expect.objectContaining({ public_key: "mail-1" })]),
  });
  expect(f.leafRequests().length).toBeGreaterThan(0);
  expect(f.leafRequests().every((request) => JSON.stringify(request.document).includes('"value":"public_key"'))).toBe(true);
});

test("nested board groups remain reachable and page records within both parent scopes", async () => {
  const f = fixture({ nested: true });
  const mail = await screen.findByRole("region", { name: "Mail" });
  const active = await within(mail).findByRole("region", { name: "active" });
  expect(within(active).queryByRole("article")).toBeNull();
  expect(f.leafRequests()).toHaveLength(0);
  fireEvent.click(within(active).getByRole("button", { name: "active" }));
  expect(await within(active).findByRole("link", { name: "mail message 1" })).toBeTruthy();
  fireEvent.click(within(active).getByRole("button", { name: "Next active records" }));
  expect(await within(active).findByRole("link", { name: "mail message 21" })).toBeTruthy();
  const request = f.leafRequests().findLast((request) => request.offset === 20 &&
    terms(request.where).some((term) => term.channel?.id._eq === "mail"));
  expect(request).toMatchObject({ offset: 20, limit: 20 });
  expect(terms(request?.where)).toEqual(expect.arrayContaining([
    expect.objectContaining({ channel: { id: { _eq: "mail" } } }), expect.objectContaining({ status: { _eq: "active" } }),
  ]));
  const signal = screen.getByRole("region", { name: "Signal" });
  expect(within(signal).queryByRole("article")).toBeNull();
});

test("a card action refreshes loaded records even when the server's channel buckets do not change", async () => {
  const f = fixture({ refreshAction: true });
  const mail = await screen.findByRole("region", { name: "Mail" });
  const link = await within(mail).findByRole("link", { name: "mail message 1" });
  const card = link.closest("article")!;
  const previousLeafCount = f.leafRequests().length;
  f.changeTitle("Updated message in the same channel");
  fireEvent.click(within(card).getByRole("button", { name: "Refresh cards" }));
  expect(await within(mail).findByRole("link", { name: "Updated message in the same channel" })).toBeTruthy();
  expect(within(mail).queryByText("mail message 1")).toBeNull();
  expect(f.leafRequests().length).toBeGreaterThan(previousLeafCount);
  expect(screen.getByRole("button", { name: "Groups 1-6 / 6 groups" })).toBeTruthy();
});

test.each(["root", "leaf"] as const)("a failed %s request is visible instead of presenting missing channels as an empty board", async (error) => {
  const f = fixture({ error });
  expect(await screen.findByText(error === "root" ? "Channel groups unavailable" : "Mail records unavailable")).toBeTruthy();
  expect(screen.queryByText("No messages")).toBeNull();
  if (error === "root") expect(f.leafRequests()).toHaveLength(0);
  else expect(await within(screen.getByRole("region", { name: "Signal" })).findByText("signal message 1 needle")).toBeTruthy();
});
