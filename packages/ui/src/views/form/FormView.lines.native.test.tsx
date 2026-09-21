// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRootRoute, createRouter, createMemoryHistory, RouterContextProvider } from "@tanstack/react-router";
import { Controller, useFieldArray, type Control } from "react-hook-form";
import type { ComponentProps } from "react";
import { schemaFieldMetadataFromDataResources, type DataResourceFieldMetadata, type ModelMetadata, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider, type ResourceSaveVariables } from "@angee/refine";
import { afterEach, expect, test, vi } from "vitest";
import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { defaultWidgets } from "../../widgets";
import { FormView } from "./FormView";
import { useFormViewSave, type FormSubmit, type FormViewSaveSurface } from "./use-form-view-save";
import type { FieldDescriptor } from "../page";

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
  submit?: FormSubmit;
  lines?: readonly Row[];
  isCreate?: boolean;
  create?: () => Promise<{ data: Row }>;
  publicView?: boolean;
  save?: (variables: ResourceSaveVariables) => Promise<Row>;
  recordExtras?: ComponentProps<typeof FormView>["recordExtras"];
} = {}) {
  const seedLines = options.lines ?? initialLines;
  const activeResource = options.publicView ? renderedResource : resource;
  let record: Row = { id: "doc-1", title: "Original", lines: seedLines };
  const getOne = vi.fn(async () => ({ data: record }));
  const submit = vi.fn(options.submit ?? (async () => null));
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
    getList: vi.fn(async () => ({ data: [], total: 0 })),
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
      dataResource: resource, modelMetadata: model, formFields, fieldByName, refineFields,
      submit: options.isCreate ? undefined : submit, t: (key) => key,
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
          <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
            <FormView
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
    if (options.publicView) await screen.findByDisplayValue("Charlie");
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
      act(() => surface.reload());
      await waitFor(() => expect(surface.form.getValues("title")).toBe("Remote title"));
    },
  };
}

function edit(name: string, value: string) { fireEvent.change(screen.getByLabelText(name), { target: { value } }); }

test("new documents render Add line and create their draft lines in one native nested insert", async () => {
  const saved = { id: "doc-new", title: "Quotation", lines: [{ id: "line-new", label: "Lamp", quantity: 1, position: 0 }] };
  const f = await fixture({ isCreate: true, publicView: true, create: async () => ({ data: saved }) });
  expect(f.surface().linesActive).toBe(true);
  edit("Title", "Quotation");
  fireEvent.click(screen.getByRole("button", { name: "Add line" }));
  edit("Label", "Lamp");
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(f.provider.create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    variables: { title: "Quotation", lines: { data: [{ label: "Lamp", position: 0 }] } },
  })));
  expect(f.provider.update).not.toHaveBeenCalled();
  expect(f.custom).not.toHaveBeenCalled();
  expect(f.getOne).not.toHaveBeenCalled();
  await waitFor(() => expect(f.surface().formIsDirty).toBe(false));
  expect(f.surface().form.getValues("lines")).toEqual(saved.lines);
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
  edit("title", "Quotation");
  f.append({ label: "Lamp", quantity: "1" });
  await act(async () => f.surface().submitForm());
  expect(f.provider.create).toHaveBeenCalledTimes(1);
  expect(f.surface().form.getValues("title")).toBe("Quotation");
  expect(f.surface().form.getValues("lines")).toMatchObject([{ label: "Lamp", quantity: "1" }]);
  expect(f.surface().formIsDirty).toBe(true);
  expect(f.surface().form.formState.errors.root?.server?.message).toBe("Line rejected");
  expect(f.provider.update).not.toHaveBeenCalled();
});

test("successful semantic no-op line saves accept the native draft baseline", async () => {
  const f = await fixture({ submit: async () => ({ id: "doc-1", title: "Original", lines: initialLines }) });
  act(() => f.surface().form.setValue<string>("lines.0.quantity", "10", { shouldDirty: true }));
  expect(f.surface().formIsDirty).toBe(true);
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledWith({}, expect.objectContaining({ lines: expect.objectContaining({ hasChanges: false }) }));
  await waitFor(() => expect(f.surface().formIsDirty).toBe(false));
  expect(f.surface().form.getValues("lines")).toEqual(initialLines);
});

test("semantic no-op line saves preserve a later edit while the request is pending", async () => {
  let resolve!: (row: Row) => void;
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }) });
  act(() => f.surface().form.setValue<string>("lines.0.quantity", "10", { shouldDirty: true }));
  let saving!: Promise<void>;
  act(() => { saving = f.surface().submitForm(); });
  await waitFor(() => expect(f.submit).toHaveBeenCalledTimes(1));
  edit("a.quantity", "11");
  await act(async () => { resolve({ id: "doc-1", title: "Original", lines: initialLines }); await saving; });
  expect((f.surface().form.getValues("lines") as Row[])[0]?.quantity).toBe("11");
  expect(f.surface().formIsDirty).toBe(true);
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
  act(() => f.surface().reload());
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
  expect(f.surface().saveError).toBe("form.linesChanged");
  expect(f.surface().form.getValues("lines")).toEqual([{ ...initialLines[0], label: "Local alpha" }, ...initialLines.slice(1)]);
  act(() => f.surface().discardChanges());
  expect(f.surface().form.getValues("lines")).toEqual(remote);
  expect(f.surface().saveError).toBeNull();
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
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }) });
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
  f.submit.mockResolvedValueOnce(null);
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledTimes(2);
  expect(f.submit.mock.calls[1]?.[1].lines?.created).toEqual([]);
  expect(f.submit.mock.calls[1]?.[1].lines?.payload.map((row) => row.id)).toEqual(["b", "a", "c"]);
});

test("a new line receives its saved ID normally while post-submission scalar edits remain dirty", async () => {
  let resolve!: (value: Row) => void;
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }) });
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
  f.submit.mockResolvedValueOnce(null);
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledTimes(2);
  expect(f.submit.mock.calls[1]?.[0]).toEqual({ title: "Later title" });
  expect(f.submit.mock.calls[1]?.[1].lines?.hasChanges).toBe(false);
});

test.each([true, false])("concurrent edits to a newly saved line remain intact and cannot recreate it (response includes IDs: %s)", async (includesLines) => {
  let resolve!: (value: Row) => void;
  const f = await fixture({ submit: () => new Promise<Row>((done) => { resolve = done; }) });
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
  expect(f.surface().saveError).toBe("form.linesChanged");
  if (!includesLines) {
    await act(async () => resolveRead({ data: accepted }));
    await waitFor(() => expect(f.surface().displayRecord?.lines).toEqual(accepted.lines));
  }
  act(() => f.surface().discardChanges());
  expect(f.surface().form.getValues("lines")).toEqual(accepted.lines);
  expect(f.surface().formIsDirty).toBe(false);
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
  const f = await fixture({ lines });
  edit("a.label", "First edit");
  await f.refresh(lines);
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledTimes(1);
  expect(f.surface().saveError).toBeNull();
  expect(f.submit.mock.calls[0]?.[1].lines?.payload.map((row) => row.position)).toEqual([0, 1, 2]);
  act(() => f.surface().discardChanges());
  edit("b.label", "Edit after discard");
  await act(async () => f.surface().submitForm());
  expect(f.submit).toHaveBeenCalledTimes(2);
  expect(f.surface().saveError).toBeNull();
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
