// @vitest-environment happy-dom

import { formSubmitError, savedFormSubmitResult } from "./validation-errors";
import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRootRoute, createRouter, createMemoryHistory, RouterContextProvider } from "@tanstack/react-router";
import { Controller, useForm } from "react-hook-form";
import { schemaFieldMetadataFromDataResources, type DataResourceMetadata, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider, type RuntimeViewAs } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { OperationDocumentsProvider, useAuthoredQuery, type TypedDocumentNode } from "@angee/refine";
import gql from "graphql-tag";
import { useList, type MetaQuery } from "@refinedev/core";
import { defaultWidgets, type WidgetDefinition } from "../../widgets";
import { BoundDescriptorField, BoundFormValue } from "./BoundDescriptorField";
import { FormView } from "./FormView";
import {
  acknowledgeFormSubmit,
  useFormViewSave,
  type FormSubmit,
  type FormViewAcknowledgedSource,
  type FormViewSaveSurface,
} from "./use-form-view-save";
import type { DescriptorField } from "./DescriptorFieldList";
import type { FieldDescriptor, GroupDescriptor } from "../page";
import type { RecordTabDescriptor } from "./form-view-surface";
import { useAuthoredResourceMutation } from "../resource/authored-resource-mutation";

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
const { Provider, createClient, clearClients } = createUiTestProviders({ resources: [resource], apiUrl: "test://notes" });
afterEach(() => { cleanup(); clearClients(); });

const preview: RuntimeViewAs = {
  viewAs: { userId: "person" }, currentUser: { id: "person", name: "Person" }, realUser: null,
  viewablePeople: [], enter: () => undefined, exit: () => undefined,
};

test("preview keeps form submit visible and disabled, including Enter and native submit", async () => {
  const submit = vi.fn(async () => ({ status: "ok" as const, data: { id: "created" } }));
  const f = await fixture({ preview, publicView: true, id: null, submit });
  f.rerender({ viewFields: [{ name: "title", label: "Title" }] });
  const button = await screen.findByRole("button", { name: "Create" });
  expect(button.hasAttribute("disabled")).toBe(true);
  // Preview renders fields read-only, so there is no editable textbox to press Enter in.
  expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
  fireEvent.keyDown(button, { key: "Enter" });
  fireEvent.click(button);
  const form = button.closest("form");
  if (!form) throw new Error("Missing form");
  await act(async () => { fireEvent.submit(form); });
  expect(submit).not.toHaveBeenCalled();
});

test("preview blocks imperative form saves and patches without changing write capabilities", async () => {
  const submit = vi.fn(async () => ({ status: "ok" as const, data: { id: "note-1", title: "Changed" } }));
  const f = await fixture({ preview, submit });
  expect(f.surface().formReadOnly).toBe(false);
  act(() => f.surface().form.setValue("title", "Changed", { shouldDirty: true }));
  await act(async () => { await f.surface().submitForm(); await f.surface().applyPatch({ title: "Patched" }); });
  expect(submit).not.toHaveBeenCalled();
});

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
  preview?: RuntimeViewAs;
  rootArguments?: boolean;
  resourceMetadata?: DataResourceMetadata;
  widgets?: Record<string, WidgetDefinition>;
  readOnlyWhen?: (record: Row) => boolean;
  id?: string | null;
  submit?: FormSubmit;
  createSubmit?: FormSubmit;
  mountedFields?: readonly string[];
  presenceValues?: boolean;
  relationValues?: boolean;
  acknowledgedSource?: FormViewAcknowledgedSource;
  boundFields?: readonly { field: DescriptorField; scope?: string; readOnly?: boolean }[];
  publicView?: boolean;
  onFieldInteractionStart?: (path: string) => void;
  onFieldInteractionCommit?: (path: string) => void;
  recordTabs?: readonly RecordTabDescriptor[];
  formExtras?: React.ComponentProps<typeof FormView>["formExtras"];
  headerExtras?: React.ComponentProps<typeof FormView>["headerExtras"];
  recordExtras?: React.ComponentProps<typeof FormView>["recordExtras"];
  groups?: readonly GroupDescriptor[];
  title?: React.ComponentProps<typeof FormView>["title"];
} = {}) {
  let record: Row = {
    id: options.id ?? "note-1",
    title: "First",
    body: "Original body",
    deadline: "",
    ...(options.rootArguments ? { revision: 1 } : {}),
    ...(options.presenceValues ? { note: null, settings: null } : {}),
    ...(options.relationValues
      ? { parent: { id: "note-a", title: "Parent A" } }
      : {}),
  };
  const onSaved = vi.fn();
  const getOne = vi.fn(async (_params?: { meta?: MetaQuery }) => ({ data: record }));
  const update = vi.fn(async ({ variables }: { variables?: unknown; meta?: MetaQuery }) => {
    record = { ...record, ...(variables as Row) };
    return { data: record };
  });
  const provider = { getOne, update, create: update, getList: vi.fn(async () => ({ data: [], total: 0 })) } satisfies RefineTestDataProvider;
  const client = createClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  let surface!: FormViewSaveSurface;
  let acknowledgedSource = options.acknowledgedSource;
  const id = options.id === undefined ? "note-1" : options.id;
  const formResource = options.resourceMetadata ?? (options.rootArguments
    ? {
        ...resource,
        createArguments: [{ name: "idempotency_key", type: "String" }],
        updateArguments: [{ name: "expected_revision", type: "Int" }],
        fields: [...(resource.fields ?? []), {
          name: "revision", kind: "scalar" as const, scalar: "Int", readable: true,
          creatable: false, updatable: false, aggregatable: false, requiredOnCreate: false,
        }],
      } : resource);
  const formModel = schemaFieldMetadataFromDataResources([formResource]).labels["notes.Note"]!;
  function Probe({ recordId, mountedFields, viewFields, boundFields }: { recordId: string | null; mountedFields: readonly string[]; viewFields: readonly FieldDescriptor[]; boundFields?: readonly { field: DescriptorField; scope?: string; readOnly?: boolean }[] }) {
    surface = useFormViewSave({
      resource: "notes.Note", id: recordId, isCreate: recordId === null,
      dataResource: formResource, modelMetadata: formModel, formFields: viewFields, fieldByName, refineFields,
      submit: options.submit, createSubmit: options.createSubmit, readOnlyWhen: options.readOnlyWhen, onSaved, t: (key) => key,
      acknowledgedSource,
      onFieldInteractionStart: options.onFieldInteractionStart,
      onFieldInteractionCommit: options.onFieldInteractionCommit,
    });
    return <>{mountedFields.map((name) => <Controller key={name} name={name} control={surface.form.control} render={({ field }) => (
      <input aria-label={name} value={String(field.value ?? "")} onChange={field.onChange} />
    )} />)}{boundFields?.map((bound, index) => (
      <BoundDescriptorField key={index} form={surface} resource="notes.Note" {...bound} />
    ))}</>;
  }
  function Tree({ recordId = id, mountedFields = options.mountedFields ?? ["title", "body"], viewFields = fields, boundFields = options.boundFields }: { recordId?: string | null; mountedFields?: readonly string[]; viewFields?: readonly FieldDescriptor[]; boundFields?: readonly { field: DescriptorField; scope?: string; readOnly?: boolean }[] }) {
    return <Provider resources={[formResource]} dataProvider={provider} queryClient={client}>
      <RouterContextProvider router={router}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: { ...defaultWidgets, ...options.widgets },
        ...(options.preview ? { auth: { user: options.preview.currentUser, status: "authenticated", hasRole: () => false, viewAs: options.preview } } : {}),
      }}>
        {options.publicView ? (
          <FormView
            resource="notes.Note"
            id={recordId}
            fields={viewFields}
            groups={options.groups}
            title={options.title}
            acknowledgedSource={acknowledgedSource}
            submit={options.submit}
            createSubmit={options.createSubmit}
            onFieldInteractionStart={options.onFieldInteractionStart}
            onFieldInteractionCommit={options.onFieldInteractionCommit}
            recordTabs={options.recordTabs}
            formExtras={options.formExtras}
            headerExtras={options.headerExtras}
            recordExtras={options.recordExtras}
            defaultRecordTab={options.recordTabs ? "activity" : undefined}
          />
        ) : (
          <Probe key={recordId ?? "create"} recordId={recordId} mountedFields={mountedFields} viewFields={viewFields} boundFields={boundFields} />
        )}
      </AppRuntimeProvider></ToastProvider></ModalsHost></RouterContextProvider>
    </Provider>;
  }
  const view = render(<Tree />);
  if (!options.publicView && id !== null && options.acknowledgedSource?.values !== null) {
    await waitFor(() => expect(surface?.form.getValues("title")).toBe("First"));
  }
  return { surface: () => surface, onSaved, getOne, update, client,
    setRecord: (next: Row) => { record = next; },
    setAcknowledgedSource: (next: FormViewAcknowledgedSource) => { acknowledgedSource = next; },
    rerender: (props: Parameters<typeof Tree>[0]) => view.rerender(<Tree {...props} />) };
}

test("dirty scalar edits keep their original saved revision across a refreshed record", async () => {
  const source: FormViewAcknowledgedSource = {
    record: { id: "note-1", title: "First", draft_revision: "rev-1" },
    values: { title: "First", body: "Original body" },
  };
  const submit = vi.fn<FormSubmit>(async () => formSubmitError("No record returned."));
  const f = await fixture({ acknowledgedSource: source, submit });
  edit("title", "My edit");
  f.setAcknowledgedSource({
    record: { id: "note-1", title: "Remote edit", draft_revision: "rev-2" },
    values: { title: "Remote edit", body: "Original body" },
  });
  f.rerender({});
  await waitFor(() => expect(f.surface().displayRecord?.draft_revision).toBe("rev-2"));
  expect(f.surface().form.getValues("title")).toBe("My edit");
  await act(async () => f.surface().submitForm());
  expect(submit).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({
    record: expect.objectContaining({ draft_revision: "rev-2" }),
    baselineRecord: expect.objectContaining({ draft_revision: "rev-1" }),
  }));
});

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

test("registry widgets receive live sibling values in record forms", async () => {
  const read: WidgetDefinition["read"] = ({ row }) => <output aria-label="Sibling title">{row && typeof row === "object" && "title" in row ? String(row.title) : ""}</output>;
  const f = await fixture({ publicView: true, widgets: { sibling: { read, edit: read } } });
  f.rerender({ viewFields: [{ name: "title", label: "Title" }, { name: "body", widget: "sibling" }] });
  await waitFor(() => expect(screen.getByLabelText("Sibling title").textContent).toBe("First"));
  fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Changed" } });
  expect(screen.getByLabelText("Sibling title").textContent).toBe("Changed");
});

test("header extras render from the same live record context as the title", async () => {
  const extras = vi.fn((context: Parameters<NonNullable<React.ComponentProps<typeof FormView>["headerExtras"]>>[0]) => (
    <span>contact for {String(context.record?.title ?? "")}</span>
  ));
  await fixture({ publicView: true, headerExtras: extras });
  expect(await screen.findByText("contact for First")).toBeTruthy();
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
  await waitFor(() => {
    const current = screen.getByRole("button", { name: /Parent/ });
    expect(current.textContent).toContain("note-b");
    expect(current.textContent).not.toContain("note-a");
  });
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

test("a pane focuses a sheet field's native control in place", async () => {
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
          <button type="button" onClick={() => context.focusField("title")}>
            Focus title
          </button>
          <button type="button" onClick={() => context.focusField("note")}>
            Focus absent value
          </button>
        </>
      ),
    }],
  });

  fireEvent.click(await screen.findByRole("button", { name: "Focus title" }));

  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Title" })));
  expect(screen.queryByRole("tablist")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Focus absent value" }));
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
  await act(async () => { await f.surface().reload(); });
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
  let saving!: Promise<Row | null>;
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

test.each(["add", "remove", "reorder"] as const)("a full acknowledgement preserves an in-flight array %s", async (change) => {
  let resolve!: (value: ReturnType<typeof acknowledgeFormSubmit>) => void;
  const original = { key: "first", name: "First" };
  const other = { key: "second", name: "Second" };
  const initial = change === "reorder" ? [original, other] : [original];
  const later = change === "add" ? [original, { key: "later", name: "Later" }]
    : change === "remove" ? [] : [other, original];
  const accepted = {
    title: "Submitted",
    body: "Accepted",
    definition: { nodes: initial.map((node) => ({ ...node, serverValue: "accepted" })) },
  };
  const source: FormViewAcknowledgedSource = {
    record: { id: "note-1", title: "First" },
    values: { title: "First", body: "Initial", definition: { nodes: initial } },
  };
  const f = await fixture({
    acknowledgedSource: source,
    submit: () => new Promise((done) => { resolve = done; }),
  });
  act(() => f.surface().form.setValue("title", "Submitted", { shouldDirty: true }));
  let saving!: Promise<Row | null>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.surface().pending).toBe(true));
  act(() => f.surface().form.setValue(
    "definition.nodes",
    later as never,
    { shouldDirty: true },
  ));
  await act(async () => {
    resolve(acknowledgeFormSubmit(
      { id: "note-1", title: "Submitted" },
      accepted,
    ));
    await saving;
  });
  expect(f.surface().form.getValues("definition.nodes")).toEqual(later);
  expect(f.surface().form.getValues("body")).toBe("Accepted");
  expect(f.surface().form.formState.defaultValues).toMatchObject(accepted);
  expect(f.surface().formIsDirty).toBe(true);
  const latest = f.surface().form.getValues();
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.surface().pending).toBe(true));
  await act(async () => {
    resolve(acknowledgeFormSubmit({ id: "note-1", title: "Submitted" }, latest));
    await saving;
  });
  expect(f.surface().form.getValues("definition.nodes")).toEqual(later);
  expect(f.surface().formIsDirty).toBe(false);
});

test("bound descriptors scope prefill, null, errors and readonly to the declaring record", async () => {
  const source = {
    record: { id: "note-1", title: "First" },
    values: { title: "First", definition: { node: { operation: "old", config: null } } },
  } satisfies FormViewAcknowledgedSource;
  const operation: DescriptorField = {
    name: "operation",
    label: "Operation",
    prefill: () => ({ config: { mode: "fresh" } }),
    prefillReplace: ["config"],
  };
  const optional: DescriptorField = {
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
  const until: DescriptorField = { name: "until", label: "Until", widget: "datetime" };
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

test("updates send the edit baseline revision and retain a stale draft", async () => {
  const f = await fixture({ rootArguments: true });
  expect(f.getOne.mock.calls[0]?.[0]?.meta?.fields).toContain("revision");
  edit("title", "Local edit");
  f.setRecord({ id: "note-1", title: "Remote edit", body: "Fresh body", revision: 2 });
  await act(async () => f.surface().reload());
  await waitFor(() => expect(f.surface().displayRecord?.revision).toBe(2));
  f.update.mockRejectedValueOnce({ graphQLErrors: [{ message: "Changed", extensions: { code: "STALE_REVISION" } }] });
  await act(async () => f.surface().submitForm());
  expect(f.update).toHaveBeenCalledWith(expect.objectContaining({
    variables: { title: "Local edit" }, meta: expect.objectContaining({ gqlVariables: { expected_revision: 1 } }),
  }));
  expect(f.surface().saveConflict).toBe(true);
  expect(f.surface().form.getValues("title")).toBe("Local edit");
});

test("create retries reuse the form session's idempotency key", async () => {
  const f = await fixture({ id: null, rootArguments: true, mountedFields: ["title", "body", "deadline"] });
  edit("title", "New record");
  edit("deadline", "2026-09-28");
  f.update.mockRejectedValueOnce(new Error("Unavailable"));
  await act(async () => f.surface().submitForm());
  await act(async () => f.surface().submitForm());
  expect(f.update).toHaveBeenCalledTimes(2);
  const key = f.update.mock.calls[0]?.[0].meta?.gqlVariables?.idempotency_key;
  expect(key).toEqual(expect.any(String));
  expect(f.update.mock.calls[1]?.[0].meta?.gqlVariables?.idempotency_key).toBe(key);
});

test("an accepted create renews the key for the next record without remounting", async () => {
  const f = await fixture({ id: null, rootArguments: true, mountedFields: ["title", "body", "deadline"] });
  edit("title", "One");
  edit("deadline", "2026-09-28");
  await act(async () => f.surface().submitForm());
  const first = f.update.mock.calls[0]?.[0].meta?.gqlVariables?.idempotency_key;
  edit("title", "Two");
  await act(async () => f.surface().submitForm());
  const second = f.update.mock.calls[1]?.[0].meta?.gqlVariables?.idempotency_key;
  expect(first).toEqual(expect.any(String));
  expect(second).toEqual(expect.any(String));
  expect(second).not.toBe(first);
});

test("CREATION_KEY_CONFLICT keeps the draft and renews the next attempt's key", async () => {
  const f = await fixture({ id: null, rootArguments: true, mountedFields: ["title", "body", "deadline"] });
  edit("title", "My record");
  edit("deadline", "2026-09-28");
  f.update.mockRejectedValueOnce({ graphQLErrors: [{ message: "Conflict", extensions: { code: "CREATION_KEY_CONFLICT" } }] });
  await act(async () => f.surface().submitForm());
  expect(f.surface().saveConflict).toBe(true);
  expect(f.surface().form.getValues("title")).toBe("My record");
  const first = f.update.mock.calls[0]?.[0].meta?.gqlVariables?.idempotency_key;
  await act(async () => f.surface().submitForm());
  const next = f.update.mock.calls[1]?.[0].meta?.gqlVariables?.idempotency_key;
  expect(next).toEqual(expect.any(String));
  expect(next).not.toBe(first);
});

test("custom create owners receive a retry-stable key that clears on acceptance", async () => {
  const submit = vi.fn<FormSubmit>().mockRejectedValueOnce(new Error("Unavailable"))
    .mockImplementation(async (data) => ({ status: "ok", data: { id: "note-1", ...data } }));
  const f = await fixture({ id: null, submit, mountedFields: ["title", "body", "deadline"] });
  edit("title", "One");
  edit("deadline", "2026-09-28");
  await act(async () => f.surface().submitForm());
  await act(async () => f.surface().submitForm());
  const first = submit.mock.calls[0]?.[1].idempotencyKey;
  expect(first).toEqual(expect.any(String));
  expect(submit.mock.calls[1]?.[1].idempotencyKey).toBe(first);
  edit("title", "Two");
  await act(async () => f.surface().submitForm());
  expect(submit.mock.calls[2]?.[1].idempotencyKey).not.toBe(first);
});

test.each(["submit", "createSubmit"] as const)("%s owns custom create fields and receives corrected retry values without native insert capabilities", async (owner) => {
  const submit = vi.fn<FormSubmit>().mockRejectedValueOnce(new Error("Create refused"))
    .mockImplementation(async (data) => ({ status: "ok", data: { id: "note-1", ...data } }));
  const f = await fixture({ id: null, [owner]: submit, resourceMetadata: {
    ...resource, roots: { ...resource.roots, create: null, update: null },
    createFields: [], requiredCreateFields: [],
    fields: resource.fields?.map((field) => ({ ...field, creatable: false, updatable: false, requiredOnCreate: false })),
  } });
  f.rerender({ viewFields: [
    { name: "title" }, { name: "parent", kind: "relation", widget: "many2one" },
    { name: "secret" }, { name: "read_only", readOnly: true },
    { name: "invisible", showWhen: () => false },
  ] });
  act(() => {
    f.surface().form.setValue("title", "First", { shouldDirty: true });
    f.surface().form.setValue("parent", { id: "parent-a" }, { shouldDirty: true });
    f.surface().form.setValue("secret", "first", { shouldDirty: true });
    f.surface().form.setValue("read_only", "Excluded", { shouldDirty: true });
    f.surface().form.setValue("invisible", "Excluded", { shouldDirty: true });
  });
  await act(async () => f.surface().submitForm());
  expect(submit).toHaveBeenCalledExactlyOnceWith({ title: "First", parent: "parent-a", secret: "first" }, expect.anything());
  expect(f.surface().pending).toBe(false);
  expect(f.surface().formIsDirty).toBe(true);
  expect(f.onSaved).not.toHaveBeenCalled();
  const key = submit.mock.calls[0]?.[1].idempotencyKey;
  expect(key).toEqual(expect.any(String));
  act(() => {
    f.surface().form.setValue("title", "Corrected", { shouldDirty: true });
    f.surface().form.setValue("parent", { id: "parent-b" }, { shouldDirty: true });
    f.surface().form.setValue("secret", "second", { shouldDirty: true });
  });
  await act(async () => f.surface().submitForm());
  expect(submit).toHaveBeenLastCalledWith({ title: "Corrected", parent: "parent-b", secret: "second" }, expect.objectContaining({ idempotencyKey: key }));
  expect(f.update).not.toHaveBeenCalled();
  expect(f.onSaved).toHaveBeenCalledOnce();
  expect(f.surface().formIsDirty).toBe(false);
  expect(f.surface().saveError).toBeNull();
});

test("native create submissions retain their resource-owned writable field restriction", async () => {
  const f = await fixture({ id: null });
  act(() => {
    f.surface().form.setValue("title", "Native", { shouldDirty: true });
    f.surface().form.setValue("parent", { id: "excluded-parent" }, { shouldDirty: true });
    f.surface().form.setValue("summary", "Excluded", { shouldDirty: true });
  });
  await act(async () => f.surface().submitForm());
  expect(f.update).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ variables: { title: "Native" } }));
});

test.each([
  { name: "summary", label: "Summary" },
  { name: "body", label: "Body", body: true, kind: "textarea" },
  { name: "parent", label: "Parent", kind: "relation", widget: "many2one" },
] satisfies FieldDescriptor[])("a form-owned authored mutation binds $name errors and retries corrected values without a duplicate notification", async (field) => {
  const document = gql`mutation CreateNote($data: NoteInput!) { create_note(data: $data) { id } }` as TypedDocumentNode<{ create_note: Row }, { data: Row }>;
  const corrected = field.name === "parent" ? "note-2" : "Corrected";
  const custom = vi.fn().mockRejectedValueOnce({ graphQLErrors: [{
    message: "Validation failed", extensions: { code: "VALIDATION", validationErrors: { [field.name]: ["Choose a different value."] }, formErrors: [] },
  }] }).mockResolvedValue({ data: { create_note: { id: "note-1", [field.name]: corrected } } });
  const notify = vi.fn();
  const onSaved = vi.fn();
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  function NoteForm() {
    const [create] = useAuthoredResourceMutation(document, { errorNotification: false });
    const submit = React.useCallback<FormSubmit>(async (data) =>
      savedFormSubmitResult((await create({ data }))?.create_note, "No record returned."), [create]);
    return <FormView resource="notes.Note" id={null} fields={[{ ...field, description: "Field hint." }]} submit={submit} onSaved={onSaved} />;
  }
  render(<Provider resources={[{ ...resource, roots: { ...resource.roots, create: null }, createFields: [], requiredCreateFields: [] }]}
    dataProvider={{ custom, getList: async () => ({ data: [{ id: "note-2" }], total: 1 }), getOne: async () => ({ data: { id: "note-2" } }) }}
    notificationProvider={{ open: notify, close: vi.fn() }}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <NoteForm />
    </AppRuntimeProvider></ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>);
  const control = screen.getByRole(field.name === "parent" ? "button" : "textbox", { name: field.label });
  if (field.name !== "parent") fireEvent.change(control, { target: { value: "Rejected" } });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  const message = await screen.findByText("Choose a different value.");
  expect(message.closest("[data-layout]")?.contains(control)).toBe(true);
  expect(control.getAttribute("aria-invalid")).toBe("true");
  expect(message.id).not.toBe("");
  const descriptionId = screen.getByText("Field hint.").id;
  expect(control.getAttribute("aria-describedby")?.split(" ")).toEqual(expect.arrayContaining([message.id, descriptionId]));
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  expect(notify).not.toHaveBeenCalled();
  expect(onSaved).not.toHaveBeenCalled();
  if (field.name === "parent") {
    fireEvent.click(control);
    fireEvent.click(await screen.findByRole("option", { name: "note-2" }));
  } else {
    fireEvent.change(control, { target: { value: corrected } });
  }
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(custom).toHaveBeenLastCalledWith(expect.objectContaining({ payload: { data: { [field.name]: corrected } } }));
  expect(screen.queryByText("Choose a different value.")).toBeNull();
  expect(control.getAttribute("aria-invalid")).not.toBe("true");
  expect(control.getAttribute("aria-describedby")).toBe(descriptionId);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(notify).not.toHaveBeenCalled();
});

test("plain updates ignore save-only revision arguments and unexposed revision fields", async () => {
  const f = await fixture({
    resourceMetadata: { ...resource, saveArguments: [{ name: "expected_revision", type: "Int" }] },
  });
  expect(f.getOne.mock.calls[0]?.[0]?.meta?.fields).not.toContain("revision");
  edit("title", "Changed");
  await act(async () => f.surface().submitForm());
  expect(f.update).toHaveBeenCalledOnce();
  expect(f.update.mock.calls[0]?.[0].meta?.gqlVariables).toBeUndefined();
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

test.each([null, "note-1"])("submitForm returns the acknowledged record for %s after native validation and saving", async (id) => {
  const f = await fixture({ id });
  edit("title", "Accepted");
  let accepted: Row | null = null;
  await act(async () => { accepted = await f.surface().submitForm(); });
  expect(accepted).toMatchObject({ id: "note-1", title: "Accepted" });
  expect(f.onSaved).toHaveBeenCalledWith(accepted);
  expect(f.surface().formIsDirty).toBe(false);
});

test("partial custom saves preserve omitted fields and refetch canonical detail data", async () => {
  let f!: Awaited<ReturnType<typeof fixture>>;
  const submit = vi.fn(async () => {
    f.setRecord({ id: "note-1", title: "Canonical title", body: "Original body" });
    return { status: "ok" as const, data: { id: "note-1" } };
  });
  f = await fixture({ submit });
  edit("title", "Canonical title");
  await act(async () => f.surface().submitForm());
  await waitFor(() => expect(f.surface().displayRecord?.title).toBe("Canonical title"));
  expect(f.surface().form.getValues("body")).toBe("Original body");
  expect(f.surface().formIsDirty).toBe(false);
  expect(f.getOne.mock.calls.length).toBeGreaterThan(1);
});

test("a missing custom response reports failure and retains the draft", async () => {
  const f = await fixture({ submit: async () => savedFormSubmitResult<Row>(undefined, "No record returned.") });
  edit("title", "Unsaved");
  await act(async () => { expect(await f.surface().submitForm()).toBeNull(); });
  expect(f.surface().saveError).toBe("No record returned.");
  expect(f.surface().formIsDirty).toBe(true);
  expect(f.onSaved).not.toHaveBeenCalled();
});

test("native validation includes unmounted required fields and respects visibility", async () => {
  const f = await fixture({ id: null });
  edit("title", "Scheduled");
  await act(async () => { expect(await f.surface().submitForm()).toBeNull(); });
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
  expect(f.surface().saveError).toBe("Cannot save lines.0.title: Invalid line");
  expect(f.surface().serverFieldErrors).toMatchObject({ "lines.0.title": ["Invalid line"] });
  act(() => f.surface().clearServerFieldError("title"));
  expect(f.surface().form.getFieldState("title").error).toBeUndefined();
});

test("duplicate submits are ignored and a previous record save cannot reset or notify the new form", async () => {
  let resolve!: (value: Row) => void;
  const submit = vi.fn(() => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })));
  const f = await fixture({ submit });
  edit("title", "Old edit");
  let first!: Promise<Row | null>;
  act(() => { first = f.surface().submitForm(); void f.surface().submitForm(); });
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  f.setRecord({ id: "note-2", title: "Second", body: "Second body" });
  f.rerender({ recordId: "note-2" });
  await waitFor(() => expect(f.surface().form.getValues("title")).toBe("Second"));
  edit("title", "New edit");
  await act(async () => { resolve({ id: "note-1", title: "Old saved" }); expect(await first).toBeNull(); });
  expect(f.surface().form.getValues("title")).toBe("New edit");
  expect(f.surface().formIsDirty).toBe(true);
  expect(f.onSaved).not.toHaveBeenCalled();
});


test("same-record refresh during custom submit cannot clear the transport pending state", async () => {
  let resolve!: (value: Row) => void;
  const submit = vi.fn(() => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })));
  const f = await fixture({ submit });
  edit("title", "Submitting");
  let saving!: Promise<Row | null>;
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
  const f = await fixture({ submit: async () => ({ status: "ok", data: { id: "note-1", body: "Normalized body" } }) });
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
  const submit = vi.fn(() => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })));
  const f = await fixture({ submit });
  edit("title", "Submitted title");
  let saving!: Promise<Row | null>;
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
  let saving!: Promise<Row | null>;
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
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })) });
  edit("title", "Submitted title");
  let saving!: Promise<Row | null>;
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
  const f = await fixture({ submit: () => new Promise<Row | null>((done, fail) => { resolve = done; reject = fail; }).then((data) => savedFormSubmitResult(data, "No record returned.")) });
  edit("title", "Submitted title");
  let saving!: Promise<Row | null>;
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
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })) });
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

test.each([false, true])("a native create accepts an unmounted server-assigned relation and preserves later edits (%s)", async (laterEdit) => {
  const title: FieldDescriptor = { name: "title", label: "Title", prefill: () => ({ parent: null }) };
  const f = await fixture({ id: null, mountedFields: [], boundFields: [{ field: title }] });
  f.rerender({ viewFields: fields.map((field) => field.name === "parent"
    ? { ...field, omittable: false, readOnly: true } : field) });
  let resolve!: (value: { data: Row }) => void;
  f.update.mockImplementationOnce(() => new Promise<{ data: Row }>((done) => { resolve = done; }));
  fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Submitted title" } });
  expect(f.surface().form.getValues("parent")).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Parent" })).toBeNull();
  let saving!: Promise<Row | null>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.update).toHaveBeenCalledTimes(1));
  expect(f.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { title: "Submitted title" } }));
  if (laterEdit) fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Later title" } });
  const parent = { id: "note-parent", title: "Server-assigned parent" };
  const accepted = { id: "note-created", title: "Submitted title", body: "Server default body", parent };
  await act(async () => { resolve({ data: accepted }); await saving; });
  expect(f.surface().form.getValues("parent")).toEqual(parent);
  expect(f.surface().form.getValues("body")).toBe(accepted.body);
  expect(f.surface().form.formState.defaultValues?.parent).toEqual(parent);
  expect(f.surface().form.getFieldState("parent").isDirty).toBe(false);
  expect(f.surface().form.getValues("title")).toBe(laterEdit ? "Later title" : "Submitted title");
  expect(f.surface().formIsDirty).toBe(laterEdit);
  expect(f.onSaved).toHaveBeenCalledWith(accepted);
  const leaving = f.surface().requestLeave();
  if (laterEdit) fireEvent.click(await screen.findByRole("button", { name: "Stay" }));
  await expect(leaving).resolves.toBe(!laterEdit);
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

test.each([false, true])("a created record survives descriptor rerenders and preserves later edits (%s)", async (laterEdit) => {
  const f = await fixture({ id: null });
  let resolve!: (value: { data: Row }) => void;
  f.update.mockImplementationOnce(() => new Promise<{ data: Row }>((done) => { resolve = done; }));
  edit("title", "Submitted title");
  let saving!: Promise<Row | null>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.update).toHaveBeenCalledTimes(1));
  if (laterEdit) edit("title", "Later title");
  const accepted = { id: "note-created", title: "Submitted title", body: "", deadline: "" };
  await act(async () => { resolve({ data: accepted }); await saving; });
  f.rerender({ viewFields: fields.map((field) => ({ ...field })) });
  expect(f.surface().form.getValues("title")).toBe(laterEdit ? "Later title" : "Submitted title");
  expect(f.surface().form.formState.defaultValues?.title).toBe("Submitted title");
  expect(f.surface().formIsDirty).toBe(laterEdit);
  expect(f.onSaved).toHaveBeenCalledWith(accepted);
  if (laterEdit) {
    const leaving = f.surface().requestLeave();
    fireEvent.click(await screen.findByRole("button", { name: "Stay" }));
    await expect(leaving).resolves.toBe(false);
  } else {
    await expect(f.surface().requestLeave()).resolves.toBe(true);
  }
  act(() => f.surface().discardChanges());
  f.rerender({ viewFields: fields.map((field) => ({ ...field })) });
  expect(f.surface().form.getValues("title")).toBe("");
  expect(f.surface().formIsDirty).toBe(false);
});

test("a new create seed replaces a locally acknowledged create", async () => {
  const f = await fixture({ id: null });
  edit("title", "Saved title");
  await act(async () => f.surface().submitForm());
  f.rerender({ viewFields: fields.map((field) => field.name === "title"
    ? { ...field, defaultValue: "Next draft" } : { ...field }) });
  expect(f.surface().form.getValues("title")).toBe("Next draft");
  expect(f.surface().formIsDirty).toBe(false);
});

test("a later edit equal to the canonical accepted value becomes clean", async () => {
  let resolve!: (value: Row) => void;
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })) });
  edit("title", "  Normalized title  ");
  let saving!: Promise<Row | null>;
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

test.each(["invalid", "conflict"] as const)("a typed %s result keeps the edit baseline until a successful retry", async (status) => {
  const failed = status === "invalid"
    ? { status, issues: { fieldErrors: { title: ["Choose another title."] }, formErrors: [] } }
    : { status, message: "The record changed." };
  const submit = vi.fn().mockResolvedValueOnce(failed).mockResolvedValueOnce({
    status: "ok", data: { id: "note-1", title: "Draft", body: "Original body", deadline: "" },
  });
  const f = await fixture({ submit });
  edit("title", "Draft");
  await act(async () => f.surface().submitForm());
  expect(f.surface().form.getValues("title")).toBe("Draft");
  expect(f.surface().form.formState.defaultValues?.title).toBe("First");
  expect(f.surface().formIsDirty).toBe(true);
  expect(f.onSaved).not.toHaveBeenCalled();
  expect(f.surface().form.formState.errors.root?.server?.type).toBe(status === "conflict" ? "conflict" : "server");
  await act(async () => f.surface().submitForm());
  expect(f.surface().saveError).toBeNull();
  expect(f.surface().form.getFieldState("title").error).toBeUndefined();
  expect(f.surface().formIsDirty).toBe(false);
  expect(f.onSaved).toHaveBeenCalledTimes(1);
});

test("a post-save callback failure does not convert an acknowledged save into a rejected draft", async () => {
  const f = await fixture();
  f.onSaved.mockImplementationOnce(() => { throw new Error("Continuation failed"); });
  edit("title", "Accepted");
  await act(async () => { await expect(f.surface().submitForm()).rejects.toThrow("Continuation failed"); });
  expect(f.surface().formIsDirty).toBe(false);
  expect(f.surface().saveError).toBeNull();
  expect(f.surface().form.getValues("title")).toBe("Accepted");
});

test("the record chrome distinguishes conflicts and reloads the saved record", async () => {
  let surface!: FormViewSaveSurface;
  const submit = vi.fn<FormSubmit>(async () => ({ status: "conflict", message: "A newer edit was saved." }));
  const f = await fixture({ publicView: true, submit, formExtras: (context) => { surface = context.form; return null; } });
  const title = await screen.findByLabelText("Title");
  await waitFor(() => expect((title as HTMLInputElement).value).toBe("First"));
  fireEvent.change(title, { target: { value: "Local edit" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Record changed")).toBeTruthy();
  expect(surface.saveConflict).toBe(true);
  expect(surface.formIsDirty).toBe(true);
  expect(surface.history.canUndo).toBe(true);
  f.setRecord({ id: "note-1", title: "Saved elsewhere", body: "Latest body", deadline: "" });
  fireEvent.click(screen.getByRole("button", { name: "Reload saved record" }));
  await waitFor(() => expect((title as HTMLInputElement).value).toBe("Saved elsewhere"));
  expect(surface.saveConflict).toBe(false);
  expect(surface.formIsDirty).toBe(false);
  expect(surface.history.canUndo).toBe(false);
  expect(surface.history.canRedo).toBe(false);
  expect(submit).toHaveBeenCalledTimes(1);
});

test("accepted saves clear history and later field interactions start a fresh group", async () => {
  const f = await fixture();
  act(() => f.surface().startFieldInteraction("title"));
  edit("title", "Accepted title");
  expect(f.surface().history.canUndo).toBe(true);
  await act(async () => f.surface().submitForm());
  expect(f.surface().history.canUndo).toBe(false);
  expect(f.surface().history.canRedo).toBe(false);
  act(() => f.surface().startFieldInteraction("title"));
  edit("title", "Later edit");
  act(() => {
    f.surface().commitFieldInteraction("title");
    f.surface().history.undo();
  });
  expect(f.surface().form.getValues("title")).toBe("Accepted title");
  expect(f.surface().formIsDirty).toBe(false);
});

test("overlapping field interactions keep history groups independent of observer deduplication", async () => {
  const started = vi.fn();
  const f = await fixture({ onFieldInteractionStart: started });
  act(() => f.surface().startFieldInteraction("title"));
  edit("title", "Local title");
  act(() => f.surface().startFieldInteraction("body"));
  edit("body", "Local body");
  act(() => {
    f.surface().commitFieldInteraction("body");
    f.surface().startFieldInteraction("title");
  });
  edit("title", "Later title");
  act(() => { f.surface().commitFieldInteraction("title"); f.surface().history.undo(); });
  expect(f.surface().form.getValues("title")).toBe("Local title");
  expect(f.surface().form.getValues("body")).toBe("Local body");
  expect(started.mock.calls).toEqual([["title"], ["body"]]);
});

test("accepting a remote baseline clears old undo frames without losing local edits", async () => {
  const f = await fixture({ acknowledgedSource: {
    record: { id: "note-1", title: "First", body: "Original body" },
    values: { title: "First", body: "Original body" },
  } });
  act(() => f.surface().startFieldInteraction("title"));
  edit("title", "Local title");
  expect(f.surface().history.canUndo).toBe(true);
  f.setAcknowledgedSource({
    record: { id: "note-1", title: "First", body: "Remote body" },
    values: { title: "First", body: "Remote body" },
  });
  f.rerender({});
  await waitFor(() => expect(f.surface().form.getValues("body")).toBe("Remote body"));
  expect(f.surface().history.canUndo).toBe(false);
  act(() => f.surface().history.undo());
  expect(f.surface().form.getValues()).toMatchObject({ title: "Local title", body: "Remote body" });
  act(() => f.surface().startFieldInteraction("title"));
  edit("title", "Later title");
  act(() => { f.surface().commitFieldInteraction("title"); f.surface().history.undo(); });
  expect(f.surface().form.getValues()).toMatchObject({ title: "Local title", body: "Remote body" });
  expect(f.surface().form.getFieldState("body").isDirty).toBe(false);
});

test("the form save owner displays an explicit success message once", async () => {
  const f = await fixture({ submit: async () => ({ status: "ok", data: { id: "note-1", title: "Saved", body: "Original body", deadline: "" }, message: "Accepted edit" }) });
  edit("title", "Saved");
  await act(async () => f.surface().submitForm());
  expect(await screen.findAllByText("Accepted edit")).toHaveLength(1);
  expect(f.onSaved).toHaveBeenCalledTimes(1);
});

test("an outdated custom save result fails loudly without presenting a retry banner", async () => {
  const submit = vi.fn().mockResolvedValue({ id: "note-1", title: "Already saved" });
  const f = await fixture({ submit });
  edit("title", "Already saved");
  await act(async () => { await expect(f.surface().submitForm()).rejects.toThrow(/FormSubmitResult contract/); });
  expect(f.surface().saveError).toBeNull();
  expect(f.onSaved).not.toHaveBeenCalled();
});


const relatedSummary = gql`query RelatedSummary { summary }` as TypedDocumentNode<{ summary: string }, Record<string, never>>;
const currentSummary = gql`query CurrentSummary { summary }` as TypedDocumentNode<{ summary: string }, Record<string, never>>;
const createNoteCommand = gql`mutation CreateNote($data: NoteInput!) { create_note(data: $data) { id title } }` as TypedDocumentNode<{ create_note: Row }, { data: Row }>;

test.each(["create", "update", "patch", "nested", "command", "rejected"] as const)("%s saves refresh declared native and authored caches only after acceptance", async (mode) => {
  const related = testDataResource("catalog.Model");
  const unrelated = testDataResource("catalog.Unrelated");
  const formResource = mode === "nested" ? {
    ...resource, roots: { ...resource.roots, save: "notes_save" },
    linesResource: { field: "lines", modelLabel: "notes.Line", positionField: "position", fields: [{
      name: "label", kind: "scalar" as const, scalar: "String", readable: true,
      creatable: true, updatable: true, filterable: false, sortable: false, aggregatable: false, groupable: false, requiredOnCreate: false,
    }] },
  } : resource;
  let title = "Before";
  const getList = vi.fn(async () => ({ data: [{ id: "model-1", title }], total: 1 }));
  const custom = vi.fn(async ({ meta }: { meta?: MetaQuery }) => {
    if (meta?.gqlVariables && "data" in meta.gqlVariables) {
      title = "After";
      return { data: { create_note: { id: "note-1", title } } };
    }
    if (meta?.gqlVariables && "lines" in meta.gqlVariables) {
      title = "After";
      return { data: { notes_save: { id: "note-1", title, body: "", lines: [{ id: "line-1", label: "After" }] } } };
    }
    return { data: { summary: title } };
  });
  const write = vi.fn(async () => { title = "After"; return { data: { id: "note-1", title, body: "" } }; });
  const client = createClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false }, mutations: { retry: false } } });
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  let surface!: FormViewSaveSurface;
  let nativeTitle: unknown;
  let authoredTitle: unknown;
  let currentAuthoredTitle: unknown;
  let unrelatedTitle: unknown;
  function Probe() {
    const list = useList({ resource: related.roots.list!, dataProviderName: "console" });
    const other = useList({ resource: unrelated.roots.list!, dataProviderName: "console" });
    const authored = useAuthoredQuery(relatedSummary, {}, { dataProviderName: "console", models: [related.modelLabel] });
    const currentAuthored = useAuthoredQuery(currentSummary, {}, { dataProviderName: "console", models: [resource.modelLabel] });
    const [command] = useAuthoredResourceMutation(createNoteCommand, { dataProviderName: "console", invalidateModels: [resource.modelLabel] });
    currentAuthoredTitle = currentAuthored.data?.summary;
    nativeTitle = list.result.data?.[0]?.title;
    unrelatedTitle = other.result.data?.[0]?.title;
    authoredTitle = authored.data?.summary;
    surface = useFormViewSave({
      resource: resource.modelLabel, id: mode === "create" || mode === "command" ? null : "note-1", isCreate: mode === "create" || mode === "command",
      dataResource: formResource, modelMetadata: schemaFieldMetadataFromDataResources([formResource]).labels[resource.modelLabel]!,
      formFields: fields, fieldByName, refineFields,
      defaultValues: { title: "Before", body: "", deadline: "" },
      invalidateModels: [related.modelLabel],
      ...(mode === "rejected" ? { submit: async () => formSubmitError("Refused") } : {}),
      ...(mode === "command" ? { createSubmit: async (data: Row) => savedFormSubmitResult((await command({ data }))?.create_note, "No record returned.") } : {}),
      t: (key) => key,
    });
    return null;
  }
  render(<Provider resources={[formResource, related, unrelated]} queryClient={client} dataProvider={{
    getList, custom, create: write, update: write, getOne: async () => ({ data: { id: "note-1", title, body: "", deadline: "", ...(mode === "nested" ? { lines: [{ id: "line-1", label: title }] } : {}) } }),
  }}><RouterContextProvider router={router}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><OperationDocumentsProvider documents={{ console: { saves: { [resource.modelLabel]: gql`mutation Save { notes_save { id } }` } } }}><Probe /></OperationDocumentsProvider></AppRuntimeProvider></ToastProvider></ModalsHost></RouterContextProvider></Provider>);
  await waitFor(() => { expect(surface.loading).toBe(false); expect(nativeTitle).toBe("Before"); expect(authoredTitle).toBe("Before"); expect(currentAuthoredTitle).toBe("Before"); expect(unrelatedTitle).toBe("Before"); });
  await act(async () => {
    if (mode === "patch") await surface.applyPatch({ title: "After" });
    else {
      surface.form.setValue("title", "After", { shouldDirty: true });
      if (mode === "nested") surface.form.setValue("lines", [{ id: "line-1", label: "After" }], { shouldDirty: true });
      await surface.submitForm();
    }
  });
  if (mode === "rejected") {
    expect(surface.saveError).toBe("Refused");
    expect(write).not.toHaveBeenCalled();
    expect(getList).toHaveBeenCalledTimes(2);
    expect(custom).toHaveBeenCalledTimes(2);
  } else {
    await waitFor(() => { expect(nativeTitle).toBe("After"); expect(authoredTitle).toBe("After"); expect(currentAuthoredTitle).toBe("After"); });
    expect(write).toHaveBeenCalledTimes(mode === "nested" || mode === "command" ? 0 : 1);
    expect(getList.mock.calls).toHaveLength(3);
    expect(custom).toHaveBeenCalledTimes(mode === "nested" || mode === "command" ? 5 : 4);
    // The command owns its own model refresh; the Form must not issue a second one.
    expect(custom.mock.calls.filter(([params]) => params.meta?.gqlQuery === currentSummary)).toHaveLength(2);
  }
  expect(unrelatedTitle).toBe("Before");
});
