// @vitest-environment happy-dom

import { refineResourceName, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import {
  AppRuntimeProvider,
  Field,
  FormView,
  ModalsHost,
  ToastProvider,
  baseIcons,
  defaultWidgets,
  formViewSectionsSlot,
  type ListViewProps,
} from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import {
  RouterContextProvider,
  createMemoryHistory,
  createRootRoute,
  createRouter,
} from "@tanstack/react-router";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const state = vi.hoisted(() => ({
  list: null as ListViewProps | null,
  mutate: vi.fn(),
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const { createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal, {
    ListView: (props: ListViewProps) => {
      state.list = props;
      return <div data-testid="sync-data-view" data-resource={props.resource}>{props.toolbarActions}</div>;
    },
    useActionResultMutation: (field: string) => [
      (id: string) => state.mutate(field, id),
      { fetching: false, error: null },
    ],
  });
});

import integrate from "./index";
import { INTEGRATION_MODEL } from "./IntegrationLifecycleActions";
import {
  RECORD_LINK_MODEL,
  SYNC_DISCREPANCY_MODEL,
  SYNC_STREAM_MODEL,
  integrationSyncCursorWidget,
} from "./IntegrationStreams";

const CHILD_MODEL = "example.RecordBridge";
const resources = [...[INTEGRATION_MODEL, "messaging.Channel", CHILD_MODEL].map((modelLabel) =>
  testDataResource(modelLabel, {
    canonicalLabel: INTEGRATION_MODEL,
    recordRepresentation: "display_name",
    fields: ["id", "display_name", ...(modelLabel === CHILD_MODEL ? [] : ["stream_count"])].map((name) => ({
      name,
      kind: "scalar" as const,
      scalar: name === "stream_count" ? "Int" : "String",
      readable: true,
      filterable: false,
      sortable: false,
      aggregatable: false,
      groupable: false,
      creatable: name === "display_name",
      updatable: name === "display_name",
      requiredOnCreate: false,
    })),
  }),
), testDataResource(SYNC_DISCREPANCY_MODEL, {
  fields: [{
    name: "status", kind: "enum", values: ["OPEN", "RETRY", "RESOLVED"].map((value) => ({ value })),
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
  }],
})];

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://integration-streams" });
const CursorSummary = integrationSyncCursorWidget.read;

beforeEach(() => {
  state.list = null;
  state.mutate.mockReset().mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  clearClients();
});

function renderIntegration(resource = INTEGRATION_MODEL, streamCount: number | null = 2, saved = true) {
  const record: Row = { id: "bridge_1", display_name: "Calendar bridge", stream_count: streamCount };
  const selectedFields: string[] = [];
  const requests: { resource: string | undefined; fields: string[] }[] = [];
  const provider = {
    getOne: vi.fn(async ({ meta, resource: requestedResource }) => {
      const fields = Array.isArray(meta?.fields)
        ? meta.fields.filter((field: unknown): field is string => typeof field === "string")
        : [];
      selectedFields.push(...fields);
      requests.push({ resource: requestedResource, fields });
      // Match GraphQL selection: a tab cannot accidentally work only because a
      // fixture returned a field its form never requested.
      return { data: Object.fromEntries(Object.entries(record).filter(([name]) => fields.includes(name))) };
    }),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
  } satisfies RefineTestDataProvider;
  const router = createRouter({
    routeTree: createRootRoute(),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const target = formViewSectionsSlot(INTEGRATION_MODEL);
  render(<Provider resources={resources} dataProvider={provider}
    queryClientConfig={{ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }}>
    <RouterContextProvider router={router}>
      <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{
        widgets: { ...defaultWidgets, ...integrate.widgets },
        icons: { ...baseIcons, ...integrate.icons },
        slots: (integrate.slots ?? []).filter((entry) => entry.slot === target.slot),
      }}>
        <FormView resource={resource} id={saved ? "bridge_1" : null}>
          <Field name="display_name" label="Name" title />
        </FormView>
      </AppRuntimeProvider></ToastProvider></ModalsHost>
    </RouterContextProvider>
  </Provider>);
  return { selectedFields, requests };
}

function listProps(): ListViewProps {
  if (!state.list) throw new Error("The Streams pane has not rendered its resource view.");
  return state.list;
}

async function selectRowAction(id: string, row: Row): Promise<void> {
  const action = listProps().rowActions?.find((candidate) => candidate.id === id);
  if (!action || action.kind !== "page") throw new Error(`Missing page row action: ${id}`);
  expect(action.visible(row)).toBe(true);
  await act(async () => { await action.onSelect(row); });
}

async function openStreams(resource = INTEGRATION_MODEL): Promise<void> {
  renderIntegration(resource);
  fireEvent.click(await screen.findByRole("tab", { name: "Streams" }));
  await screen.findByTestId("sync-data-view");
}

describe("Integration Streams contribution", () => {
  test.each([INTEGRATION_MODEL, "messaging.Channel"])("appears once on a saved %s form with streams", async (resource) => {
    const { selectedFields } = renderIntegration(resource);
    await screen.findByRole("tab", { name: "Streams" });
    expect(screen.getAllByRole("tab", { name: "Streams" })).toHaveLength(1);
    expect(selectedFields).toContain("stream_count");
  });

  test.each([0, null])("hides the tab when the saved record has no stream evidence (%s)", async (count) => {
    renderIntegration(INTEGRATION_MODEL, count);
    await screen.findByDisplayValue("Calendar bridge");
    expect(screen.queryByRole("tab", { name: "Streams" })).toBeNull();
    expect(state.list).toBeNull();
  });

  test.each([2, 0, null])("reads stream presence from Integration when the child omits it (%s)", async (count) => {
    const { requests } = renderIntegration(CHILD_MODEL, count);
    const childResource = refineResourceName(resources.find((item) => item.modelLabel === CHILD_MODEL)!);
    const parentResource = refineResourceName(resources.find((item) => item.modelLabel === INTEGRATION_MODEL)!);
    await screen.findByDisplayValue("Calendar bridge");
    await waitFor(() => expect(requests).toContainEqual({
      resource: parentResource, fields: ["id", "stream_count"],
    }));
    const childRequests = requests.filter((request) => request.resource === childResource);
    expect(childRequests.length).toBeGreaterThan(0);
    for (const request of childRequests) expect(request.fields).not.toContain("stream_count");
    if (count !== null && count > 0) await screen.findByRole("tab", { name: "Streams" });
    expect(screen.queryAllByRole("tab", { name: "Streams" })).toHaveLength(count ? 1 : 0);
  });

  test("does not expose a saved-record tab while creating an integration", () => {
    renderIntegration(INTEGRATION_MODEL, 2, false);
    expect(screen.queryByRole("tab", { name: "Streams" })).toBeNull();
    expect(state.list).toBeNull();
  });

  test("scopes streams to the open bridge and declares grouping and compact cursor presentation", async () => {
    await openStreams("messaging.Channel");
    expect(listProps().resource).toBe(SYNC_STREAM_MODEL);
    expect(listProps().baseFilter).toEqual({ integration: { exact: "bridge_1" } });
    expect(listProps().defaultGroup).toMatchObject({ field: "key" });
    expect(listProps().columns.map((column) => column.field)).toEqual([
      "key", "partition", "kind", "direction", "generation", "phase", "cursor",
      "last_advanced_at", "last_reconciled_at", "open_discrepancy_count", "link_count", "resync_required",
    ]);
    expect(listProps().columns.find((column) => column.field === "cursor")?.widget)
      .toBe("angee.integrate.sync_cursor");
  });

  test("opens only the selected stream's discrepancies, with Resolve and Retry actions", async () => {
    await openStreams();
    await selectRowAction("open-discrepancies", { id: "stream_1", key: "contacts", kind: "RECORD_REPLICA" });
    expect(listProps().resource).toBe(SYNC_DISCREPANCY_MODEL);
    expect(listProps().baseFilter).toMatchObject({ stream: { exact: "stream_1" } });
    expect(listProps().baseFilter).toMatchObject({ status: { inList: ["open", "retry"] } });
    expect(listProps().rowActions?.map((action) => action.label)).toEqual(["Resolve", "Retry"]);
    await selectRowAction("resolve", { id: "discrepancy_1", status: "OPEN" });
    await selectRowAction("retry", { id: "discrepancy_2", status: "RETRY" });
    expect(state.mutate.mock.calls).toEqual([
      ["resolveSyncDiscrepancy", "discrepancy_1"],
      ["retrySyncDiscrepancy", "discrepancy_2"],
    ]);
    for (const action of listProps().rowActions ?? []) {
      expect(action.visible({ id: "resolved_1", status: "RESOLVED" })).toBe(false);
    }
  });

  test("opens record links only for replica streams and returns to the same bridge", async () => {
    await openStreams();
    const linksAction = listProps().rowActions?.find((action) => action.id === "open-links");
    expect(linksAction?.visible({ id: "stream_1", kind: "RECORD_REPLICA" })).toBe(true);
    expect(linksAction?.visible({ id: "stream_2", kind: "EVENT_FEED" })).toBe(false);
    await selectRowAction("open-links", { id: "stream_1", key: "contacts", kind: "RECORD_REPLICA" });
    expect(listProps().resource).toBe(RECORD_LINK_MODEL);
    expect(listProps().baseFilter).toEqual({ stream: { exact: "stream_1" } });
    expect(listProps().columns.map((column) => column.field)).toEqual([
      "external_key", "status", "origin", "remote_version", "last_seen_at", "record_id",
    ]);
    expect(listProps().fields).toContain("model_label");
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(listProps().resource).toBe(SYNC_STREAM_MODEL);
    expect(listProps().baseFilter).toEqual({ integration: { exact: "bridge_1" } });
  });

  test("declares Resync confirmation on the shared row action", async () => {
    await openStreams();
    const resync = listProps().rowActions?.find((action) => action.label === "Resync");
    expect(resync).toMatchObject({ confirm: {
      title: expect.any(Function), body: expect.any(Function), confirm: expect.any(Function),
    } });
    expect(resync?.disabled({ id: "stream_1", resync_required: true })).toBe(true);
    await selectRowAction("resync", { id: "stream_1", resync_required: false });
    expect(state.mutate).toHaveBeenCalledWith("resyncSyncStream", "stream_1");
  });
});

describe("sync cursor summary widget", () => {
  test("shows bounded top-level keys and scalar values without serializing nested payloads", () => {
    const cursor = {
      offset: 42,
      token: "x".repeat(500),
      nested: { payload: "nested-payload-must-not-be-dumped" },
      page: 3,
      extra: "tail-value-must-not-be-dumped",
    };
    const { container } = render(<CursorSummary value={cursor} />);
    const text = container.textContent ?? "";
    expect(text).toContain("offset");
    expect(text).toContain("42");
    expect(text).toContain("token");
    expect(text).not.toContain(cursor.token);
    expect(text).not.toContain(cursor.nested.payload);
    expect(text).not.toContain(cursor.extra);
    expect(text.length).toBeLessThan(350);
    expect(container.querySelector("pre")).toBeNull();
    expect(container.innerHTML).not.toContain(cursor.nested.payload);
  });

  test.each([null, {}, [], "x".repeat(500)])("keeps empty and non-object cursors compact", (value) => {
    const { container } = render(<CursorSummary value={value} />);
    expect((container.textContent ?? "").length).toBeLessThan(100);
    expect(container.querySelector("pre")).toBeNull();
  });
});
