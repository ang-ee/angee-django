// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRootRoute, createRouter, createMemoryHistory, RouterContextProvider } from "@tanstack/react-router";
import { schemaFieldMetadataFromDataResources, type DataResourceMetadata, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { afterEach, expect, test, vi } from "vitest";
import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { FormView } from "./FormView";
import type { FieldDescriptor } from "../page";
import { readNextSave as readNextSaveOf } from "./next-save.test-support";
import { useFormViewSave, type FormNextSave, type FormSubmit, type FormViewSaveSurface } from "./use-form-view-save";

afterEach(() => { cleanup(); });

// `kind` resets the `detail` it hides; `stamp` is a field the save cannot write.
const fields: readonly FieldDescriptor[] = [
  { name: "title", label: "Title" },
  { name: "body", label: "Body" },
  { name: "kind", label: "Kind", prefill: (value) => (value === "b" ? { detail: "reset" } : null) },
  { name: "detail", label: "Detail", showWhen: (values) => values.kind !== "b" },
  { name: "count", label: "Count", widget: "integer" },
  { name: "stamp", label: "Stamp" },
];
// A structured field beside its dotted members, each sent as its wire value.
const structuredFields: readonly FieldDescriptor[] = [
  { name: "title", label: "Title" },
  { name: "config", label: "Config", widget: "json", hidden: true },
  { name: "config.timeout", label: "Timeout", widget: "integer", nullable: true, omittable: true },
  { name: "config.owner", label: "Owner", widget: "many2one" },
];

function resourceFor(options: { revision?: boolean } = {}): DataResourceMetadata {
  const base = testDataResource("notes.Note", {
    createFields: ["title", "body", "kind", "detail", "count"],
    fields: [...fields.map((field) => field.name), "config"].map((name) => ({
      name, kind: "scalar" as const, scalar: name === "count" ? "Int" : name === "config" ? "JSON" : "String", readable: true,
      filterable: false, sortable: false, aggregatable: false, groupable: false,
      creatable: name !== "stamp", updatable: name !== "stamp", requiredOnCreate: false,
    })),
  });
  return options.revision ? {
    ...base,
    updateArguments: [{ name: "expected_revision", type: "Int" }],
    fields: [...(base.fields ?? []), { name: "revision", kind: "scalar" as const, scalar: "Int", readable: true,
      creatable: false, updatable: false, aggregatable: false, requiredOnCreate: false }],
  } : base;
}

async function mount(options: {
  id?: string | null; submit?: FormSubmit; revision?: boolean; defaultValues?: Row; fields?: readonly FieldDescriptor[];
  readOnly?: boolean; reader?: (form: FormViewSaveSurface) => void;
} = {}) {
  const resource = resourceFor(options);
  const { Provider, createClient } = createUiTestProviders({ resources: [resource], apiUrl: "test://notes" });
  let record: Row = {
    id: "note-1", title: "First", body: "Body", kind: "a", detail: "custom", count: 3, stamp: "s",
    config: { timeout: 30, owner: { id: "usr_1", display_name: "One" }, note: "kept" },
    ...(options.revision ? { revision: 4 } : {}),
  };
  const update = vi.fn(async ({ variables }: { variables?: unknown }) => { record = { ...record, ...(variables as Row) }; return { data: record }; });
  const create = vi.fn(async ({ variables }: { variables?: unknown }) => ({ data: { id: "note-2", ...(variables as Row) } }));
  const provider = { getOne: vi.fn(async () => ({ data: record })), update, create, getList: vi.fn(async () => ({ data: [], total: 0 })) } satisfies RefineTestDataProvider;
  const client = createClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  // Every surface the form handed out, the latest last: a reader may keep an earlier one's `nextSave`.
  const surfaces: FormViewSaveSurface[] = [];
  function Reader({ form }: { form: FormViewSaveSurface }) { options.reader?.(form); return null; }
  const id = options.id === undefined ? "note-1" : options.id;
  render(<Provider resources={[resource]} dataProvider={provider} queryClient={client}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <FormView resource="notes.Note" id={id} fields={options.fields ?? fields} submit={options.submit} defaultValues={options.defaultValues}
        readOnly={options.readOnly} headerExtras={(context) => { surfaces.push(context.form); return <Reader form={context.form} />; }} />
    </AppRuntimeProvider></ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>);
  await waitFor(() => expect(surfaces.length).toBeGreaterThan(0));
  if (id !== null) await waitFor(() => expect(surfaces.at(-1)!.form.getValues("title")).toBe("First"));
  return { surface: () => surfaces.at(-1)!, surfaces, update, create };
}

function type(label: string, value: string) {
  fireEvent.change(screen.getByRole("textbox", { name: label }), { target: { value } });
}

/** `readNextSave` over this fixture's form and the requests its provider received. */
function readNextSave(f: Awaited<ReturnType<typeof mount>>): FormNextSave {
  return readNextSaveOf(f.surface, () => f.update.mock.calls.length + f.create.mock.calls.length);
}

test("an edit: the changed field the save can write, and nothing else", async () => {
  const f = await mount();
  type("Title", "Second");
  const next = readNextSave(f);
  expect(next).toEqual({ data: { title: "Second" }, lines: null });
  await act(async () => f.surface().submitForm());
  expect(f.update.mock.calls[0]![0].variables).toEqual(next.data);
});

test("a create: its seeds and typed values, not a blank number, nor a seeded field the create cannot write", async () => {
  const f = await mount({ id: null, defaultValues: { body: "Seeded", stamp: "seeded" } });
  type("Title", "New");
  const next = readNextSave(f);
  expect(next).toEqual({ data: { title: "New", body: "Seeded" }, lines: null });
  await act(async () => f.surface().submitForm());
  expect(f.create.mock.calls[0]![0].variables).toEqual(next.data);
});

test("an edit that hides a field its preset resets carries the reset", async () => {
  const f = await mount();
  type("Kind", "b");
  const next = readNextSave(f);
  expect(next.data).toEqual({ kind: "b", detail: "reset" });
  await act(async () => f.surface().submitForm());
  expect(f.update.mock.calls[0]![0].variables).toEqual(next.data);
});

test("an edit leaves out a changed field the save cannot write", async () => {
  const f = await mount();
  type("Title", "Second");
  act(() => f.surface().form.setValue("stamp", "changed", { shouldDirty: true }));
  const next = readNextSave(f);
  expect(next.data).toEqual({ title: "Second" });
  await act(async () => f.surface().submitForm());
  expect(f.update.mock.calls[0]![0].variables).toEqual(next.data);
});

test("an edit changed only where the save cannot write reads as writing nothing, and Save sends that", async () => {
  const f = await mount();
  act(() => f.surface().form.setValue("stamp", "changed", { shouldDirty: true }));
  const next = readNextSave(f);
  expect(next).toEqual({ data: {}, lines: null });
  await act(async () => f.surface().submitForm());
  expect(f.update.mock.calls[0]![0].variables).toEqual(next.data);
});

test("the form's own submit owner receives what nextSave reads", async () => {
  const submit = vi.fn<FormSubmit>(async (data) => ({ status: "ok" as const, data: { id: "note-1", ...data } }));
  const f = await mount({ submit });
  type("Body", "Owned");
  const next = readNextSave(f);
  await act(async () => f.surface().submitForm());
  expect(submit.mock.calls[0]![0]).toEqual(next.data);
  expect(submit.mock.calls[0]![1].lines).toEqual(next.lines);
});

test("a structured field and its dotted members read as their wire values and leave the form's own", async () => {
  const f = await mount({ fields: structuredFields });
  const timeout = await screen.findByLabelText("Timeout") as HTMLInputElement;
  await waitFor(() => expect(timeout.value).toBe("30"));
  fireEvent.change(timeout, { target: { value: "" } });
  const next = readNextSave(f);
  expect(next.data).toEqual({ config: { timeout: null, owner: "usr_1", note: "kept" } });
  expect(f.surface().form.getValues("config")).toEqual({ timeout: "", owner: { id: "usr_1", display_name: "One" }, note: "kept" });
  await act(async () => f.surface().submitForm());
  expect(f.update.mock.calls[0]![0].variables).toEqual(next.data);
  // Save's own copy left the form's values as they were, so the saved form is clean.
  await waitFor(() => expect(f.surface().formIsDirty).toBe(false));
});

test("root arguments stay out of what nextSave reads", async () => {
  const f = await mount({ revision: true });
  type("Body", "Revised");
  const next = readNextSave(f);
  expect(next.data).toEqual({ body: "Revised" });
  await act(async () => f.surface().submitForm());
  expect(f.update.mock.calls[0]![0].variables).toEqual(next.data);
});

test("after a first save, the next one reads only what changed since", async () => {
  const f = await mount();
  type("Title", "Second");
  await act(async () => f.surface().submitForm());
  await waitFor(() => expect(f.surface().formIsDirty).toBe(false));
  type("Body", "Later");
  const next = readNextSave(f);
  expect(next.data).toEqual({ body: "Later" });
  await act(async () => f.surface().submitForm());
  expect(f.update.mock.calls[1]![0].variables).toEqual(next.data);
});

test("a nextSave kept from before a save reads, after a later edit, what Save then sends", async () => {
  const f = await mount();
  const kept = f.surface().nextSave;
  type("Title", "Second");
  await act(async () => f.surface().submitForm());
  await waitFor(() => expect(f.surface().formIsDirty).toBe(false));
  type("Body", "Later");
  const read = kept();
  expect(read).toEqual(f.surface().nextSave());
  await act(async () => f.surface().submitForm());
  expect(f.update.mock.calls[1]![0].variables).toEqual(read.data);
  expect(read.data).toEqual({ body: "Later" });
});

test("a nextSave kept from the form's first render reads what Save then sends", async () => {
  const f = await mount();
  const first = f.surfaces[0]!.nextSave;
  type("Title", "Second");
  const read = first();
  await act(async () => f.surface().submitForm());
  expect(f.update.mock.calls[0]![0].variables).toEqual(read.data);
  expect(read.data).toEqual({ title: "Second" });
});

test("a nextSave kept across a discard reads, after a later edit, what Save then sends", async () => {
  const f = await mount();
  type("Title", "Second");
  const kept = f.surface().nextSave;
  act(() => f.surface().discardChanges());
  await waitFor(() => expect(f.surface().formIsDirty).toBe(false));
  type("Body", "After discard");
  const read = kept();
  await act(async () => f.surface().submitForm());
  expect(f.update.mock.calls[0]![0].variables).toEqual(read.data);
  expect(read.data).toEqual({ body: "After discard" });
});

test("a reader whose callback is made once reads after each pause what Save then sends", async () => {
  const reads: FormNextSave[] = [];
  let schedule: (() => void) | undefined;
  function useReadAfterPause(form: FormViewSaveSurface): () => void {
    const timer = React.useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    // Made once: the form's first `nextSave`, kept for every read.
    const [read] = React.useState(() => () => {
      clearTimeout(timer.current);
      timer.current = setTimeout(() => reads.push(form.nextSave()), 20);
    });
    return read;
  }
  const f = await mount({ reader: (form) => {
    // eslint-disable-next-line react-hooks/rules-of-hooks -- the reader is a component's body
    schedule = useReadAfterPause(form);
  } });
  type("Title", "Second");
  schedule!();
  await waitFor(() => expect(reads).toHaveLength(1));
  await act(async () => f.surface().submitForm());
  await waitFor(() => expect(f.surface().formIsDirty).toBe(false));
  type("Body", "Later");
  schedule!();
  await waitFor(() => expect(reads).toHaveLength(2));
  await act(async () => f.surface().submitForm());
  expect(reads.map((read) => read.data)).toEqual(f.update.mock.calls.map(([call]) => call.variables));
  expect(reads.map((read) => read.data)).toEqual([{ title: "Second" }, { body: "Later" }]);
});

test("a read-only form reads as writing nothing, and its read does not throw where its Save does", async () => {
  const f = await mount({ readOnly: true });
  expect(f.surface().formReadOnly).toBe(true);
  expect(readNextSave(f)).toEqual({ data: {}, lines: null });
  act(() => f.surface().form.setValue("title", "Second", { shouldDirty: true }));
  expect(readNextSave(f)).toEqual({ data: { title: "Second" }, lines: null });
  await act(async () => { await f.surface().submitForm().catch(() => undefined); });
  expect(f.update).not.toHaveBeenCalled();
});

test("a read before the record arrives reads as writing nothing, and does not throw", async () => {
  const resource = resourceFor();
  const { Provider, createClient } = createUiTestProviders({ resources: [resource], apiUrl: "test://notes" });
  const getOne = vi.fn(() => new Promise<{ data: Row }>(() => undefined));
  const update = vi.fn();
  const provider = { getOne, update, create: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })) } satisfies RefineTestDataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const model = schemaFieldMetadataFromDataResources([resource]).labels["notes.Note"]!;
  let surface!: FormViewSaveSurface;
  function Probe() {
    surface = useFormViewSave({
      resource: "notes.Note", id: "note-1", isCreate: false, dataResource: resource, modelMetadata: model,
      formFields: fields, fieldByName: new Map(fields.map((field) => [field.name, field])), refineFields: ["id", ...fields.map((field) => field.name)],
      t: (key) => key,
    });
    return null;
  }
  render(<Provider resources={[resource]} dataProvider={provider} queryClient={createClient()}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <Probe />
    </AppRuntimeProvider></ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>);
  await waitFor(() => expect(getOne).toHaveBeenCalled());
  expect(surface.displayRecord).toBeNull();
  expect(surface.formReadOnly).toBe(true);
  expect(readNextSaveOf(() => surface, () => update.mock.calls.length)).toEqual({ data: {}, lines: null });
});
