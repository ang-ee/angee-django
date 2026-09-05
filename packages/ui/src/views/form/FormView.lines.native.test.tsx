// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine, type DataProvider } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { createRootRoute, createRouter, createMemoryHistory, RouterContextProvider } from "@tanstack/react-router";
import { Controller, useFieldArray, type Control } from "react-hook-form";
import { refineResourcesFromDataResources, type DataResourceFieldMetadata, type ModelMetadata, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { ModalsHost, ToastProvider } from "../../feedback";
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
  linesResource: {
    field: "lines", modelLabel: "review.Line", positionField: "position",
    fields: [lineField("label", "String"), lineField("quantity", "Int"), lineField("position", "Int")],
  },
});
const model: ModelMetadata = {
  typeName: "DocumentType", resource,
  fields: { title: { name: "title", kind: "scalar", scalar: "String" } },
};
const initialLines: readonly Row[] = [
  { id: "a", label: "Alpha", quantity: 10, position: 0 },
  { id: "b", label: "Bravo", quantity: 20, position: 1 },
  { id: "c", label: "Charlie", quantity: 30, position: 2 },
];
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; });

async function fixture(options: { submit?: FormSubmit; lines?: readonly Row[] } = {}) {
  const seedLines = options.lines ?? initialLines;
  let record: Row = { id: "doc-1", title: "Original", lines: seedLines };
  const getOne = vi.fn(async () => ({ data: record }));
  const submit = vi.fn(options.submit ?? (async () => null));
  const provider = {
    getApiUrl: () => "test://lines", getOne,
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as DataProvider;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  let surface!: FormViewSaveSurface;
  let remove!: (index: number) => void;
  let move!: (from: number, to: number) => void;
  let append!: (row: Row) => void;
  function Probe() {
    surface = useFormViewSave({
      resource: resource.modelLabel, id: "doc-1", isCreate: false,
      dataResource: resource, modelMetadata: model, formFields, fieldByName, refineFields,
      submit, t: (key) => key,
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
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider><Probe /></ToastProvider></ModalsHost></RouterContextProvider>
  </Refine>);
  await screen.findByLabelText("c.label");
  await waitFor(() => expect(surface?.form.getValues("lines")).toMatchObject(seedLines));
  return {
    surface: () => surface, submit, getOne,
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
