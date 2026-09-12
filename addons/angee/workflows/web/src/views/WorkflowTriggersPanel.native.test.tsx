// @vitest-environment happy-dom

import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource, testQueryField } from "@angee/metadata/testing";
import { Refine, type DataProvider } from "@angee/refine";
import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets } from "@angee/ui";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, expect, test, vi } from "vitest";

const authored = vi.hoisted(() => ({
  enable: vi.fn(),
  disable: vi.fn(),
  fetching: false,
  conditionPending: false,
  conditionRequests: [] as Array<Record<string, unknown>>,
}));
vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAuthoredQuery: (document: unknown, variables?: Record<string, unknown>) => {
      if (String(document).includes("Launch")) {
        return {
          data: { workflows_by_pk: {
            id: "workflow-1", lineage_id: "workflow-1", current_published_version: 1,
          } },
          isFetching: false,
          error: null,
        };
      }
      if (String(document).includes("Preview")) {
        return {
          data: { workflow_schedule_preview: {
            timezone: "UTC", occurrences: ["2026-09-09T12:00:00Z"], errors: [],
          } },
          isFetching: false,
          error: null,
        };
      }
      if (String(document).includes("EventCondition")) {
        authored.conditionRequests.push(variables ?? {});
        if (authored.conditionPending) return { data: undefined, isFetching: true, error: null };
        const condition = variables?.condition;
        const clauses = variables?.clauses as Array<{
          field: string;
          lookup: string;
          value: unknown;
          source_key?: string | null;
        }> | null | undefined;
        return {
          data: { workflow_event_condition_draft: {
            fields: [
              { name: "state", label: "State", scalar: "string", lookups: [
                { name: "exact", key: "state", label: "Is", value_schema: { type: "string", nullable: true, presenceRequired: true } },
                { name: "in", key: "state__in", label: "Is one of", value_schema: { type: "array", widget: "list", items: { type: "string", nullable: true } } },
              ] },
              { name: "count", label: "Count", scalar: "integer", lookups: [
                { name: "exact", key: "count", label: "Is", value_schema: { type: "integer", nullable: true, presenceRequired: true } },
                { name: "gte", key: "count__gte", label: "Is at least", value_schema: { type: "integer", nullable: true, presenceRequired: true } },
                { name: "range", key: "count__range", label: "Is in range", value_schema: {
                  type: "array", widget: "list", minItems: 2, maxItems: 2,
                  items: { type: "integer", nullable: true },
                } },
              ] },
            ],
            clauses: clauses ?? (condition && typeof condition === "object" ? [
              ...("state" in condition ? [{ field: "state", lookup: "exact", value: (condition as { state: unknown }).state, source_key: "state" }] : []),
              ...("state__in" in condition ? [{ field: "state", lookup: "in", value: (condition as { state__in: unknown }).state__in, source_key: "state__in" }] : []),
              ...("count" in condition ? [{ field: "count", lookup: "exact", value: (condition as { count: unknown }).count, source_key: "count" }] : []),
            ] : []),
            opaque: variables?.opaque ?? (condition && typeof condition === "object" && "unsupported__regex" in condition
              ? { unsupported__regex: (condition as { unsupported__regex: unknown }).unsupported__regex }
              : {}),
            condition: clauses
              ? Object.assign(
                  {},
                  variables?.opaque,
                  ...clauses.map((clause) => ({
                    [clause.lookup === "exact" ? clause.field : `${clause.field}__${clause.lookup}`]: clause.value,
                  })),
                )
              : condition,
            errors: condition === null ? ["Condition must be a JSON object."] : [],
          } },
          isFetching: false,
          error: null,
        };
      }
      return {
        data: {
          workflow_trigger_declarations: [
            {
              kind: "schedule",
              label: "Schedule",
              config_schema: {
                type: "object",
                properties: {
                  cron: { type: "string", title: "Cron expression" },
                  interval_seconds: { type: "integer", label: "Interval seconds", omittable: true },
                  cooldown_seconds: {
                    type: "integer", label: "Cooldown seconds", nullable: true,
                    omittable: true, defaultValue: null,
                  },
                },
              },
            },
            {
              kind: "event",
              label: "Event",
              config_schema: {
                type: "object", required: ["model"],
                properties: {
                  model: { type: "string", label: "Model" },
                  admission_policy: {
                    type: "string",
                    label: "Run frequency",
                    default: "once_per_subject",
                    enum: ["once_per_subject", "each_change"],
                  },
                },
              },
            },
          ],
          workflow_trigger_publishers: [
            { model: "tests.TriggerSubject", label: "Trigger subject" },
            { model: "tests.OtherTriggerSubject", label: "Other trigger subject" },
          ],
        },
        isFetching: false,
        error: null,
      };
    },
    useActionMutation: (field: string) => [
      field === "disable_workflow_trigger" ? authored.disable : authored.enable,
      { fetching: authored.fetching, error: null },
    ],
  };
});
vi.mock("../documents.console", () => ({
  WorkflowTriggerAuthoringDocument: "WorkflowTriggerAuthoring",
  WorkflowSchedulePreviewDocument: "WorkflowSchedulePreview",
  WorkflowLaunchDocument: "WorkflowLaunch",
  WorkflowEventConditionDraftDocument: "WorkflowEventConditionDraft",
}));

import { TriggerWorkflowContext, workflowTriggerForm, workflowTriggerReadOnlyForm } from "./WorkflowTriggersPanel";

afterEach(() => { authored.conditionPending = false; });

const field = (name: string, scalar = "String") => ({
  name,
  kind: "scalar" as const,
  scalar,
  values: [],
  readable: true,
  filterable: true,
  sortable: true,
  aggregatable: false,
  groupable: false,
  creatable: true,
  updatable: true,
  requiredOnCreate: false,
  filter: { field: name, scalar, values: [], operators: ["exact"] },
});
const resource = testDataResource("workflows.Trigger", {
  modelName: "Trigger",
  typeNames: { node: "TriggerType" },
  fields: [
    field("id", "ID"), field("workflow", "ID"), field("kind"), field("enabled", "Boolean"),
    field("config", "JSON"), field("summary"), field("activation_blocker"),
    field("last_fire_at", "DateTime"), field("next_fire_at", "DateTime"),
  ],
  query: {
    identity: { field: "id" },
    fields: { workflow: testQueryField("workflow", { scalar: "ID" }) },
    axes: {},
    sort: { default: [] },
  },
});

afterEach(cleanup);

test("Back to triggers uses the native dirty leave guard", async () => {
  const close = vi.fn();
  const provider = {
    getApiUrl: () => "test://workflows",
    getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const Component = workflowTriggerForm.Component;
  render(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
          <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
            <TriggerWorkflowContext.Provider value={{ currentVersion: 1, close }}>
              <Component resource="workflows.Trigger" id={null} defaultValues={{
                workflow: "workflow-1", kind: "schedule", enabled: false, config: {},
              }} />
            </TriggerWorkflowContext.Provider>
          </AppRuntimeProvider></ToastProvider></ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "Interval" }));
  await screen.findByRole("button", { name: "Discard" });
  fireEvent.click(screen.getByRole("button", { name: "Back to triggers" }));
  fireEvent.click(await screen.findByRole("button", { name: "Stay" }));
  expect(screen.getByRole("button", { name: "Back to triggers" })).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Back to triggers" }));
  fireEvent.click(await screen.findByRole("button", { name: "Leave" }));
  await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
});

test("create mode presents the canonical lowercase trigger kind without enabling it", async () => {
  const create = vi.fn(async ({ variables }: { variables?: unknown }) => ({ data: { id: "trigger-1", ...(variables as object) } }));
  const provider = {
    getApiUrl: () => "test://workflows",
    getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })),
    create, update: vi.fn(), deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const Component = workflowTriggerForm.Component;
  render(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
          <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
            <Component resource="workflows.Trigger" id={null} defaultValues={{
              workflow: "workflow-1", kind: "schedule", enabled: false, config: {},
            }} />
          </AppRuntimeProvider></ToastProvider></ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );

  const kind = await screen.findByLabelText("Kind") as HTMLSelectElement;
  fireEvent.change(kind, { target: { value: "schedule" } });
  expect(kind.value).toBe("schedule");
  expect(screen.getByRole("heading", { name: "New trigger" })).toBeTruthy();
  expect(screen.queryByLabelText("Enabled")).toBeNull();
  expect(screen.queryByLabelText("Summary")).toBeNull();
  expect(screen.queryByLabelText("Last Fire At")).toBeNull();
  expect(screen.getByText("workflow-1")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Advanced" }).getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(await screen.findByRole("button", { name: "Interval" }));
  const interval = await screen.findByLabelText(/Interval Seconds/i) as HTMLInputElement;
  fireEvent.click(screen.getByRole("button", { name: "Rule JSON" }));
  const raw = await screen.findByLabelText("Rule JSON");
  fireEvent.change(interval, { target: { value: "3600" } });
  expect(raw.textContent).toContain('"interval_seconds": 3600');
  interval.focus();
  fireEvent.input(interval, { target: { value: "" } });
  await waitFor(() => expect(raw.textContent).toContain('"interval_seconds": ""'));
  expect(document.activeElement).toBe(interval);
  fireEvent.change(interval, { target: { value: "3600" } });
  expect(await screen.findByText("Timezone: UTC")).toBeTruthy();
  expect(screen.queryByRole("checkbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(create).toHaveBeenCalled());
  expect(create.mock.calls[0]?.[0]).toMatchObject({
    variables: { config: { interval_seconds: 3600 } },
  });
});

test("schedule mode changes preserve opaque rule JSON and submit one cadence", async () => {
  authored.enable.mockReset();
  authored.fetching = false;
  authored.enable.mockRejectedValueOnce(new Error("Activation denied"));
  const update = vi.fn(async ({ variables }: { variables?: Record<string, unknown> }) => ({
    data: { id: "trigger-1", ...(variables ?? {}) },
  }));
  const provider = {
    getApiUrl: () => "test://workflows",
    getOne: vi.fn(async () => ({
      data: {
        id: "trigger-1",
        workflow: "workflow-1",
        kind: "SCHEDULE",
        enabled: false,
        config: { cron: "0 * * * *", opaque: { literal: true } },
        summary: "Cron 0 * * * *",
        activation_blocker: null,
        last_fire_at: null,
        next_fire_at: null,
      },
    })),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    create: vi.fn(),
    update,
    deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const Component = workflowTriggerForm.Component;
  const router = createRouter({
    routeTree: createRootRoute(),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  render(
    <Refine
      resources={[...refineResourcesFromDataResources([resource])]}
      dataProvider={{ default: provider, console: provider }}
      options={{ disableTelemetry: true }}
    >
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
          <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
            <Component resource="workflows.Trigger" id="trigger-1" />
          </AppRuntimeProvider></ToastProvider></ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );

  const enable = await screen.findByRole("button", { name: "Enable" });
  fireEvent.click(enable);
  await waitFor(() => expect(authored.enable).toHaveBeenCalledTimes(1));
  expect((await screen.findAllByText("Activation denied")).length).toBeGreaterThan(0);
  fireEvent.click(await screen.findByRole("button", { name: "Interval" }));
  expect((enable as HTMLButtonElement).disabled).toBe(true);
  expect(enable.title).toBe("Save or discard rule changes before changing activation.");
  const interval = await screen.findByLabelText(/Interval Seconds/i) as HTMLInputElement;
  interval.focus();
  fireEvent.keyDown(interval, { key: "a", metaKey: true });
  fireEvent.input(interval, { target: { value: "" } });
  expect(document.activeElement).toBe(interval);
  expect(screen.queryByText("Left empty")).toBeNull();
  fireEvent.change(interval, { target: { value: "120" } });
  fireEvent.blur(interval);
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));

  await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
  const variables = update.mock.calls[0]?.[0]?.variables as { config?: unknown } | undefined;
  expect(variables?.config).toEqual({ interval_seconds: 120, opaque: { literal: true } });
  expect(screen.getByText("Timezone: UTC")).toBeTruthy();
  expect(screen.getByText(/Sep 9, 2026/)).toBeTruthy();
});

test("activation reports business failures without reloading and reflects pending state", async () => {
  authored.enable.mockReset();
  authored.enable
    .mockResolvedValueOnce(undefined)
    .mockResolvedValueOnce({
      ok: false,
      message: "The published workflow no longer matches this rule.",
    });
  const getOne = vi.fn(async () => ({
    data: {
      id: "trigger-1",
      workflow: "workflow-1",
      kind: "SCHEDULE",
      enabled: false,
      config: { interval_seconds: 60 },
      summary: "Every 60 seconds",
      activation_blocker: null,
      last_fire_at: null,
      next_fire_at: null,
    },
  }));
  const provider = {
    getApiUrl: () => "test://workflows",
    getOne,
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({
    routeTree: createRootRoute(),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const Component = workflowTriggerForm.Component;
  const view = render(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
          <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
            <Component resource="workflows.Trigger" id="trigger-1" />
          </AppRuntimeProvider></ToastProvider></ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );

  const enable = await screen.findByRole("button", { name: "Enable" });
  fireEvent.click(enable);
  await waitFor(() => expect(authored.enable).toHaveBeenCalledTimes(1));
  expect(getOne).toHaveBeenCalledTimes(1);
  fireEvent.click(enable);
  expect((await screen.findAllByText("The published workflow no longer matches this rule.")).length).toBeGreaterThan(0);
  expect(getOne).toHaveBeenCalledTimes(1);

  authored.fetching = true;
  view.rerender(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
          <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
            <Component resource="workflows.Trigger" id="trigger-1" />
          </AppRuntimeProvider></ToastProvider></ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );
  expect((await screen.findByRole("button", { name: "Enable" }) as HTMLButtonElement).disabled).toBe(true);
  authored.fetching = false;
});

test("disabling remains available with dirty rule edits and preserves them across reload", async () => {
  authored.disable.mockReset();
  authored.disable.mockResolvedValueOnce({
    ok: true, message: "Disabled",
  });
  const provider = {
    getApiUrl: () => "test://workflows",
    getOne: vi.fn(async () => ({
      data: {
        id: "trigger-1",
        workflow: "workflow-1",
        kind: "SCHEDULE",
        enabled: false,
        config: { interval_seconds: 60, opaque: true },
        summary: "Every 60 seconds",
        activation_blocker: null,
        last_fire_at: null,
        next_fire_at: null,
      },
    })),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as unknown as DataProvider;
  // The first read represents the enabled record; reload returns the disabled fact.
  provider.getOne = vi.fn()
    .mockResolvedValueOnce({ data: {
      id: "trigger-1", workflow: "workflow-1", kind: "SCHEDULE", enabled: true,
      config: { interval_seconds: 60, opaque: true }, summary: "Every 60 seconds",
      activation_blocker: null, last_fire_at: null, next_fire_at: null,
    } })
    .mockResolvedValue({ data: {
      id: "trigger-1", workflow: "workflow-1", kind: "SCHEDULE", enabled: false,
      config: { interval_seconds: 60, opaque: true }, summary: "Every 60 seconds",
      activation_blocker: null, last_fire_at: null, next_fire_at: null,
    } });
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const Component = workflowTriggerForm.Component;
  render(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
          <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
            <Component resource="workflows.Trigger" id="trigger-1" />
          </AppRuntimeProvider></ToastProvider></ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );

  const interval = await screen.findByLabelText(/Interval Seconds/i) as HTMLInputElement;
  fireEvent.change(interval, { target: { value: "120" } });
  const disable = screen.getByRole("button", { name: "Disable" }) as HTMLButtonElement;
  expect(disable.disabled).toBe(false);
  fireEvent.click(disable);
  await waitFor(() => expect(authored.disable).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(interval.value).toBe("120"));
  expect(await screen.findByRole("button", { name: "Enable" })).toBeTruthy();
});

test("historical trigger forms expose no executable lifecycle or save actions", async () => {
  authored.enable.mockReset();
  authored.disable.mockReset();
  const record = {
    id: "trigger-1",
    workflow: "workflow-1",
    kind: "SCHEDULE",
    enabled: true,
    config: { interval_seconds: 60 },
    summary: "Every 60 seconds",
    activation_blocker: null,
    last_fire_at: null,
    next_fire_at: null,
  };
  const provider = {
    getApiUrl: () => "test://workflows",
    getOne: vi.fn(async () => ({ data: record })),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const Component = workflowTriggerReadOnlyForm.Component;
  render(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
          <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
            <Component resource="workflows.Trigger" id="trigger-1" />
          </AppRuntimeProvider></ToastProvider></ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );

  expect((await screen.findAllByText("Every 60 seconds")).length).toBeGreaterThan(0);
  expect(screen.getByText("Activity")).toBeTruthy();
  expect(screen.getByText("Never")).toBeTruthy();
  expect(screen.getByText("None scheduled")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Disable" })).toBeNull();
  expect(authored.enable).not.toHaveBeenCalled();
  expect(authored.disable).not.toHaveBeenCalled();
});

test("event condition edits stay in the trigger form and preserve opaque lookups", async () => {
  authored.conditionRequests.length = 0;
  const update = vi.fn(async ({ variables }: { variables?: Record<string, unknown> }) => ({
    data: { id: "trigger-event", ...(variables ?? {}) },
  }));
  const provider = {
    getApiUrl: () => "test://workflows",
    getOne: vi.fn(async () => ({ data: {
      id: "trigger-event",
      workflow: "workflow-1",
      kind: "EVENT",
      enabled: false,
      config: {
        model: "tests.TriggerSubject",
        condition: { state: "ready", state__in: ["ready"], count: 2, unsupported__regex: "^x" },
      },
      summary: "Trigger subject changes",
      activation_blocker: null,
      last_fire_at: null,
      next_fire_at: null,
    } })),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    create: vi.fn(), update, deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({
    routeTree: createRootRoute(),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const Component = workflowTriggerForm.Component;
  render(
    <Refine
      resources={[...refineResourcesFromDataResources([resource])]}
      dataProvider={{ default: provider, console: provider }}
      options={{ disableTelemetry: true }}
    >
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
          <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
            <Component resource="workflows.Trigger" id="trigger-event" />
          </AppRuntimeProvider></ToastProvider></ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );

  expect(await screen.findByText("Additional unsupported conditions are preserved in Rule JSON.")).toBeTruthy();
  const [value] = await screen.findAllByLabelText("Value");
  if (!(value instanceof HTMLInputElement)) throw new Error("State condition value is missing");
  value.focus();
  fireEvent.change(value, { target: { value: "" } });
  expect(screen.getAllByLabelText("Value")[0]).toBe(value);
  expect(await screen.findByText("Enter a valid condition value.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(update).not.toHaveBeenCalled());
  authored.conditionPending = true;
  fireEvent.change(value, { target: { value: "done" } });
  expect((screen.getAllByLabelText("Value")[0] as HTMLInputElement).value).toBe("done");
  expect(document.activeElement).toBe(value);
  authored.conditionPending = false;
  fireEvent.change(value, { target: { value: "done!" } });
  fireEvent.change(value, { target: { value: "done" } });
  fireEvent.blur(value);
  await waitFor(() => expect(authored.conditionRequests.some((request) => (
    (request.condition as { state?: unknown } | undefined)?.state === "done"
  ))).toBe(true));
  await waitFor(() => expect((screen.getAllByLabelText("Value")[0] as HTMLInputElement).value).toBe("done"));
  fireEvent.click(screen.getByRole("button", { name: "Back to triggers" }));
  fireEvent.click(await screen.findByRole("button", { name: "Stay" }));
  expect((screen.getAllByLabelText("Value")[0] as HTMLInputElement).value).toBe("done");
  const count = screen.getAllByLabelText("Value").find((control) => (
    control instanceof HTMLInputElement && control.value === "2"
  ));
  if (!(count instanceof HTMLInputElement)) throw new Error("Count condition value is missing");
  fireEvent.change(count, { target: { value: "4" } });
  fireEvent.blur(count);
  await waitFor(() => expect(authored.conditionRequests.some((request) => (
    (request.condition as { count?: unknown } | undefined)?.count === 4
  ))).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Add condition" }));
  expect(await screen.findByText("Enter a valid condition value.")).toBeTruthy();
  expect(authored.conditionRequests.some((request) => (
    (request.condition as { count__gte?: unknown } | undefined)?.count__gte === ""
  ))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Back to triggers" }));
  fireEvent.click(await screen.findByRole("button", { name: "Stay" }));
  expect(await screen.findByText("Enter a valid condition value.")).toBeTruthy();
  fireEvent.click(screen.getByRole("combobox", { name: "Model" }));
  const otherModel = await screen.findByRole("option", { name: "Other trigger subject" });
  fireEvent.pointerDown(otherModel); fireEvent.pointerUp(otherModel); fireEvent.click(otherModel);
  await waitFor(() => expect(screen.queryByText("Enter a valid condition value.")).toBeNull());
  fireEvent.click(screen.getByRole("combobox", { name: "Model" }));
  const originalModel = await screen.findByRole("option", { name: "Trigger subject" });
  fireEvent.pointerDown(originalModel); fireEvent.pointerUp(originalModel); fireEvent.click(originalModel);
  expect(screen.queryByText("Enter a valid condition value.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(update).not.toHaveBeenCalled());
  fireEvent.click(screen.getAllByRole("button", { name: "Remove condition" }).at(-1)!);
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));

  await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
  expect(update.mock.calls[0]?.[0]?.variables).toMatchObject({
    config: {
      model: "tests.TriggerSubject",
      condition: { state: "done", state__in: ["ready"], count: 4, unsupported__regex: "^x" },
    },
  });
  expect(authored.conditionRequests.every((request) => request.clauses === null)).toBe(true);
});

test("event create validates its composed condition before transport", async () => {
  authored.conditionRequests.length = 0;
  const create = vi.fn();
  const provider = {
    getApiUrl: () => "test://workflows",
    getOne: vi.fn(),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    create,
    update: vi.fn(),
    deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({
    routeTree: createRootRoute(),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const Component = workflowTriggerForm.Component;
  render(
    <Refine
      resources={[...refineResourcesFromDataResources([resource])]}
      dataProvider={{ default: provider, console: provider }}
      options={{ disableTelemetry: true }}
    >
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
          <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
            <Component resource="workflows.Trigger" id={null} defaultValues={{
              workflow: "workflow-1",
              kind: "event",
              enabled: false,
              config: { model: "tests.TriggerSubject" },
            }} />
          </AppRuntimeProvider></ToastProvider></ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );

  await screen.findByRole("button", { name: "Add condition" });
  const frequency = await screen.findByRole("combobox", { name: "Run frequency" }) as HTMLSelectElement;
  expect(frequency.value).toBe("");
  fireEvent.click(frequency);
  expect(await screen.findByRole("option", { name: "Once for each subject" })).toBeTruthy();
  expect(authored.conditionRequests.some((request) => (
    JSON.stringify(request.condition) === "{}"
  ))).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Add condition" }));
  expect(await screen.findByText("Enter a valid condition value.")).toBeTruthy();
  const draftValue = screen.getByLabelText("Value") as HTMLInputElement;
  draftValue.focus();
  fireEvent.input(draftValue, { target: { value: "r" } });
  await waitFor(() => expect((screen.getByLabelText("Value") as HTMLInputElement).value).toBe("r"));
  expect(screen.getByLabelText("Value")).toBe(draftValue);
  expect(document.activeElement).toBe(draftValue);
  fireEvent.input(draftValue, { target: { value: "re" } });
  await waitFor(() => expect((screen.getByLabelText("Value") as HTMLInputElement).value).toBe("re"));
  expect(screen.getByLabelText("Value")).toBe(draftValue);
  expect(document.activeElement).toBe(draftValue);
  fireEvent.input(draftValue, { target: { value: "" } });
  expect(screen.getByLabelText("Value")).toBe(draftValue);
  expect(await screen.findByText("Enter a valid condition value.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(create).not.toHaveBeenCalled());
});

test("event edit rejects an explicitly malformed saved condition", async () => {
  const update = vi.fn();
  const provider = {
    getApiUrl: () => "test://workflows",
    getOne: vi.fn(async () => ({ data: {
      id: "trigger-invalid-event",
      workflow: "workflow-1",
      kind: "EVENT",
      enabled: false,
      config: { model: "tests.TriggerSubject", condition: null },
      summary: "Invalid legacy condition",
      activation_blocker: "Condition must be a JSON object.",
      last_fire_at: null,
      next_fire_at: null,
    } })),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    create: vi.fn(),
    update,
    deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({
    routeTree: createRootRoute(),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const Component = workflowTriggerForm.Component;
  render(
    <Refine
      resources={[...refineResourcesFromDataResources([resource])]}
      dataProvider={{ default: provider, console: provider }}
      options={{ disableTelemetry: true }}
    >
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
          <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
            <Component resource="workflows.Trigger" id="trigger-invalid-event" />
          </AppRuntimeProvider></ToastProvider></ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );

  expect((await screen.findAllByText("Condition must be a JSON object.")).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "Add condition" }));
  expect(await screen.findByText("Enter a valid condition value.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  await waitFor(() => expect(update).not.toHaveBeenCalled());
});
