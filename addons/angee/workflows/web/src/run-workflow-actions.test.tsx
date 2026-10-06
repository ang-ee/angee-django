// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { DocumentType } from "@angee/gql/console";
import type { CustomParams } from "@refinedev/core";
import { ShellPageTestProviders } from "@angee/app/testing";
import { ActionMenu, ModalsHost, RecordChromeProvider, ToastProvider } from "@angee/ui";
import { defaultWidgets } from "@angee/ui/widgets";
import { createUiTestProviders } from "@angee/ui/testing";

import { RunWorkflowRecordActions } from "./run-workflow-actions";
import { RUN_MODELS, StartableRecordWorkflowsDocument } from "./documents.console";
import { triggerNoteResourceFixture } from "./trigger-testing";

const mocks = vi.hoisted(() => ({ start: vi.fn(), options: vi.fn(), navigate: vi.fn() }));
vi.mock("@angee/ui", async (original) => ({
  ...await original<typeof import("@angee/ui")>(),
  useActionOutcomeMutation: (field: string, options: unknown) => {
    mocks.options(field, options); return [mocks.start, { fetching: false, error: null }];
  },
}));
vi.mock("@tanstack/react-router", async (original) => ({
  ...await original<typeof import("@tanstack/react-router")>(), useNavigate: () => mocks.navigate,
}));

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://record-workflows", queryClientConfig: { defaultOptions: { queries: { retry: false } } },
});
type Workflows = DocumentType<typeof StartableRecordWorkflowsDocument>;
const offered: Workflows = { workflow: [
  { id: "wfl_quiet", name: "Quiet", published: { input_schema: { type: "object", properties: {} } } },
  { id: "wfl_review", name: "Review", published: { input_schema: {
    type: "object", properties: { reason: { type: "string", title: "Reason", minLength: 1 } }, required: ["reason"],
  } } },
] };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.start.mockReset();
  mocks.start.mockImplementation(async (id: string) => ({ ok: true, id: "wfr_new",
    message: `${id === "wfl_quiet" ? "Quiet" : "Review"} started` }));
});
afterEach(() => { cleanup(); clearClients(); });

function mount(data: Workflows = offered, blocked = false) {
  const custom = vi.fn(async (_params: Partial<CustomParams>) => ({ data }));
  const record = { id: "nte_7" };
  const result = render(<Provider dataProvider={{ custom }} resources={[triggerNoteResourceFixture]}>
    <ShellPageTestProviders runtime={{ widgets: defaultWidgets }}><ModalsHost><ToastProvider>
      <RecordChromeProvider value={{ resource: "notes.Note", canonicalResource: "notes.Record",
        recordId: record.id, record, dataProviderName: "console", formReadOnly: false, actionsBlocked: blocked }}>
        <ActionMenu><RunWorkflowRecordActions /></ActionMenu>
      </RecordChromeProvider>
    </ToastProvider></ModalsHost></ShellPageTestProviders>
  </Provider>);
  return { ...result, custom };
}

async function openWorkflows() {
  fireEvent.click(screen.getByRole("button", { name: "Actions" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Run workflow" }));
}

test("lists the server's workflows for the concrete and canonical record models", async () => {
  const { custom } = mount();
  await openWorkflows();
  expect(await screen.findByRole("menuitem", { name: "Quiet" })).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: "Review" })).toBeTruthy();
  expect(custom.mock.calls[0]?.[0].meta?.gqlQuery).toBe(StartableRecordWorkflowsDocument);
  expect(custom.mock.calls[0]?.[0].meta?.gqlVariables).toEqual({ models: ["notes.Record", "notes.Note"] });
  expect(mocks.options).toHaveBeenCalledWith("start_workflow_run", expect.objectContaining({
    idArgument: "workflow_id", dataProviderName: "console",
    invalidateModels: [...RUN_MODELS, "decisions.Decision", "decisions.DecisionRecord"],
  }));
});

test("starts a workflow without required input at once and settles its outcome once", async () => {
  mount(); await openWorkflows();
  fireEvent.click(await screen.findByRole("menuitem", { name: "Quiet" }));
  await waitFor(() => expect(mocks.start).toHaveBeenCalledExactlyOnceWith("wfl_quiet", {
    subject: { model: "notes.Note", id: "nte_7" }, input: {},
    request_key: expect.stringMatching(/^[0-9a-f-]{36}$/),
  }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(await screen.findAllByText("Quiet started")).toHaveLength(1);
});

test("required input opens the shared schema form before starting", async () => {
  mount(); await openWorkflows();
  fireEvent.click(await screen.findByRole("menuitem", { name: "Review" }));
  const dialog = await screen.findByRole("dialog", { name: "Review" });
  expect(mocks.start).not.toHaveBeenCalled();
  fireEvent.change(within(dialog).getByRole("textbox", { name: "Reason" }), { target: { value: "Check record" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Review" }));
  await waitFor(() => expect(mocks.start).toHaveBeenCalledExactlyOnceWith("wfl_review", {
    subject: { model: "notes.Note", id: "nte_7" }, input: { reason: "Check record" }, request_key: expect.any(String),
  }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(await screen.findAllByText("Review started")).toHaveLength(1);
});

test("a refused input stays in the dialog and retries with the same request key", async () => {
  mocks.start.mockResolvedValueOnce({ ok: false, message: "Refused", validationErrors: { "input.reason": ["Check reason."] } });
  mount(); await openWorkflows();
  fireEvent.click(await screen.findByRole("menuitem", { name: "Review" }));
  const dialog = await screen.findByRole("dialog", { name: "Review" });
  fireEvent.change(within(dialog).getByRole("textbox", { name: "Reason" }), { target: { value: "Check record" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Review" }));
  expect(await within(dialog).findByText("Check reason.")).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "Review" }));
  await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(2));
  expect(mocks.start.mock.calls[1]?.[1].request_key).toBe(mocks.start.mock.calls[0]?.[1].request_key);
});

test("relation annotations render the standard record picker", async () => {
  const relation: Workflows = { workflow: [{ id: "wfl_source", name: "Use source", published: { input_schema: {
    type: "object", properties: { source: { type: "string", title: "Source", relation: { resource: "notes.Note" } } }, required: ["source"],
  } } }] };
  mount(relation); await openWorkflows();
  fireEvent.click(await screen.findByRole("menuitem", { name: "Use source" }));
  const dialog = await screen.findByRole("dialog", { name: "Use source" });
  expect(await within(dialog).findByRole("combobox", { name: "Source" })).toBeTruthy();
  expect(mocks.start).not.toHaveBeenCalled();
});

test("omits the contribution when no workflow applies", async () => {
  const { custom } = mount({ workflow: [] });
  await waitFor(() => expect(custom).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "Actions" }));
  expect(screen.queryByRole("menuitem", { name: "Run workflow" })).toBeNull();
});

test("inherits the record's dirty form gate", async () => {
  mount(offered, true);
  fireEvent.click(screen.getByRole("button", { name: "Actions" }));
  const item = await screen.findByRole("menuitem", { name: "Run workflow" });
  expect(item.getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(item);
  expect(mocks.start).not.toHaveBeenCalled();
});
