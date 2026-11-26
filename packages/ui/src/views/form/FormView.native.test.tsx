// @vitest-environment happy-dom

import { formSubmitError, savedFormSubmitResult } from "./validation-errors";
import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { EditorView } from "@codemirror/view";
import { createRootRoute, createRouter, createMemoryHistory, RouterContextProvider } from "@tanstack/react-router";
import { Controller, get, useForm } from "react-hook-form";
import { schemaFieldMetadataFromDataResources, type DataResourceMetadata, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider, type RuntimeViewAs } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { OperationDocumentsProvider, boundedGraphQLTransportError, useAuthoredQuery, type TypedDocumentNode } from "@angee/refine";
import gql from "graphql-tag";
import { useList, type MetaQuery } from "@refinedev/core";
import { defaultWidgets, type WidgetDefinition } from "../../widgets";
import { useDraftScope } from "../../lib/idempotency-key";
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
import type { FormSpecFieldDescriptor } from "./form-spec";
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
  /** Stored values beside the record's defaults. */
  record?: Row;
  viewFields?: readonly FieldDescriptor[];
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
    ...options.record,
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
  function Tree({ recordId = id, mountedFields = options.mountedFields ?? ["title", "body"], viewFields = options.viewFields ?? fields, boundFields = options.boundFields }: { recordId?: string | null; mountedFields?: readonly string[]; viewFields?: readonly FieldDescriptor[]; boundFields?: readonly { field: DescriptorField; scope?: string; readOnly?: boolean }[] }) {
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
  return { surface: () => surface, onSaved, getOne, update, client, router,
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

test("a form names its draft to the surfaces inside it: a create form of its resource embedded in it is a draft of its own", async () => {
  // As a list of the form's own resource, embedded in it, names its inline create form's draft.
  function Embedded(): React.ReactElement {
    return <output aria-label="Embedded draft">{useDraftScope("record.create:console:notes.Note")}</output>;
  }
  const read: WidgetDefinition["read"] = () => <Embedded />;
  const f = await fixture({ publicView: true, widgets: { embedded: { read, edit: read } } });
  const viewFields: FieldDescriptor[] = [{ name: "title", label: "Title" }, { name: "body", widget: "embedded" }];
  f.rerender({ viewFields });
  await waitFor(() => expect(screen.getByLabelText("Embedded draft").textContent)
    .toBe("record.edit:console:notes.Note:note-1/record.create:console:notes.Note"));
  f.rerender({ recordId: null, viewFields });
  await waitFor(() => expect(screen.getByLabelText("Embedded draft").textContent)
    .toBe("record.create:console:notes.Note/record.create:console:notes.Note"));
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
  const relation = await screen.findByRole("combobox", { name: /Parent/ });
  expect(relation.textContent).toContain("note-a");
  fireEvent.click(screen.getByRole("button", { name: "Choose B" }));
  await waitFor(() => {
    const current = screen.getByRole("combobox", { name: /Parent/ });
    expect(current.textContent).toContain("note-b");
    expect(current.textContent).not.toContain("note-a");
  });
  fireEvent.click(screen.getByRole("button", { name: "Clear parent" }));
  await waitFor(() => expect(screen.queryByRole("combobox", { name: /Parent/ })).toBeNull());
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

test("a record's tab switch keeps a dirty form and asks nothing; any other search change asks, as leaving does", async () => {
  const f = await fixture();
  edit("title", "Local edit");
  // The form publishes its dirty state a render after the value, as the person's next move finds it.
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await f.router.history.push("/?recordTab=activity"); });
  await waitFor(() => expect(f.router.history.location.search).toBe("?recordTab=activity"));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(f.surface().form.getValues("title")).toBe("Local edit");

  act(() => { void f.router.history.push("/?recordTab=activity&page=2"); });
  expect(await screen.findByRole("alertdialog", { name: "Unsaved changes - leave without saving?" })).toBeTruthy();
  expect(f.router.history.location.search).toBe("?recordTab=activity");
});

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
  let saving!: Promise<void>;
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
  };
  const optional: DescriptorField = {
    name: "optional", label: "Optional", nullable: true, omittable: true,
  };
  const f = await fixture({
    acknowledgedSource: source,
    mountedFields: [],
    viewFields: [...fields, { name: "definition", hidden: true }],
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

test("a preset that gives a sibling its loaded value back leaves the form clean, and one that replaces it leaves it dirty", async () => {
  const source = {
    record: { id: "note-1", title: "First" },
    values: { title: "First", kind: "letter", config: { paper: "A4" } },
  } satisfies FormViewAcknowledgedSource;
  const loadedSeen: unknown[] = [];
  const kind: DescriptorField = {
    name: "kind",
    label: "Kind",
    // Another kind starts its configuration empty; the loaded kind gets its loaded configuration back.
    prefill: (value, _values, loaded) => {
      loadedSeen.push(loaded);
      return { config: loaded !== undefined && value === loaded.kind ? loaded.config : {} };
    },
  };
  const f = await fixture({
    acknowledgedSource: source,
    mountedFields: [],
    viewFields: [...fields, { name: "kind", hidden: true }, { name: "config", hidden: true }],
    boundFields: [{ field: kind }, { field: { name: "config", label: "Configuration", widget: "json" }, readOnly: true }],
  });
  expect(f.surface().form.formState.isDirty).toBe(false);
  edit("Kind", "card");
  expect(loadedSeen.at(-1)).toMatchObject({ kind: "letter", config: { paper: "A4" } });
  expect(f.surface().form.getValues("config")).toEqual({});
  expect(f.surface().form.formState.isDirty).toBe(true);
  edit("Kind", "letter");
  expect(f.surface().form.getValues()).toMatchObject({ kind: "letter", config: { paper: "A4" } });
  expect(f.surface().form.formState.isDirty).toBe(false);
});

/** A resource (the notes one unless given) with more writable String fields. */
const withFields = (names: readonly string[], base: DataResourceMetadata = resource): DataResourceMetadata => ({
  ...base,
  fields: [...(base.fields ?? []), ...names.map((name) => ({
    name, kind: "scalar" as const, scalar: "String", readable: true,
    filterable: false, sortable: false, aggregatable: false, groupable: false,
    creatable: true, updatable: true, requiredOnCreate: false,
  }))],
});
// A kind decides its settings: `rich` edits them whole, `split` field by field, `plain` and `bare`
// take none. A mode `off` hides the whole settings too, and a detail shows only in mode `x`.
const presetResource = withFields(["kind", "mode", "detail", "provider", "secret", "style", "settingsNote"]);
const presetFields: readonly FieldDescriptor[] = [
  {
    name: "kind", label: "Kind", widget: "select",
    options: ["plain", "bare", "rich", "split"].map((value) => ({ value, label: value })),
    // Another kind starts its settings empty; the loaded kind gets its loaded settings back.
    prefill: (value, _values, loaded) => ({
      settings: loaded !== undefined && value === loaded.kind ? loaded.settings ?? {} : {},
    }),
  },
  { name: "settings", label: "Settings", widget: "json", showWhen: (values) => values.kind === "rich" && values.mode !== "off" },
  { name: "settings.a", label: "A", showWhen: (values) => values.kind === "split" },
  { name: "settings.b", label: "B", showWhen: (values) => values.kind === "split" },
  { name: "mode", label: "Mode" },
  { name: "detail", label: "Detail", showWhen: (values) => values.mode === "x" },
];

/** A saved record's form over the kinds above, with the values it holds. */
async function presetForm(stored: Row, options: { fields?: readonly FieldDescriptor[]; submit?: FormSubmit; resource?: DataResourceMetadata } = {}) {
  let surface!: FormViewSaveSurface;
  const f = await fixture({
    publicView: true, resourceMetadata: options.resource ?? presetResource, record: stored, viewFields: options.fields ?? presetFields,
    submit: options.submit, formExtras: (context) => { surface = context.form; return null; },
  });
  if (stored.kind !== undefined) {
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Kind" }).textContent).toContain(String(stored.kind)));
  }
  return { ...f, surface: () => surface, values: () => surface.form.getValues(), isDirty: () => surface.form.formState.isDirty };
}

async function chooseKind(kind: string) {
  fireEvent.click(screen.getByRole("combobox", { name: "Kind" }));
  const option = await screen.findByRole("option", { name: kind });
  fireEvent.pointerDown(option, { pointerType: "mouse" });
  fireEvent.click(option);
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Kind" }).textContent).toContain(kind));
}

function expectClean() {
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
}

test("a preset that hides a field leaves the form as loaded, and saves the preset's value only over a different loaded one", async () => {
  // Loaded without settings: another kind, then the loaded one again, is what was loaded.
  const without = await presetForm({ kind: "plain", settings: {} });
  await chooseKind("rich");
  expect(await screen.findByRole("button", { name: "Save" })).toBeTruthy();
  await chooseKind("plain");
  await waitFor(expectClean);
  expect(without.update).not.toHaveBeenCalled();
  cleanup();

  // Loaded with settings: the kind without them is dirty, and back again before saving is clean.
  const stored = await presetForm({ kind: "rich", settings: { depth: 2 } });
  await chooseKind("plain");
  expect(Object.hasOwn(stored.values(), "settings")).toBe(false);
  expect(await screen.findByRole("button", { name: "Discard" })).toBeTruthy();
  await chooseKind("rich");
  await waitFor(expectClean);
  expect(stored.values()).toMatchObject({ kind: "rich", settings: { depth: 2 } });
  // Saved, the kind without settings takes them empty; the form's own dirty state says so.
  await chooseKind("plain");
  expect(stored.isDirty()).toBe(true);
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(stored.update).toHaveBeenCalledOnce());
  expect(stored.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { kind: "plain", settings: {} } }));
  await waitFor(expectClean);
  expect(stored.isDirty()).toBe(false);
});

test("a preset that hides a field the form was loaded without writes nothing for it, and hidden nested fields leave no keys", async () => {
  const empty = await presetForm({ kind: "plain", settings: {} });
  await chooseKind("bare");
  expect(Object.hasOwn(empty.values(), "settings")).toBe(false);
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(empty.update).toHaveBeenCalledOnce());
  expect(empty.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { kind: "bare" } }));
  cleanup();

  const split = await presetForm({ kind: "split", settings: { a: "one", b: "two" } });
  expect(split.values()).toMatchObject({ settings: { a: "one", b: "two" } });
  await chooseKind("plain");
  expect(Object.hasOwn(split.values(), "settings")).toBe(false);
  expect(split.isDirty()).toBe(true);
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(split.update).toHaveBeenCalledOnce());
  expect(split.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { kind: "plain", settings: {} } }));
  await waitFor(() => expect(split.isDirty()).toBe(false));
});

test("a field hidden when the form loaded is never written by a preset, through undo and redo", async () => {
  // The record holds settings its kind takes none of; the form was loaded without them.
  const stale = await presetForm({ kind: "plain", settings: { left: "over" } });
  await chooseKind("rich");
  await chooseKind("plain");
  await waitFor(expectClean);
  act(() => stale.surface().history.undo());
  expect(await screen.findByRole("button", { name: "Save" })).toBeTruthy();
  act(() => stale.surface().history.redo());
  await waitFor(expectClean);
  expect(stale.update).not.toHaveBeenCalled();
  // Another change saves alone.
  edit("Mode", "m");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(stale.update).toHaveBeenCalledOnce());
  expect(stale.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { mode: "m" } }));
});

test("undo after a preset gives the hidden field back clean, and redo hides it again for the save", async () => {
  const stored = await presetForm({ kind: "rich", settings: { depth: 2 } });
  await chooseKind("plain");
  act(() => stored.surface().history.undo());
  await waitFor(expectClean);
  expect(stored.values()).toMatchObject({ kind: "rich", settings: { depth: 2 } });
  act(() => stored.surface().history.redo());
  expect(Object.hasOwn(stored.values(), "settings")).toBe(false);
  expect(stored.isDirty()).toBe(true);
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(stored.update).toHaveBeenCalledOnce());
  expect(stored.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { kind: "plain", settings: {} } }));
  await waitFor(expectClean);
  cleanup();

  // Undone, the settings the draft holds again save as they are, whatever set the next value.
  const undone = await presetForm({ kind: "rich", settings: { depth: 2 } });
  await chooseKind("plain");
  act(() => undone.surface().history.undo());
  await waitFor(expectClean);
  act(() => undone.surface().form.setValue("mode", "m", { shouldDirty: true }));
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(undone.update).toHaveBeenCalledOnce());
  expect(undone.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { mode: "m" } }));
});

test("a preset's value is written only where the save may write the field", async () => {
  const fields = presetFields.map((field) => field.name === "settings" ? { ...field, readOnly: true } : field);
  const locked = await presetForm({ kind: "rich", settings: { depth: 2 } }, { fields });
  await chooseKind("plain");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(locked.update).toHaveBeenCalledOnce());
  expect(locked.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { kind: "plain" } }));
});

test("a preset leaves alone what it did not set: an edit hidden by a plain change survives it", async () => {
  await presetForm({ kind: "plain", settings: {}, mode: "x", detail: "saved" });
  edit("Detail", "edited");
  edit("Mode", "y");
  expect(screen.queryByLabelText("Detail")).toBeNull();
  await chooseKind("rich");
  edit("Mode", "x");
  expect((await screen.findByLabelText("Detail") as HTMLInputElement).value).toBe("edited");
});

test("a preset that seeds a parent no field declares, of declared children, writes nothing for it", async () => {
  const fields = presetFields.filter((field) => field.name !== "settings");
  const loose = await presetForm({ kind: "plain" }, { fields });
  await chooseKind("split");
  await chooseKind("plain");
  await waitFor(expectClean);
  expect(Object.hasOwn(loose.values(), "settings")).toBe(false);
  cleanup();

  const split = await presetForm({ kind: "split", settings: { a: "one", b: "two" } }, { fields });
  await chooseKind("plain");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(split.update).toHaveBeenCalledOnce());
  expect(split.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { kind: "plain" } }));
});

test("a failed save keeps the preset's value for the retry, which writes it once", async () => {
  const stored = await presetForm({ kind: "rich", settings: { depth: 2 } });
  await chooseKind("plain");
  stored.update.mockRejectedValueOnce(new Error("Unavailable"));
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(stored.update).toHaveBeenCalledOnce());
  await waitFor(() => expect(stored.surface().saveErrors).not.toEqual([]));
  expect(stored.isDirty()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(stored.update).toHaveBeenCalledTimes(2));
  expect(stored.update.mock.calls.map(([call]) => call.variables)).toEqual([
    { kind: "plain", settings: {} }, { kind: "plain", settings: {} },
  ]);
  await waitFor(expectClean);
  expect(stored.isDirty()).toBe(false);
});

test("discard leaves nothing for a preset to write", async () => {
  const stored = await presetForm({ kind: "rich", settings: { depth: 2 }, mode: "" });
  await chooseKind("plain");
  fireEvent.click(await screen.findByRole("button", { name: "Discard" }));
  await waitFor(expectClean);
  expect(stored.values()).toMatchObject({ kind: "rich", settings: { depth: 2 } });
  // Hidden by a plain change, the settings are not the preset's to write.
  edit("Mode", "off");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(stored.update).toHaveBeenCalledOnce());
  expect(stored.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { mode: "off" } }));
});

test("the saved record holds the preset's written value where the response omits it", async () => {
  const submit = vi.fn<FormSubmit>(async (data) => ({ status: "ok", data: { id: "note-1", kind: data.kind } }));
  const stored = await presetForm({ kind: "rich", settings: { depth: 2 } }, { submit });
  // The canonical read the omission asks for stays pending.
  stored.getOne.mockImplementationOnce(() => new Promise<{ data: Row }>(() => undefined));
  await chooseKind("plain");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(submit).toHaveBeenCalledOnce());
  expect(submit.mock.calls[0]?.[0]).toEqual({ kind: "plain", settings: {} });
  await waitFor(() => expect(stored.surface().displayRecord?.kind).toBe("plain"));
  expect(stored.surface().displayRecord?.settings).toEqual({});
});

test("a split kind's fields hidden and shown again by presets end as loaded", async () => {
  const split = await presetForm({ kind: "split", settings: { a: "one", b: "two" } });
  await chooseKind("plain");
  await chooseKind("split");
  await waitFor(expectClean);
  expect((screen.getByLabelText("A") as HTMLInputElement).value).toBe("one");
  expect(split.values()).toMatchObject({ settings: { a: "one", b: "two" } });
});

test("a dirty-preserving preset keeps an edit it skips even where its change hides that field, and writes no default for it", async () => {
  const fields: readonly FieldDescriptor[] = [
    { name: "provider", label: "Provider", prefill: () => ({ secret: "preset" }), prefillPreserveDirty: true },
    { name: "secret", label: "Secret", showWhen: (values) => values.provider !== "none" },
  ];
  const preserving = await presetForm({ provider: "first", secret: "kept" }, { fields });
  expect(await screen.findByDisplayValue("kept")).toBeTruthy();
  edit("Secret", "mine");
  edit("Provider", "none");
  expect(screen.queryByLabelText("Secret")).toBeNull();
  edit("Provider", "first");
  expect((await screen.findByLabelText("Secret") as HTMLInputElement).value).toBe("mine");
  edit("Provider", "none");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(preserving.update).toHaveBeenCalledOnce());
  expect(preserving.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { provider: "none" } }));
});

// Leaving the loaded kind empties the settings; coming back seeds nothing.
const leavingEmpties: FieldDescriptor["prefill"] = (value, _values, loaded) => value === loaded?.kind ? null : { settings: {} };
const richSettings: FieldDescriptor = {
  name: "settings", label: "Settings", widget: "json", showWhen: (values) => values.kind === "rich",
};

/**
 * A saved record's form whose `kind` (and any `bound` field) is bound beside the sheet, the other
 * controls not rendered; the form declares the bound kind's preset unless `undeclared`.
 */
async function boundKindForm(stored: Row, others: readonly FieldDescriptor[], options: {
  prefill?: FieldDescriptor["prefill"];
  undeclared?: boolean;
  bound?: readonly DescriptorField[];
} = {}) {
  const kind: DescriptorField = { name: "kind", label: "Kind", prefill: options.prefill ?? leavingEmpties };
  return fixture({
    resourceMetadata: presetResource, record: stored, mountedFields: ["title"],
    boundFields: [kind, ...options.bound ?? []].map((field) => ({ field })),
    viewFields: [{ name: "title", label: "Title" }, options.undeclared ? { name: "kind", label: "Kind" } : kind, ...others],
  });
}

test("a field a preset dropped and shown again without a seed holds its loaded value, its control not rendered", async () => {
  const f = await boundKindForm({ kind: "rich", settings: { depth: 2 } }, [richSettings]);
  edit("Kind", "plain");
  expect(Object.hasOwn(f.surface().form.getValues(), "settings")).toBe(false);
  edit("Kind", "rich");
  expect(f.surface().form.getValues("settings")).toEqual({ depth: 2 });
  await waitFor(() => expect(f.surface().form.formState.isDirty).toBe(false));
});

test("the restore gives a dropped parent of declared fields its loaded value, its controls not rendered", async () => {
  const split = (values: Row) => values.kind === "split";
  const f = await boundKindForm({ kind: "split", settings: { a: "one", b: "two" } }, [
    { name: "settings.a", label: "A", showWhen: split }, { name: "settings.b", label: "B", showWhen: split },
  ]);
  edit("Kind", "plain");
  expect(Object.hasOwn(f.surface().form.getValues(), "settings")).toBe(false);
  edit("Kind", "split");
  expect(f.surface().form.getValues("settings")).toEqual({ a: "one", b: "two" });
  await waitFor(() => expect(f.surface().form.formState.isDirty).toBe(false));
});

test("a bound field's declared preset drops at the change and writes at the save", async () => {
  const f = await boundKindForm({ kind: "rich", settings: { depth: 2 } }, [richSettings]);
  edit("Kind", "plain");
  expect(Object.hasOwn(f.surface().form.getValues(), "settings")).toBe(false);
  await act(async () => f.surface().submitForm());
  expect(f.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { kind: "plain", settings: {} } }));
});

test("a bound field's preset the form does not declare seeds, but neither drops nor writes a hidden field", async () => {
  const f = await boundKindForm({ kind: "rich", settings: { depth: 2 } }, [richSettings], { undeclared: true });
  edit("Kind", "plain");
  expect(f.surface().form.getValues("settings")).toEqual({});
  await act(async () => f.surface().submitForm());
  expect(f.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { kind: "plain" } }));
});

test("a dropped field comes back with its loaded value after undo and redo, its control not rendered", async () => {
  const f = await boundKindForm({ kind: "rich", settings: { depth: 2 } }, [richSettings]);
  edit("Kind", "plain");
  act(() => f.surface().history.undo());
  expect(f.surface().form.getValues("settings")).toEqual({ depth: 2 });
  act(() => f.surface().history.redo());
  expect(Object.hasOwn(f.surface().form.getValues(), "settings")).toBe(false);
  edit("Kind", "rich");
  expect(f.surface().form.getValues("settings")).toEqual({ depth: 2 });
  await waitFor(() => expect(f.surface().form.formState.isDirty).toBe(false));
});

test("nothing comes back for a field the form was loaded without", async () => {
  const f = await boundKindForm({ kind: "plain", settings: { left: "over" } }, [richSettings], {
    prefill: (value) => value === "rich" ? null : { settings: {} },
  });
  edit("Kind", "rich");
  expect(Object.hasOwn(f.surface().form.getValues(), "settings")).toBe(false);
});

test("a field left unset keeps that choice through a preset's drop, undo and another edit, its control not rendered", async () => {
  const mode: DescriptorField = { name: "mode", label: "Mode" };
  const f = await boundKindForm({ kind: "rich", note: "kept", mode: "" }, [
    { name: "note", label: "Note", omittable: true, showWhen: (values) => values.kind === "rich" }, mode,
  ], { prefill: (value, _values, loaded) => ({ note: value === loaded?.kind ? loaded?.note : "" }), bound: [mode] });
  // What an omittable field's "Not set" leaves: the key, holding no value.
  act(() => f.surface().form.setValue("note", undefined, { shouldDirty: true }));
  edit("Kind", "plain");
  expect(Object.hasOwn(f.surface().form.getValues(), "note")).toBe(false);
  act(() => f.surface().history.undo());
  edit("Mode", "m");
  expect(Object.hasOwn(f.surface().form.getValues(), "note")).toBe(true);
  expect(f.surface().form.getValues("note")).toBeUndefined();
});

test("a preset that says nothing for a hidden relation writes nothing for it", async () => {
  const fields: readonly FieldDescriptor[] = [
    { ...presetFields[0]!, prefill: (value, _values, loaded) => ({ parent: value === loaded?.kind ? loaded?.parent : undefined }) },
    { name: "parent", label: "Parent", kind: "relation", widget: "many2one", showWhen: (values) => values.kind === "rich" },
  ];
  const related = await presetForm({ kind: "rich", parent: { id: "note-a", title: "Parent A" } }, { fields });
  await chooseKind("plain");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(related.update).toHaveBeenCalledOnce());
  expect(related.update.mock.calls[0]?.[0].variables).toStrictEqual({ kind: "plain" });
});

test("a nested value a preset wrote fills its path inside the cached record where neither response nor draft holds the parent", async () => {
  const fields: readonly FieldDescriptor[] = [
    {
      ...presetFields[0]!,
      prefill: (value, _values, loaded) => ({
        "style.color": value === loaded?.kind ? (loaded?.style as Row | undefined)?.color : "none",
      }),
    },
    { name: "style.color", label: "Color", showWhen: (values) => values.kind === "rich" },
  ];
  const submit = vi.fn<FormSubmit>(async (data) => ({ status: "ok", data: { id: "note-1", kind: data.kind } }));
  const styled = await presetForm({ kind: "rich", style: { color: "red", size: "big" } }, { fields, submit });
  styled.getOne.mockImplementationOnce(() => new Promise<{ data: Row }>(() => undefined));
  await chooseKind("plain");
  expect(Object.hasOwn(styled.values(), "style")).toBe(false);
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(submit).toHaveBeenCalledOnce());
  expect(submit.mock.calls[0]?.[0]).toEqual({ kind: "plain", style: { color: "none" } });
  await waitFor(() => expect(styled.surface().displayRecord?.kind).toBe("plain"));
  expect(styled.surface().displayRecord?.style).toEqual({ color: "none", size: "big" });
});

test("undo back to a state a preset left writes its value for the hidden field, and redo past it writes none", async () => {
  // Loaded rich; plain; a mode edit; rich again: undo returns to plain with the edit.
  const undone = await presetForm({ kind: "rich", settings: { depth: 2 }, mode: "" });
  await chooseKind("plain");
  edit("Mode", "m");
  await chooseKind("rich");
  act(() => undone.surface().history.undo());
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Kind" }).textContent).toContain("plain"));
  expect(Object.hasOwn(undone.values(), "settings")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(undone.update).toHaveBeenCalledOnce());
  expect(undone.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { kind: "plain", mode: "m", settings: {} } }));
  cleanup();

  const redone = await presetForm({ kind: "rich", settings: { depth: 2 }, mode: "" });
  await chooseKind("plain");
  edit("Mode", "m");
  await chooseKind("rich");
  act(() => redone.surface().history.undo());
  act(() => redone.surface().history.redo());
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Kind" }).textContent).toContain("rich"));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(redone.update).toHaveBeenCalledOnce());
  expect(redone.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { mode: "m" } }));
});

test("a preset field at its loaded value writes nothing, whatever its preset seeds", async () => {
  const fields = presetFields.map((field) => field.name === "kind" ? { ...field, prefill: () => ({ settings: {} }) } : field);
  const naive = await presetForm({ kind: "rich", settings: { depth: 2 }, mode: "" }, { fields });
  edit("Mode", "off");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(naive.update).toHaveBeenCalledOnce());
  expect(naive.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { mode: "off" } }));
});

test("a preset's relation value for a hidden field is sent as the related id", async () => {
  const fields: readonly FieldDescriptor[] = [
    {
      ...presetFields[0]!,
      prefill: (value, _values, loaded) => ({
        parent: value === loaded?.kind ? loaded?.parent : { id: "note-b", title: "Note B" },
      }),
    },
    { name: "parent", label: "Parent", kind: "relation", widget: "many2one", showWhen: (values) => values.kind === "rich" },
  ];
  const related = await presetForm({ kind: "rich", parent: { id: "note-a", title: "Parent A" } }, { fields });
  await chooseKind("plain");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(related.update).toHaveBeenCalledOnce());
  expect(related.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { kind: "plain", parent: "note-b" } }));
});

test("the saved record keeps what the response says for a field a preset hid", async () => {
  const submit = vi.fn<FormSubmit>(async (data) => ({
    status: "ok", data: { id: "note-1", kind: data.kind, settings: { from: "server" } },
  }));
  const stored = await presetForm({ kind: "rich", settings: { depth: 2 } }, { submit });
  stored.getOne.mockImplementationOnce(() => new Promise<{ data: Row }>(() => undefined));
  await chooseKind("plain");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(stored.surface().displayRecord?.kind).toBe("plain"));
  expect(stored.surface().displayRecord?.settings).toEqual({ from: "server" });
});

test("a preset's value for a nested hidden field is written and kept by path", async () => {
  const fields: readonly FieldDescriptor[] = [
    {
      ...presetFields[0]!,
      prefill: (value, _values, loaded) => ({
        "style.color": value === loaded?.kind ? (loaded?.style as Row | undefined)?.color : "none",
      }),
    },
    { name: "style.color", label: "Color", showWhen: (values) => values.kind === "rich" },
    { name: "style.size", label: "Size" },
  ];
  const submit = vi.fn<FormSubmit>(async (data) => ({
    status: "ok", data: { id: "note-1", kind: data.kind, style: { size: "big" } },
  }));
  const styled = await presetForm({ kind: "rich", style: { color: "red", size: "big" } }, { fields, submit });
  styled.getOne.mockImplementationOnce(() => new Promise<{ data: Row }>(() => undefined));
  await chooseKind("plain");
  expect(styled.values()).toMatchObject({ style: { size: "big" } });
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(submit).toHaveBeenCalledOnce());
  expect(submit.mock.calls[0]?.[0]).toEqual({ kind: "plain", style: { color: "none" } });
  await waitFor(() => expect(styled.surface().displayRecord?.kind).toBe("plain"));
  expect(styled.surface().displayRecord?.style).toEqual({ size: "big", color: "none" });
});

test("an acknowledged save adds nothing of its own to the saved record", async () => {
  const submit = vi.fn<FormSubmit>(async (_data, context) => acknowledgeFormSubmit({ id: "note-1", kind: "plain" }, context.values));
  const stored = await presetForm({ kind: "rich", settings: { depth: 2 } }, { submit });
  stored.getOne.mockImplementationOnce(() => new Promise<{ data: Row }>(() => undefined));
  await chooseKind("plain");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(submit).toHaveBeenCalledOnce());
  expect(submit.mock.calls[0]?.[0]).toEqual({ kind: "plain", settings: {} });
  await waitFor(() => expect(stored.surface().displayRecord?.kind).toBe("plain"));
  expect(stored.surface().displayRecord?.settings).toEqual({ depth: 2 });
});

test("a sibling sharing the hidden field's name prefix is neither written nor in the way", async () => {
  const fields = [...presetFields, { name: "settingsNote", label: "Settings note" }];
  const prefixed = await presetForm({ kind: "rich", settings: { depth: 2 }, settingsNote: "note" }, { fields });
  await chooseKind("plain");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(prefixed.update).toHaveBeenCalledOnce());
  expect(prefixed.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { kind: "plain", settings: {} } }));
});

test("a second save after a successful one sends nothing more for the hidden field", async () => {
  const stored = await presetForm({ kind: "rich", settings: { depth: 2 }, mode: "" });
  await chooseKind("plain");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(stored.update).toHaveBeenCalledOnce());
  await waitFor(expectClean);
  edit("Mode", "m");
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(stored.update).toHaveBeenCalledTimes(2));
  expect(stored.update.mock.calls[1]?.[0].variables).toEqual({ mode: "m" });
});

test("a preset's value is never written over what a visible field holds", async () => {
  // The preset sets one setting; the raw settings of `rich` hold it whole.
  const fields = presetFields.map((field) => field.name === "kind" ? {
    ...field,
    prefill: (value: unknown, _values: Row, loaded?: Row) => ({
      "settings.a": value === loaded?.kind ? (loaded?.settings as Row | undefined)?.a : "preset",
    }),
  } : field);
  const raw = await presetForm({ kind: "split", settings: { a: "one", b: "two" } }, { fields });
  await chooseKind("rich");
  act(() => raw.surface().form.setValue("settings", { a: "mine" }, { shouldDirty: true }));
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(raw.update).toHaveBeenCalledOnce());
  expect(raw.update).toHaveBeenCalledWith(expect.objectContaining({ variables: { kind: "rich", settings: { a: "mine" } } }));
});

test("a field a preset dropped keeps the value its own control gives it afterwards, none included", async () => {
  const fields: readonly FieldDescriptor[] = [
    { ...presetFields[0]!, prefill: (value, _values, loaded) => value === loaded?.kind ? null : { note: "" } },
    { name: "note", label: "Note", omittable: true, showWhen: (values) => values.kind === "rich" },
  ];
  const noted = await presetForm({ kind: "rich", note: "kept" }, { fields });
  await chooseKind("plain");
  await chooseKind("rich");
  expect((await screen.findByLabelText("Note") as HTMLInputElement).value).toBe("kept");
  fireEvent.click(screen.getByRole("button", { name: "Not set" }));
  await waitFor(() => expect(noted.values().note).toBeUndefined());
  expect(screen.queryByLabelText("Note")).toBeNull();
});

test("typed into a kind's nested fields and back to the loaded kind, the form is as loaded", async () => {
  const typed = await presetForm({ kind: "plain", settings: {} });
  await chooseKind("split");
  edit("A", "typed");
  await chooseKind("plain");
  expect(Object.hasOwn(typed.values(), "settings")).toBe(false);
  await waitFor(expectClean);
  expect(typed.update).not.toHaveBeenCalled();
});

// One saved record's form holding each kind of value an undo step must land on as it was recorded,
// every control mounted at once. FormView binds no undo key or button of its own: its record
// contexts carry `form.history` for the page to bind, which these tests call.
const undoResource = withFields(["layout", "config"], presetResource);
const undoFields: readonly FieldDescriptor[] = [
  { name: "body", label: "Body" },
  { name: "summary", label: "Summary", omittable: true },
  { name: "note", label: "Note", nullable: true },
  { name: "style.color", label: "Color", omittable: true },
  { name: "layout", label: "Layout", widget: "object", objectTemplate: [
    { name: "heading", label: "Heading" }, { name: "caption", label: "Caption", omittable: true },
  ] },
  { name: "config", label: "Config", widget: "json" },
  ...presetFields.filter((field) => ["kind", "settings", "mode"].includes(field.name)),
];
const undoRecord: Row = {
  body: "Loaded body", summary: "Loaded summary", note: "Loaded note", style: { color: "red", size: "big" },
  layout: { heading: "Loaded heading", caption: "Loaded caption" }, config: { count: 1, label: "a" },
  kind: "rich", settings: { depth: 2 }, mode: "",
};

async function undoForm() {
  const form = await presetForm(undoRecord, { fields: undoFields, resource: undoResource });
  await screen.findByText("Caption", { selector: "label" });
  await screen.findByRole("textbox", { name: "Config" });
  return form;
}

/** The record field labelled `label`: the root holding its control and its presence buttons. */
function recordField(label: string): HTMLElement {
  const root = screen.getByText(label, { selector: "label" }).closest<HTMLElement>("[data-layout]");
  if (!root) throw new Error(`No field labelled ${label}`);
  return root;
}

/** What a field with presence buttons shows: its value in its input, or the state it was left in. */
function expectPresence(label: string, shown: { value: string } | { left: "Not set" | "Left empty" }) {
  const field = within(recordField(label));
  if ("value" in shown) {
    expect((field.getByRole("textbox") as HTMLInputElement).value).toBe(shown.value);
    return;
  }
  expect(field.queryByRole("textbox")).toBeNull();
  expect(field.getByText(shown.left)).toBeTruthy();
  expect(field.getByRole("button", { name: "Set value" })).toBeTruthy();
}

/** The value a JSON editor's text holds now. */
function jsonShown(label: string): unknown {
  return JSON.parse(EditorView.findFromDOM(screen.getByRole("textbox", { name: label }))!.state.doc.toString());
}

type UndoFrame = "loaded" | "changed";
const undoKinds: readonly {
  kind: string;
  change: () => void | Promise<void>;
  shows: (frame: UndoFrame) => void;
  saves: Row;
  /** A key the save holds as its own, or does not; a top-level key has the parent path "". */
  member?: readonly [parent: string, key: string, held: boolean];
  /** The known defect: undo and redo do not land on this frame (see the expected-failure rows below). */
  lost?: true;
}[] = [
  {
    kind: "plain text",
    change: () => { edit("Body", "Second"); },
    shows: (frame) => expect((screen.getByRole("textbox", { name: "Body" }) as HTMLInputElement).value)
      .toBe(frame === "loaded" ? "Loaded body" : "Second"),
    saves: { body: "Second" },
  },
  {
    kind: "an omittable field left Not set", lost: true,
    change: () => { fireEvent.click(within(recordField("Summary")).getByRole("button", { name: "Not set" })); },
    shows: (frame) => expectPresence("Summary", frame === "loaded" ? { value: "Loaded summary" } : { left: "Not set" }),
    saves: { summary: undefined }, member: ["", "summary", true],
  },
  {
    kind: "a nullable field left empty",
    change: () => { fireEvent.click(within(recordField("Note")).getByRole("button", { name: "Leave empty" })); },
    shows: (frame) => expectPresence("Note", frame === "loaded" ? { value: "Loaded note" } : { left: "Left empty" }),
    saves: { note: null },
  },
  {
    kind: "a nested member left Not set", lost: true,
    change: () => { fireEvent.click(within(recordField("Color")).getByRole("button", { name: "Not set" })); },
    shows: (frame) => expectPresence("Color", frame === "loaded" ? { value: "red" } : { left: "Not set" }),
    saves: { style: { color: undefined } }, member: ["style", "color", true],
  },
  {
    kind: "a structured field's member left Not set, the object losing it",
    change: () => { fireEvent.click(within(recordField("Caption")).getByRole("button", { name: "Not set" })); },
    shows: (frame) => {
      expectPresence("Heading", { value: "Loaded heading" });
      expectPresence("Caption", frame === "loaded" ? { value: "Loaded caption" } : { left: "Not set" });
    },
    saves: { layout: { heading: "Loaded heading" } }, member: ["layout", "caption", false],
  },
  {
    kind: "a whole object replaced by one without a member",
    change: () => {
      const editor = EditorView.findFromDOM(screen.getByRole("textbox", { name: "Config" }))!;
      act(() => editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: '{"count": 2}' } }));
    },
    shows: (frame) => expect(jsonShown("Config")).toStrictEqual(frame === "loaded" ? { count: 1, label: "a" } : { count: 2 }),
    saves: { config: { count: 2 } }, member: ["config", "label", false],
  },
  {
    kind: "a preset that hides its target",
    change: () => chooseKind("plain"),
    shows: (frame) => {
      expect(screen.getByRole("combobox", { name: "Kind" }).textContent).toContain(frame === "loaded" ? "rich" : "plain");
      if (frame === "loaded") expect(jsonShown("Settings")).toStrictEqual({ depth: 2 });
      else expect(screen.queryByRole("textbox", { name: "Settings" })).toBeNull();
    },
    // The draft drops the hidden target; the save writes the preset's value over the loaded one.
    saves: { kind: "plain", settings: {} },
  },
];

async function landsOnItsFrame({ change, shows, saves, member }: (typeof undoKinds)[number]): Promise<void> {
  const form = await undoForm();
  shows("loaded");
  expectClean();
  await change();
  await waitFor(() => shows("changed"));
  expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  // A second, unrelated change, so the first undo lands ON the changed frame rather than on the loaded one.
  edit("Mode", "m");
  act(() => form.surface().history.undo());
  await waitFor(() => shows("changed"));
  expect((screen.getByRole("textbox", { name: "Mode" }) as HTMLInputElement).value).toBe("");
  expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Discard" })).toBeTruthy();
  act(() => form.surface().history.undo());
  await waitFor(() => shows("loaded"));
  await waitFor(expectClean);
  act(() => form.surface().history.redo());
  await waitFor(() => shows("changed"));
  expect(screen.getByRole("button", { name: "Discard" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(form.update).toHaveBeenCalledOnce());
  const { variables } = form.update.mock.calls[0]![0] as { variables: Row };
  // `toStrictEqual` tells a key holding `undefined` from a missing key; `member` names the key in question.
  expect(variables).toStrictEqual(saves);
  if (member) {
    const [parent, key, held] = member;
    expect(Object.hasOwn((parent ? get(variables, parent) : variables) as object, key)).toBe(held);
  }
}

test.each(undoKinds.filter((row) => !row.lost))("undo and redo land on the frame $kind recorded: its control shows it and the save writes it", landsOnItsFrame);

// KNOWN DEFECT, expected to fail until it is fixed: react-hook-form reads `undefined` as take-the-default at registration. After the undo the
// control still shows "Not set" while the form holds the loaded value and counts as unchanged, so a
// save sends nothing. These two rendered rows are the tripwire: they flip to passing when a field
// that can be left "Not set" is no longer a registered field of its own (see `useFormHistory`);
// move them up then.
test.fails.each(undoKinds.filter((row) => row.lost))("undo and redo lose $kind: react-hook-form reads `undefined` as take-the-default at registration", landsOnItsFrame);

test("undo while a text input has focus leaves it focused, and typing on is recorded and undoable", async () => {
  const form = await undoForm();
  const body = screen.getByRole("textbox", { name: "Body" }) as HTMLInputElement;
  act(() => body.focus());
  fireEvent.change(body, { target: { value: "Second" } });
  act(() => form.surface().history.undo());
  expect(body.value).toBe("Loaded body");
  expect(screen.getByRole("textbox", { name: "Body" })).toBe(body);
  expect(document.activeElement).toBe(body);
  expect(body.selectionStart).toBe(body.value.length);
  fireEvent.change(body, { target: { value: "Third" } });
  expect(form.surface().history.canUndo).toBe(true);
  expect(form.surface().history.canRedo).toBe(false);
  act(() => form.surface().history.undo());
  expect(body.value).toBe("Loaded body");
  expect(document.activeElement).toBe(body);
  act(() => form.surface().history.redo());
  expect(body.value).toBe("Third");
  expect(document.activeElement).toBe(body);
});

// An `object` widget accepts only an object and a `list` widget only an array, so the form gives
// them one before a record's values arrive and in a create form.
const structuredResource: DataResourceMetadata = { ...withFields(["layout", "steps"]), createFields: ["title", "layout", "steps"] };
const structuredFields: readonly FieldDescriptor[] = [
  { name: "title", label: "Title" },
  { name: "layout", label: "Layout", widget: "object", objectTemplate: [
    { name: "heading", label: "Heading" }, { name: "caption", label: "Caption" },
  ] },
  { name: "steps", label: "Steps", widget: "list", itemTemplate: { name: "step" } },
];

function textboxValue(name: string): string {
  return (screen.getByRole("textbox", { name }) as HTMLInputElement).value;
}

// A code-split widget's first mount waits for its code, by when the record's values are in; every
// later form renders it at once, with what the form holds when the record first paints. So each
// test opens one record, then the next. Each record lacks the other structured field.
test("an object field shows its members with the values of each record that arrives after the first paint", async () => {
  for (const opened of ["First", "Next"]) {
    await presetForm({ layout: { heading: `${opened} heading`, caption: `${opened} caption` } }, { fields: structuredFields, resource: structuredResource });
    await screen.findByRole("textbox", { name: "Heading" });
    expect(textboxValue("Heading")).toBe(`${opened} heading`);
    expect(textboxValue("Caption")).toBe(`${opened} caption`);
    expect(await screen.findByRole("button", { name: "Add item" })).toBeTruthy();
    expectClean();
    cleanup();
  }
});

test("a list field shows its items with the values of each record that arrives after the first paint", async () => {
  for (const opened of ["First", "Next"]) {
    await presetForm({ steps: [`${opened} step one`, `${opened} step two`] }, { fields: structuredFields, resource: structuredResource });
    await screen.findByRole("textbox", { name: "Item 1" });
    expect(textboxValue("Item 1")).toBe(`${opened} step one`);
    expect(textboxValue("Item 2")).toBe(`${opened} step two`);
    expect(((await screen.findByRole("textbox", { name: "Heading" })) as HTMLInputElement).value).toBe("");
    expectClean();
    cleanup();
  }
});

// The form's structured check refuses null in a templated field it declares not nullable, and so
// every save of that record; a record holding null is read through nullable declarations.
const nullableStructuredFields = structuredFields.map((field) => field.name === "title" ? field : { ...field, nullable: true });

test.each([
  ["absent", {}, structuredFields],
  ["absent from a nullable field", {}, nullableStructuredFields],
  ["null in a nullable field", { layout: null, steps: null }, nullableStructuredFields],
] as const)("untouched object and list fields whose record value is %s leave the form clean and out of a save of another field", async (_value, stored, fields) => {
  const form = await presetForm(stored, { fields, resource: structuredResource });
  await waitFor(() => expect(textboxValue("Title")).toBe("First"));
  expectClean();
  fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Changed" } });
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(form.update).toHaveBeenCalledOnce());
  expect((form.update.mock.calls[0]![0] as { variables: Row }).variables).toStrictEqual({ title: "Changed" });
});

test("a create form renders an object field's members and a list field's add button, and a save sends what they hold", async () => {
  const f = await fixture({ publicView: true, id: null, resourceMetadata: structuredResource, viewFields: structuredFields });
  fireEvent.change(await screen.findByRole("textbox", { name: "Heading" }), { target: { value: "New heading" } });
  fireEvent.click(await screen.findByRole("button", { name: "Add item" }));
  fireEvent.change(await screen.findByRole("textbox", { name: "Item 1" }), { target: { value: "New step" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "New note" } });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(f.update).toHaveBeenCalledOnce());
  expect((f.update.mock.calls[0]![0] as { variables: Row }).variables)
    .toStrictEqual({ title: "New note", layout: { heading: "New heading" }, steps: ["New step"] });
});

// A `rows` widget accepts only an array of objects, so the form gives it an empty list as it gives a
// `list` widget one. Its row count is fixed: an empty list shows the columns and no row.
const rowsResource: DataResourceMetadata = { ...withFields(["mappings"]), createFields: ["title", "mappings"] };
const mappingsField: FormSpecFieldDescriptor = { name: "mappings", label: "Mappings", kind: "array", widget: "rows", rowTemplate: [
  { name: "source", label: "Source" }, { name: "target", label: "Target" },
] };
const rowsFields: readonly FieldDescriptor[] = [{ name: "title", label: "Title" }, mappingsField];

function textboxValues(name: string): string[] {
  return screen.getAllByRole("textbox", { name }).map((input) => (input as HTMLInputElement).value);
}

test("a rows field shows its rows with the values of each record that arrives after the first paint", async () => {
  for (const opened of ["First", "Next"]) {
    await presetForm({ mappings: [
      { source: `${opened} source one`, target: `${opened} target one` },
      { source: `${opened} source two`, target: `${opened} target two` },
    ] }, { fields: rowsFields, resource: rowsResource });
    await screen.findAllByRole("textbox", { name: "Source" });
    expect(textboxValues("Source")).toEqual([`${opened} source one`, `${opened} source two`]);
    expect(textboxValues("Target")).toEqual([`${opened} target one`, `${opened} target two`]);
    expectClean();
    cleanup();
  }
});

test("a create form renders a rows field's columns each time it opens", async () => {
  for (let opened = 0; opened < 2; opened += 1) {
    await fixture({ publicView: true, id: null, resourceMetadata: rowsResource, viewFields: rowsFields });
    const table = await screen.findByRole("table", { name: "Mappings" });
    expect(within(table).getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["Source", "Target"]);
    expect(within(table).queryByRole("textbox")).toBeNull();
    cleanup();
  }
});

// The create rule for a container field, whichever widget holds it: untouched, it is sent as an
// empty container. The json, object, list and rows fields are JSON scalars, as a model's JSON
// column is: with that metadata an untouched object, list or rows field used to be left out.
const containerNames = ["config", "members", "labels", "layout", "steps", "mappings"];
const jsonColumns = ["config", "layout", "steps", "mappings"];
const containerBase = withFields(containerNames);
const containerResource: DataResourceMetadata = {
  ...containerBase,
  createFields: ["title", ...containerNames],
  fields: containerBase.fields?.map((field) => jsonColumns.includes(field.name) ? { ...field, scalar: "JSON" } : field),
};
const containerFields: readonly FieldDescriptor[] = [
  { name: "title", label: "Title" },
  { name: "config", label: "Config", widget: "json" },
  { name: "members", label: "Members", widget: "many2many" },
  { name: "labels", label: "Labels", widget: "tagInput" },
  ...structuredFields.filter((field) => field.name !== "title"),
  mappingsField,
];

test("a create form sends each untouched container field as an empty container: an object for json and object, a list for list, rows, many2many and tagInput", async () => {
  const f = await fixture({ publicView: true, id: null, resourceMetadata: containerResource, viewFields: containerFields });
  fireEvent.change(await screen.findByRole("textbox", { name: "Title" }), { target: { value: "New note" } });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(f.update).toHaveBeenCalledOnce());
  expect((f.update.mock.calls[0]![0] as { variables: Row }).variables).toStrictEqual({
    title: "New note", config: {}, layout: {}, members: [], labels: [], steps: [], mappings: [],
  });
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
    viewFields: [...fields, { name: "definition", hidden: true }],
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

test("create retries reuse the form session's idempotency key, and a lost answer says the retry is safe", async () => {
  const f = await fixture({ id: null, rootArguments: true, mountedFields: ["title", "body", "deadline"] });
  edit("title", "New record");
  edit("deadline", "2026-09-28");
  f.update.mockRejectedValueOnce(new Error("Unavailable"));
  await act(async () => f.surface().submitForm());
  // The fixture's translator answers with the key: the keyed create's lost-answer sentence.
  expect(f.surface().saveErrors).toEqual(["form.answerLostResend"]);
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
  // A spent key is no stale edit: the stale banner and its reload are not offered.
  expect(f.surface().saveConflict).toBe(false);
  expect(f.surface().form.getValues("title")).toBe("My record");
  const first = f.update.mock.calls[0]?.[0].meta?.gqlVariables?.idempotency_key;
  await act(async () => f.surface().submitForm());
  const next = f.update.mock.calls[1]?.[0].meta?.gqlVariables?.idempotency_key;
  expect(next).toEqual(expect.any(String));
  expect(next).not.toBe(first);
});

test("a create whose key an earlier attempt spent is told in the form's own words, with nothing to undo; a stale edit keeps its banner", async () => {
  const f = await fixture({ id: null, rootArguments: true, publicView: true,
    viewFields: [{ name: "title", label: "Title" }, { name: "deadline", label: "Deadline" }] });
  fireEvent.change(await screen.findByRole("textbox", { name: "Title" }), { target: { value: "My record" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Deadline" }), { target: { value: "2026-09-28" } });
  f.update.mockRejectedValueOnce({ graphQLErrors: [{ message: "CREATION_KEY_CONFLICT", extensions: { code: "CREATION_KEY_CONFLICT" } }] });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  expect(await screen.findByText("Created earlier")).toBeTruthy();
  expect(screen.getByText("An earlier attempt to save from this form already created a record; it is in the list. Saving again creates another.")).toBeTruthy();
  expect(screen.queryByText("CREATION_KEY_CONFLICT")).toBeNull();
  expect(screen.queryByRole("button", { name: "Reload saved record" })).toBeNull();
  expect((screen.getByRole("textbox", { name: "Title" }) as HTMLInputElement).value).toBe("My record");

  f.update.mockRejectedValueOnce({ graphQLErrors: [{ message: "Changed", extensions: { code: "STALE_REVISION" } }] });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  expect(await screen.findByText("Record changed")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Reload saved record" })).toBeTruthy();
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

test("a custom create owner's lost answer says only that it may have been sent: the form cannot tell that its owner sends the key", async () => {
  const submit = vi.fn<FormSubmit>().mockRejectedValueOnce(boundedGraphQLTransportError(new TypeError("Failed to fetch")));
  const f = await fixture({ id: null, submit, mountedFields: ["title", "body", "deadline"] });
  edit("title", "One");
  edit("deadline", "2026-09-28");
  await act(async () => f.surface().submitForm());
  // The fixture's translator answers with the key.
  expect(f.surface().saveErrors).toEqual(["form.answerLostMaybeSent"]);
});

test("a new record is one draft: a refused create keeps its key for the corrected retry, and discarding it then starts a new draft", async () => {
  const f = await fixture({ id: null, rootArguments: true, mountedFields: ["title", "body", "deadline"] });
  edit("title", "Refused");
  edit("deadline", "2026-09-28");
  f.update.mockRejectedValueOnce({ graphQLErrors: [{ message: "Invalid.", extensions: {
    code: "VALIDATION", validationErrors: { title: ["Taken."] }, formErrors: [] } }] });
  await act(async () => f.surface().submitForm());
  f.update.mockRejectedValueOnce({ graphQLErrors: [{ message: "Invalid.", extensions: {
    code: "VALIDATION", validationErrors: { title: ["Taken."] }, formErrors: [] } }] });
  edit("title", "Corrected");
  await act(async () => f.surface().submitForm());
  act(() => f.surface().discardChanges());
  edit("title", "Another record");
  edit("deadline", "2026-09-28");
  await act(async () => f.surface().submitForm());
  const [refused, corrected, afterDiscard] = f.update.mock.calls.map(([call]) => call.meta?.gqlVariables?.idempotency_key);
  expect(refused).toEqual(expect.any(String));
  expect(corrected).toBe(refused);
  expect(afterDiscard).not.toBe(refused);
});

test("a create whose answer was lost keeps its key when the form is discarded, or left and returned to: the record may exist, and the identical retry replays to it", async () => {
  const f = await fixture({ id: null, rootArguments: true, mountedFields: ["title", "body", "deadline"] });
  edit("title", "Lost");
  edit("deadline", "2026-09-28");
  f.update.mockRejectedValueOnce(new TypeError("Failed to fetch")).mockRejectedValueOnce(new TypeError("Failed to fetch"));
  await act(async () => f.surface().submitForm());
  act(() => f.surface().discardChanges());
  edit("title", "Lost");
  edit("deadline", "2026-09-28");
  await act(async () => f.surface().submitForm());
  f.rerender({ recordId: "note-1" });
  f.rerender({ recordId: null });
  edit("title", "Lost");
  edit("deadline", "2026-09-28");
  await act(async () => f.surface().submitForm());
  const keys = f.update.mock.calls.filter(([call]) => call.meta?.gqlVariables?.idempotency_key).map(([call]) => call.meta?.gqlVariables?.idempotency_key);
  expect(keys).toHaveLength(3);
  expect(new Set(keys).size).toBe(1);
  // The server answers the same key with the same content with the record it created: the form lands on it.
  expect(f.onSaved.mock.lastCall?.[0]).toMatchObject({ id: "note-1", title: "Lost" });
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
  expect(f.surface().saveErrors).toEqual([]);
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
  const control = screen.getByRole(field.name === "parent" ? "combobox" : "textbox", { name: field.label });
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
  await act(async () => f.surface().submitForm());
  expect(f.surface().saveErrors).toEqual(["No record returned."]);
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
  // The form-level reason, the line naming the refused field the form shows, then the reason no field shows.
  expect(f.surface().saveErrors).toEqual(["Cannot save", "form.fixHighlightedFieldsNamed", "lines.0.title: Invalid line"]);
  expect(f.surface().serverFieldErrors).toMatchObject({ "lines.0.title": ["Invalid line"] });
  act(() => f.surface().clearServerFieldError("title"));
  expect(f.surface().form.getFieldState("title").error).toBeUndefined();
});

test("duplicate submits are ignored and a previous record save cannot reset or notify the new form", async () => {
  let resolve!: (value: Row) => void;
  const submit = vi.fn(() => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })));
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
  const submit = vi.fn(() => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })));
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
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })) });
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
  const f = await fixture({ submit: () => new Promise<Row | null>((done, fail) => { resolve = done; reject = fail; }).then((data) => savedFormSubmitResult(data, "No record returned.")) });
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
  let saving!: Promise<void>;
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
  let saving!: Promise<void>;
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

test("a bound descriptor whose resolve returns another name reads and writes its declared path", async () => {
  const f = await fixture({
    acknowledgedSource: {
      record: { id: "note-1", title: "First" },
      values: { title: "First", settings: { retry: "later" } },
    },
    mountedFields: [],
    boundFields: [{ scope: "settings", field: {
      // This cast hides the returned `name` from the compiler; the declared path must still win.
      name: "retry", label: "Retry", resolve: () => ({ name: "elsewhere", label: "Retry" }) as Omit<DescriptorField, "name">,
    } }],
  });
  expect((await screen.findByLabelText("Retry") as HTMLInputElement).value).toBe("later");
  edit("Retry", "soon");
  expect(f.surface().form.getValues("settings")).toEqual({ retry: "soon" });
});

test("a keystroke in a field without a preset evaluates the form's conditions no more than it has to", async () => {
  const showWhen = vi.fn((values: Row) => values.title === "Scheduled");
  await fixture({ publicView: true, viewFields: [{ name: "title", label: "Title" }, { name: "deadline", label: "Deadline", showWhen }] });
  const title = await screen.findByRole("textbox", { name: "Title" });
  await waitFor(() => expect((title as HTMLInputElement).value).toBe("First"));
  showWhen.mockClear();
  fireEvent.change(title, { target: { value: "Firsts" } });
  // Twice: the draft's restore step and the sheet's render. A field without a preset writes no seed, so no hidden-path step runs.
  expect(showWhen).toHaveBeenCalledTimes(2);
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
  expect(f.surface().saveErrors).toEqual([]);
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
  expect(f.surface().saveErrors).toEqual([]);
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
  expect(f.surface().saveErrors).toEqual([]);
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
  let currentNativeTitle: unknown;
  let unrelatedTitle: unknown;
  function Probe() {
    const list = useList({ resource: related.roots.list!, dataProviderName: "console" });
    const current = useList({ resource: formResource.roots.list!, dataProviderName: "console" });
    const other = useList({ resource: unrelated.roots.list!, dataProviderName: "console" });
    const authored = useAuthoredQuery(relatedSummary, {}, { dataProviderName: "console", models: [related.modelLabel] });
    const currentAuthored = useAuthoredQuery(currentSummary, {}, { dataProviderName: "console", models: [resource.modelLabel] });
    const [command] = useAuthoredResourceMutation(createNoteCommand, { dataProviderName: "console", invalidateModels: [resource.modelLabel] });
    currentAuthoredTitle = currentAuthored.data?.summary;
    currentNativeTitle = current.result.data?.[0]?.title;
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
  await waitFor(() => { expect(surface.loading).toBe(false); expect(nativeTitle).toBe("Before"); expect(authoredTitle).toBe("Before"); expect(currentAuthoredTitle).toBe("Before"); expect(currentNativeTitle).toBe("Before"); expect(unrelatedTitle).toBe("Before"); });
  await act(async () => {
    if (mode === "patch") await surface.applyPatch({ title: "After" });
    else {
      surface.form.setValue("title", "After", { shouldDirty: true });
      if (mode === "nested") surface.form.setValue("lines", [{ id: "line-1", label: "After" }], { shouldDirty: true });
      await surface.submitForm();
    }
  });
  if (mode === "rejected") {
    expect(surface.saveErrors).toEqual(["Refused"]);
    expect(write).not.toHaveBeenCalled();
    expect(getList).toHaveBeenCalledTimes(3);
    expect(custom).toHaveBeenCalledTimes(2);
  } else {
    await waitFor(() => { expect(nativeTitle).toBe("After"); expect(authoredTitle).toBe("After"); expect(currentAuthoredTitle).toBe("After"); expect(currentNativeTitle).toBe("After"); });
    expect(write).toHaveBeenCalledTimes(mode === "nested" || mode === "command" ? 0 : 1);
    expect(getList.mock.calls).toHaveLength(5);
    expect(custom).toHaveBeenCalledTimes(mode === "nested" || mode === "command" ? 5 : 4);
    // The command owns its own model refresh; the Form must not issue a second one.
    expect(custom.mock.calls.filter(([params]) => params.meta?.gqlQuery === currentSummary)).toHaveLength(2);
  }
  expect(unrelatedTitle).toBe("Before");
});
