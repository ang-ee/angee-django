// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine, type DataProvider } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { createRootRoute, createRouter, createMemoryHistory, RouterContextProvider } from "@tanstack/react-router";
import { Controller } from "react-hook-form";
import { refineResourcesFromDataResources, type ModelMetadata, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { ModalsHost, ToastProvider } from "../../feedback";
import { useFormViewSave, type FormSubmit, type FormViewSaveSurface } from "./use-form-view-save";
import type { FieldDescriptor } from "../page";

const fields: readonly FieldDescriptor[] = [
  { name: "title", label: "Title" }, { name: "body", label: "Body" },
  { name: "deadline", label: "Deadline", showWhen: (values) => values.title === "Scheduled" },
];
const refineFields = ["id", "title", "body", "deadline"];
const fieldByName = new Map(fields.map((field) => [field.name, field]));
const resource = testDataResource("notes.Note");
const model: ModelMetadata = {
  typeName: "NoteType", resource,
  fields: Object.fromEntries(fields.map((field) => [field.name, { name: field.name, kind: "scalar", scalar: "String" }])),
  rootFields: { createFields: ["title", "body", "deadline"], requiredCreateFields: ["deadline"] },
};
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; });

async function fixture(options: { id?: string | null; submit?: FormSubmit; mountedFields?: readonly string[] } = {}) {
  let record: Row = { id: options.id ?? "note-1", title: "First", body: "Original body", deadline: "" };
  const onSaved = vi.fn();
  const getOne = vi.fn(async () => ({ data: record }));
  const update = vi.fn(async ({ variables }: { variables?: unknown }) => {
    record = { ...record, ...(variables as Row) };
    return { data: record };
  });
  const provider = { getApiUrl: () => "test://notes", getOne, update, create: update, getList: vi.fn(async () => ({ data: [], total: 0 })), deleteOne: vi.fn() } as DataProvider;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  let surface!: FormViewSaveSurface;
  const id = options.id === undefined ? "note-1" : options.id;
  function Probe({ recordId, mountedFields, viewFields }: { recordId: string | null; mountedFields: readonly string[]; viewFields: readonly FieldDescriptor[] }) {
    surface = useFormViewSave({
      resource: "notes.Note", id: recordId, isCreate: recordId === null,
      dataResource: resource, modelMetadata: model, formFields: viewFields, fieldByName, refineFields,
      submit: options.submit, onSaved, t: (key) => key,
    });
    return <>{mountedFields.map((name) => <Controller key={name} name={name} control={surface.form.control} render={({ field }) => (
      <input aria-label={name} value={String(field.value ?? "")} onChange={field.onChange} />
    )} />)}</>;
  }
  function Tree({ recordId = id, mountedFields = options.mountedFields ?? ["title", "body"], viewFields = fields }: { recordId?: string | null; mountedFields?: readonly string[]; viewFields?: readonly FieldDescriptor[] }) {
    return <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>
      <RouterContextProvider router={router}><ModalsHost><ToastProvider>
        <Probe key={recordId ?? "create"} recordId={recordId} mountedFields={mountedFields} viewFields={viewFields} />
      </ToastProvider></ModalsHost></RouterContextProvider>
    </Refine>;
  }
  const view = render(<Tree />);
  if (id !== null) await waitFor(() => expect(surface?.form.getValues("title")).toBe("First"));
  return { surface: () => surface, onSaved, getOne, update, client, setRecord: (next: Row) => { record = next; }, rerender: (props: Parameters<typeof Tree>[0]) => view.rerender(<Tree {...props} />) };
}

function edit(name: string, value: string) { fireEvent.change(screen.getByLabelText(name), { target: { value } }); }

test("dirty values survive same-record refresh, late fields mount from the native baseline, and discard uses that baseline", async () => {
  const f = await fixture({ mountedFields: ["title"] });
  edit("title", "Local edit");
  f.setRecord({ id: "note-1", title: "Remote edit", body: "Fresh body" });
  await act(async () => f.surface().reload());
  await waitFor(() => expect(f.surface().form.getValues("body")).toBe("Fresh body"));
  expect(f.surface().form.getValues("title")).toBe("Local edit");
  expect(f.surface().formIsDirty).toBe(true);
  f.rerender({ mountedFields: ["title", "body"] });
  expect((screen.getByLabelText("body") as HTMLInputElement).value).toBe("Fresh body");
  act(() => f.surface().discardChanges());
  expect(f.surface().form.getValues()).toMatchObject({ title: "Remote edit", body: "Fresh body" });
  expect(f.surface().formIsDirty).toBe(false);
});

test("full native Refine saves submit only dirty fields and establish a clean baseline", async () => {
  const f = await fixture();
  edit("title", "Saved title");
  await act(async () => f.surface().submitForm());
  expect(f.update).toHaveBeenCalledWith(expect.objectContaining({ id: "note-1", variables: { title: "Saved title" } }));
  await waitFor(() => expect(f.surface().formIsDirty).toBe(false));
  expect(f.surface().displayRecord?.title).toBe("Saved title");
  expect(f.surface().form.formState.dirtyFields).toEqual({});
});

test("accepted patches update the real detail cache and displayed record without a competing patched-record state", async () => {
  const f = await fixture();
  act(() => f.surface().patchRecord({ title: "Patched" }));
  await waitFor(() => expect(f.surface().displayRecord?.title).toBe("Patched"));
  expect((screen.getByLabelText("title") as HTMLInputElement).value).toBe("Patched");
  expect(f.update).not.toHaveBeenCalled();
  expect(f.getOne).toHaveBeenCalledTimes(1);
});

test("partial custom saves preserve omitted fields and refetch canonical detail data", async () => {
  let f!: Awaited<ReturnType<typeof fixture>>;
  const submit = vi.fn(async () => {
    f.setRecord({ id: "note-1", title: "Canonical title", body: "Original body" });
    return { id: "note-1" };
  });
  f = await fixture({ submit });
  edit("title", "Canonical title");
  await act(async () => f.surface().submitForm());
  await waitFor(() => expect(f.surface().displayRecord?.title).toBe("Canonical title"));
  expect(f.surface().form.getValues("body")).toBe("Original body");
  expect(f.surface().formIsDirty).toBe(false);
  expect(f.getOne.mock.calls.length).toBeGreaterThan(1);
});

test("a no-row custom response retains dirty state and does not invent a successful save", async () => {
  const f = await fixture({ submit: async () => null });
  edit("title", "Unsaved");
  await act(async () => f.surface().submitForm());
  expect(f.surface().formIsDirty).toBe(true);
  expect(f.onSaved).not.toHaveBeenCalled();
});

test("native validation includes unmounted required fields and respects visibility", async () => {
  const f = await fixture({ id: null });
  edit("title", "Scheduled");
  await act(async () => f.surface().submitForm());
  expect(f.update).not.toHaveBeenCalled();
  expect(f.surface().form.getFieldState("deadline").error?.type).toBe("required");
  edit("title", "Unscheduled");
  await act(async () => f.surface().submitForm());
  expect(f.update).toHaveBeenCalledTimes(1);
});

test("nested server errors and root failures share the native form store", async () => {
  const f = await fixture({ submit: async () => { throw { graphQLErrors: [{ extensions: { validationErrors: { "lines.0.title": ["Invalid line"], title: ["Invalid title"] }, formErrors: ["Cannot save"] } }] }; } });
  edit("title", "Rejected");
  await act(async () => f.surface().submitForm());
  expect(f.surface().form.getFieldState("lines.0.title").error?.message).toBe("Invalid line");
  expect(f.surface().saveError).toBe("Cannot save");
  expect(f.surface().serverFieldErrors).toMatchObject({ "lines.0.title": ["Invalid line"] });
  act(() => f.surface().clearServerFieldError("title"));
  expect(f.surface().form.getFieldState("title").error).toBeUndefined();
});

test("duplicate submits are ignored and a previous record save cannot reset or notify the new form", async () => {
  let resolve!: (value: Row) => void;
  const submit = vi.fn(() => new Promise<Row>((done) => { resolve = done; }));
  const f = await fixture({ submit });
  edit("title", "Old edit");
  let first!: Promise<void>;
  act(() => { first = f.surface().submitForm(); void f.surface().submitForm(); });
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  f.setRecord({ id: "note-2", title: "Second", body: "Second body" });
  f.rerender({ recordId: "note-2" });
  await waitFor(() => expect(f.surface().form.getValues("title")).toBe("Second"));
  edit("title", "New edit");
  await act(async () => { resolve({ id: "note-1", title: "Old saved" }); await first; });
  expect(f.surface().form.getValues("title")).toBe("New edit");
  expect(f.surface().formIsDirty).toBe(true);
  expect(f.onSaved).not.toHaveBeenCalled();
});


test("same-record refresh during custom submit cannot clear the transport pending state", async () => {
  let resolve!: (value: Row) => void;
  const submit = vi.fn(() => new Promise<Row>((done) => { resolve = done; }));
  const f = await fixture({ submit });
  edit("title", "Submitting");
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.surface().pending).toBe(true));
  f.setRecord({ id: "note-1", title: "Remote", body: "Fresh" });
  await act(async () => f.surface().reload());
  await waitFor(() => expect(f.surface().form.getValues("body")).toBe("Fresh"));
  expect(f.surface().pending).toBe(true);
  await act(async () => { resolve({ id: "note-1", title: "Submitting" }); await saving; });
  await waitFor(() => expect(f.surface().pending).toBe(false));
  expect(f.surface().form.getValues("body")).toBe("Fresh");
});

test("a partial response changing another selected field preserves omitted submitted values until canonical reload completes", async () => {
  const f = await fixture({ submit: async () => ({ id: "note-1", body: "Normalized body" }) });
  let resolve!: (value: { data: Row }) => void;
  f.getOne.mockImplementationOnce(() => new Promise<{ data: Row }>((done) => { resolve = done; }));
  edit("title", "Accepted title");
  await act(async () => f.surface().submitForm());
  await waitFor(() => expect(f.surface().form.getValues("body")).toBe("Normalized body"));
  expect(f.surface().form.getValues("title")).toBe("Accepted title");
  await act(async () => resolve({ data: { id: "note-1", title: "Accepted title", body: "Normalized body" } }));
});

test("a delayed custom save preserves later edits and rebases only the accepted submission", async () => {
  let resolve!: (value: Row) => void;
  const submit = vi.fn(() => new Promise<Row>((done) => { resolve = done; }));
  const f = await fixture({ submit });
  edit("title", "Submitted title");
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  edit("title", "Later title");
  edit("body", "Later body");
  const accepted = { id: "note-1", title: "Normalized title", body: "Original body", deadline: "" };
  f.setRecord(accepted);
  await act(async () => { resolve(accepted); await saving; });
  expect(f.surface().form.getValues()).toMatchObject({ title: "Later title", body: "Later body" });
  expect(f.surface().form.formState.defaultValues).toMatchObject({ title: accepted.title, body: accepted.body });
  expect(f.surface().form.formState.dirtyFields).toMatchObject({ title: true, body: true });
  expect(f.surface().formIsDirty).toBe(true);
  expect(f.onSaved).toHaveBeenCalledWith(accepted);
  act(() => f.surface().discardChanges());
  expect(f.surface().form.getValues()).toMatchObject({ title: "Normalized title", body: "Original body" });
  expect(f.surface().formIsDirty).toBe(false);
});

test("native Refine write acceptance keeps later edits available to the next save", async () => {
  const f = await fixture();
  let resolve!: (value: { data: Row }) => void;
  f.update.mockImplementationOnce(() => new Promise<{ data: Row }>((done) => { resolve = done; }));
  edit("title", "Submitted title");
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.update).toHaveBeenCalledTimes(1));
  edit("title", "Later title");
  const accepted = { id: "note-1", title: "Submitted title", body: "Original body", deadline: "" };
  f.setRecord(accepted);
  await act(async () => { resolve({ data: accepted }); await saving; });
  expect(f.surface().form.getValues("title")).toBe("Later title");
  expect(f.surface().formIsDirty).toBe(true);
  await act(async () => f.surface().submitForm());
  expect(f.update).toHaveBeenLastCalledWith(expect.objectContaining({ variables: { title: "Later title" } }));
  expect(f.surface().formIsDirty).toBe(false);
});

test("a change back to the old value during save stays dirty against the accepted submission", async () => {
  let resolve!: (value: Row) => void;
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }) });
  edit("title", "Submitted title");
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.surface().pending).toBe(true));
  edit("title", "First");
  const accepted = { id: "note-1", title: "Submitted title", body: "Original body", deadline: "" };
  f.setRecord(accepted);
  await act(async () => { resolve(accepted); await saving; });
  expect(f.surface().form.getValues("title")).toBe("First");
  expect(f.surface().form.getFieldState("title").isDirty).toBe(true);
});

test.each(["failure", "no row"])("later edits survive a delayed %s without advancing defaults", async (outcome) => {
  let resolve!: (value: Row | null) => void;
  let reject!: (reason: Error) => void;
  const f = await fixture({ submit: () => new Promise<Row | null>((done, fail) => { resolve = done; reject = fail; }) });
  edit("title", "Submitted title");
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.surface().pending).toBe(true));
  edit("title", "Later title");
  await act(async () => {
    if (outcome === "failure") reject(new Error("Temporary failure")); else resolve(null);
    await saving;
  });
  expect(f.surface().form.getValues("title")).toBe("Later title");
  expect(f.surface().form.formState.defaultValues?.title).toBe("First");
  expect(f.surface().formIsDirty).toBe(true);
  expect(f.onSaved).not.toHaveBeenCalled();
});

test("a delayed toolbar patch preserves a dirty draft while adopting clean response fields", async () => {
  let resolve!: (value: Row) => void;
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }) });
  edit("title", "Draft title");
  let saving!: Promise<Row | null>;
  act(() => { saving = f.surface().applyPatch({ body: "Patched body" }); });
  await waitFor(() => expect(f.surface().pending).toBe(true));
  edit("title", "Later draft");
  const accepted = { id: "note-1", title: "First", body: "Patched body", deadline: "" };
  f.setRecord(accepted);
  await act(async () => { resolve(accepted); await saving; });
  expect(f.surface().form.getValues()).toMatchObject({ title: "Later draft", body: "Patched body" });
  expect(f.surface().formIsDirty).toBe(true);
});

test("recreated field descriptors read the accepted native cache and retain later edits", async () => {
  const f = await fixture();
  edit("title", "Saved title");
  await act(async () => f.surface().submitForm());
  f.rerender({ viewFields: fields.map((field) => ({ ...field })) });
  expect(f.surface().form.getValues("title")).toBe("Saved title");
  expect(f.surface().formIsDirty).toBe(false);
  edit("title", "Later title");
  await waitFor(() => expect(f.surface().formIsDirty).toBe(true));
  f.rerender({ viewFields: fields.map((field) => ({ ...field })) });
  expect(f.surface().form.getValues("title")).toBe("Later title");
  expect(f.surface().formIsDirty).toBe(true);
});

test("a later edit equal to the canonical accepted value becomes clean", async () => {
  let resolve!: (value: Row) => void;
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }) });
  edit("title", "  Normalized title  ");
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.surface().pending).toBe(true));
  edit("title", "Normalized title");
  const accepted = { id: "note-1", title: "Normalized title", body: "Original body", deadline: "" };
  f.setRecord(accepted);
  await act(async () => { resolve(accepted); await saving; });
  expect(f.surface().form.getValues("title")).toBe("Normalized title");
  expect(f.surface().form.getFieldState("title").isDirty).toBe(false);
  expect(f.surface().formIsDirty).toBe(false);
});
