// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { schemaFieldMetadataFromDataResources, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { QueryClient } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider, containersFromChildren } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import type { FieldDescriptor } from "../page";
import { repairChoosable } from "../resource/implementation-fields";
import { BoundDescriptorField } from "./BoundDescriptorField";
import { FORM_CONTAINERS, FormView } from "./FormView";
import { withModeLockedFields } from "./form-view-model";

const base = { readable: true, aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false };
const backend = { ...base, name: "backend", kind: "enum" as const, createOnly: true,
  values: [{ value: "LOCAL", description: "Local" }, { value: "STUB", description: "Stub" }] };
const config = { ...base, name: "config", kind: "scalar" as const, scalar: "JSON" };
const title = { ...base, name: "title", kind: "scalar" as const, scalar: "String" };
const CARRIED = { root: "/srv/team", org: "local" };
const STORED_CONFIG = { root: "/srv/team", retired: true };
const PAGE: FieldDescriptor[] = [{ name: "title", title: true }, { name: "backend", label: "Backend" }, { name: "config", label: "Config" }];
const RETIRED = { id: "note-1", title: "First", backend: null, config: STORED_CONFIG, permissions: ["read", "write"] };
const ONCE = "Its implementation cannot run until another is chosen. Once saved, the choice cannot be changed.";
const UNCHANGEABLE = "Its implementation cannot run. It cannot be changed here.";

type Choices = (variables: Record<string, unknown> | undefined) => Promise<{ data?: unknown }>;
const answer: Choices = async () => ({ data: { impl_choices: [
  { key: "local", category: "", defaults: { config: CARRIED }, config_schema: null },
  { key: "stub", category: "", defaults: { config: STORED_CONFIG }, config_schema: null },
] } });

function fixture(stored: Row, options: {
  fields?: FieldDescriptor[]; meta?: Record<string, unknown>; choices?: Choices;
  update?: (variables: Row) => Promise<{ data: Row }>;
} = {}) {
  const server = { row: { ...stored } as Row };
  const getOne = vi.fn(async () => ({ data: server.row }));
  const custom = vi.fn(async ({ meta }: { meta?: { gqlVariables?: Record<string, unknown> } }) =>
    (options.choices ?? answer)(meta?.gqlVariables));
  const update = vi.fn(async ({ variables }: { variables: Row }) => {
    if (options.update) return options.update(variables);
    server.row = { ...server.row, ...variables, backend: String(variables.backend ?? server.row.backend).toUpperCase() };
    return { data: server.row };
  });
  const resource = testDataResource("notes.Note", {
    fields: [title, { ...backend, ...options.meta }, config], implFields: ["backend"],
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const { Provider, clearClients } = createUiTestProviders({ resources: [resource], apiUrl: "test://repair", router: true });
  render(<Provider dataProvider={{ getOne, custom, update }} queryClient={queryClient}><ModalsHost><ToastProvider>
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets, containers: containersFromChildren(FORM_CONTAINERS, {}) }}>
      <FormView resource="notes.Note" id="note-1" fields={options.fields ?? PAGE} />
    </AppRuntimeProvider>
  </ToastProvider></ModalsHost></Provider>);
  const recordReads = () => custom.mock.calls.filter(([request]) => request?.meta?.gqlVariables?.record != null);
  return { server, custom, update, queryClient, clearClients, recordReads };
}

async function pick(label: string) {
  await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
  fireEvent.click(await screen.findByRole("combobox", { name: "Backend" }));
  const option = await screen.findByRole("option", { name: label });
  fireEvent.pointerDown(option, { pointerType: "mouse" });
  fireEvent.click(option);
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Backend" }).textContent).toContain(label));
}
const save = () => fireEvent.click(screen.getByRole("button", { name: "Save" }));
const settle = () => new Promise((done) => setTimeout(done, 50));
afterEach(cleanup);

test("a retired create-only selector is chosen once more, twice before saving, and saves with the config it carries over", async () => {
  const f = fixture(RETIRED);

  await waitFor(() => expect(f.recordReads().length).toBe(1));
  expect(f.recordReads()[0]?.[0]?.meta?.gqlVariables).toEqual({ model: "notes.Note", field: "backend", record: "note-1" });
  expect(await screen.findByText(ONCE)).toBeTruthy();
  // Where the field takes edits its control is the one stop: the mark is no second Tab stop.
  expect(screen.getByLabelText("Not registered: Backend").getAttribute("tabindex")).toBeNull();
  // A pick does not lock it: another may be chosen before saving.
  await pick("Stub");
  await pick("Local");
  save();

  await waitFor(() => expect(f.update).toHaveBeenCalledOnce());
  expect(f.update.mock.calls[0]?.[0]?.variables).toEqual({ backend: "LOCAL", config: CARRIED });
  await waitFor(() => expect(screen.queryByRole("combobox", { name: "Backend" })).toBeNull());
  f.clearClients();
});

test("a registered key's selector stays locked and its record is never asked what a choice would set", async () => {
  const f = fixture({ ...RETIRED, backend: "LOCAL", config: CARRIED });

  await waitFor(() => expect(screen.getAllByText("First").length).toBeGreaterThan(0));
  await settle();
  expect(screen.queryByRole("combobox", { name: "Backend" })).toBeNull();
  expect(f.recordReads()).toEqual([]);
  f.clearClients();
});

test("while the record's answer is pending the selector is loading and takes no pick, then takes one when it comes", async () => {
  let release: () => void = () => undefined;
  const late = new Promise<void>((done) => { release = done; });
  const f = fixture(RETIRED, { choices: async (variables) => { if (variables?.record != null) await late; return answer(variables); } });

  const control = await screen.findByRole("combobox", { name: "Backend" });
  await waitFor(() => expect(f.recordReads().length).toBe(1));
  expect(control.getAttribute("aria-busy")).toBe("true");
  expect(control.hasAttribute("disabled") || control.getAttribute("aria-disabled") === "true").toBe(true);
  expect(screen.queryByText(UNCHANGEABLE)).toBeNull();
  await act(async () => { release(); });
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Backend" }).getAttribute("aria-busy")).not.toBe("true"));
  await pick("Local");
  save();
  await waitFor(() => expect(f.update).toHaveBeenCalledOnce());
  expect(f.update.mock.calls[0]?.[0]?.variables).toEqual({ backend: "LOCAL", config: CARRIED });
  f.clearClients();
});

test("a refused answer keeps the selector read-only with the server's words under it, never 'cannot be changed here'", async () => {
  const refusal = "Note 'note-1' was not found.";
  const f = fixture(RETIRED, { choices: async (variables) => { if (variables?.record != null) throw new Error(refusal); return answer(variables); } });

  await screen.findByLabelText("Not registered: Backend");
  await waitFor(() => expect(f.recordReads().length).toBe(1));
  await waitFor(() => expect(screen.getAllByText(refusal).length).toBeGreaterThan(0));
  expect(screen.queryByRole("combobox", { name: "Backend" })).toBeNull();
  expect(screen.queryByText(UNCHANGEABLE)).toBeNull();
  f.clearClients();
});

test("a pick stays the save's while a save is pending: the selector takes no second pick, and after the answer it is locked", async () => {
  let respond: (row: { data: Row }) => void = () => undefined;
  const f = fixture(RETIRED, { update: () => new Promise((done) => { respond = done; }) });

  await waitFor(() => expect(f.recordReads().length).toBe(1));
  await pick("Local");
  save();
  await waitFor(() => expect(f.update).toHaveBeenCalledOnce());
  const during = screen.getByRole("combobox", { name: "Backend" });
  expect(during.hasAttribute("disabled") || during.getAttribute("aria-disabled") === "true").toBe(true);
  f.server.row = { ...RETIRED, backend: "LOCAL", config: CARRIED };
  await act(async () => { respond({ data: f.server.row }); });
  await waitFor(() => expect(screen.queryByRole("combobox", { name: "Backend" })).toBeNull());
  expect(f.update).toHaveBeenCalledOnce();
  expect(f.update.mock.calls[0]?.[0]?.variables).toEqual({ backend: "LOCAL", config: CARRIED });
  f.clearClients();
});

test("a re-read that finds the row repaired elsewhere keeps the pick in the save, so the server refuses it in words", async () => {
  const f = fixture(RETIRED, { update: async (variables) => ({ data: { ...f.server.row, ...variables } }) });

  await waitFor(() => expect(f.recordReads().length).toBe(1));
  await pick("Local");
  f.server.row = { ...RETIRED, backend: "STUB" };
  await act(async () => { await f.queryClient.invalidateQueries(); });
  await settle();
  // The lock reads the record the draft was made against: the pick is still offered and still sent with its config.
  expect(screen.getByRole("combobox", { name: "Backend" }).textContent).toContain("Local");
  save();
  await waitFor(() => expect(f.update).toHaveBeenCalledOnce());
  expect(f.update.mock.calls[0]?.[0]?.variables).toEqual({ backend: "LOCAL", config: CARRIED });
  f.clearClients();
});

test("a pick whose read is refused on a re-read still leaves with its selector, and the server decides", async () => {
  let refused = false;
  const f = fixture(RETIRED, { choices: async (variables) => {
    if (variables?.record != null && refused) throw new Error("Implementation selection is create-only.");
    return answer(variables);
  } });

  await waitFor(() => expect(f.recordReads().length).toBe(1));
  await pick("Local");
  refused = true;
  await act(async () => { await f.queryClient.invalidateQueries(); });
  await waitFor(() => expect(screen.queryByRole("combobox", { name: "Backend" })).toBeNull());
  save();
  await waitFor(() => expect(f.update).toHaveBeenCalledOnce());
  expect(f.update.mock.calls[0]?.[0]?.variables).toEqual({ backend: "LOCAL", config: CARRIED });
  f.clearClients();
});

test("a page's own createOnly on a selector the model does not make create-only stays locked, with no repair read", async () => {
  const f = fixture(RETIRED, { meta: { createOnly: undefined },
    fields: [{ name: "title", title: true }, { name: "backend", label: "Backend", createOnly: true }, { name: "config", label: "Config" }] });

  await waitFor(() => expect(screen.getAllByText("First").length).toBeGreaterThan(0));
  await settle();
  expect(screen.queryByRole("combobox", { name: "Backend" })).toBeNull();
  expect(f.recordReads()).toEqual([]);
  f.clearClients();
});

test("a form whose only editable field is the repair's selector takes edits", async () => {
  const f = fixture(RETIRED, { fields: [{ name: "title", title: true, readOnly: true }, { name: "backend", label: "Backend" }] });

  expect(await screen.findByRole("combobox", { name: "Backend" })).toBeTruthy();
  await waitFor(() => expect(f.recordReads().length).toBe(1));
  f.clearClients();
});

test("a person who cannot write the record is never asked about it and is offered no repair", async () => {
  const f = fixture({ ...RETIRED, permissions: ["read"] });

  await screen.findByLabelText("Not registered: Backend");
  await settle();
  expect(screen.queryByRole("combobox", { name: "Backend" })).toBeNull();
  expect(f.recordReads()).toEqual([]);
  f.clearClients();
});

test("a create-only selector bound through BoundDescriptorField on a registered key takes the record form's lock", async () => {
  const row = { ...RETIRED, backend: "LOCAL", config: CARRIED };
  const update = vi.fn(async ({ variables }: { variables: Row }) => ({ data: { ...row, ...variables } }));
  const resource = testDataResource("notes.Note", { fields: [title, backend, config], implFields: ["backend"] });
  const { Provider, clearClients } = createUiTestProviders({ resources: [resource], apiUrl: "test://bound", router: true,
    queryClientConfig: { defaultOptions: { queries: { retry: false }, mutations: { retry: false } } } });
  render(<Provider dataProvider={{ getOne: vi.fn(async () => ({ data: row })), custom: vi.fn(async () => ({ data: {} })), update }}>
    <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets, containers: containersFromChildren(FORM_CONTAINERS, {}) }}>
      <FormView resource="notes.Note" id="note-1" fields={[{ name: "title", title: true }, { name: "backend", hidden: true }]}
        formExtras={({ form }) => <BoundDescriptorField form={form} resource="notes.Note"
          field={{ name: "backend", label: "Backend", widget: "select", options: [{ value: "LOCAL", label: "Local" }, { value: "STUB", label: "Stub" }] }} />} />
    </AppRuntimeProvider></ToastProvider></ModalsHost></Provider>);

  await waitFor(() => expect(screen.getAllByText("First").length).toBeGreaterThan(0));
  await settle();
  expect(screen.queryByRole("combobox", { name: "Backend" })).toBeNull();
  clearClients();
});

test("the form offers the choice on the same three rows the server allows it on", () => {
  // The read contract joins the halves (the server's in test_integrate_graphql): a retired key reads null.
  const metadata = schemaFieldMetadataFromDataResources([testDataResource("notes.Note", {
    fields: [title, backend, config], implFields: ["backend"],
  })]);
  const model = Object.values(metadata.types)[0] ?? null;
  const offered = (stored: Row) => !withModeLockedFields([{ name: "backend", createOnly: true }], false,
    (field) => repairChoosable(model, stored, field.name))[0]?.readOnly;
  expect([
    offered({ id: "note-1", backend: null }),
    offered({ id: "note-1", backend: "LOCAL" }),
    offered({ id: "note-1", backend: "STUB" }),
  ]).toEqual([true, false, false]);
});
