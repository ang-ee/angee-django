// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { Filter, ResourceQuery, type QueryFilter } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { PrimaryPaneTestHost, ShellPageTestProviders } from "@angee/app/testing";
import { createUiTestProviders } from "@angee/ui/testing";
import { createRouteHref, ModalsHost, ToastProvider } from "@angee/ui";

const route = vi.hoisted(() => ({ id: null as string | null, navigate: vi.fn(), search: {}, preview: { viewAs: null as string | null, pending: false } }));
vi.mock("@tanstack/react-router", async (original) => ({
  ...(await original<typeof import("@tanstack/react-router")>()), useNavigate: () => route.navigate,
}));
vi.mock("@angee/ui", async (original) => ({
  ...(await original<typeof import("@angee/ui")>()), useRouteRecordId: () => route.id,
  useRouteSearch: () => route.search, useRuntimeViewAs: () => route.preview,
}));
// The independent native form is exercised in KnowledgePageView.test.tsx.
vi.mock("../KnowledgePageView", () => ({ KnowledgePageView: ({ pageId }: { pageId: string }) => <output data-testid="detail">{pageId}</output> }));
import { KnowledgePage } from "./KnowledgePage";
import { KnowledgeCreatePage, KnowledgePage as PageDocument, KnowledgePages, KnowledgeVault, KnowledgeVaults, type KnowledgePageDetail } from "../data/documents";

const pageQuery = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, title: { scalar: "String" }, vault: { scalar: "ID" }, parent: { scalar: "ID" },
  is_trashed: { scalar: "Boolean" }, navigation_root: { scalar: "Boolean" }, removed_root: { scalar: "Boolean" },
} });
const vaultQuery = ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, name: { scalar: "String" }, is_trashed: { scalar: "Boolean" } } });
const { Provider, clearClients } = createUiTestProviders({ resources: [
  testDataResource("knowledge.Page", { query: pageQuery.contract }),
  testDataResource("knowledge.Vault", { query: vaultQuery.contract }),
], queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } } });
const href = createRouteHref([{ name: "knowledge.home", path: "/knowledge" }, { name: "knowledge.page", path: "/knowledge/$id" }, { name: "knowledge.settings", path: "/knowledge/settings" }]);
const folder: KnowledgePageDetail = {
  id: "pg_parent", title: "Projects", kind: "folder", icon: "", vault: "vlt_1", vault_label: "Reference",
  permissions: ["write", "delete"], parent: null, is_trashed: false, has_children: true, ancestors: [], wikilinks: [], backlinks: [],
  markdown: null, updated_at: "2026-10-20", created_by_label: "Author",
};
beforeEach(() => { route.id = null; route.navigate.mockReset(); route.preview = { viewAs: null, pending: false }; });
afterEach(() => { cleanup(); clearClients(); });

function fixture(options: {
  detail?: KnowledgePageDetail; noVaults?: boolean; writable?: boolean; scope?: QueryFilter;
  beforeVaultSearch?: () => Promise<void>;
  beforePageRead?: (id: string) => Promise<void>;
} = {}) {
  const vaults = options.noVaults ? [] : [{ id: "vlt_1", name: "Reference", permissions: options.writable === false ? [] : ["write"], is_trashed: false }];
  const rows = [{ ...folder, navigation_root: true }, { ...folder, id: "pg_child", title: "Child", kind: "note", parent: folder.id, has_children: false, navigation_root: false }];
  const create = vi.fn(async () => ({ create_page: { id: "pg_created" } }));
  const custom = vi.fn<NonNullable<RefineTestDataProvider["custom"]>>(async ({ meta }) => {
    const variables = meta?.gqlVariables ?? {};
    if (meta?.gqlMutation === KnowledgeCreatePage) return { data: await create() };
    if (meta?.gqlQuery === PageDocument) {
      await options.beforePageRead?.(String(variables.id));
      return { data: { pages_by_pk: options.detail ?? rows.find((row) => row.id === variables.id) ?? null } };
    }
    if (meta?.gqlQuery === KnowledgeVault) return { data: { vaults_by_pk: vaults.find((vault) => vault.id === variables.id) ?? null } };
    if (meta?.gqlQuery === KnowledgeVaults) {
      await options.beforeVaultSearch?.();
      return { data: { vaults: vaults.filter((vault) => vaultQuery.matches(vault, vaultQuery.fromWhere(variables.where))) } };
    }
    if (meta?.gqlQuery === KnowledgePages) {
      const filtered = rows.filter((row) => pageQuery.matches(row, pageQuery.fromWhere(variables.where)));
      return { data: { pages: filtered, pages_aggregate: { aggregate: { count: filtered.length } } } };
    }
    throw new Error("Unexpected query");
  });
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/knowledge"] }) });
  const view = render(<KnowledgePage />, { wrapper: ({ children }: { children: ReactNode }) => <Provider dataProvider={{ custom, getList: async () => ({ data: [], total: 0 }) }}>
    <RouterContextProvider router={router}><ToastProvider><ModalsHost><ShellPageTestProviders runtime={{ routeHref: href,
      defaultResourceFilter: options.scope ? { resource: "knowledge.Page", filter: options.scope } : undefined,
    }}>{children}<PrimaryPaneTestHost /></ShellPageTestProviders></ModalsHost></ToastProvider></RouterContextProvider>
  </Provider> });
  return { create, custom, rerender: () => view.rerender(<KnowledgePage />) };
}

test("the existing New menu creates a subpage under the open page and keeps a failed draft", async () => {
  route.id = folder.id;
  const f = fixture({ detail: folder });
  await screen.findByText("Projects");
  fireEvent.click(await screen.findByRole("button", { name: "New" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Subpage" }));
  const input = await screen.findByRole("textbox", { name: "New page title" });
  fireEvent.change(input, { target: { value: "My child" } });
  f.create.mockRejectedValueOnce(new Error("Offline"));
  fireEvent.submit(screen.getByRole("form", { name: "New page title" }));
  await screen.findByText("Offline");
  expect(screen.getByDisplayValue("My child")).toBeTruthy();
  expect(route.navigate).not.toHaveBeenCalled();
  fireEvent.submit(screen.getByRole("form", { name: "New page title" }));
  await waitFor(() => expect(route.navigate).toHaveBeenCalledWith(expect.objectContaining({ to: "/knowledge/pg_created" })));
  expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({ meta: expect.objectContaining({ gqlMutation: KnowledgeCreatePage,
    gqlVariables: { vault: "vlt_1", parent: folder.id, kind: "note", title: "My child" } }) }));
  expect(screen.queryByText("New page here")).toBeNull();
});

test("a direct page still opens without access to any vault", async () => {
  route.id = "pg_child";
  fixture({ noVaults: true, detail: { ...folder, id: route.id } });
  expect((await screen.findByTestId("detail")).textContent).toBe("pg_child");
  expect(screen.queryByRole("button", { name: "New" })).toBeNull();
});

test("a directly opened page owns the vault after a prior picker selection", async () => {
  const f = fixture({ detail: { ...folder, vault: "vlt_2" } });
  fireEvent.click(await screen.findByRole("combobox", { name: "Vault: Reference" }));
  fireEvent.click(await screen.findByRole("option", { name: "Reference" }));
  route.id = folder.id;
  f.rerender();
  await waitFor(() => expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({
    meta: expect.objectContaining({ gqlQuery: KnowledgeVault, gqlVariables: { id: "vlt_2" } }),
  })));
});

test.each([null, "pg_parent"])("opening a page preserves the vault, navigator and search while it loads (from %s)", async (initialPage) => {
  route.id = initialPage;
  let resolvePage!: () => void;
  const pending = new Promise<void>((resolve) => { resolvePage = resolve; });
  const pageRead = vi.fn(async (id: string) => { if (id === "pg_child") await pending; });
  const f = fixture({ beforePageRead: pageRead });
  const picker = await screen.findByRole("combobox", { name: "Vault: Reference" });
  await screen.findByText("Projects");
  const input = screen.getByRole("combobox", { name: "Filter records" });
  fireEvent.input(input, { target: { value: "Child" }, inputType: "insertText" });
  fireEvent.click(await screen.findByRole("option", { name: "Search Page title for: Child" }));
  fireEvent.click(await screen.findByText("Child"));
  await waitFor(() => expect(route.navigate).toHaveBeenCalledWith(expect.objectContaining({ to: "/knowledge/pg_child" })));
  const tree = screen.getByRole("tree");

  route.id = "pg_child";
  f.rerender();
  await waitFor(() => expect(pageRead).toHaveBeenCalledWith("pg_child"));
  expect(screen.getByRole("combobox", { name: "Vault: Reference" })).toBe(picker);
  expect(screen.getByRole("combobox", { name: "Filter records" })).toBe(input);
  expect(screen.getByRole("tree")).toBe(tree);
  expect(screen.getByText("Child")).toBeTruthy();
  expect(screen.queryByText("Projects")).toBeNull();

  resolvePage();
  await waitFor(() => expect(screen.getByRole("treeitem", { selected: true }).textContent).toContain("Child"));
  expect(screen.getByRole("combobox", { name: "Vault: Reference" })).toBe(picker);
  expect(screen.getByRole("combobox", { name: "Filter records" })).toBe(input);
  expect(screen.getByRole("tree")).toBe(tree);
  expect(screen.queryByText("Projects")).toBeNull();
});

test("vault search keeps the initial vault and a clearable picker through pending and empty results", async () => {
  let resolvePending!: () => void;
  const pending = new Promise<void>((resolve) => { resolvePending = resolve; });
  let searching = false;
  const searchRead = vi.fn(async () => { if (searching) await pending; });
  const f = fixture({ beforeVaultSearch: searchRead });
  await screen.findByText("Projects");
  fireEvent.click(await screen.findByRole("combobox", { name: "Vault: Reference" }));
  const input = await screen.findByPlaceholderText("Search vaults…");
  searching = true;
  searchRead.mockClear();
  fireEvent.change(input, { target: { value: "Missing" } });
  await waitFor(() => expect(searchRead).toHaveBeenCalledOnce());
  expect(screen.getByPlaceholderText("Search vaults…")).toBe(input);
  expect(screen.getByRole("combobox", { name: "Vault: Reference" })).toBeTruthy();
  expect(screen.getByText("Projects")).toBeTruthy();

  resolvePending();
  await waitFor(() => expect(screen.queryByText("Searching…")).toBeNull());
  expect(screen.getByPlaceholderText("Search vaults…")).toBe(input);
  expect(screen.getByRole("combobox", { name: "Vault: Reference" })).toBeTruthy();
  expect(screen.getByText("Projects")).toBeTruthy();
  fireEvent.change(input, { target: { value: "" } });
  expect(await screen.findByRole("option", { name: "Reference" })).toBeTruthy();
  expect(route.navigate).not.toHaveBeenCalled();
  const requests = f.custom.mock.calls.filter(([call]) => call.meta?.gqlQuery === KnowledgePages);
  expect(requests.length).toBeGreaterThan(0);
  expect(requests.every(([call]) => Filter.from(pageQuery.fromWhere(call.meta?.gqlVariables?.where))
    .conjunctions().some(({ field, operator, value }) => field === "vault" && operator === "exact" && value === "vlt_1"))).toBe(true);
});

test("server search finds descendants while the unfiltered navigator requests only roots", async () => {
  const f = fixture();
  await screen.findByText("Projects");
  expect(screen.queryByText("Child")).toBeNull();
  const input = screen.getByRole("combobox", { name: "Filter records" });
  fireEvent.input(input, { target: { value: "Child" }, inputType: "insertText" });
  fireEvent.click(await screen.findByRole("option", { name: "Search Page title for: Child" }));
  expect(await screen.findByText("Child")).toBeTruthy();
  const requests = f.custom.mock.calls.filter(([call]) => call.meta?.gqlQuery === KnowledgePages).map(([call]) => call.meta?.gqlVariables);
  expect(requests[0]).toMatchObject({ limit: 50, offset: 0 });
  expect(Filter.from(pageQuery.fromWhere(requests[0]?.where)).conjunctions()).toEqual(expect.arrayContaining([
    { field: "navigation_root", operator: "exact", value: true }, { field: "is_trashed", operator: "exact", value: false },
    { field: "vault", operator: "exact", value: "vlt_1" },
  ]));
  const filters = Filter.from(pageQuery.fromWhere(requests.at(-1)?.where)).conjunctions();
  expect(filters).toEqual(expect.arrayContaining([{ field: "title", operator: "iLike", value: "%Child%" }]));
  expect(filters.some(({ field }) => field === "navigation_root")).toBe(false);
});

test.each([false, true])("write and view-as restrictions disable the native New menu (%s)", async (preview) => {
  if (preview) route.preview = { viewAs: "reader", pending: false };
  fixture({ writable: preview });
  expect((await screen.findByRole("button", { name: "New" })).hasAttribute("disabled")).toBe(true);
});

test("an invalid mounted filter fails closed through the collection owner", async () => {
  const f = fixture({ scope: { unknown: { exact: "x" } } });
  expect((await screen.findByRole("alert")).textContent).toContain("unknown");
  expect(f.custom.mock.calls.some(([call]) => call.meta?.gqlQuery === KnowledgePages)).toBe(false);
});

test("the navigator publishes native tree and vault controls in the primary pane", async () => {
  fixture();
  const pane = within(screen.getByTestId("shell-primary"));
  await pane.findByText("Projects");
  expect(pane.getByRole("tree")).toBeTruthy();
  expect(pane.getByRole("combobox", { name: "Vault: Reference" })).toBeTruthy();
});
