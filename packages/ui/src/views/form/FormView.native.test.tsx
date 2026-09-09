// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine, type DataProvider } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { createRootRoute, createRouter, createMemoryHistory, RouterContextProvider } from "@tanstack/react-router";
import { Controller, useForm } from "react-hook-form";
import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources, type ModelMetadata, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import { BoundDescriptorField, BoundFormValue } from "./BoundDescriptorField";
import { FormView } from "./FormView";
import {
  acknowledgeFormSubmit,
  useFormViewSave,
  type FormSubmit,
  type FormViewAcknowledgedSource,
  type FormViewSaveSurface,
} from "./use-form-view-save";
import type { MutationDialogField } from "./MutationDialog";
import type { FieldDescriptor, GroupDescriptor } from "../page";
import type { RecordTabDescriptor } from "./form-view-surface";

const fields: readonly FieldDescriptor[] = [
  { name: "title", label: "Title" }, { name: "body", label: "Body" },
  { name: "deadline", label: "Deadline", showWhen: (values) => values.title === "Scheduled" },
  { name: "note", label: "Note", nullable: true, omittable: true },
  { name: "settings", label: "Settings", kind: "object", nullable: true, omittable: true },
  { name: "summary", label: "Summary", omittable: true },
  {
    name: "parent",
    label: "Parent",
    kind: "relation",
    widget: "many2one",
    omittable: true,
  },
];
const refineFields = ["id", "title", "body", "deadline", "note", "settings", "summary", "parent"];
const fieldByName = new Map(fields.map((field) => [field.name, field]));
const resource = testDataResource("notes.Note", {
  createFields: ["title", "body", "deadline"],
  requiredCreateFields: ["deadline"],
  fields: fields.map((field) => ({
    name: field.name,
    kind: field.name === "parent" ? "relation" : "scalar",
    scalar: field.name === "parent" ? "ID" : "String",
    relationModelLabel: field.name === "parent" ? "notes.Note" : undefined,
    relationObject: field.name === "parent" ? true : undefined,
    readable: true,
    filterable: false, sortable: false, aggregatable: false, groupable: false,
    creatable: true, updatable: true, requiredOnCreate: field.name === "deadline",
  })),
});
const model: ModelMetadata = schemaFieldMetadataFromDataResources([resource]).labels["notes.Note"]!;
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; });

test("a domain controlled value uses FormView interaction ownership and remounts by full name", () => {
  const start = vi.fn(); const commit = vi.fn();
  function Harness({ name }: { name: string }) {
    const form = useForm({ defaultValues: { first: "one", second: "two" } });
    const surface = { form, startFieldInteraction: start, commitFieldInteraction: commit, clearServerFieldError: vi.fn() } as unknown as FormViewSaveSurface;
    return <BoundFormValue form={surface} name={name}>{({ value, onChange, onCommit }) => <input aria-label={name} value={String(value)} onChange={(event) => onChange(event.currentTarget.value)} onBlur={onCommit} />}</BoundFormValue>;
  }
  const view = render(<Harness name="first" />);
  fireEvent.change(screen.getByRole("textbox", { name: "first" }), { target: { value: "changed" } });
  fireEvent.blur(screen.getByRole("textbox", { name: "first" }));
  expect(start).toHaveBeenCalledWith("first"); expect(commit).toHaveBeenCalledWith("first");
  view.rerender(<Harness name="second" />);
  expect((screen.getByRole("textbox", { name: "second" }) as HTMLInputElement).value).toBe("two");
});

test("a domain controlled value preserves nested form error messages", async () => {
  function Harness() {
    const form = useForm({ defaultValues: { binding: { fields: { literal: null } } } });
    React.useEffect(() => {
      form.setError("binding.fields.literal", { message: "Nested value is required" });
    }, [form]);
    const surface = { form, startFieldInteraction: vi.fn(), commitFieldInteraction: vi.fn(), clearServerFieldError: vi.fn() } as unknown as FormViewSaveSurface;
    return <BoundFormValue form={surface} name="binding">{({ messages }) => <span>{messages.join(" ")}</span>}</BoundFormValue>;
  }
  render(<Harness />);
  expect(await screen.findByText("fields.literal: Nested value is required")).toBeTruthy();
});

test("a bound parent value follows native dotted child updates", async () => {
  function Harness() {
    const form = useForm({ defaultValues: { config: { interval_seconds: "" } } });
    const surface = {
      form,
      startFieldInteraction: vi.fn(),
      commitFieldInteraction: vi.fn(),
      clearServerFieldError: vi.fn(),
    } as unknown as FormViewSaveSurface;
    return <>
      <BoundFormValue form={surface} name="config">
        {({ value }) => <output>{JSON.stringify(value)}</output>}
      </BoundFormValue>
      <Controller
        control={form.control}
        name="config.interval_seconds"
        render={({ field }) => <input aria-label="Interval" {...field} />}
      />
    </>;
  }
  render(<Harness />);
  fireEvent.change(screen.getByRole("textbox", { name: "Interval" }), { target: { value: "3600" } });
  expect(await screen.findByText('{"interval_seconds":"3600"}')).toBeTruthy();
});

async function fixture(options: {
  readOnlyWhen?: (record: Row) => boolean;
  id?: string | null;
  submit?: FormSubmit;
  mountedFields?: readonly string[];
  presenceValues?: boolean;
  relationValues?: boolean;
  acknowledgedSource?: FormViewAcknowledgedSource;
  boundFields?: readonly { field: MutationDialogField; scope?: string; readOnly?: boolean }[];
  publicView?: boolean;
  onFieldInteractionStart?: (path: string) => void;
  onFieldInteractionCommit?: (path: string) => void;
  recordTabs?: readonly RecordTabDescriptor[];
  formExtras?: React.ComponentProps<typeof FormView>["formExtras"];
  recordExtras?: React.ComponentProps<typeof FormView>["recordExtras"];
  groups?: readonly GroupDescriptor[];
  title?: React.ComponentProps<typeof FormView>["title"];
} = {}) {
  let record: Row = {
    id: options.id ?? "note-1",
    title: "First",
    body: "Original body",
    deadline: "",
    ...(options.presenceValues ? { note: null, settings: null } : {}),
    ...(options.relationValues
      ? { parent: { id: "note-a", title: "Parent A" } }
      : {}),
  };
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
  function Probe({ recordId, mountedFields, viewFields, boundFields }: { recordId: string | null; mountedFields: readonly string[]; viewFields: readonly FieldDescriptor[]; boundFields?: readonly { field: MutationDialogField; scope?: string; readOnly?: boolean }[] }) {
    surface = useFormViewSave({
      resource: "notes.Note", id: recordId, isCreate: recordId === null,
      dataResource: resource, modelMetadata: model, formFields: viewFields, fieldByName, refineFields,
      submit: options.submit, readOnlyWhen: options.readOnlyWhen, onSaved, t: (key) => key,
      acknowledgedSource: options.acknowledgedSource,
      onFieldInteractionStart: options.onFieldInteractionStart,
      onFieldInteractionCommit: options.onFieldInteractionCommit,
    });
    return <>{mountedFields.map((name) => <Controller key={name} name={name} control={surface.form.control} render={({ field }) => (
      <input aria-label={name} value={String(field.value ?? "")} onChange={field.onChange} />
    )} />)}{boundFields?.map((bound, index) => (
      <BoundDescriptorField key={index} form={surface} resource="notes.Note" {...bound} />
    ))}</>;
  }
  function Tree({ recordId = id, mountedFields = options.mountedFields ?? ["title", "body"], viewFields = fields, boundFields = options.boundFields }: { recordId?: string | null; mountedFields?: readonly string[]; viewFields?: readonly FieldDescriptor[]; boundFields?: readonly { field: MutationDialogField; scope?: string; readOnly?: boolean }[] }) {
    return <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>
      <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        {options.publicView ? (
          <FormView
            resource="notes.Note"
            id={recordId}
            fields={viewFields}
            groups={options.groups}
            title={options.title}
            acknowledgedSource={options.acknowledgedSource}
            submit={options.submit}
            onFieldInteractionStart={options.onFieldInteractionStart}
            onFieldInteractionCommit={options.onFieldInteractionCommit}
            recordTabs={options.recordTabs}
            formExtras={options.formExtras}
            recordExtras={options.recordExtras}
            defaultRecordTab={options.recordTabs ? "activity" : undefined}
          />
        ) : (
          <Probe key={recordId ?? "create"} recordId={recordId} mountedFields={mountedFields} viewFields={viewFields} boundFields={boundFields} />
        )}
      </AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider>
    </Refine>;
  }
  const view = render(<Tree />);
  if (!options.publicView && id !== null && options.acknowledgedSource?.values !== null) {
    await waitFor(() => expect(surface?.form.getValues("title")).toBe("First"));
  }
  return { surface: () => surface, onSaved, getOne, update, client, setRecord: (next: Row) => { record = next; }, rerender: (props: Parameters<typeof Tree>[0]) => view.rerender(<Tree {...props} />) };
}

test("form extras receive the native create and edit form contexts", async () => {
  const extras = vi.fn((context: Parameters<NonNullable<React.ComponentProps<typeof FormView>["formExtras"]>>[0]) => (
    <span>{context.recordId ?? "creating"}</span>
  ));
  const f = await fixture({ id: null, publicView: true, formExtras: extras });
  expect((await screen.findByText("creating")).closest("form")).not.toBeNull();

  f.rerender({ recordId: "note-1" });
  expect((await screen.findAllByText("note-1")).length).toBeGreaterThan(0);
  expect(extras).toHaveBeenCalled();
});

test("composed field validation participates in the native submit resolver", async () => {
  const f = await fixture();
  const unregister = f.surface().registerFieldValidation(
    "title",
    (value) => value === "Blocked" ? "Title is blocked" : undefined,
  );

  fireEvent.change(screen.getByLabelText("title"), { target: { value: "Blocked" } });
  await act(async () => f.surface().submitForm());
  expect(f.update).not.toHaveBeenCalled();
  expect(f.surface().form.getFieldState("title").error?.message).toBe("Title is blocked");

  unregister();
  await act(async () => f.surface().submitForm());
  await waitFor(() => expect(f.update).toHaveBeenCalledTimes(1));
});

test("an editable metadata relation drops a stale expanded option when its id changes", async () => {
  await fixture({
    publicView: true,
    relationValues: true,
    recordExtras: (context) => <>
      <button type="button" onClick={() => context.form.form.setValue("parent", "note-b")}>Choose B</button>
      <button type="button" onClick={() => context.form.form.setValue("parent", undefined)}>Clear parent</button>
    </>,
  });
  const relation = await screen.findByRole("button", { name: /Parent/ });
  expect(relation.textContent).toContain("note-a");
  fireEvent.click(screen.getByRole("button", { name: "Choose B" }));
  await waitFor(() => expect(relation.textContent).toContain("note-b"));
  fireEvent.click(screen.getByRole("button", { name: "Clear parent" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: /Parent/ })).toBeNull());
});

test("a focused field opens its collapsed group and a live title uses form context", async () => {
  await fixture({
    id: "note-1",
    publicView: true,
    acknowledgedSource: {
      record: { id: "note-1", title: "First", body: "Body", summary: "" },
      values: { title: "First", body: "Body", summary: "" },
    },
    submit: vi.fn(async (_data, context) => acknowledgeFormSubmit(
      { id: "note-1", ...context.values },
      context.values,
    )),
    title: (context) => context.recordId === null ? "New note" : "Saved note",
    recordExtras: (context) => <button type="button" onClick={() => context.focusField("summary")}>Focus summary</button>,
    groups: [{
      label: "Advanced",
      collapsible: true,
      fields: [{ name: "summary", label: "Summary", required: true }],
      actions: [],
    }],
  });
  expect(await screen.findByRole("heading", { name: "Saved note" })).toBeTruthy();
  const disclosure = screen.getByRole("button", { name: "Advanced" });
  expect(disclosure.getAttribute("aria-expanded")).toBe("false");
  disclosure.focus();
  expect(disclosure.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "Focus summary" }));
  await waitFor(() => expect(disclosure.getAttribute("aria-expanded")).toBe("true"));
  const summary = screen.getByRole("textbox", { name: "Summary" });
  expect(document.activeElement).toBe(summary);
  fireEvent.click(disclosure);
  fireEvent.click(screen.getByRole("button", { name: "Focus summary" }));
  await waitFor(() => expect(disclosure.getAttribute("aria-expanded")).toBe("true"));
});

test("local child panels share the routed unsaved-change leave owner", async () => {
  const f = await fixture();
  edit("title", "Unsaved");

  const pending = f.surface().requestLeave();
  fireEvent.click(await screen.findByRole("button", { name: "Stay" }));
  await expect(pending).resolves.toBe(false);
  expect(f.surface().form.getValues("title")).toBe("Unsaved");

  const leaving = f.surface().requestLeave();
  fireEvent.click(await screen.findByRole("button", { name: "Leave" }));
  await expect(leaving).resolves.toBe(true);
});

test("a record panel switches to the field tab before focusing its native control", async () => {
  await fixture({
    publicView: true,
    acknowledgedSource: {
      record: { id: "note-1", title: "First", body: "Body" },
      values: { title: "First", body: "Body" },
    },
    submit: vi.fn(async (_data, context) => acknowledgeFormSubmit(
      { id: "note-1", title: String(context.values.title), body: String(context.values.body) },
      context.values,
    )),
    recordTabs: [{
      id: "activity",
      label: "Activity",
      render: (context) => (
        <>
          <button type="button" onClick={() => context.focusField("title", { recordTabId: "overview" })}>
            Focus title
          </button>
          <button type="button" onClick={() => context.focusField("note", { recordTabId: "overview" })}>
            Focus absent value
          </button>
        </>
      ),
    }],
  });

  fireEvent.click(await screen.findByRole("button", { name: "Focus title" }));

  await waitFor(() => expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected")).toBe("true"));
  expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Title" }));

  fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
  fireEvent.click(await screen.findByRole("button", { name: "Focus absent value" }));
  await waitFor(() => expect(document.activeElement?.textContent).toBe("Set value"));
});

function edit(name: string, value: string) { fireEvent.change(screen.getByLabelText(name), { target: { value } }); }

test("persisted state locks every field, save and patch while lifecycle cache updates remain available", async () => {
  const f = await fixture({ readOnlyWhen: (record) => record.status === "confirmed" });
  edit("title", "Local edit");
  act(() => f.surface().form.setValue("status", "confirmed"));
  expect(f.surface().formReadOnly).toBe(false);
  f.setRecord({ id: "note-1", title: "First", body: "Original body", status: "confirmed" });
  await act(async () => f.surface().reload());
  await waitFor(() => expect(f.surface().formReadOnly).toBe(true));
  expect(f.surface().fieldReadOnly(fields[0]!)).toBe(true);
  await expect(f.surface().submitForm()).rejects.toThrow("disabled");
  await expect(f.surface().applyPatch({ title: "Forbidden" })).rejects.toThrow("disabled");
  expect(f.update).not.toHaveBeenCalled();
  act(() => f.surface().patchRecord({ status: "draft" }));
  await waitFor(() => expect(f.surface().formReadOnly).toBe(false));
  expect(f.surface().fieldReadOnly(fields[0]!)).toBe(false);
});

test("a read-only policy does not lock a new record from draft defaults", async () => {
  const policy = vi.fn(() => true);
  const f = await fixture({ id: null, readOnlyWhen: policy });
  expect(f.surface().formReadOnly).toBe(false);
  expect(policy).not.toHaveBeenCalled();
});

test("external acknowledged values share one save and disable the native detail read", async () => {
  const source = {
    record: { id: "note-1", title: "First" },
    values: { title: "First", definition: { node: { name: "Node one", config: null } } },
  } satisfies FormViewAcknowledgedSource;
  const submit = vi.fn(async (_data, context) => acknowledgeFormSubmit(
    { id: "note-1", title: String(context.values.title) },
    context.values,
  ));
  const f = await fixture({
    acknowledgedSource: source,
    submit,
    mountedFields: ["title", "definition.node.name"],
  });

  edit("title", "Outer edit");
  edit("definition.node.name", "Nested edit");
  await act(async () => f.surface().submitForm());

  expect(f.getOne).not.toHaveBeenCalled();
  expect(submit).toHaveBeenCalledTimes(1);
  expect(submit.mock.calls[0]?.[0]).toEqual({ title: "Outer edit" });
  expect(submit.mock.calls[0]?.[1].values).toMatchObject({
    title: "Outer edit",
    definition: { node: { name: "Nested edit", config: null } },
  });
  expect(submit.mock.calls[0]?.[1].baselineValues).toMatchObject(source.values);
  expect(f.surface().formIsDirty).toBe(false);
  f.rerender({});
  expect(f.surface().form.getValues("title")).toBe("Outer edit");
  expect(f.surface().formIsDirty).toBe(false);
});

test("an explicitly empty external source never falls back to the native detail cache", async () => {
  const f = await fixture({
    acknowledgedSource: { record: null, values: null },
    mountedFields: ["title"],
  });
  expect(f.getOne).not.toHaveBeenCalled();
  expect(f.surface().displayRecord).toBeNull();
  expect(f.surface().formReadOnly).toBe(true);
  act(() => f.surface().reload());
  expect(f.getOne).not.toHaveBeenCalled();
});

test("public FormView forwards the external acknowledged source", async () => {
  const f = await fixture({
    publicView: true,
    acknowledgedSource: {
      record: { id: "note-1", title: "External", body: "Body" },
      values: { title: "External", body: "Body" },
    },
  });
  expect(await screen.findByDisplayValue("External")).toBeTruthy();
  expect(f.getOne).not.toHaveBeenCalled();
});

test("a full acknowledgement rebases submitted graph values while retaining later nested edits", async () => {
  let resolve!: (value: ReturnType<typeof acknowledgeFormSubmit>) => void;
  const source = {
    record: { id: "note-1", title: "First" },
    values: { title: "First", definition: { nodes: { new: { name: "Node" } } } },
  } satisfies FormViewAcknowledgedSource;
  const f = await fixture({
    acknowledgedSource: source,
    mountedFields: ["title", "definition.nodes.new.name"],
    submit: () => new Promise((done) => { resolve = done; }),
  });
  edit("title", "Submitted");
  edit("definition.nodes.new.name", "Submitted node");
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.surface().pending).toBe(true));
  edit("definition.nodes.new.name", "Later node");
  const accepted = {
    title: "Submitted",
    definition: { nodes: { new: { id: "step-1", name: "Submitted node" } } },
  };
  await act(async () => {
    resolve(acknowledgeFormSubmit(
      { id: "note-1", title: "Submitted" },
      accepted,
      ({ accepted: server, current }) => ({
        ...server,
        definition: {
          nodes: {
            new: {
              ...((server.definition as { nodes: { new: object } }).nodes.new),
              name: (current.definition as { nodes: { new: { name: string } } }).nodes.new.name,
            },
          },
        },
      }),
    ));
    await saving;
  });
  expect(f.surface().form.getValues("title")).toBe("Submitted");
  expect(f.surface().form.getValues("definition.nodes.new")).toEqual({
    id: "step-1", name: "Later node",
  });
  expect(f.surface().form.formState.defaultValues).toMatchObject(accepted);
  expect(f.surface().form.getFieldState("definition.nodes.new.name").isDirty).toBe(true);
  expect(f.surface().formIsDirty).toBe(true);
});

test.each(["add", "remove"] as const)("a full acknowledgement preserves an in-flight array %s", async (change) => {
  let resolve!: (value: ReturnType<typeof acknowledgeFormSubmit>) => void;
  const original = { key: "first", name: "First" };
  const source: FormViewAcknowledgedSource = {
    record: { id: "note-1", title: "First" },
    values: { title: "First", definition: { nodes: [original] } },
  };
  const f = await fixture({
    acknowledgedSource: source,
    submit: () => new Promise((done) => { resolve = done; }),
  });
  act(() => f.surface().form.setValue("title", "Submitted", { shouldDirty: true }));
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.surface().pending).toBe(true));
  act(() => f.surface().form.setValue(
    "definition.nodes",
    (change === "add" ? [original, { key: "later", name: "Later" }] : []) as never,
    { shouldDirty: true },
  ));
  await act(async () => {
    resolve(acknowledgeFormSubmit(
      { id: "note-1", title: "Submitted" },
      { title: "Submitted", definition: { nodes: [{ ...original, serverValue: "accepted" }] } },
    ));
    await saving;
  });
  expect(f.surface().form.getValues("definition.nodes")).toEqual(
    change === "add"
      ? [original, { key: "later", name: "Later" }]
      : [],
  );
  expect(f.surface().formIsDirty).toBe(true);
});

test("bound descriptors scope prefill, null, errors and readonly to the declaring record", async () => {
  const source = {
    record: { id: "note-1", title: "First" },
    values: { title: "First", definition: { node: { operation: "old", config: null } } },
  } satisfies FormViewAcknowledgedSource;
  const operation: MutationDialogField = {
    name: "operation",
    label: "Operation",
    prefill: () => ({ config: { mode: "fresh" } }),
    prefillReplace: ["config"],
  };
  const optional: MutationDialogField = {
    name: "optional", label: "Optional", nullable: true, omittable: true,
  };
  const f = await fixture({
    acknowledgedSource: source,
    mountedFields: [],
    boundFields: [
      { field: operation, scope: "definition.node" },
      { field: optional, scope: "definition.node" },
      { field: { name: "config", label: "Configuration", widget: "json", nullable: true }, scope: "definition.node", readOnly: true },
    ],
  });
  expect(f.surface().form.getValues("definition.node.config")).toBeNull();
  expect(f.surface().form.getValues("definition.node.optional")).toBeUndefined();
  edit("Operation", "new");
  expect(f.surface().form.getValues("definition.node.config")).toEqual({ mode: "fresh" });
  act(() => f.surface().form.setError("definition.node.optional", { type: "server", message: "Nested problem" }));
  expect(await screen.findByText("Nested problem")).toBeTruthy();
  expect(screen.queryByLabelText("Configuration")).toBeNull();
});

test("a stateful bound widget remounts when its nested scope changes", async () => {
  const until: MutationDialogField = { name: "until", label: "Until", widget: "datetime" };
  const f = await fixture({
    acknowledgedSource: {
      record: { id: "note-1", title: "First" },
      values: {
        title: "First",
        definition: {
          nodes: {
            first: { config: { until: "2026-09-10T08:00" } },
            second: { config: { until: "2026-09-12T09:30" } },
            fresh: { config: {} },
          },
        },
      },
    },
    mountedFields: [],
    boundFields: [{ field: until, scope: "definition.nodes.first.config" }],
  });
  await waitFor(() => expect(screen.getByLabelText("Until").textContent).toContain("Sep 10, 2026"));
  f.rerender({ mountedFields: [], boundFields: [{ field: until, scope: "definition.nodes.second.config" }] });
  await waitFor(() => expect(screen.getByLabelText("Until").textContent).toContain("Sep 12, 2026"));
  f.rerender({ mountedFields: [], boundFields: [{ field: until, scope: "definition.nodes.fresh.config" }] });
  expect(screen.getByLabelText("Until").textContent).not.toContain("Sep 10, 2026");
  expect(screen.getByLabelText("Until").textContent).not.toContain("Sep 12, 2026");
  expect(f.surface().form.getValues("definition.nodes.first.config.until")).toBe("2026-09-10T08:00");
  expect(f.surface().form.getValues("definition.nodes.second.config.until")).toBe("2026-09-12T09:30");
  expect(f.surface().form.getValues("definition.nodes.fresh.config.until")).toBeUndefined();
});

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

test("persisted null and omitted fields stay pristine during an unrelated save", async () => {
  const f = await fixture({ presenceValues: true });
  expect(f.surface().form.getValues("note")).toBeNull();
  expect(f.surface().form.getValues("settings")).toBeNull();
  expect(Object.hasOwn(f.surface().form.getValues(), "summary")).toBe(false);
  expect(f.surface().formIsDirty).toBe(false);

  edit("title", "Only title changed");
  await act(async () => f.surface().submitForm());

  expect(f.update).toHaveBeenCalledWith(expect.objectContaining({
    id: "note-1",
    variables: { title: "Only title changed" },
  }));
  expect(f.surface().form.getValues("note")).toBeNull();
  expect(f.surface().form.getValues("settings")).toBeNull();
  expect(Object.hasOwn(f.surface().form.getValues(), "summary")).toBe(false);
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
  const f = await fixture({ submit: async () => { throw { graphQLErrors: [{ message: "Validation failed.", extensions: { code: "VALIDATION", validationErrors: { "lines.0.title": ["Invalid line"], title: ["Invalid title"] }, formErrors: ["Cannot save"] } }] }; } });
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

test("bound descriptors apply scoped variant visibility through the native form owner", async () => {
  const f = await fixture({
    acknowledgedSource: {
      record: { id: "note-1", title: "First" },
      values: { title: "First", settings: { kind: "gate", retry: "later", target: "child" } },
    },
    mountedFields: [],
    boundFields: [
      { scope: "settings", field: { name: "kind", label: "Operation" } },
      { scope: "settings", field: { name: "retry", label: "Retry", showWhen: (values) => values.kind === "gate" } },
      { scope: "settings", field: { name: "target", label: "Target", showWhen: (values) => values.kind === "map" } },
    ],
  });
  expect(await screen.findByLabelText("Retry")).toBeTruthy();
  expect(screen.queryByLabelText("Target")).toBeNull();
  act(() => f.surface().form.setValue("settings.kind", "map" as never, { shouldDirty: true }));
  expect(await screen.findByLabelText("Target")).toBeTruthy();
  expect(screen.queryByLabelText("Retry")).toBeNull();
  expect(f.surface().form.getValues("settings.retry")).toBe("later");
});

test("native bound widgets delimit text, discrete, structured and presence interactions", async () => {
  const starts = vi.fn();
  const commits = vi.fn();
  await fixture({
    acknowledgedSource: {
      record: { id: "note-1", title: "First" },
      values: {
        title: "First",
        settings: { title: "Old", kind: "gate", items: [], optional: "set", locked: "fixed" },
      },
    },
    mountedFields: [],
    onFieldInteractionStart: starts,
    onFieldInteractionCommit: commits,
    boundFields: [
      { scope: "settings", field: { name: "title", label: "Title" } },
      { scope: "settings", field: { name: "items", label: "Items", widget: "list", itemTemplate: { name: "item", label: "Item" } } },
      { scope: "settings", field: { name: "optional", label: "Optional", omittable: true } },
      { scope: "settings", field: { name: "locked", label: "Locked" }, readOnly: true },
    ],
  });

  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "N" } });
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "New" } });
  expect(starts.mock.calls.filter(([path]) => path === "settings.title")).toHaveLength(1);
  expect(commits).not.toHaveBeenCalledWith("settings.title");
  fireEvent.blur(screen.getByLabelText("Title"), { relatedTarget: screen.getByLabelText("Optional") });
  await waitFor(() => expect(commits).toHaveBeenCalledWith("settings.title"));

  fireEvent.click(await screen.findByRole("button", { name: "Add item" }));
  expect(starts).toHaveBeenCalledWith("settings.items");
  expect(commits).toHaveBeenCalledWith("settings.items");

  fireEvent.click(screen.getAllByRole("button", { name: "Not set" })[0]!);
  expect(starts).toHaveBeenCalledWith("settings.optional");
  expect(commits).toHaveBeenCalledWith("settings.optional");
  expect(starts).not.toHaveBeenCalledWith("settings.locked");
  expect(commits).not.toHaveBeenCalledWith("settings.locked");
});
