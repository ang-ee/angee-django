// @vitest-environment happy-dom

import { act, cleanup, render, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { schemaFieldMetadataFromDataResources, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import type { MetaQuery } from "@refinedev/core";
import { OperationDocumentsProvider, useAuthoredQuery, type TypedDocumentNode } from "@angee/refine";
import gql from "graphql-tag";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import type { FieldDescriptor } from "../page";
import { useFormViewSave, type FormViewSaveSurface } from "./use-form-view-save";

const fields: readonly FieldDescriptor[] = [{ name: "title", label: "Title" }, { name: "body", label: "Body" }];
const fieldByName = new Map(fields.map((field) => [field.name, field]));
const resource = testDataResource("notes.Note", {
  createFields: ["title", "body"],
  fields: fields.map((field) => ({
    name: field.name, kind: "scalar" as const, scalar: "String", readable: true,
    filterable: false, sortable: false, aggregatable: false, groupable: false,
    creatable: true, updatable: true, requiredOnCreate: false,
  })),
});
const { Provider, createClient, clearClients } = createUiTestProviders({ resources: [resource], apiUrl: "test://notes" });
const summary = gql`query NoteSummary { summary }` as TypedDocumentNode<{ summary: string }, Record<string, never>>;

afterEach(() => { cleanup(); clearClients(); });

/**
 * A record form's save beside one authored read registered on the saved model, as the record's own
 * chrome mounts them. The read answers its first load at once unless `first` says otherwise; `refetch`
 * decides every later answer.
 */
function renderSave(
  refetch: () => Promise<{ data: { summary: string } }>,
  first: () => Promise<{ data: { summary: string } }> = async () => ({ data: { summary: "Before" } }),
) {
  const reads = vi.fn(async ({ meta }: { meta?: MetaQuery }) => {
    if (meta?.gqlQuery !== summary) throw new Error("unexpected read");
    return reads.mock.calls.length === 1 ? first() : refetch();
  });
  const update = vi.fn(async ({ variables }: { variables?: unknown }) => ({ data: { id: "note-1", title: "Before", body: "", ...(variables as Row) } }));
  const onSaved = vi.fn();
  // `fetchStatuses`: what each render of the read's owner saw, as a reader that waits for a first answer does.
  const state: { surface?: FormViewSaveSurface; summary?: string; fetchStatuses: string[] } = { fetchStatuses: [] };
  function Harness() {
    const read = useAuthoredQuery(summary, {}, { dataProviderName: "console", models: [resource.modelLabel] });
    state.summary = read.data?.summary;
    state.fetchStatuses.push(read.fetchStatus);
    state.surface = useFormViewSave({
      resource: resource.modelLabel, id: "note-1", isCreate: false, dataResource: resource,
      modelMetadata: schemaFieldMetadataFromDataResources([resource]).labels[resource.modelLabel]!,
      formFields: fields, fieldByName, refineFields: ["id", "title", "body"], onSaved, t: (key) => key,
    });
    return null;
  }
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Provider resources={[resource]} queryClient={createClient()} dataProvider={{
    custom: reads, update, create: update, getList: async () => ({ data: [], total: 0 }),
    getOne: async () => ({ data: { id: "note-1", title: "Before", body: "" } }),
  }}><RouterContextProvider router={router}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <OperationDocumentsProvider documents={{ console: {} }}><Harness /></OperationDocumentsProvider>
  </AppRuntimeProvider></ToastProvider></ModalsHost></RouterContextProvider></Provider>);
  // Presses Save and returns once the press is handled, not once the save settles: each test waits for
  // the effect it names, so a save that is held fails that assertion and not a timeout.
  const save = async (name: string, value: string) => {
    act(() => { state.surface!.form.setValue(name, value, { shouldDirty: true }); });
    await act(async () => { void state.surface!.submitForm(); });
  };
  return { reads, update, onSaved, state, save };
}

async function loaded(view: ReturnType<typeof renderSave>) {
  await waitFor(() => { expect(view.state.surface?.loading).toBe(false); expect(view.state.summary).toBe("Before"); });
}

test("a save is confirmed when its write answers, while a read of the saved model that never answers is still refreshing", async () => {
  const view = renderSave(() => new Promise(() => undefined));
  await loaded(view);
  await view.save("title", "After");
  // The refresh was started, so the confirmation does not come from skipping it: the read was asked again.
  await waitFor(() => expect(view.reads).toHaveBeenCalledTimes(2));
  expect(view.update).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(view.onSaved).toHaveBeenCalledTimes(1));
  expect(view.state.surface!.pending).toBe(false);
});

test("Save sends the next write after a read of the saved model never answered the first save's refresh", async () => {
  const view = renderSave(() => new Promise(() => undefined));
  await loaded(view);
  await view.save("title", "After");
  await waitFor(() => expect(view.reads).toHaveBeenCalledTimes(2));
  await view.save("body", "Second edit");
  await waitFor(() => expect(view.update).toHaveBeenCalledTimes(2));
  expect((view.update.mock.calls[1]![0] as { variables: Row }).variables).toStrictEqual({ body: "Second edit" });
  await waitFor(() => expect(view.onSaved).toHaveBeenCalledTimes(2));
});

test("guard: the read of the saved model still refreshes after the confirmation, when its answer arrives", async () => {
  let answer!: (value: { data: { summary: string } }) => void;
  const view = renderSave(() => new Promise((resolve) => { answer = resolve; }));
  await loaded(view);
  await view.save("title", "After");
  await waitFor(() => expect(view.reads).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(view.onSaved).toHaveBeenCalledTimes(1));
  expect(view.state.summary).toBe("Before");
  await act(async () => { answer({ data: { summary: "After" } }); });
  await waitFor(() => expect(view.state.summary).toBe("After"));
});

test("a save confirmed while a read of the saved model has not answered its first load never renders that read idle", async () => {
  const never = () => new Promise<{ data: { summary: string } }>(() => undefined);
  const view = renderSave(never, never);
  await waitFor(() => expect(view.state.surface?.loading).toBe(false));
  expect(view.reads).toHaveBeenCalledTimes(1);
  act(() => { view.state.surface!.form.setValue("title", "After", { shouldDirty: true }); });
  // Counted after the edit's own render, so what follows is rendered by the save.
  const rendersBeforeSave = view.state.fetchStatuses.length;
  await act(async () => { void view.state.surface!.submitForm(); });
  // The refresh cancelled the unanswered read and sent it again, and the confirmation rendered meanwhile.
  await waitFor(() => expect(view.reads).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(view.onSaved).toHaveBeenCalledTimes(1));
  expect(view.state.fetchStatuses.length).toBeGreaterThan(rendersBeforeSave);
  expect([...new Set(view.state.fetchStatuses)]).toEqual(["fetching"]);
});
