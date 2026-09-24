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
  type GroupByResult,
  type TypedDocumentNode,
} from "@angee/refine";
import { gql } from "graphql-tag";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";
import {
  collectionQuery,
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
const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://collection",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => {
  cleanup();
  clearClients();
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
        textFilterField="title"
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
  act(() => f.view.setFilter({ title: { iContains: "document" } }));
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

  fireEvent.click(screen.getByLabelText("Filter and group"));
  expect(screen.queryByRole("button", { name: "Account" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
  const field = screen.getByLabelText("Group field");
  expect(field.textContent).toContain("Account");
  fireEvent.click(field);
  expect(await screen.findByRole("option", { name: "Account" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "Title" })).toBeNull();
  fireEvent.click(field);
  fireEvent.click(screen.getByRole("button", { name: "Add" }));

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
  fireEvent.click(screen.getByLabelText("Filter and group"));
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
