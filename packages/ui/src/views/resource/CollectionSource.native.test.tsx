// @vitest-environment happy-dom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  Filter,
  ResourceQuery,
} from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import {
  OperationDocumentsProvider,
  invalidateAuthoredQueries,
  type GroupByResult,
  type TypedDocumentNode,
} from "@angee/refine";
import { gql } from "graphql-tag";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";
import {
  collectionQuery,
  collectionBatchQuery,
  useCollectionQueryBatch,
  type CollectionGroupRequest,
  type CollectionPage,
  type CollectionPageRequest,
  type CollectionSource,
} from "./collection-source";
import {
  ResourceViewProvider,
  useResourceView,
  type ResourceViewContextValue,
} from "./resource-view-context";
import {
  useGroupedResourceViewSurface,
  type GroupedResourceViewSurface,
} from "./resource-view-surface";
import { ListView } from "./ListView";
import { CollectionTreeView } from "../tree/CollectionTreeView";
import { ToastProvider } from "../../feedback";

type ActivityRow = { id: string; title: string; parent?: string; expandable?: boolean };
type Variables = { request: CollectionPageRequest | CollectionGroupRequest };

function filterOperatorValue(
  filter: CollectionPageRequest["filter"],
  field: string,
  operator: string,
): unknown {
  const condition = filter?.[field];
  if (!condition || typeof condition !== "object" || Array.isArray(condition)) {
    return undefined;
  }
  return (condition as Readonly<Record<string, unknown>>)[operator];
}

const PageDocument: TypedDocumentNode<
  { page: CollectionPage<ActivityRow> },
  Variables
> = gql`
  query Page($request: JSON!) {
    page(request: $request) {
      rows {
        id
        title
      }
      total
    }
  }
`;
const GroupsDocument: TypedDocumentNode<{ groups: GroupByResult }, Variables> =
  gql`
    query Groups($request: JSON!) {
      groups(request: $request) {
        count
        totalCount
        buckets {
          key
          count
        }
      }
    }
  `;
const BatchDocument: TypedDocumentNode<
  { batch: { key: string; page: CollectionPage<ActivityRow> }[] },
  { requests: readonly { key: string; page: number; pageSize: number }[] }
> = gql`
  query Batch($requests: JSON!) {
    batch(requests: $requests) { key page { rows { id title } total } }
  }
`;
const contract = ResourceQuery.forRows({
  fields: {
    id: { scalar: "ID" },
    title: { scalar: "String" },
    account: { scalar: "String" },
  },
}).contract;
contract.axes.account = {
  ...contract.axes.account!,
  server: {
    input: "account",
    key: "account",
    labelInput: "label",
    labelKey: "label",
  },
  drill: {
    kind: "value",
    field: "account",
    valueKey: "account",
    nullMode: "isNull",
    valueMap: [],
  },
};
const source: CollectionSource<ActivityRow> = {
  query: ResourceQuery.fromContract(contract),
  rows: collectionQuery({
    document: PageDocument,
    variables: (request: CollectionPageRequest) => ({ request }),
    select: (data) => data.page,
  }),
  groups: collectionQuery({
    document: GroupsDocument,
    variables: (request: CollectionGroupRequest) => ({ request }),
    select: (data) => data.groups,
  }),
  leafPageSize: 3,
};
const { Provider, createClient, clearClients } = createUiTestProviders({
  apiUrl: "test://collection",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => {
  cleanup();
  clearClients();
});

test("authored collection batches independently keyed leaf pages into one request", async () => {
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const variables = meta?.gqlVariables as { requests: { key: string; page: number }[] };
    return { data: { batch: variables.requests.map(({ key, page }) => ({ key, page: {
      total: 20, rows: [{ id: key, title: `${key} page ${page}` }],
    } })) } };
  });
  const batched = collectionQuery({
    batch: collectionBatchQuery({
      document: BatchDocument,
      variables: (requests: readonly (CollectionPageRequest & { key: string })[]) => ({
        requests: requests.map(({ key, page, pageSize }) => ({ key, page, pageSize })),
      }),
      select: (data) => new Map(data.batch.map(({ key, page }) => [key, { data: page }])),
    }),
  });
  function Probe({ count }: { count: number }) {
    const requests = Array.from({ length: count }, (_, index) => ({
      key: `group-${index}`, filter: undefined, order: undefined, page: index + 1, pageSize: 3,
    }));
    const pages = useCollectionQueryBatch(batched, requests);
    return <output>{[...pages].map(([key, result]) => `${key}:${result.data?.rows[0]?.title ?? "loading"}`).join("|")}</output>;
  }
  const view = render(<Provider dataProvider={{ custom }} providerNames={[]}><Probe count={1} /></Provider>);
  await screen.findByText("group-0:group-0 page 1");
  view.rerender(<Provider dataProvider={{ custom }} providerNames={[]}><Probe count={5} /></Provider>);
  await screen.findByText(/group-4:group-4 page 5/);
  view.rerender(<Provider dataProvider={{ custom }} providerNames={[]}><Probe count={10} /></Provider>);
  await screen.findByText(/group-9:group-9 page 10/);
  expect(custom).toHaveBeenCalledTimes(3);
  expect(custom.mock.calls.map(([request]) => (request.meta?.gqlVariables as { requests: unknown[] }).requests.length)).toEqual([1, 4, 5]);
});

test("a failed transport retries its whole batch once under the client policy", async () => {
  let attempts = 0;
  const client = createClient({ defaultOptions: { queries: {
    retry: (failureCount, error) => failureCount < 1 && error.message === "offline",
    retryDelay: () => 0,
  } } });
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    if (++attempts === 1) throw new Error("offline");
    const variables = meta?.gqlVariables as { requests: { key: string }[] };
    return { data: { batch: variables.requests.map(({ key }) => ({ key, page: {
      total: 1, rows: [{ id: key, title: key }],
    } })) } };
  });
  const batched = collectionQuery({ batch: collectionBatchQuery({
    document: BatchDocument,
    variables: (requests: readonly (CollectionPageRequest & { key: string })[]) => ({
      requests: requests.map(({ key, page, pageSize }) => ({ key, page, pageSize })),
    }),
    select: (data) => new Map(data.batch.map(({ key, page }) => [key, { data: page }])),
  }) });
  function Probe() {
    const requests = ["first", "second"].map((key) => ({ key, filter: undefined, order: undefined, page: 1, pageSize: 3 }));
    const pages = useCollectionQueryBatch(batched, requests);
    return <output>{[...pages.values()].filter((page) => page.data).length}</output>;
  }
  render(<Provider dataProvider={{ custom }} providerNames={[]} queryClient={client}><Probe /></Provider>);
  await screen.findByText("2");
  expect(custom).toHaveBeenCalledTimes(2);
  expect(custom.mock.calls.map(([call]) => (call.meta?.gqlVariables as { requests: unknown[] }).requests.length)).toEqual([2, 2]);
});

test("a capped batch splits before sending pages to its transport", async () => {
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const variables = meta?.gqlVariables as { requests: { key: string }[] };
    return { data: { batch: variables.requests.map(({ key }) => ({ key, page: {
      total: 1, rows: [{ id: key, title: key }],
    } })) } };
  });
  const batched = collectionQuery({ batch: collectionBatchQuery({
    document: BatchDocument,
    maxPages: 100,
    variables: (requests: readonly (CollectionPageRequest & { key: string })[]) => ({
      requests: requests.map(({ key, page, pageSize }) => ({ key, page, pageSize })),
    }),
    select: (data) => new Map(data.batch.map(({ key, page }) => [key, { data: page }])),
  }) });
  function Probe() {
    const requests = Array.from({ length: 101 }, (_, index) => ({
      key: `page-${index}`, filter: undefined, order: undefined, page: 1, pageSize: 3,
    }));
    const pages = useCollectionQueryBatch(batched, requests);
    return <output>{[...pages.values()].filter((page) => page.data).length}</output>;
  }
  render(<Provider dataProvider={{ custom }} providerNames={[]}><Probe /></Provider>);
  await screen.findByText("101");
  expect(custom.mock.calls.map(([call]) => (call.meta?.gqlVariables as { requests: unknown[] }).requests.length)).toEqual([100, 1]);
});

test("a cached batch page takes its caption from the current render", async () => {
  const custom = vi.fn(async () => ({ data: { batch: [{ key: "first", page: {
    total: 1, rows: [{ id: "first", title: "First" }],
  } }] } }));
  function Probe({ caption }: { caption: string }) {
    const batched = collectionQuery({ batch: collectionBatchQuery({
      document: BatchDocument,
      variables: (requests: readonly (CollectionPageRequest & { key: string })[]) => ({
        requests: requests.map(({ key, page, pageSize }) => ({ key, page, pageSize })),
      }),
      select: (data) => new Map(data.batch.map(({ key, page }) => [key, { data: page }])),
      present: (page) => ({ ...page, summary: caption }),
    }) });
    const pages = useCollectionQueryBatch(batched, [
      { key: "first", filter: undefined, order: undefined, page: 1, pageSize: 3 },
    ]);
    return <output>{pages.get("first")?.data?.summary ?? "loading"}</output>;
  }
  const view = render(<Provider dataProvider={{ custom }} providerNames={[]}><Probe caption="One" /></Provider>);
  await screen.findByText("One");
  view.rerender(<Provider dataProvider={{ custom }} providerNames={[]}><Probe caption="Uno" /></Provider>);
  expect(screen.getByText("Uno")).toBeTruthy();
  expect(custom).toHaveBeenCalledTimes(1);
});

test("paging one batched group retains sibling rows and reads only the changed page", async () => {
  let release: (() => void) | undefined;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const variables = meta?.gqlVariables as { requests: { key: string; page: number }[] };
    if (variables.requests.some(({ page }) => page === 2)) await pending;
    return { data: { batch: variables.requests.map(({ key, page }) => ({ key, page: {
      total: 20, rows: [{ id: key, title: `${key} page ${page}` }],
    } })) } };
  });
  const batched = collectionQuery({
    batch: collectionBatchQuery({
      document: BatchDocument,
      variables: (requests: readonly (CollectionPageRequest & { key: string })[]) => ({
        requests: requests.map(({ key, page, pageSize }) => ({ key, page, pageSize })),
      }),
      select: (data) => new Map(data.batch.map(({ key, page }) => [key, { data: page }])),
    }),
  });
  function Probe({ secondPage }: { secondPage: number }) {
    const requests = ["first", "second"].map((key) => ({
      key, filter: undefined, order: undefined, page: key === "first" ? 1 : secondPage, pageSize: 3,
    }));
    const pages = useCollectionQueryBatch(batched, requests);
    return <output>{[...pages].map(([key, result]) => `${key}:${result.data?.rows[0]?.title ?? "loading"}`).join("|")}</output>;
  }
  const view = render(<Provider dataProvider={{ custom }} providerNames={[]}><Probe secondPage={1} /></Provider>);
  await screen.findByText("first:first page 1|second:second page 1");
  view.rerender(<Provider dataProvider={{ custom }} providerNames={[]}><Probe secondPage={2} /></Provider>);
  await waitFor(() => expect(custom).toHaveBeenCalledTimes(2));
  expect(screen.getByText("first:first page 1|second:second page 1")).toBeTruthy();
  expect((custom.mock.calls[1]![0].meta?.gqlVariables as { requests: { key: string }[] }).requests.map(({ key }) => key)).toEqual(["second"]);
  release?.();
  await screen.findByText("first:first page 1|second:second page 2");
});

test("rebuilt batch sources retain pages across route changes, collapse and live refresh", async () => {
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const resources = [testDataResource("messaging.Message")];
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const variables = meta?.gqlVariables as { requests: { key: string; page: number }[] };
    return { data: { batch: variables.requests.map(({ key, page }) => ({ key, page: {
      total: 20, rows: [{ id: key, title: `${key} page ${page}` }],
    } })) } };
  });
  function Probe({ second, route, secondPage = 1 }: { second: boolean; route: string; secondPage?: number }) {
    const source = collectionQuery({
      models: ["messaging.Message"],
      batch: collectionBatchQuery({
        document: BatchDocument,
        variables: (requests: readonly (CollectionPageRequest & { key: string })[]) => ({
          requests: requests.map(({ key, page, pageSize }) => ({ key, page, pageSize })),
        }),
        select: (data) => new Map(data.batch.map(({ key, page }) => [key, { data: page }])),
      }),
    });
    const requests = (second ? ["first", "second"] : ["first"]).map((key) => ({
      key, filter: undefined, order: undefined, page: key === "second" ? secondPage : 1, pageSize: 3,
    }));
    const pages = useCollectionQueryBatch(source, requests);
    return <output data-route={route}>{[...pages].map(([key, result]) =>
      `${key}:${result.data?.rows[0]?.title ?? "loading"}`).join("|")}</output>;
  }
  const view = render(<Provider dataProvider={{ custom }} providerNames={[]} queryClient={client} resources={resources}>
    <Probe second route="results" />
  </Provider>);
  await screen.findByText("first:first page 1|second:second page 1");
  expect(custom).toHaveBeenCalledTimes(1);
  expect((custom.mock.calls[0]![0].meta?.gqlVariables as { requests: unknown[] }).requests).toHaveLength(2);
  view.rerender(<Provider dataProvider={{ custom }} providerNames={[]} queryClient={client} resources={resources}>
    <Probe second route="links" />
  </Provider>);
  expect(screen.getByText("first:first page 1|second:second page 1")).toBeTruthy();
  expect(custom).toHaveBeenCalledTimes(1);
  view.rerender(<Provider dataProvider={{ custom }} providerNames={[]} queryClient={client} resources={resources}>
    <Probe second={false} route="links" />
  </Provider>);
  view.rerender(<Provider dataProvider={{ custom }} providerNames={[]} queryClient={client} resources={resources}>
    <Probe second route="links" />
  </Provider>);
  expect(screen.getByText("first:first page 1|second:second page 1")).toBeTruthy();
  expect(custom).toHaveBeenCalledTimes(1);
  view.rerender(<Provider dataProvider={{ custom }} providerNames={[]} queryClient={client} resources={resources}>
    <Probe second route="links" secondPage={2} />
  </Provider>);
  await screen.findByText("first:first page 1|second:second page 2");
  expect(custom).toHaveBeenCalledTimes(2);
  expect((custom.mock.calls[1]![0].meta?.gqlVariables as { requests: { key: string }[] }).requests.map(({ key }) => key)).toEqual(["second"]);
  await act(async () => { await invalidateAuthoredQueries(client, ["messaging.Message"]); });
  await waitFor(() => expect(custom).toHaveBeenCalledTimes(3));
  expect((custom.mock.calls[2]![0].meta?.gqlVariables as { requests: { key: string; page: number }[] }).requests)
    .toEqual([{ key: "first", page: 1, pageSize: 3 }, { key: "second", page: 2, pageSize: 3 }]);
  await act(async () => {
    await client.invalidateQueries({ predicate: (query) => {
      const variables = query.queryKey[5] as { requests?: { key: string }[] };
      return variables.requests?.[0]?.key === "second";
    } });
  });
  await waitFor(() => expect(custom).toHaveBeenCalledTimes(4));
  expect((custom.mock.calls[3]![0].meta?.gqlVariables as { requests: { key: string }[] }).requests.map(({ key }) => key)).toEqual(["second"]);
  expect(screen.getByText("first:first page 1|second:second page 2")).toBeTruthy();
});

test("a failed batch page sets only that key's error", async () => {
  const custom = vi.fn(async () => ({ data: { batch: [
    { key: "first", page: { total: 1, rows: [{ id: "first", title: "Visible" }] } },
    { key: "second", page: { total: 0, rows: [] } },
  ] } }));
  const batched = collectionQuery({
    batch: collectionBatchQuery({
      document: BatchDocument,
      variables: (requests: readonly (CollectionPageRequest & { key: string })[]) => ({
        requests: requests.map(({ key, page, pageSize }) => ({ key, page, pageSize })),
      }),
      select: (data) => new Map(data.batch.map(({ key, page }) => [key,
        key === "second" ? { error: new Error("Conversation unavailable.") } : { data: page },
      ])),
    }),
  });
  function Probe() {
    const requests = ["first", "second"].map((key) => ({ key, filter: undefined, order: undefined, page: 1, pageSize: 3 }));
    const pages = useCollectionQueryBatch(batched, requests);
    return <output>{[...pages].map(([key, result]) => `${key}:${result.data?.rows[0]?.title ?? result.error?.message ?? "loading"}`).join("|")}</output>;
  }
  render(<Provider dataProvider={{ custom }} providerNames={[]}><Probe /></Provider>);
  await screen.findByText("first:Visible|second:Conversation unavailable.");
});

function fixture(grouped: boolean, interactive = false, tree = false) {
  const preview = vi.fn();
  const open = vi.fn();
  let surface: GroupedResourceViewSurface<ActivityRow>;
  let view: ResourceViewContextValue;
  const requests: Variables["request"][] = [];
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const { request } = meta?.gqlVariables as Variables;
    requests.push(request);
    if ("group" in request) {
      return {
        data: {
          groups: {
            count: 60,
            totalCount: 27,
            buckets: [
              {
                key: {
                  account: `account-${request.page}`,
                  label: `Account ${request.page}`,
                },
                count: 60,
              },
            ],
          },
        },
      };
    }
    if (tree) return { data: { page: { total: 60, rows: [{ id: request.parentId ? `child-${request.page}` : "root", title: request.parentId ? `Child page ${request.page}` : "Root", parent: request.parentId, expandable: !request.parentId }] } } };
    const account =
      Filter.from(request.filter).facetValues("account")[0] ?? "all";
    return {
      data: {
        page: {
          total: 60,
          rows: [
            {
              id: `${account}-${request.page}`,
              title: `Activity ${account} page ${request.page}`,
            },
          ],
        },
      },
    };
  });
  const getList = vi.fn();
  function Surface() {
    view = useResourceView();
    surface = useGroupedResourceViewSurface({
      resource: "authored.Activity",
      source,
      columns: [{ field: "title" }],
      resourceView: view,
    });
    return null;
  }
  function List() {
    view = useResourceView();
    if (tree) return <CollectionTreeView resource="authored.Activity" source={source} rowKey="id" parent="parent" label="title" hasChildren="expandable" columns={[{ field: "title" }]} textFilterField="title" customFilterFields={[]} filterOptions={[]} />;
    return (
      <ListView
        resource="authored.Activity"
        source={source}
        columns={[
          {
            field: "title",
            interactive,
            ...(interactive
              ? {
                  render: (row: ActivityRow) => (
                    <button onClick={preview}>{row.title}</button>
                  ),
                }
              : {}),
          },
        ]}
        onRowClick={open}
        availableViews={["list"]}
        customFilterFields={[]}
        groupOptions={[]}
      />
    );
  }
  render(
    <Provider resources={[testDataResource("catalog.Reference")]} refineResources={[]}
      providerNames={[]} dataProvider={{ custom, getList }}>
      <OperationDocumentsProvider documents={{}}>
        <ToastProvider>
          <ResourceViewProvider
            scope="local"
            initialState={{
              pageSize: 25,
              ...(grouped ? { groupStack: [{ field: "account" }] } : {}),
            }}
          >
            {grouped ? <Surface /> : <List />}
          </ResourceViewProvider>
        </ToastProvider>
      </OperationDocumentsProvider>
    </Provider>,
  );
  return {
    get surface() {
      return surface!;
    },
    get view() {
      return view!;
    },
    requests,
    custom,
    getList,
    preview,
    open,
  };
}

test("an authored server page uses native list paging without a model-resource query", async () => {
  const f = fixture(false);
  await screen.findByText("Activity all page 1");
  act(() => f.view.setPage(2));
  await screen.findByText("Activity all page 2");
  expect(f.requests.at(-1)).toMatchObject({ page: 2, pageSize: 25 });
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  const search = await screen.findByRole("combobox", { name: "Filter records" });
  fireEvent.input(search, { target: { value: "document" }, inputType: "insertText" });
  fireEvent.click(await screen.findByRole("option", { name: "Search Title for: document" }));
  await waitFor(() =>
    expect(f.requests.at(-1)).toMatchObject({
      page: 1,
      filter: { title: { iContains: "document" } },
    }),
  );
  expect(f.getList).not.toHaveBeenCalled();
});

test("an authored source groups through the complete custom catalog without preset or visible-column declarations", async () => {
  const f = fixture(false);
  await screen.findByText("Activity all page 1");
  act(() => f.view.setFilter({ title: { iContains: "document" } }));
  await waitFor(() => expect(f.requests.at(-1)).toMatchObject({
    filter: { title: { iContains: "document" } },
  }));

  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  expect(screen.queryByRole("button", { name: "Account" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "More axes…" }));
  const field = screen.getByRole("combobox", { name: "Group axis" });
  expect(field.textContent).toContain("Account");
  fireEvent.click(field);
  expect(await screen.findByRole("option", { name: "Account" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "Title" })).toBeNull();
  fireEvent.click(field);
  fireEvent.click(screen.getByRole("button", { name: "Add level" }));

  await waitFor(() => expect(f.requests.some((request) =>
    "group" in request
    && request.group.field === "account"
    && filterOperatorValue(request.filter, "title", "iContains") === "document",
  )).toBe(true));
  await waitFor(() => expect(f.requests.some((request) =>
    !("group" in request)
    && filterOperatorValue(request.filter, "title", "iContains") === "document"
    && Filter.from(request.filter).facetValues("account")[0] === "account-1",
  )).toBe(true));
  expect(f.getList).not.toHaveBeenCalled();
});

test("an authored source keeps its complete filter catalog when shortcut inference is disabled", async () => {
  fixture(false);
  await screen.findByText("Activity all page 1");
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  fireEvent.click(screen.getByRole("button", { name: "Add custom filter" }));
  const field = screen.getByLabelText("Filter field");
  fireEvent.click(field);
  expect(await screen.findByRole("option", { name: "Title" })).toBeTruthy();
  expect(screen.getByRole("option", { name: "Account" })).toBeTruthy();
});

test("authored groups page headers and expanded members independently through semantic scopes", async () => {
  const f = fixture(true);
  await waitFor(() =>
    expect(f.surface.groupedItems.some((item) => item.kind === "record")).toBe(
      true,
    ),
  );
  const header = f.surface.groupedItems.find(
    (item) => item.kind === "groupHeader",
  );
  expect(header).toMatchObject({
    label: "Account 1",
    count: 60,
    pager: { pageSize: 3 },
  });
  expect(f.surface.list.total).toBe(27);
  if (!header?.pager) throw new Error("Missing expanded group pager");
  act(() => f.surface.setScopePage(header.pager!.pageKey, 2));
  await waitFor(() =>
    expect(f.requests.at(-1)).toMatchObject({
      page: 2,
      pageSize: 3,
      filter: { account: { exact: "account-1" } },
    }),
  );
  act(() => f.view.setPage(2));
  await waitFor(() =>
    expect(
      f.surface.groupedItems.some(
        (item) => item.kind === "groupHeader" && item.label === "Account 2",
      ),
    ).toBe(true),
  );
  expect(
    f.requests.some(
      (request) =>
        "group" in request && request.page === 2 && request.pageSize === 25,
    ),
  ).toBe(true);
  expect(f.getList).not.toHaveBeenCalled();
});

test("interactive cells retain their own actions and the row remains keyboard accessible", async () => {
  const f = fixture(false, true);
  const action = await screen.findByRole("button", {
    name: "Activity all page 1",
  });
  expect(action.parentElement?.closest("button")).toBeNull();
  fireEvent.click(action);
  expect(f.preview).toHaveBeenCalledOnce();
  expect(f.open).not.toHaveBeenCalled();
  const row = action.closest("tr");
  expect(row?.getAttribute("tabindex")).toBe("0");
  fireEvent.keyDown(row!, { key: "Enter" });
  expect(f.open).toHaveBeenCalledOnce();
});


test("tree branches page independently, clamp after shrinking, and re-expand after filter changes", async () => {
  const f = fixture(false, false, true);
  const root = await screen.findByText("Root");
  fireEvent.click(root.closest('[role="treeitem"]')!.querySelector('button')!);
  await screen.findByText("Child page 1");
  act(() => f.view.setPaginationByScope({ root: { pageIndex: 1, pageSize: 25 } }));
  await screen.findByText("Child page 2");
  expect(f.view.state.pagination.pageIndex).toBe(0);
  act(() => f.view.setPaginationByScope({ root: { pageIndex: 99, pageSize: 25 } }));
  await screen.findByText("Child page 3");
  expect(f.view.paginationByScope.root?.pageIndex).toBe(2);
  act(() => f.view.setFilter({ title: { iContains: "new" } }));
  await waitFor(() => expect(screen.queryByText("Child page 3")).toBeNull());
  const refreshed = await screen.findByText("Root");
  fireEvent.click(refreshed.closest('[role="treeitem"]')!.querySelector('button')!);
  await screen.findByText("Child page 1");
  expect(f.requests.at(-1)).toMatchObject({ parentId: "root", page: 1, filter: { title: { iContains: "new" } } });
});


test("a failed authored page retries the same native scope", async () => {
  const f = fixture(false);
  await screen.findByText("Activity all page 1");
  f.custom.mockRejectedValueOnce(new Error("Temporary collection failure"));
  act(() => f.view.setPage(2));
  await screen.findByText("Temporary collection failure");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText("Activity all page 2");
  expect(f.requests.at(-1)).toMatchObject({ page: 2, pageSize: 25 });
  expect(f.getList).not.toHaveBeenCalled();
});

test("a revealed orphan remains reachable after its parent page resolves and an empty branch becomes a leaf", async () => {
  const revealed: ActivityRow = { id: "folder", title: "Deep-linked folder", parent: "unloaded-parent", expandable: true };
  let resolveParent!: (result: { data: { page: CollectionPage<ActivityRow> } }) => void;
  const parentPage = new Promise<{ data: { page: CollectionPage<ActivityRow> } }>((resolve) => { resolveParent = resolve; });
  const requests: CollectionPageRequest[] = [];
  const custom = vi.fn(({ meta }: { meta?: Record<string, unknown> }) => {
    const { request } = meta?.gqlVariables as { request: CollectionPageRequest };
    requests.push(request);
    return request.parentId === "unloaded-parent" ? parentPage
      : Promise.resolve({ data: { page: { rows: [], total: 0 } } });
  });
  render(<Provider resources={[testDataResource("catalog.Reference")]} refineResources={[]} providerNames={[]}
    dataProvider={{ custom, getList: vi.fn() }}>
    <OperationDocumentsProvider documents={{}}><ToastProvider><ResourceViewProvider scope="local" initialState={{ pageSize: 25 }}>
      <CollectionTreeView resource="authored.Activity" source={source} rowKey="id" parent="parent" label="title" hasChildren="expandable"
        columns={[{ field: "title" }]} textFilterField="title" customFilterFields={[]} filterOptions={[]}
        selectedId="folder" revealRows={[revealed]} />
    </ResourceViewProvider></ToastProvider></OperationDocumentsProvider>
  </Provider>);
  await screen.findByText("Deep-linked folder");
  await act(async () => resolveParent({ data: { page: { rows: [revealed], total: 1 } } }));
  const row = screen.getByText("Deep-linked folder").closest('[role="treeitem"]')!;
  expect(row.getAttribute("aria-level")).toBe("1");
  fireEvent.click(row.querySelector("button")!);
  await waitFor(() => expect(requests.some((request) => request.parentId === "folder")).toBe(true));
  await waitFor(() => expect(row.hasAttribute("aria-expanded")).toBe(false));
  expect(screen.getByText("Deep-linked folder")).toBeTruthy();
  expect(revealed.parent).toBe("unloaded-parent");
});
