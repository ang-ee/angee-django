// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { createUiTestProviders } from "@angee/ui/testing";
import { testDataResource } from "@angee/metadata/testing";
import { useAuthoredQuery } from "@angee/refine";
import type { DashboardSnapshot } from "@angee/ui/dashboard/headless";
import { dashboardStore } from "./store";
import { DashboardDocument, DashboardSummariesDocument, SaveDashboardDocument } from "./documents.console";

const snapshot: DashboardSnapshot = { schemaVersion: 1, columns: 12, widgets: [] };
const payload = (revision: number) => ({ status: "ready", id: "db_1", revision, name: "Overview",
  description: "", snapshot, declaration_revision: "decl-1", current_revision: null, message: null,
  can_edit: true, can_reset: true, can_trash: true });
const { Provider, createClient, clearClients } = createUiTestProviders({ resources: [
  testDataResource("dashboards.Dashboard"), testDataResource("dashboards.DashboardWidget"),
], queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } } });
let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => { root.unmount(); });
  host.remove(); clearClients(); vi.unstubAllGlobals();
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((release) => { resolve = release; });
  return { promise, resolve };
}

test.each([false, true])("dashboard accepts its snapshot before derived reads, and a refusal changes nothing (refused=%s)", async (refused) => {
  const held = deferred();
  let summaryReads = 0;
  const custom = vi.fn<NonNullable<RefineTestDataProvider["custom"]>>(async ({ meta }) => {
    if (meta?.gqlQuery === DashboardDocument) return { data: { dashboard: payload(1) } };
    if (meta?.gqlQuery === DashboardSummariesDocument) {
      if (++summaryReads > 1) await held.promise;
      return { data: { dashboard_summaries: { status: "ready", version: "v1", next_cursor: null, total: 0, items: [] } } };
    }
    if (meta?.gqlMutation === SaveDashboardDocument) return { data: { save_dashboard: refused
      ? { ...payload(1), status: "conflict", current_revision: 2, message: "Changed elsewhere" } : payload(2) } };
    throw new Error("Unexpected dashboard operation");
  });
  const client = createClient();
  let dashboard!: ReturnType<typeof dashboardStore.useDashboard>;
  function Probe() {
    dashboard = dashboardStore.useDashboard({ scope: "personal", id: "db_1" });
    useAuthoredQuery(DashboardSummariesDocument, { cursor: null, version: null }, { models: ["dashboards.Dashboard"] });
    return null;
  }
  await act(async () => { root.render(<Provider queryClient={client} dataProvider={{ custom }}><Probe /></Provider>); });
  await expect.poll(() => dashboard.state.status).toBe("ready");
  await expect.poll(() => summaryReads).toBe(1);
  await act(async () => {
    const save = dashboard.save({ target: { scope: "personal", id: "db_1" }, snapshot,
      persistedId: "db_1", expectedRevision: 1, declarationRevision: "decl-1" });
    if (refused) await expect(save).rejects.toThrow("Changed elsewhere");
    else await expect(save).resolves.toMatchObject({ revision: 2, snapshot, capabilities: { canEdit: true } });
  });
  expect(dashboard.state).toMatchObject({ status: "ready", revision: refused ? 1 : 2 });
  expect(summaryReads).toBe(refused ? 1 : 2);
  expect(custom.mock.calls.filter(([input]) => input.meta?.gqlQuery === DashboardDocument)).toHaveLength(1);
  await act(async () => { held.resolve(); });
});

test("catalogue refresh replaces native pages using the newly returned version and cursor", async () => {
  let generation = 1;
  const reads: Array<{ cursor: unknown; version: unknown }> = [];
  const custom = vi.fn<NonNullable<RefineTestDataProvider["custom"]>>(async ({ meta }) => {
    if (meta?.gqlQuery !== DashboardSummariesDocument) throw new Error("Unexpected catalogue operation");
    const variables = meta.gqlVariables ?? {};
    reads.push({ cursor: variables.cursor, version: variables.version });
    const second = variables.cursor != null;
    const id = `db_${generation}_${second ? "b" : "a"}`;
    return { data: { dashboard_summaries: {
      status: "ready", version: `v${generation}`, next_cursor: second ? null : `next-${generation}`, total: 2,
      items: [{ id, scope: "PERSONAL", scope_key: null, name: id, description: "", owner: null, owner_label: null,
        revision: generation, is_trashed: false, resources: [], presets: [], can_edit: true, can_trash: true }],
    } } };
  });
  const client = createClient();
  let catalogue!: ReturnType<typeof dashboardStore.useCatalogue>;
  function Probe() { catalogue = dashboardStore.useCatalogue(); return null; }
  await act(async () => { root.render(<Provider queryClient={client} dataProvider={{ custom }}><Probe /></Provider>); });
  await expect.poll(() => catalogue.loading).toBe(false);
  expect(catalogue.summaries.map((row) => row.id)).toEqual(["db_1_a", "db_1_b"]);
  generation = 2;
  await act(async () => { await client.invalidateQueries(); });
  await expect.poll(() => catalogue.summaries.map((row) => row.id)).toEqual(["db_2_a", "db_2_b"]);
  expect(reads).toEqual([
    { cursor: null, version: null }, { cursor: "next-1", version: "v1" },
    { cursor: null, version: null }, { cursor: "next-2", version: "v2" },
  ]);
});
