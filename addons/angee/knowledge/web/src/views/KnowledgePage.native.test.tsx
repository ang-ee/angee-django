// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { ResourceQuery } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { PrimaryPaneTestHost, ShellPageTestProviders } from "@angee/app/testing";
import { createUiTestProviders } from "@angee/ui/testing";
import { createRouteHref, defaultWidgets, ModalsHost, ToastProvider } from "@angee/ui";
import { KnowledgePage } from "./KnowledgePage";
import { KnowledgePage as PageDocument, KnowledgePages, KnowledgeVault, KnowledgeVaults, type KnowledgePageDetail } from "../data/documents";

const pageQuery = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, title: { scalar: "String" }, vault: { scalar: "ID" }, parent: { scalar: "ID" },
  is_trashed: { scalar: "Boolean" }, navigation_root: { scalar: "Boolean" }, removed_root: { scalar: "Boolean" },
} });
const vaultQuery = ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, name: { scalar: "String" }, is_trashed: { scalar: "Boolean" } } });
const { Provider, clearClients } = createUiTestProviders({ resources: [
  testDataResource("knowledge.Page", { query: pageQuery.contract, fields: [
    { name: "title", kind: "scalar", scalar: "String", readable: true, updatable: true,
      creatable: true, requiredOnCreate: true, aggregatable: false },
  ] }),
  testDataResource("knowledge.MarkdownPage"),
  testDataResource("knowledge.Vault", { query: vaultQuery.contract }),
], queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } } });
const href = createRouteHref([{ name: "knowledge.home", path: "/knowledge" }, { name: "knowledge.page", path: "/knowledge/$id" }, { name: "knowledge.settings", path: "/knowledge/settings" }]);
const vaults = [{ id: "vlt_a", name: "Reference", permissions: ["write"], is_trashed: false },
  { id: "vlt_b", name: "Research", permissions: ["write"], is_trashed: false }];
function page(id: string, title: string, vault = "vlt_a"): KnowledgePageDetail {
  return { id, title, vault, kind: "note", icon: "", vault_label: vault === "vlt_a" ? "Reference" : "Research",
    permissions: ["write"], parent: null, is_trashed: false, has_children: false, ancestors: [], wikilinks: [], backlinks: [],
    markdown: null, updated_at: "2026-10-20", created_by_label: "Author" };
}
const rows = [page("pg_a", "Alpha"), page("pg_b", "Beta"), page("pg_c", "Gamma", "vlt_b")].map((record) => ({ ...record, navigation_root: true }));
rows.push({ ...page("pg_folder", "Projects"), kind: "folder", has_children: true, navigation_root: true });
rows.push({ ...page("pg_leaf", "Nested"), parent: "pg_folder", navigation_root: false });
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((release) => { resolve = release; });
  return { promise, resolve };
}
afterEach(() => { cleanup(); clearClients(); });

function fixture(initial = "/knowledge", pending: Record<string, ReturnType<typeof deferred>> = {}) {
  const custom = vi.fn<NonNullable<RefineTestDataProvider["custom"]>>(async ({ meta }) => {
    const variables = meta?.gqlVariables ?? {};
    if (meta?.gqlQuery === PageDocument) {
      await pending[String(variables.id)]?.promise;
      return { data: { pages_by_pk: rows.find((row) => row.id === variables.id) ?? null } };
    }
    if (meta?.gqlQuery === KnowledgeVault) return { data: { vaults_by_pk: vaults.find((vault) => vault.id === variables.id) ?? null } };
    if (meta?.gqlQuery === KnowledgeVaults) return { data: { vaults: vaults.filter((vault) => vaultQuery.matches(vault, vaultQuery.fromWhere(variables.where))) } };
    if (meta?.gqlQuery === KnowledgePages) {
      const pages = rows.filter((row) => pageQuery.matches(row, pageQuery.fromWhere(variables.where)));
      return { data: { pages, pages_aggregate: { aggregate: { count: pages.length } } } };
    }
    throw new Error("Unexpected query");
  });
  const root = createRootRoute({ component: () => <ShellPageTestProviders runtime={{ routeHref: href, widgets: defaultWidgets }}>
    <Outlet /><PrimaryPaneTestHost />
  </ShellPageTestProviders> });
  const home = createRoute({ getParentRoute: () => root, path: "/knowledge", component: KnowledgePage });
  const detail = createRoute({ getParentRoute: () => home, path: "$id" });
  const router = createRouter({ routeTree: root.addChildren([home.addChildren([detail])]),
    history: createMemoryHistory({ initialEntries: [initial] }),
  });
  render(<Provider dataProvider={{ custom, getList: async () => ({ data: [], total: 0 }) }}>
    <ToastProvider><ModalsHost><RouterProvider router={router} /></ModalsHost></ToastProvider>
  </Provider>);
  return { router, custom };
}

test("native navigation keeps the same-root tree and search while the next record is pending", async () => {
  const beta = deferred();
  const f = fixture("/knowledge/pg_a", { pg_b: beta });
  await screen.findByDisplayValue("Alpha");
  const picker = screen.getByRole("combobox", { name: "Vault: Reference" });
  await screen.findByRole("tree");
  const input = screen.getByRole("combobox", { name: "Filter records" });
  fireEvent.input(input, { target: { value: "Beta" }, inputType: "insertText" });
  fireEvent.click(await screen.findByRole("option", { name: "Search Page title for: Beta" }));
  const betaRow = await screen.findByText("Beta");
  const tree = screen.getByRole("tree");
  fireEvent.click(betaRow);
  await waitFor(() => expect(f.router.state.location.pathname).toBe("/knowledge/pg_b"));
  expect(screen.queryByDisplayValue("Alpha")).toBeNull();
  expect(screen.getByRole("tree")).toBe(tree);
  expect(screen.getByRole("combobox", { name: "Filter records" })).toBe(input);
  expect(screen.getByRole("combobox", { name: "Vault: Reference" })).toBe(picker);
  expect(screen.queryByRole("textbox", { name: "Page title" })).toBeNull();
  await act(async () => beta.resolve());
  await screen.findByDisplayValue("Beta");
  expect(screen.getByRole("tree")).toBe(tree);
  expect(screen.getByRole("combobox", { name: "Filter records" })).toBe(input);
  await act(async () => f.router.history.back());
  await screen.findByDisplayValue("Alpha");
  expect(screen.getByRole("tree")).toBe(tree);
  await act(async () => f.router.history.forward());
  await screen.findByDisplayValue("Beta");
  expect(screen.getByRole("tree")).toBe(tree);
});

test("native cross-root reads win earlier picker choices and late abandoned reads cannot move the current scope", async () => {
  const beta = deferred();
  const gamma = deferred();
  const f = fixture("/knowledge", { pg_b: beta, pg_c: gamma });
  const picker = await screen.findByRole("combobox", { name: "Vault: Reference" });
  fireEvent.click(picker);
  fireEvent.click(await screen.findByRole("option", { name: "Reference" }));
  const input = screen.getByRole("combobox", { name: "Filter records" });
  await act(async () => f.router.navigate({ to: "/knowledge/pg_b" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({ meta: expect.objectContaining({ gqlQuery: PageDocument, gqlVariables: { id: "pg_b" } }) })));
  await act(async () => f.router.navigate({ to: "/knowledge/pg_c" }));
  expect(screen.getByRole("combobox", { name: "Vault: Reference" })).toBe(picker);
  await act(async () => gamma.resolve());
  await screen.findByDisplayValue("Gamma");
  const researchPicker = await screen.findByRole("combobox", { name: "Vault: Research" });
  expect(screen.getByRole("combobox", { name: "Filter records" })).not.toBe(input);
  const researchTree = await screen.findByRole("tree");
  await act(async () => beta.resolve());
  expect(screen.getByRole("combobox", { name: "Vault: Research" })).toBe(researchPicker);
  expect(screen.getByRole("tree")).toBe(researchTree);
  expect(screen.queryByDisplayValue("Beta")).toBeNull();
  await act(async () => f.router.history.back());
  await screen.findByDisplayValue("Beta");
  await screen.findByRole("combobox", { name: "Vault: Reference" });
  await act(async () => f.router.history.forward());
  await screen.findByDisplayValue("Gamma");
  await screen.findByRole("combobox", { name: "Vault: Research" });
});

test("a pending direct link never initializes a different vault from the picker catalogue", async () => {
  const gamma = deferred();
  const f = fixture("/knowledge/pg_c", { pg_c: gamma });
  await waitFor(() => expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({ meta: expect.objectContaining({ gqlQuery: KnowledgeVaults }) })));
  expect(screen.queryByRole("combobox", { name: "Vault: Reference" })).toBeNull();
  expect(f.custom.mock.calls.some(([call]) => call.meta?.gqlQuery === KnowledgePages)).toBe(false);
  await act(async () => gamma.resolve());
  await screen.findByDisplayValue("Gamma");
  await screen.findByRole("combobox", { name: "Vault: Research" });
});


test("an expanded native branch survives a same-root deferred record read", async () => {
  const beta = deferred();
  const f = fixture("/knowledge/pg_a", { pg_b: beta });
  const branch = (await screen.findByText("Projects")).closest<HTMLElement>('[role="treeitem"]')!;
  fireEvent.click(within(branch).getByRole("button", { name: "Expand" }));
  await screen.findByText("Nested");
  const tree = screen.getByRole("tree");
  await act(async () => f.router.navigate({ to: "/knowledge/pg_b" }));
  expect(screen.getByRole("tree")).toBe(tree);
  expect(screen.getByText("Nested")).toBeTruthy();
  expect(branch.getAttribute("aria-expanded")).toBe("true");
  await act(async () => beta.resolve());
  await screen.findByDisplayValue("Beta");
  expect(screen.getByRole("tree")).toBe(tree);
  expect(screen.getByText("Nested")).toBeTruthy();
});
