// @vitest-environment happy-dom

import { formSubmitError } from "./validation-errors";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRootRoute, createRouter, createMemoryHistory, RouterContextProvider } from "@tanstack/react-router";
import { Controller, useFieldArray, type Control } from "react-hook-form";
import type { ComponentProps } from "react";
import { schemaFieldMetadataFromDataResources, type DataResourceFieldMetadata, type DataResourceMetadata, type ModelMetadata, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider, type ResourceSaveVariables } from "@angee/refine";
import { useList } from "@refinedev/core";
import { afterEach, expect, test, vi } from "vitest";
import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider, containersFromChildren, type ComposedContainers } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { defaultWidgets, type WidgetDefinition, type WidgetRenderProps } from "../../widgets";
import { CLIENT_LINE_KEY, duplicateLineRow, emptyLineRow, lineDiffConfig } from "./editable-lines";
import { EditableLines } from "./EditableLines";
import type { RecordTabDescriptor } from "./form-view-surface";
import { FORM_CONTAINERS, FormView } from "./FormView";
import { readNextSave } from "./next-save.test-support";
import { acknowledgeFormSubmit, useFormViewSave, type FormSubmit, type FormViewSaveSurface } from "./use-form-view-save";
import { Group, type FieldDescriptor } from "../page";

const formFields: readonly FieldDescriptor[] = [{ name: "title", label: "Title" }];
const fieldByName = new Map(formFields.map((field) => [field.name, field]));
const refineFields = ["id", "title", "lines"];
const lineField = (name: string, scalar: string): DataResourceFieldMetadata => ({
  name, kind: "scalar", scalar, readable: true, filterable: false, sortable: false,
  aggregatable: false, groupable: false, creatable: true, updatable: true, requiredOnCreate: false,
});
const resource = testDataResource("review.Document", {
  fields: [lineField("title", "String")],
  linesResource: {
    field: "lines", modelLabel: "review.Line", positionField: "position",
    fields: [lineField("label", "String"), lineField("quantity", "Int"), lineField("position", "Int")],
  },
});
const model: ModelMetadata = schemaFieldMetadataFromDataResources([resource]).labels["review.Document"]!;
const renderedResource = testDataResource(resource.modelLabel, {
  ...resource,
  roots: { ...resource.roots, save: "documents_save" },
  linesResource: {
    ...resource.linesResource!,
    fields: [...(resource.linesResource!.fields ?? []), lineField("price", "Decimal")],
  },
});
const saveDocument = { kind: "Document", definitions: [] };
const initialLines: readonly Row[] = [
  { id: "a", label: "Alpha", quantity: 10, position: 0 },
  { id: "b", label: "Bravo", quantity: 20, position: 1 },
  { id: "c", label: "Charlie", quantity: 30, position: 2 },
];
const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://lines",
  queryClientConfig: { defaultOptions: { queries: { retry: false }, mutations: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); });

async function fixture(options: {
  resourceMetadata?: DataResourceMetadata;
  revision?: number;
  submit?: FormSubmit;
  onSaved?: () => void;
  lines?: readonly Row[];
  isCreate?: boolean;
  create?: () => Promise<{ data: Row }>;
  publicView?: boolean;
  save?: (variables: ResourceSaveVariables) => Promise<Row>;
  recordExtras?: ComponentProps<typeof FormView>["recordExtras"];
  formProps?: Pick<
    ComponentProps<typeof FormView>,
    "recordTabs" | "linesTabLabel" | "groups" | "onFieldInteractionStart" | "onFieldInteractionCommit" | "lineFields"
  >;
  containers?: ComposedContainers;
  /** Widgets registered beside the defaults, for a line field's authored widget. */
  widgets?: Record<string, WidgetDefinition>;
  /** The form loads with its lines in values but not on the page. */
  linesHidden?: boolean;
  /** The resource's create takes an idempotency key. */
  keyed?: boolean;
} = {}) {
  const seedLines = options.lines ?? initialLines;
  const activeResource = options.resourceMetadata ?? (options.publicView ? renderedResource : resource);
  let record: Row = { id: "doc-1", title: "Original", lines: seedLines, revision: options.revision };
  const getOne = vi.fn(async () => ({ data: record }));
  const submit = vi.fn<FormSubmit>(options.submit ?? (async () => formSubmitError("No record returned.")));
  const update = vi.fn(async ({ variables }: { variables: Row }) => {
    record = { ...record, ...variables };
    return { data: record };
  });
  const custom = vi.fn(async ({ payload }: { payload: ResourceSaveVariables }) => {
    const root = activeResource.roots.save;
    if (!root) throw new Error("No save root configured for this resource.");
    record = options.save ? await options.save(payload) : {
      ...record,
      ...payload.patch,
      lines: payload.lines?.map((line, index) => ({ ...line, id: line.id ?? `new-${index}` })) ?? record.lines,
    };
    return { data: { [root]: record } };
  });
  const provider = {
    getOne,
    getList: vi.fn(async (): Promise<{ data: Row[]; total: number }> => ({ data: [], total: 0 })),
    create: vi.fn(options.create), update, custom,
  } satisfies RefineTestDataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  let surface!: FormViewSaveSurface;
  let remove!: (index: number) => void;
  let move!: (from: number, to: number) => void;
  let append!: (row: Row) => void;
  function Probe() {
    surface = useFormViewSave({
      resource: resource.modelLabel, id: options.isCreate ? null : "doc-1", isCreate: options.isCreate ?? false,
      dataResource: options.keyed ? { ...resource, createArguments: [{ name: "idempotency_key", type: "String" }] } : resource,
      modelMetadata: model, formFields, fieldByName, refineFields,
      submit: options.isCreate ? undefined : submit, onSaved: options.onSaved, t: (key) => key,
    });
    const array = useFieldArray({
      control: surface.form.control as unknown as Control<{ lines: Row[] }>, name: "lines", keyName: "rhfKey",
    });
    remove = array.remove;
    move = array.move;
    append = array.append;
    return <>
      <Controller name="title" control={surface.form.control} render={({ field }) => (
        <input aria-label="title" value={String(field.value ?? "")} onChange={field.onChange} />
      )} />
      {array.fields.map((row, index) => <div key={row.rhfKey}>
        {["label", "quantity"].map((name) => <Controller key={name} name={`lines.${index}.${name}`} control={surface.form.control} render={({ field }) => (
          <input aria-label={`${row.id}.${name}`} value={String(field.value ?? "")} onChange={field.onChange} />
        )} />)}
      </div>)}
    </>;
  }
  render(<Provider resources={[activeResource]} dataProvider={provider}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider>
      {options.publicView ? (
        <OperationDocumentsProvider documents={{ [activeResource.schemaName]: { saves: { [activeResource.modelLabel]: saveDocument } } }}>
          <AppRuntimeProvider runtime={{
            widgets: { ...defaultWidgets, ...options.widgets },
            ...(options.containers ? { containers: options.containers } : {}),
          }}>
            <FormView
              {...options.formProps}
              resource={activeResource.modelLabel}
              id={options.isCreate ? null : "doc-1"}
              fields={[{ name: "title", label: "Title", title: true }]}
              formExtras={(context) => { surface = context.form; return null; }}
              recordExtras={options.recordExtras}
            />
          </AppRuntimeProvider>
        </OperationDocumentsProvider>
      ) : <Probe />}
    </ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>);
  if (!options.isCreate) {
    if (options.publicView) await screen.findByDisplayValue(options.linesHidden ? "Original" : "Charlie");
    else await screen.findByLabelText("c.label");
    await waitFor(() => expect(surface?.form.getValues("lines")).toMatchObject(seedLines));
  }
  return {
    surface: () => surface, submit, getOne, provider, update, custom,
    setRecord: (next: Row) => { record = next; },
    append: (row: Row) => act(() => append(row)),
    remove: (index: number) => act(() => remove(index)),
    move: (from: number, to: number) => act(() => move(from, to)),
    refresh: async (lines: readonly Row[]) => {
      record = { id: "doc-1", title: "Remote title", lines };
      await act(async () => { await surface.reload(); });
      await waitFor(() => expect(surface.form.getValues("title")).toBe("Remote title"));
    },
  };
}

function edit(name: string, value: string) { fireEvent.change(screen.getByLabelText(name), { target: { value } }); }

test("trailing editable lines lead the pane strip and field reveal returns from a record pane to their control", async () => {
  await fixture({ publicView: true, formProps: {
    linesTabLabel: "Lines",
    recordTabs: [{ id: "evidence", label: "Evidence", render: ({ focusField }) =>
      <button type="button" onClick={() => focusField("lines.0.label")}>Reveal first line</button> }],
  } });
  expect(screen.getAllByRole("tablist")).toHaveLength(1);
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Lines", "Evidence"]);
  expect(screen.getByRole("tab", { name: "Lines" }).getAttribute("aria-selected")).toBe("true");
  fireEvent.click(screen.getByRole("tab", { name: "Evidence" }));
  fireEvent.click(await screen.findByRole("button", { name: "Reveal first line" }));
  await waitFor(() => expect(document.activeElement).toBe(screen.getByDisplayValue("Alpha")));
  expect(screen.getByRole("tab", { name: "Lines" }).getAttribute("aria-selected")).toBe("true");
});

test("trailing lines alone are one pane beneath the sheet, headed by their label without a strip", async () => {
  await fixture({ publicView: true, formProps: { linesTabLabel: "Stages" } });
  expect(screen.queryByRole("tablist")).toBeNull();
  expect(screen.getByRole("heading", { name: "Stages" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Lines" })).toBeNull();
  const line = screen.getByDisplayValue("Alpha");
  expect(line.closest("form")).toBeNull();
  expect(screen.getByLabelText("Title").compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("a save the lines fail on the server selects the lines pane", async () => {
  const f = await fixture({ publicView: true, formProps: {
    recordTabs: [{ id: "evidence", label: "Evidence", render: () => <p>Evidence pane</p> }],
  }, save: async () => {
    throw { graphQLErrors: [{
      message: "Validation failed.",
      extensions: {
        code: "VALIDATION",
        validationErrors: { "lines.1.label": ["This field is required."] },
        formErrors: [],
      },
    }] };
  } });
  fireEvent.change(screen.getByDisplayValue("Alpha"), { target: { value: "Edited alpha" } });
  fireEvent.click(screen.getByRole("tab", { name: "Evidence" }));
  expect(await screen.findByText("Evidence pane")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByRole("tab", { name: "Lines" }).getAttribute("aria-selected")).toBe("true"));
  expect(await screen.findByText("This field is required.")).toBeTruthy();
});

test("a save refused because the lines changed elsewhere says so on the banner and leaves the selected pane", async () => {
  const f = await fixture({ publicView: true, formProps: {
    recordTabs: [{ id: "evidence", label: "Evidence", render: () => <p>Evidence pane</p> }],
  } });
  fireEvent.change(screen.getByDisplayValue("Alpha"), { target: { value: "Edited alpha" } });
  fireEvent.click(screen.getByRole("tab", { name: "Evidence" }));
  expect(await screen.findByText("Evidence pane")).toBeTruthy();
  await f.refresh([{ id: "x", label: "Xray", quantity: 40, position: 0 }, ...initialLines.map((row, index) => ({ ...row, position: index + 1 }))]);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  // The rendered form words the conflict; it is a reason no field shows, so nothing selects a pane for it,
  // the lines included.
  await waitFor(() => expect(f.surface().saveErrors).toEqual(["These lines changed on the server. Discard local changes to load the saved lines before saving again."]));
  // Past every timer: an effect that selected the lines for it would have run by now.
  await act(() => new Promise<void>((resolve) => { setTimeout(resolve, 100); }));
  expect(screen.getByRole("tab", { name: "Evidence" }).getAttribute("aria-selected")).toBe("true");
  expect(f.custom).not.toHaveBeenCalled();
});

test("a declared lines group renders the lines in its place, titled by the group, and nothing trails", async () => {
  await fixture({ publicView: true, formProps: {
    linesTabLabel: "Unused trailing label",
    groups: [{ label: "Order lines", lines: true, fields: [], actions: [] }],
  } });
  expect(screen.getByRole("heading", { name: "Order lines" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Unused trailing label" })).toBeNull();
  expect(screen.getAllByDisplayValue("Alpha")).toHaveLength(1);
});

test("a contributed lines section is narrowed like any section: narrowed away, the lines are gone", async () => {
  const contributed = containersFromChildren(FORM_CONTAINERS, {
    "review.Document#sections": { "review.lines": { sequence: 20, content: <Group label="Order lines" lines /> } },
  });
  await fixture({ publicView: true, containers: contributed });
  expect(screen.getByRole("heading", { name: "Order lines" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Lines" })).toBeNull();
  cleanup();
  clearClients();
  const narrowed: ComposedContainers = {
    ...contributed,
    rules: { "review.Document#sections": [{ layer: "review", rank: 1, only: [], exempt: [] }] },
  };
  const f = await fixture({ publicView: true, containers: narrowed, linesHidden: true });
  // The lines still load into the form, unchanged, so a save sends none of them.
  expect(f.surface().form.getValues("lines")).toMatchObject(initialLines);
  expect(screen.queryByDisplayValue("Alpha")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Order lines" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Lines" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Add line" })).toBeNull();
});

test("new documents render Add line and create their draft lines in one native nested insert", async () => {
  const saved = { id: "doc-new", title: "Draft document", lines: [{ id: "line-new", label: "Lamp", quantity: 1, position: 0 }] };
  const f = await fixture({ isCreate: true, publicView: true, create: async () => ({ data: saved }) });
  expect(f.surface().linesActive).toBe(true);
  edit("Title", "Draft document");
  fireEvent.click(screen.getByRole("button", { name: "Add line" }));
  edit("Label", "Lamp");
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(f.provider.create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    variables: { title: "Draft document", lines: { data: [{ label: "Lamp", position: 0 }] } },
  })));
  expect(f.provider.update).not.toHaveBeenCalled();
  expect(f.custom).not.toHaveBeenCalled();
  expect(f.getOne).not.toHaveBeenCalled();
  await waitFor(() => expect(f.surface().form.getValues("lines")).toEqual(saved.lines));
  expect(f.surface().formIsDirty).toBe(false);
});

test("seeds rendered document lines without a reseed loop", async () => {
  const f = await fixture({ publicView: true });
  expect(screen.getByDisplayValue("Alpha")).toBeTruthy();
  expect(screen.getByDisplayValue("Bravo")).toBeTruthy();
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("Original");
  expect(screen.getByDisplayValue("Alpha")).toBeTruthy();
  expect(f.custom).not.toHaveBeenCalled();
});

test("routes a dirty-lines save through the resource save mutation", async () => {
  const f = await fixture({ publicView: true });
  fireEvent.change(screen.getByDisplayValue("Alpha"), { target: { value: "Edited alpha" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  const payload = {
    pk: "doc-1",
    patch: {},
    lines: [{ ...initialLines[0], label: "Edited alpha" }, ...initialLines.slice(1)],
  };
  expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({
    payload,
    meta: expect.objectContaining({ gqlMutation: saveDocument, gqlVariables: payload }),
  }));
  expect(f.update).not.toHaveBeenCalled();
  await waitFor(() => expect(f.surface().formIsDirty).toBe(false));
});

test.each([true, false])("line saves use only their own revision advertisement (save: %s)", async (saveRevision) => {
  const argumentsList = [{ name: "expected_revision", type: "Int" }];
  const f = await fixture({
    publicView: true,
    revision: saveRevision ? 3 : undefined,
    resourceMetadata: {
      ...renderedResource,
      fields: [...(renderedResource.fields ?? []), {
        ...lineField("revision", "Int"), creatable: false, updatable: false,
      }],
      updateArguments: saveRevision ? [] : argumentsList,
      saveArguments: saveRevision ? argumentsList : [],
    },
  });
  fireEvent.change(screen.getByDisplayValue("Alpha"), { target: { value: "Edited alpha" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledOnce());
  const payload = f.custom.mock.calls[0]?.[0].payload;
  if (saveRevision) expect(payload).toHaveProperty("expected_revision", 3);
  else expect(payload).not.toHaveProperty("expected_revision");
  expect(f.update).not.toHaveBeenCalled();
});

test("a rendered new line with untouched numeric cells saves without those keys", async () => {
  const f = await fixture({ publicView: true });
  fireEvent.click(screen.getByRole("button", { name: "Add line" }));
  const newLabelCell = screen.getAllByLabelText("Label")
    .find((cell) => (cell as HTMLInputElement).value === "");
  expect(newLabelCell).toBeTruthy();
  fireEvent.change(newLabelCell as HTMLInputElement, { target: { value: "New" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  const lines = f.custom.mock.calls[0]![0].payload.lines!;
  expect(lines[3]).toEqual({ label: "New", position: 3 });
  expect(lines[3]).not.toHaveProperty("quantity");
  expect(lines[3]).not.toHaveProperty("price");
  expect(lines[0]).toEqual(expect.objectContaining({ id: "a", quantity: 10 }));
});

test("a created line edited and reordered during save keeps its server ID for the next save", async () => {
  let resolveFirst!: (row: Row) => void;
  let first = true;
  const f = await fixture({
    publicView: true,
    save: async (variables) => {
      if (first) {
        first = false;
        return new Promise<Row>((resolve) => { resolveFirst = resolve; });
      }
      return { id: "doc-1", title: "Original", lines: variables.lines };
    },
    recordExtras: (context) => <button type="button" onClick={() => {
      const rows = context.form.form.getValues("lines") as Row[];
      context.form.form.setValue("lines", [rows[3], ...rows.slice(0, 3)], { shouldDirty: true });
    }}>Move new line first</button>,
  });
  fireEvent.click(screen.getByRole("button", { name: "Add line" }));
  const newLabelCell = screen.getAllByLabelText("Label")
    .find((cell) => (cell as HTMLInputElement).value === "");
  expect(newLabelCell).toBeTruthy();
  fireEvent.change(newLabelCell as HTMLInputElement, { target: { value: "Submitted" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByDisplayValue("Submitted"), { target: { value: "Later edit" } });
  fireEvent.click(screen.getByRole("button", { name: "Move new line first" }));
  const lines = f.custom.mock.calls[0]![0].payload.lines!;
  const accepted = {
    id: "doc-1", title: "Original",
    lines: lines.map((line, index) => ({ ...line, id: line.id ?? `new-${index}` })),
  };
  await act(async () => resolveFirst(accepted));
  expect(screen.getByDisplayValue("Later edit")).toBeTruthy();
  await waitFor(() => expect(f.surface().pending).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(2));
  const nextLines = f.custom.mock.calls[1]![0].payload.lines!;
  expect(nextLines[0]).toEqual(expect.objectContaining({ id: "new-3", label: "Later edit", position: 0 }));
  expect(nextLines.filter((line) => line.id == null)).toHaveLength(0);
});

test("keeps a parent-only edit on the stock update path with rendered lines", async () => {
  const f = await fixture({ publicView: true });
  edit("Title", "Renamed");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.update).toHaveBeenCalledTimes(1));
  expect(f.update).toHaveBeenCalledWith(expect.objectContaining({
    id: "doc-1", variables: { title: "Renamed" },
  }));
  expect(f.custom).not.toHaveBeenCalled();
});

test("maps a line save validation error to its rendered row", async () => {
  const f = await fixture({ publicView: true, save: async () => {
    throw { graphQLErrors: [{
      message: "Validation failed.",
      extensions: {
        code: "VALIDATION",
        validationErrors: { "lines.1.label": ["This field is required."] },
        formErrors: [],
      },
    }] };
  } });
  fireEvent.change(screen.getByDisplayValue("Alpha"), { target: { value: "Edited alpha" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  expect(await screen.findByText("This field is required.")).toBeTruthy();
});

test("failed nested creation preserves the header and lines for retry", async () => {
  const f = await fixture({ isCreate: true, create: async () => { throw new Error("Line rejected"); } });
  edit("title", "Draft document");
  f.append({ label: "Lamp", quantity: "1" });
  await act(async () => f.surface().submitForm());
  expect(f.provider.create).toHaveBeenCalledTimes(1);
  expect(f.surface().form.getValues("title")).toBe("Draft document");
  expect(f.surface().form.getValues("lines")).toMatchObject([{ label: "Lamp", quantity: "1" }]);
  expect(f.surface().formIsDirty).toBe(true);
  expect(f.surface().form.formState.errors.root?.server?.message).toBe("Line rejected");
  expect(f.provider.update).not.toHaveBeenCalled();
});

test("a create that carries its lines is one keyed write: a retry after a lost answer sends the same key with the lines", async () => {
  const saved: Row = { id: "doc-2", title: "Draft document", lines: [{ id: "line-9", label: "Lamp", quantity: 1, position: 0 }] };
  const create = vi.fn<() => Promise<{ data: Row }>>().mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValueOnce({ data: saved });
  const f = await fixture({ isCreate: true, keyed: true, create });
  edit("title", "Draft document");
  f.append({ label: "Lamp", quantity: "1" });
  await act(async () => f.surface().submitForm());
  await act(async () => f.surface().submitForm());
  expect(f.provider.create).toHaveBeenCalledTimes(2);
  // The provider's create is typed by the fixture's option, without its arguments.
  const calls = (f.provider.create.mock.calls as unknown as [{ variables: Row; meta?: { gqlVariables?: Row } }][]).map(([call]) => call);
  expect(calls.map((call) => call.meta?.gqlVariables?.idempotency_key)).toEqual([expect.any(String), calls[0]!.meta?.gqlVariables?.idempotency_key]);
  expect(calls[1]!.variables.lines).toEqual({ data: [{ label: "Lamp", quantity: "1", position: 0 }] });
});

test("successful semantic no-op line saves accept the native draft baseline", async () => {
  const f = await fixture({ submit: async () => ({ status: "ok", data: { id: "doc-1", title: "Original", lines: initialLines } }) });
  act(() => f.surface().form.setValue<string>("lines.0.quantity", "10", { shouldDirty: true }));
  expect(f.surface().formIsDirty).toBe(true);
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledWith({}, expect.objectContaining({ lines: expect.objectContaining({ hasChanges: false }) }));
  await waitFor(() => expect(f.surface().formIsDirty).toBe(false));
  expect(f.surface().form.getValues("lines")).toEqual(initialLines);
});

test("semantic no-op line saves preserve a later edit while the request is pending", async () => {
  let resolve!: (row: Row) => void;
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })) });
  act(() => f.surface().form.setValue<string>("lines.0.quantity", "10", { shouldDirty: true }));
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.submit).toHaveBeenCalledTimes(1));
  edit("a.quantity", "11");
  await act(async () => { resolve({ id: "doc-1", title: "Original", lines: initialLines }); await saving; });
  expect((f.surface().form.getValues("lines") as Row[])[0]?.quantity).toBe("11");
  expect(f.surface().formIsDirty).toBe(true);
});

test.each(["none", "stay before save", "stay after save"])("an acknowledged canonical line save clears the leave guard after completion (%s)", async (navigation) => {
  let accept!: () => void;
  let leavingAfterSave: Promise<boolean> | undefined;
  const acceptedLines = [{ ...initialLines[0], quantity: 11 }, ...initialLines.slice(1)];
  const accepted = { id: "doc-1", title: "Original", lines: acceptedLines, amount_total: "353.64" };
  const f = await fixture({
    submit: async (_data, context) => {
      await new Promise<void>((done) => { accept = done; });
      return acknowledgeFormSubmit(accepted, { ...context.values, lines: acceptedLines });
    },
    onSaved: () => { leavingAfterSave = f.surface().requestLeave(); },
  });
  edit("a.quantity", "11");
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.submit).toHaveBeenCalledOnce());
  let pendingLeave: Promise<boolean> | undefined;
  if (navigation !== "none") {
    pendingLeave = f.surface().requestLeave();
    await screen.findByRole("button", { name: "Stay" });
    if (navigation === "stay before save") {
      fireEvent.click(screen.getByRole("button", { name: "Stay" }));
      await expect(pendingLeave).resolves.toBe(false);
    }
  }
  f.setRecord(accepted);
  await act(async () => { accept(); await saving; });
  expect(f.surface().form.getValues("lines")).toEqual(acceptedLines);
  expect(f.surface().formIsDirty).toBe(false);
  await expect(leavingAfterSave).resolves.toBe(true);
  if (navigation === "stay after save") {
    // A confirmation opened before acceptance remains a pending user decision;
    // its presence alone does not mean the saved form is still dirty.
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stay" }));
    await expect(pendingLeave).resolves.toBe(false);
  }
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

test("reordered remote lines keep dirty cells attached to their public row IDs", async () => {
  const f = await fixture();
  edit("a.label", "Local alpha");
  await f.refresh([initialLines[1]!, initialLines[0]!, initialLines[2]!]);
  expect(f.surface().form.getValues("lines")).toEqual([
    { ...initialLines[0], label: "Local alpha" }, initialLines[1], initialLines[2],
  ]);
});

test("inserting a remote row cannot overwrite another row's clean cells", async () => {
  const f = await fixture();
  edit("b.label", "Local bravo");
  await f.refresh([
    { id: "x", label: "Xray", quantity: 40, position: 0 }, ...initialLines,
  ]);
  expect(f.surface().form.getValues("lines")).toEqual([
    initialLines[0], { ...initialLines[1], label: "Local bravo" }, initialLines[2],
  ]);
});

test("deleting a remote row cannot move another row's clean cells into its ID", async () => {
  const f = await fixture();
  edit("b.label", "Local bravo");
  await f.refresh([initialLines[1]!, initialLines[2]!]);
  expect(f.surface().form.getValues("lines")).toEqual([
    initialLines[0], { ...initialLines[1], label: "Local bravo" }, initialLines[2],
  ]);
});

test("a locally removed last row stays removed after an unrelated server refresh", async () => {
  const f = await fixture();
  f.remove(2);
  expect(f.surface().form.getValues("lines")).toEqual(initialLines.slice(0, 2));
  expect(f.surface().form.getFieldState("lines").isDirty).toBe(true);
  await f.refresh(initialLines);
  expect(f.surface().form.getValues("lines")).toEqual(initialLines.slice(0, 2));
});

test("a locally removed middle row stays removed after an unrelated server refresh", async () => {
  const f = await fixture();
  f.remove(1);
  await f.refresh(initialLines);
  expect(f.surface().form.getValues("lines")).toEqual([initialLines[0], initialLines[2]]);
});

test("locally reordered rows keep every cell associated with its own ID", async () => {
  const f = await fixture();
  f.move(1, 0);
  await f.refresh(initialLines);
  expect(f.surface().form.getValues("lines")).toEqual([initialLines[1], initialLines[0], initialLines[2]]);
});

test("clean line arrays adopt server insertions while dirty scalar values survive", async () => {
  const f = await fixture();
  edit("title", "Local title");
  const remote = [{ id: "x", label: "Xray", quantity: 40, position: 0 }, ...initialLines.map((row, index) => ({ ...row, position: index + 1 }))];
  f.setRecord({ id: "doc-1", title: "Remote title", lines: remote });
  await act(async () => { await f.surface().reload(); });
  await waitFor(() => expect(f.surface().form.getValues("lines")).toEqual(remote));
  expect(f.surface().form.getValues("title")).toBe("Local title");
});

test("changed server lines refuse a destructive full-list save and discard loads canonical rows", async () => {
  const f = await fixture();
  edit("a.label", "Local alpha");
  const remote = [{ id: "x", label: "Xray", quantity: 40, position: 0 }, ...initialLines.map((row, index) => ({ ...row, position: index + 1 }))];
  await f.refresh(remote);
  await act(async () => f.surface().submitForm());
  expect(f.submit).not.toHaveBeenCalled();
  expect(f.surface().saveErrors).toEqual(["form.linesChanged"]);
  expect(f.surface().form.getValues("lines")).toEqual([{ ...initialLines[0], label: "Local alpha" }, ...initialLines.slice(1)]);
  act(() => f.surface().discardChanges());
  expect(f.surface().form.getValues("lines")).toEqual(remote);
  expect(f.surface().saveErrors).toEqual([]);
  expect(f.surface().formIsDirty).toBe(false);
  edit("x.label", "Reconciled xray");
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledTimes(1);
});

test("removing a row then refreshing unchanged lines submits its deletion without an ID-less replacement", async () => {
  const f = await fixture();
  f.remove(2);
  expect(f.surface().form.getValues("lines")).toEqual(initialLines.slice(0, 2));
  expect(f.surface().form.getFieldState("lines").isDirty).toBe(true);
  await f.refresh(initialLines);
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledWith({}, expect.objectContaining({ lines: expect.objectContaining({
    deleted: ["c"], created: [], payload: [expect.objectContaining({ id: "a" }), expect.objectContaining({ id: "b" })],
  }) }));
});

test("a delayed existing-line save keeps later cells and rebases the next atomic write", async () => {
  let resolve!: (value: Row) => void;
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })) });
  edit("a.label", "Submitted alpha");
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.submit).toHaveBeenCalledTimes(1));
  edit("a.label", "Later alpha");
  f.move(1, 0);
  const savedLines = [{ ...initialLines[0], label: "Submitted alpha" }, ...initialLines.slice(1)];
  const accepted = { id: "doc-1", title: "Original", lines: savedLines };
  f.setRecord(accepted);
  await act(async () => { resolve(accepted); await saving; });
  expect(f.surface().form.getValues("lines")).toEqual([initialLines[1], { ...initialLines[0], label: "Later alpha" }, initialLines[2]]);
  expect(f.surface().form.formState.defaultValues?.lines).toEqual(savedLines);
  f.submit.mockResolvedValueOnce(formSubmitError("No record returned."));
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledTimes(2);
  expect(f.submit.mock.calls[1]?.[1].lines?.created).toEqual([]);
  expect(f.submit.mock.calls[1]?.[1].lines?.payload.map((row) => row.id)).toEqual(["b", "a", "c"]);
});

test("a new line receives its saved ID normally while post-submission scalar edits remain dirty", async () => {
  let resolve!: (value: Row) => void;
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })) });
  const newLine = { label: "Delta", quantity: 40, position: 3 };
  f.append(newLine);
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.submit).toHaveBeenCalledTimes(1));
  edit("title", "Later title");
  const savedLines = [...initialLines, { ...newLine, id: "d" }];
  const accepted = { id: "doc-1", title: "Original", lines: savedLines };
  f.setRecord(accepted);
  await act(async () => { resolve(accepted); await saving; });
  expect(f.surface().form.getValues("lines")).toEqual(savedLines);
  expect(f.surface().form.getFieldState("lines").isDirty).toBe(false);
  expect(f.surface().form.getValues("title")).toBe("Later title");
  f.submit.mockResolvedValueOnce(formSubmitError("No record returned."));
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledTimes(2);
  expect(f.submit.mock.calls[1]?.[0]).toEqual({ title: "Later title" });
  expect(f.submit.mock.calls[1]?.[1].lines?.hasChanges).toBe(false);
});

test.each([true, false])("concurrent edits to a newly saved line remain intact and cannot recreate it (response includes IDs: %s)", async (includesLines) => {
  let resolve!: (value: Row) => void;
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }).then((data) => ({ status: "ok" as const, data })) });
  const newLine = { label: "Delta", quantity: 40, position: 3 };
  f.append(newLine);
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.submit).toHaveBeenCalledTimes(1));
  edit("undefined.label", "Later delta");
  const accepted = { id: "doc-1", title: "Original", lines: [...initialLines, { ...newLine, id: "d" }] };
  f.setRecord(accepted);
  let resolveRead!: (value: { data: Row }) => void;
  if (!includesLines) f.getOne.mockImplementationOnce(() => new Promise<{ data: Row }>((done) => { resolveRead = done; }));
  await act(async () => { resolve(includesLines ? accepted : { id: "doc-1", title: "Original" }); await saving; });
  expect(f.surface().form.getValues("lines")).toEqual([...initialLines, { ...newLine, label: "Later delta" }]);
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledTimes(1);
  expect(f.surface().saveErrors).toEqual(["form.linesChanged"]);
  if (!includesLines) {
    await act(async () => resolveRead({ data: accepted }));
    await waitFor(() => expect(f.surface().displayRecord?.lines).toEqual(accepted.lines));
  }
  act(() => f.surface().discardChanges());
  expect(f.surface().form.getValues("lines")).toEqual(accepted.lines);
  expect(f.surface().formIsDirty).toBe(false);
});

const lineLabels = () => screen.getAllByLabelText("Label").map((cell) => (cell as HTMLInputElement).value);
const loadedLabels = initialLines.map((row) => String(row.label));
const titleValue = () => (screen.getByLabelText("Title") as HTMLTextAreaElement).value;
type Fixture = Awaited<ReturnType<typeof fixture>>;
const formLines = (f: Fixture) => f.surface().form.getValues("lines") as Row[];
/** Replay one step and let the effects it causes (a client-key write among them) land. */
const replay = (f: Fixture, direction: "undo" | "redo") => act(async () => { f.surface().history[direction](); });
/** The row's identity: its server id, or `+` and its client key while it is unsaved. */
const rowIdentity = (row: Row) => row.id != null ? String(row.id) : `+${String(row[CLIENT_LINE_KEY])}`;
/** Each line as `identity:label/quantity`, so right cells on the wrong rows do not pass. */
const cellsOf = (rows: readonly Row[]) => rows.map((row) => `${rowIdentity(row)}:${String(row.label)}/${String(row.quantity)}`);
const loadedCells = cellsOf(initialLines);

/**
 * The form's interaction callbacks as one ordered list. After `readFrom`, a commit also
 * records the value its path holds when the commit arrives.
 */
function interactionLog() {
  const log: string[] = [];
  let read: ((path: string) => unknown) | undefined;
  return {
    log,
    readFrom: (f: Fixture) => { read = (path) => f.surface().form.getValues(path); },
    formProps: {
      onFieldInteractionStart: (path: string) => { log.push(`start ${path}`); },
      onFieldInteractionCommit: (path: string) => {
        log.push(read ? `commit ${path}: ${String(read(path))}` : `commit ${path}`);
      },
    },
  };
}

test("adding a line, typing its label and changing the title are one undo step each, walked back to the loaded rows and forward to the save", async () => {
  const f = await fixture({ publicView: true });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Add line" })); });
  const added = screen.getAllByLabelText("Label").at(-1)!;
  fireEvent.change(added, { target: { value: "N" } });
  fireEvent.change(added, { target: { value: "New" } });
  fireEvent.blur(added);
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Other" } });
  fireEvent.blur(screen.getByLabelText("Title"));
  const frames = [
    { title: "Original", labels: loadedLabels },
    { title: "Original", labels: [...loadedLabels, ""] },
    { title: "Original", labels: [...loadedLabels, "New"] },
    { title: "Other", labels: [...loadedLabels, "New"] },
  ];
  for (const frame of frames.slice(0, -1).reverse()) {
    await replay(f, "undo");
    expect({ title: titleValue(), labels: lineLabels() }).toEqual(frame);
  }
  expect(f.surface().history.canUndo).toBe(false);
  expect(formLines(f)).toEqual(initialLines);
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  for (const frame of frames.slice(1)) {
    await replay(f, "redo");
    expect({ title: titleValue(), labels: lineLabels() }).toEqual(frame);
  }
  expect(f.surface().history.canRedo).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledOnce());
  expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({
    payload: { pk: "doc-1", patch: { title: "Other" }, lines: [...initialLines, { label: "New", position: 3 }] },
  }));
});

test.each([
  { verb: "removing", control: "Remove line", labels: ["Alpha", "Charlie"] },
  { verb: "duplicating", control: "Duplicate line", labels: ["Alpha", "Bravo", "Bravo", "Charlie"] },
  { verb: "reordering", control: "Reorder row", labels: ["Bravo", "Alpha", "Charlie"] },
])("$verb a line is one undo step: undo restores the loaded rows, their order and cells with nothing to save, and redo repeats it", async ({ control, labels }) => {
  const f = await fixture({ publicView: true });
  const target = screen.getAllByRole("button", { name: control })[1]!;
  await act(async () => {
    if (control === "Reorder row") fireEvent.keyDown(target, { key: "ArrowUp", altKey: true });
    else fireEvent.click(target);
  });
  expect(lineLabels()).toEqual(labels);
  await replay(f, "undo");
  expect(lineLabels()).toEqual(loadedLabels);
  expect(formLines(f)).toEqual(initialLines);
  expect(f.surface().history.canUndo).toBe(false);
  expect(f.surface().formIsDirty).toBe(false);
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  await replay(f, "redo");
  expect(lineLabels()).toEqual(labels);
  expect(f.surface().history.canUndo).toBe(true);
  expect(f.surface().history.canRedo).toBe(false);
  expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
});

test("adding a line is exactly one step: its undo leaves the loaded rows without a client key, and its redo brings the row back keyed while the save sends no key", async () => {
  const f = await fixture({ publicView: true });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Add line" })); });
  expect(formLines(f)).toHaveLength(4);
  expect(typeof formLines(f)[3]![CLIENT_LINE_KEY]).toBe("string");
  await replay(f, "undo");
  expect(f.surface().history.canUndo).toBe(false);
  expect(formLines(f)).toEqual(initialLines);
  expect(formLines(f).filter((row) => Object.hasOwn(row, CLIENT_LINE_KEY))).toEqual([]);
  expect(lineLabels()).toEqual(loadedLabels);
  await replay(f, "redo");
  expect(lineLabels()).toEqual([...loadedLabels, ""]);
  expect(typeof formLines(f)[3]![CLIENT_LINE_KEY]).toBe("string");
  expect(f.surface().history.canRedo).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledOnce());
  expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({
    payload: { pk: "doc-1", patch: {}, lines: [...initialLines, { label: "", position: 3 }] },
  }));
});

test("adding a line while a cell's typing is still open records no step a person cannot see", async () => {
  const f = await fixture({ publicView: true });
  fireEvent.change(screen.getByDisplayValue("Alpha"), { target: { value: "Alpha edited" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Add line" })); });
  expect(typeof formLines(f)[3]![CLIENT_LINE_KEY]).toBe("string");
  const shown: string[][] = [lineLabels()];
  while (f.surface().history.canUndo && shown.length < 6) {
    await replay(f, "undo");
    shown.push(lineLabels());
  }
  expect(shown).toEqual([
    ["Alpha edited", "Bravo", "Charlie", ""],
    ["Alpha edited", "Bravo", "Charlie"],
    loadedLabels,
  ]);
  expect(formLines(f)).toEqual(initialLines);
});

test.each([
  {
    between: "leaving the first cell", leave: true,
    interactions: ["start lines.0.label", "commit lines.0.label", "start lines.1.label", "commit lines.1.label"],
  },
  {
    // The history records the first cell's step when the second starts; the first cell's
    // commit arrives when the undo gives the rows new identities and the editor ends its groups.
    between: "without leaving the first cell", leave: false,
    interactions: ["start lines.0.label", "start lines.1.label", "commit lines.1.label", "commit lines.0.label"],
  },
])("typing in one cell is one undo step until another cell starts the next ($between), and the form's interaction callbacks see each cell's path, every start with its commit", async ({ leave, interactions }) => {
  const { log, formProps } = interactionLog();
  const f = await fixture({ publicView: true, formProps });
  const alpha = screen.getByDisplayValue("Alpha");
  for (const value of ["AlphaX", "AlphaXY", "AlphaXYZ"]) fireEvent.change(alpha, { target: { value } });
  if (leave) fireEvent.blur(alpha);
  const bravo = screen.getByDisplayValue("Bravo");
  fireEvent.change(bravo, { target: { value: "Bravo edited" } });
  fireEvent.blur(bravo);
  await replay(f, "undo");
  expect(lineLabels()).toEqual(["AlphaXYZ", "Bravo", "Charlie"]);
  await replay(f, "undo");
  expect(lineLabels()).toEqual(loadedLabels);
  expect(f.surface().history.canUndo).toBe(false);
  expect(log).toEqual(interactions);
});

const linesConfig = lineDiffConfig(renderedResource.linesResource!);

test.each([
  { builder: "emptyLineRow", build: (rows: readonly Row[]) => emptyLineRow(rows.length, linesConfig), added: "" },
  { builder: "duplicateLineRow", build: (rows: readonly Row[]) => duplicateLineRow(rows[0]!, linesConfig), added: "Alpha" },
])("a line code builds with $builder and performs while the title's typing is open is one step, and each undo back to the loaded rows changes what the form shows", async ({ build, added }) => {
  const f = await fixture({ publicView: true });
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Other" } });
  act(() => { f.surface().history.perform(() => f.surface().form.setValue("lines", [...formLines(f), build(formLines(f))], { shouldDirty: true })); });
  await waitFor(() => expect(lineLabels()).toEqual([...loadedLabels, added]));
  const shown = [{ title: titleValue(), labels: lineLabels() }];
  while (f.surface().history.canUndo && shown.length < 6) {
    await replay(f, "undo");
    shown.push({ title: titleValue(), labels: lineLabels() });
  }
  expect(shown).toEqual([
    { title: "Other", labels: [...loadedLabels, added] },
    { title: "Other", labels: loadedLabels },
    { title: "Original", labels: loadedLabels },
  ]);
  expect(f.surface().history.canUndo).toBe(false);
  expect(formLines(f)).toEqual(initialLines);
});

test.each([
  {
    operation: "removing the first line",
    operate: () => fireEvent.click(screen.getAllByRole("button", { name: "Remove line" })[0]!),
    shown: [["Bravo12", "Charlie"], ["Bravo1", "Charlie"], ["Alpha", "Bravo1", "Charlie"], loadedLabels],
  },
  {
    operation: "moving it above the first line",
    operate: () => fireEvent.keyDown(screen.getAllByRole("button", { name: "Reorder row" })[1]!, { key: "ArrowUp", altKey: true }),
    shown: [["Bravo12", "Alpha", "Charlie"], ["Bravo1", "Alpha", "Charlie"], ["Alpha", "Bravo1", "Charlie"], loadedLabels],
  },
])("typing in the second line's cell without leaving it, then $operation, then typing on: each started cell is committed before the next starts, and each undo changes what the form shows", async ({ operate, shown: expected }) => {
  const interactions: string[] = [];
  const f = await fixture({ publicView: true, formProps: {
    onFieldInteractionStart: (path) => interactions.push(`start ${path}`),
    onFieldInteractionCommit: (path) => interactions.push(`commit ${path}`),
  } });
  fireEvent.change(screen.getByDisplayValue("Bravo"), { target: { value: "Bravo1" } });
  await act(async () => { operate(); });
  const moved = screen.getByDisplayValue("Bravo1");
  fireEvent.change(moved, { target: { value: "Bravo12" } });
  fireEvent.blur(moved);
  expect(interactions).toEqual(["start lines.1.label", "commit lines.1.label", "start lines.0.label", "commit lines.0.label"]);
  const shown: string[][] = [lineLabels()];
  while (f.surface().history.canUndo && shown.length < 6) {
    await replay(f, "undo");
    shown.push(lineLabels());
  }
  expect(shown).toEqual(expected);
  expect(formLines(f)).toEqual(initialLines);
});

test("a relation cell commits its group when a record is picked, and the pick is one step", async () => {
  const related: DataResourceFieldMetadata = {
    ...lineField("related", "ID"), kind: "relation", scalar: null, relationModelLabel: renderedResource.modelLabel,
  };
  const withRelation = testDataResource(renderedResource.modelLabel, {
    ...renderedResource,
    linesResource: { ...renderedResource.linesResource!, fields: [...renderedResource.linesResource!.fields!, related] },
  });
  const interactions: string[] = [];
  const f = await fixture({ publicView: true, resourceMetadata: withRelation, formProps: {
    onFieldInteractionStart: (path) => interactions.push(`start ${path}`),
    onFieldInteractionCommit: (path) => interactions.push(`commit ${path}`),
  } });
  f.provider.getList.mockResolvedValue({ data: [{ id: "doc-2" }], total: 1 });
  fireEvent.click(screen.getAllByRole("combobox", { name: /^Related/ })[0]!);
  await act(async () => { fireEvent.click(await screen.findByRole("option", { name: "doc-2" })); });
  expect(formLines(f)[0]).toMatchObject({ id: "a", related: "doc-2" });
  expect(interactions).toEqual(["start lines.0.related", "commit lines.0.related"]);
  await replay(f, "undo");
  expect(formLines(f)).toEqual(initialLines);
  expect(f.surface().history.canUndo).toBe(false);
});

test("a relation cell offers inline edit of its picked record, and an authored line field declines it with `relationEdit: null`", async () => {
  const related: DataResourceFieldMetadata = {
    ...lineField("related", "ID"), kind: "relation", scalar: null, relationModelLabel: renderedResource.modelLabel,
  };
  const withRelation = testDataResource(renderedResource.modelLabel, {
    ...renderedResource,
    linesResource: { ...renderedResource.linesResource!, fields: [...renderedResource.linesResource!.fields!, related] },
  });
  const pick = async () => {
    fireEvent.click(screen.getAllByRole("combobox", { name: /^Related/ })[0]!);
    await act(async () => { fireEvent.click(await screen.findByRole("option", { name: "doc-2" })); });
  };

  const offered = await fixture({ publicView: true, resourceMetadata: withRelation });
  offered.provider.getList.mockResolvedValue({ data: [{ id: "doc-2" }], total: 1 });
  await pick();
  expect(formLines(offered)[0]).toMatchObject({ related: "doc-2" });
  expect(screen.getAllByRole("button", { name: "Edit record" }).length).toBeGreaterThan(0);
  cleanup();

  const declined = await fixture({ publicView: true, resourceMetadata: withRelation, formProps: { lineFields: [{ name: "related", relationEdit: null }] } });
  declined.provider.getList.mockResolvedValue({ data: [{ id: "doc-2" }], total: 1 });
  await pick();
  expect(formLines(declined)[0]).toMatchObject({ related: "doc-2" });
  expect(screen.queryByRole("button", { name: "Edit record" })).toBeNull();
});

const previewWidget: WidgetDefinition = {
  read: ({ value }: WidgetRenderProps) => <span>{String(value)}</span>,
  edit: ({ value, row, onChange, onRowChange, onCommit }: WidgetRenderProps) => {
    const label = String((row as Row).label);
    return <>
      <button type="button" onClick={() => onRowChange?.({ label: `${label} patched` })}>Patch {label}</button>
      <button type="button" onClick={() => { onChange?.(99); onRowChange?.({ label: `${label} picked` }); }}>Pick for {label}</button>
      {/* The same two calls in the other order: the row patch first. */}
      <button type="button" onClick={() => { onRowChange?.({ label: `${label} picked` }); onChange?.(99); }}>Patch, then pick for {label}</button>
      {/* A cell change its control never commits, as a tag input's or a status bar's. */}
      <button type="button" onClick={() => onChange?.(Number(value) + 1)}>Bump {label}</button>
      <button type="button" onClick={() => { onChange?.(Number(value) + 5); onCommit?.(); }}>Settle {label}</button>
    </>;
  },
};
/** The record form with the preview widget as the lines' quantity cell. */
const previewFixture = (formProps: ReturnType<typeof interactionLog>["formProps"]) => fixture({
  publicView: true, widgets: { "review.preview": previewWidget },
  formProps: { lineFields: [{ name: "quantity", widget: "review.preview" }], ...formProps },
});
const press = (name: string) => act(async () => { fireEvent.click(screen.getByRole("button", { name })); });
/** Undo until nothing is left (at most six times), reading `shown` before and after each. */
async function undoWalk<T>(f: Fixture, shown: () => T): Promise<T[]> {
  const walk = [shown()];
  while (f.surface().history.canUndo && walk.length < 7) {
    await replay(f, "undo");
    walk.push(shown());
  }
  return walk;
}

test.each([
  { control: "Patch Bravo", changed: { label: "Bravo patched", quantity: 20 } },
  { control: "Pick for Bravo", changed: { label: "Bravo picked", quantity: 99 } },
])("a widget's row patch through the record form is one step ($control), joining the cell change sent before it in the same click, inside one start and commit of the cell", async ({ control, changed }) => {
  const { log, formProps } = interactionLog();
  const f = await previewFixture(formProps);
  await press(control);
  expect(formLines(f)[1]).toMatchObject({ id: "b", ...changed });
  expect(log).toEqual(["start lines.1.quantity", "commit lines.1.quantity"]);
  expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  await replay(f, "undo");
  expect(formLines(f)).toEqual(initialLines);
  expect(f.surface().history.canUndo).toBe(false);
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  await replay(f, "redo");
  expect(formLines(f)[1]).toMatchObject({ id: "b", ...changed });
  expect(f.surface().history.canRedo).toBe(false);
});

test("the order decides: a row patch sent BEFORE its cell's change in the same click is a step of its own, and the change another", async () => {
  const { log, formProps } = interactionLog();
  const f = await previewFixture(formProps);
  await press("Patch, then pick for Bravo");
  const afterClick = [...log];
  const shown = await undoWalk(f, () => cellsOf(formLines(f))[1]);
  // The patch ends the cell's group; the change opens another, which the first undo ends.
  expect({ afterClick, log, shown }).toEqual({
    afterClick: ["start lines.1.quantity", "commit lines.1.quantity", "start lines.1.quantity"],
    log: ["start lines.1.quantity", "commit lines.1.quantity", "start lines.1.quantity", "commit lines.1.quantity"],
    shown: ["b:Bravo picked/99", "b:Bravo picked/20", "b:Bravo/20"],
  });
});

const patchesAfter: readonly {
  after: string;
  prepare: (f: Fixture) => Promise<unknown>;
  /** The interaction callbacks the preparation leaves. */
  prepared: readonly string[];
  /** The second line's quantity when the patches are sent, and its cells below them back to the loaded record. */
  quantity: number;
  below: readonly string[];
}[] = [
  { after: "nothing", prepare: async () => {}, prepared: [], quantity: 20, below: ["b:Bravo/20"] },
  {
    after: "a change its control committed", prepare: () => press("Settle Bravo"),
    prepared: ["start lines.1.quantity", "commit lines.1.quantity"], quantity: 25, below: ["b:Bravo/25", "b:Bravo/20"],
  },
  {
    // The discard ends the form's interaction without a commit callback, as for any field.
    after: "a change the form discarded", prepare: async (f) => {
      await press("Bump Bravo");
      act(() => f.surface().discardChanges());
    },
    prepared: ["start lines.1.quantity"], quantity: 20, below: ["b:Bravo/20"],
  },
  {
    // The refresh keeps the dirty lines, resets the history and ends the form's interaction, as for any field.
    after: "a change, then a refresh of the same record", prepare: async (f) => {
      await press("Bump Bravo");
      await f.refresh(initialLines);
    },
    prepared: ["start lines.1.quantity"], quantity: 21, below: ["b:Bravo/21"],
  },
];

test.each(patchesAfter)("after $after, two row patches in a row are a step each, each inside one start and commit of its cell", async ({ prepare, prepared, quantity, below }) => {
  const { log, formProps } = interactionLog();
  const f = await previewFixture(formProps);
  await prepare(f);
  await press("Patch Bravo");
  await press("Patch Bravo patched");
  const shown = await undoWalk(f, () => cellsOf(formLines(f))[1]);
  expect({ log, shown }).toEqual({
    log: [...prepared, "start lines.1.quantity", "commit lines.1.quantity", "start lines.1.quantity", "commit lines.1.quantity"],
    shown: [`b:Bravo patched patched/${quantity}`, `b:Bravo patched/${quantity}`, ...below],
  });
});

/**
 * Code outside the lines editor changing the rows the way the docs say: built rows,
 * performed. `shown` is the same change on the lines' cells, given the rows after it.
 */
const outsideOperations = {
  "removes the line above": {
    change: (rows: readonly Row[]) => rows.slice(1),
    shown: (cells: readonly string[], _after: readonly Row[]) => cells.slice(1),
  },
  "inserts a duplicated line above": {
    change: (rows: readonly Row[]) => [rows[0]!, duplicateLineRow(rows[2]!, linesConfig), ...rows.slice(1)],
    shown: (cells: readonly string[], after: readonly Row[]) => [
      cells[0]!, `+${String(after[1]![CLIENT_LINE_KEY])}:Charlie/30`, ...cells.slice(1),
    ],
  },
};
/** Perform `change` on the lines as outside code; resolves to the element focused when it ran. */
const performOutside = async (f: Fixture, change: (rows: readonly Row[]) => Row[]) => {
  let focused: Element | null = null;
  await act(async () => {
    f.surface().history.perform(() => {
      focused = document.activeElement;
      f.surface().form.setValue("lines", change(formLines(f)), { shouldDirty: true });
    });
  });
  return focused;
};
/** Change the second line's cell and leave its group open; resolves to that cell's control. */
const cellKinds = {
  "a widget that never commits": {
    path: "lines.1.quantity", edited: "b:Bravo/21", focused: false,
    edit: async () => { await press("Bump Bravo"); return screen.getByRole("button", { name: "Bump Bravo" }); },
  },
  "a text cell kept focused": {
    path: "lines.1.label", edited: "b:Bravo1/20", focused: true,
    edit: async () => {
      const cell = screen.getByDisplayValue("Bravo");
      act(() => cell.focus());
      fireEvent.change(cell, { target: { value: "Bravo1" } });
      return cell;
    },
  },
};

test.each(Object.keys(outsideOperations).flatMap((operation) => Object.keys(cellKinds).map((cell) => ({
  operation: operation as keyof typeof outsideOperations, cell: cell as keyof typeof cellKinds,
}))))("with the second line's cell open in $cell, code that $operation is a step of its own: the first undo reverts it alone, the second the cell edit, and redo replays both", async ({ operation, cell }) => {
  const { log, formProps } = interactionLog();
  const f = await previewFixture(formProps);
  const { path, edit, edited, focused } = cellKinds[cell];
  const { change, shown } = outsideOperations[operation];
  const control = await edit();
  const editedCells = loadedCells.map((cells, index) => index === 1 ? edited : cells);
  const focusedWhenPerformed = await performOutside(f, change);
  const operatedCells = shown(editedCells, formLines(f));
  const frame = () => ({ cells: cellsOf(formLines(f)), canUndo: f.surface().history.canUndo, canRedo: f.surface().history.canRedo });
  const frames = [frame()];
  for (const direction of ["undo", "undo", "redo", "redo"] as const) {
    await replay(f, direction);
    frames.push(frame());
  }
  expect({ cellFocusedWhenPerformed: focusedWhenPerformed === control, log, frames }).toEqual({
    cellFocusedWhenPerformed: focused,
    log: [`start ${path}`, `commit ${path}`],
    frames: [
      { cells: operatedCells, canUndo: true, canRedo: false },
      { cells: editedCells, canUndo: true, canRedo: true },
      { cells: loadedCells, canUndo: false, canRedo: true },
      { cells: editedCells, canUndo: true, canRedo: true },
      { cells: operatedCells, canUndo: true, canRedo: false },
    ],
  });
});

test.each([
  {
    operation: "removes the line above" as const, moved: "lines.0.quantity",
    shown: (_added: string) => [
      ["b:Bravo/22", "c:Charlie/30"], ["b:Bravo/21", "c:Charlie/30"], ["a:Alpha/10", "b:Bravo/21", "c:Charlie/30"], loadedCells,
    ],
  },
  {
    operation: "inserts a duplicated line above" as const, moved: "lines.2.quantity",
    shown: (added: string) => [
      ["a:Alpha/10", `${added}:Charlie/30`, "b:Bravo/22", "c:Charlie/30"],
      ["a:Alpha/10", `${added}:Charlie/30`, "b:Bravo/21", "c:Charlie/30"],
      ["a:Alpha/10", "b:Bravo/21", "c:Charlie/30"], loadedCells,
    ],
  },
])("with a cell's change open, which its control never commits, code that $operation, and the cell changed on: each started path is committed once before the next starts, and each undo changes what the form shows", async ({ operation, moved, shown: expected }) => {
  const { log, formProps } = interactionLog();
  const f = await previewFixture(formProps);
  await press("Bump Bravo");
  await performOutside(f, outsideOperations[operation].change);
  const added = rowIdentity(formLines(f)[1]!);
  await press("Bump Bravo");
  const shown = await undoWalk(f, () => cellsOf(formLines(f)));
  expect({ log, shown }).toEqual({
    log: ["start lines.1.quantity", "commit lines.1.quantity", `start ${moved}`, `commit ${moved}`],
    shown: expected(added),
  });
  expect(formLines(f)).toEqual(initialLines);
});

test.each([
  {
    operation: "removes the line above" as const, cell: "Bravo", path: "lines.1.quantity", names: "the next line",
    read: "30", atPath: (_after: readonly Row[]) => "c:Charlie/30",
  },
  {
    operation: "inserts a duplicated line above" as const, cell: "Bravo", path: "lines.1.quantity", names: "the inserted line",
    read: "30", atPath: (after: readonly Row[]) => `+${String(after[1]![CLIENT_LINE_KEY])}:Charlie/30`,
  },
  {
    operation: "removes the line above" as const, cell: "Charlie", path: "lines.2.quantity", names: "no line",
    read: "undefined", atPath: (_after: readonly Row[]) => "no row",
  },
])("known limit: with a cell change open at $path, code that $operation gets that cell's commit only after the change, when $path names $names", async ({ operation, cell, path, read, atPath }) => {
  const { log, readFrom, formProps } = interactionLog();
  const f = await previewFixture(formProps);
  readFrom(f);
  await press(`Bump ${cell}`);
  await performOutside(f, outsideOperations[operation].change);
  const after = formLines(f);
  expect({ log, atPath: cellsOf(after)[Number(path.split(".")[1])] ?? "no row" }).toEqual({
    log: [`start ${path}`, `commit ${path}: ${read}`],
    atPath: atPath(after),
  });
});

test.each([
  {
    operation: "removing the first line",
    operate: () => fireEvent.click(screen.getAllByRole("button", { name: "Remove line" })[0]!),
    shown: [["b:Bravo/22", "c:Charlie/30"], ["b:Bravo/21", "c:Charlie/30"], ["a:Alpha/10", "b:Bravo/21", "c:Charlie/30"], loadedCells],
  },
  {
    operation: "moving it above the first line",
    operate: () => fireEvent.keyDown(screen.getAllByRole("button", { name: "Reorder row" })[1]!, { key: "ArrowUp", altKey: true }),
    shown: [
      ["b:Bravo/22", "a:Alpha/10", "c:Charlie/30"], ["b:Bravo/21", "a:Alpha/10", "c:Charlie/30"],
      ["a:Alpha/10", "b:Bravo/21", "c:Charlie/30"], loadedCells,
    ],
  },
])("with the second line's cell change open, which its control never commits, $operation in the editor commits it while its path still names its row, and each undo changes what the form shows", async ({ operate, shown: expected }) => {
  const { log, readFrom, formProps } = interactionLog();
  const f = await previewFixture(formProps);
  readFrom(f);
  await press("Bump Bravo");
  await act(async () => { operate(); });
  await press("Bump Bravo");
  const shown = await undoWalk(f, () => cellsOf(formLines(f)));
  // The first undo lands on the frame after the operation, where the second line's change was 21.
  expect({ log, shown }).toEqual({
    log: ["start lines.1.quantity", "commit lines.1.quantity: 21", "start lines.0.quantity", "commit lines.0.quantity: 21"],
    shown: expected,
  });
});

test("a keyless row code builds by hand and performs while a cell's change is open gets its client key after the cell's group ends: each undo changes what the form shows", async () => {
  const f = await previewFixture(interactionLog().formProps);
  await press("Bump Bravo");
  await performOutside(f, (rows) => [...rows, { label: "Hand", quantity: 1, position: 3 }]);
  expect(typeof formLines(f)[3]![CLIENT_LINE_KEY]).toBe("string");
  const hand = rowIdentity(formLines(f)[3]!);
  const shown = await undoWalk(f, () => cellsOf(formLines(f)));
  expect(shown).toEqual([
    ["a:Alpha/10", "b:Bravo/21", "c:Charlie/30", `${hand}:Hand/1`], ["a:Alpha/10", "b:Bravo/21", "c:Charlie/30"], loadedCells,
  ]);
});

test("an undo with a cell's change open ends that cell's group, and the cell's next change is a step of its own", async () => {
  const { log, formProps } = interactionLog();
  const f = await previewFixture(formProps);
  const frame = () => ({ bravo: cellsOf(formLines(f))[1], canUndo: f.surface().history.canUndo, canRedo: f.surface().history.canRedo });
  await press("Bump Bravo");
  await press("Bump Bravo");
  const frames = [frame()];
  await replay(f, "undo");
  frames.push(frame());
  const afterUndo = [...log];
  await press("Bump Bravo");
  frames.push(frame());
  await replay(f, "undo");
  frames.push(frame());
  await replay(f, "redo");
  frames.push(frame());
  expect({ afterUndo, log, frames }).toEqual({
    afterUndo: ["start lines.1.quantity", "commit lines.1.quantity"],
    log: ["start lines.1.quantity", "commit lines.1.quantity", "start lines.1.quantity", "commit lines.1.quantity"],
    frames: [
      { bravo: "b:Bravo/22", canUndo: true, canRedo: false },
      { bravo: "b:Bravo/20", canUndo: false, canRedo: true },
      { bravo: "b:Bravo/21", canUndo: true, canRedo: false },
      { bravo: "b:Bravo/20", canUndo: false, canRedo: true },
      { bravo: "b:Bravo/21", canUndo: true, canRedo: false },
    ],
  });
});

/**
 * A record tab holding a lines editor wired as the record form wires its own lines. Like
 * any tab not kept mounted, it unmounts when another tab is chosen.
 */
const quantitiesTab: RecordTabDescriptor = {
  id: "quantities", label: "Quantities",
  render: ({ form }) => <EditableLines
    control={form.form.control} setValue={form.form.setValue} name="lines" lines={renderedResource.linesResource!}
    fields={[{ name: "quantity", widget: "review.preview" }]}
    history={{ perform: form.history.perform, start: form.startFieldInteraction, commit: form.commitFieldInteraction }}
  />,
};

test("a lines editor that unmounts with a cell's change open ends that cell's group, and the next edit elsewhere is a step of its own", async () => {
  const { log, formProps } = interactionLog();
  const f = await fixture({ publicView: true, widgets: { "review.preview": previewWidget }, formProps: {
    ...formProps, recordTabs: [quantitiesTab, { id: "notes", label: "Notes", render: () => <p>Notes pane</p> }],
  } });
  await act(async () => { fireEvent.click(screen.getByRole("tab", { name: "Quantities" })); });
  await press("Bump Bravo");
  await act(async () => { fireEvent.click(screen.getByRole("tab", { name: "Notes" })); });
  expect(await screen.findByText("Notes pane")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Bump Bravo" })).toBeNull();
  const afterUnmount = [...log];
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Other" } });
  fireEvent.blur(screen.getByLabelText("Title"));
  const shown = await undoWalk(f, () => ({ title: titleValue(), bravo: cellsOf(formLines(f))[1] }));
  expect({ afterUnmount, log, shown }).toEqual({
    afterUnmount: ["start lines.1.quantity", "commit lines.1.quantity"],
    log: ["start lines.1.quantity", "commit lines.1.quantity", "start title", "commit title"],
    shown: [
      { title: "Other", bravo: "b:Bravo/21" },
      { title: "Original", bravo: "b:Bravo/21" },
      { title: "Original", bravo: "b:Bravo/20" },
    ],
  });
});

test("a recorded step that adds a line: the lines show the rows each undo and redo lands on, and the save writes them", async () => {
  const f = await fixture({ publicView: true });
  act(() => { f.surface().history.perform(() => f.surface().form.setValue("lines", [...initialLines, { label: "New", position: 3 }], { shouldDirty: true })); });
  const changed = [...loadedLabels, "New"];
  await waitFor(() => expect(lineLabels()).toEqual(changed));
  // A second, unrelated change, so the first undo lands ON the changed frame rather than on the loaded one.
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Other" } });
  act(() => f.surface().history.undo());
  await waitFor(() => expect(lineLabels()).toEqual(changed));
  expect((screen.getByLabelText("Title") as HTMLTextAreaElement).value).toBe("Original");
  expect(screen.getByRole("button", { name: "Discard" })).toBeTruthy();
  act(() => f.surface().history.undo());
  await waitFor(() => expect(lineLabels()).toEqual(loadedLabels));
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
  act(() => f.surface().history.redo());
  await waitFor(() => expect(lineLabels()).toEqual(changed));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledOnce());
  expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({
    payload: { pk: "doc-1", patch: {}, lines: [...initialLines, { label: "New", position: 3 }] },
  }));
});

test("undoing the last local line edit releases the held canonical refresh", async () => {
  const f = await fixture();
  edit("a.label", "Local alpha");
  const remote = [initialLines[1]!, initialLines[0]!, initialLines[2]!].map((row, index) => ({ ...row, position: index }));
  await f.refresh(remote);
  expect(f.surface().form.getValues("lines")).not.toEqual(remote);
  edit("a.label", "Alpha");
  await waitFor(() => expect(f.surface().form.getValues("lines")).toEqual(remote));
  expect(f.surface().formIsDirty).toBe(false);
});


test.each([
  { label: "noncontiguous positions", positions: [10, 20, 30] },
  { label: "duplicate default positions", positions: [0, 0, 0] },
])("unchanged server lines with $label remain saveable before and after discard", async ({ positions }) => {
  const lines = initialLines.map((row, index) => ({ ...row, position: positions[index] }));
  const f = await fixture({ lines, submit: async () => ({ status: "ok", data: { id: "doc-1", title: "Original", lines } }) });
  edit("a.label", "First edit");
  await f.refresh(lines);
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledTimes(1);
  expect(f.surface().saveErrors).toEqual([]);
  expect(f.submit.mock.calls[0]?.[1].lines?.payload.map((row) => row.position)).toEqual([0, 1, 2]);
  act(() => f.surface().discardChanges());
  edit("b.label", "Edit after discard");
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledTimes(2);
  expect(f.surface().saveErrors).toEqual([]);
});

test.each(["position change", "reorder", "field change"])("an actual observed server %s still conflicts when stored positions are duplicate defaults", async (change) => {
  const lines = initialLines.map((row) => ({ ...row, position: 0 }));
  const f = await fixture({ lines });
  edit("a.label", "Local alpha");
  const remote = change === "reorder" ? [lines[1]!, lines[0]!, lines[2]!]
    : lines.map((row, index) => index !== 1 ? row : change === "position change"
      ? { ...row, position: 20 } : { ...row, quantity: 200 });
  await f.refresh(remote);
  await act(async () => f.surface().submitForm());
  expect(f.submit).not.toHaveBeenCalled();
  expect(f.surface().form.getFieldState("lines").error?.type).toBe("conflict");
});

/** Every request the fixture's provider and submit owner received. */
function requests(f: Awaited<ReturnType<typeof fixture>>): number {
  return f.provider.create.mock.calls.length + f.update.mock.calls.length + f.custom.mock.calls.length + f.submit.mock.calls.length;
}

test("nextSave reads a lines create as Save sends it, in one nested insert", async () => {
  const saved = { id: "doc-new", title: "Draft document", lines: [{ id: "line-new", label: "Lamp", quantity: 1, position: 0 }] };
  const f = await fixture({ isCreate: true, publicView: true, create: async () => ({ data: saved }) });
  edit("Title", "Draft document");
  fireEvent.click(screen.getByRole("button", { name: "Add line" }));
  edit("Label", "Lamp");
  const next = readNextSave(f.surface, () => requests(f));
  expect(next.data).toEqual({ title: "Draft document" });
  expect(next.lines?.created).toEqual([{ label: "Lamp", position: 0 }]);
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(f.provider.create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    variables: { ...next.data, lines: { data: next.lines!.payload } },
  })));
});

test("nextSave reads a lines edit as the resource save receives it, a list cell's own list included", async () => {
  const taggedResource = testDataResource(renderedResource.modelLabel, {
    ...renderedResource,
    linesResource: {
      ...renderedResource.linesResource!,
      fields: [...(renderedResource.linesResource!.fields ?? []), { ...lineField("tags", "String"), kind: "list" }],
    },
  });
  const lines = initialLines.map((row) => ({ ...row, tags: [`tag-${String(row.id)}`] }));
  const f = await fixture({ publicView: true, resourceMetadata: taggedResource, lines });
  fireEvent.change(screen.getByDisplayValue("Alpha"), { target: { value: "Edited alpha" } });
  const next = readNextSave(f.surface, () => requests(f));
  expect(next.data).toEqual({});
  expect(next.lines?.payload).toEqual([{ ...lines[0], label: "Edited alpha" }, ...lines.slice(1)]);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledOnce());
  expect(f.custom.mock.calls[0]![0].payload).toEqual({ pk: "doc-1", patch: next.data, lines: next.lines!.payload });
});

test("an own submit owner receives what nextSave reads of an edited, removed and reordered line list", async () => {
  const f = await fixture({ submit: async () => ({ status: "ok" as const, data: { id: "doc-1", title: "Original", lines: [] } }) });
  edit("a.label", "Edited alpha");
  f.remove(1);
  f.move(0, 1);
  const next = readNextSave(f.surface, () => requests(f));
  expect(next.lines?.payload.map((row) => row.id)).toEqual(["c", "a"]);
  expect(next.lines?.deleted).toEqual(["b"]);
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledOnce();
  expect(f.submit.mock.calls[0]![0]).toEqual(next.data);
  expect(f.submit.mock.calls[0]![1].lines).toEqual(next.lines);
});

/** The saved model's own native list, mounted beside the form as a list beside an open record is. */
function OwnModelList() {
  useList({ resource: renderedResource.roots.list!, dataProviderName: renderedResource.schemaName });
  return null;
}

test("guard: a save that writes lines confirms only after its own model's lists have refreshed", async () => {
  const f = await fixture({ publicView: true, recordExtras: () => <OwnModelList /> });
  await waitFor(() => expect(f.provider.getList).toHaveBeenCalledTimes(1));
  let answer!: () => void;
  f.provider.getList.mockImplementationOnce(() => new Promise((resolve) => { answer = () => resolve({ data: [], total: 0 }); }));
  fireEvent.change(screen.getByDisplayValue("Alpha"), { target: { value: "Edited alpha" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  // The write answered and the list was asked again: the save is at its wait.
  await waitFor(() => expect(f.custom).toHaveBeenCalledOnce());
  await waitFor(() => expect(f.provider.getList).toHaveBeenCalledTimes(2));
  // This test holds the list's answer, so a save that waits for it cannot have confirmed, however slow the
  // run; a save that stopped waiting confirms in the turn of the write's answer, before the lines above return.
  expect(screen.queryByText("Changes saved")).toBeNull();
  await act(async () => { answer(); });
  expect(await screen.findByText("Changes saved")).toBeTruthy();
});
