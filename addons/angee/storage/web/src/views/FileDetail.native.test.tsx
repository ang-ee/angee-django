// @vitest-environment happy-dom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { Refine, type DataProvider } from "@refinedev/core";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider, ModalsHost, ToastProvider, baseIcons, defaultWidgets } from "@angee/ui";
import { afterEach, expect, test, vi } from "vitest";

import { FileDetail } from "./FileDetail";

const resource = testDataResource("storage.File", {
  recordRepresentation: "title",
  updateFields: ["title"],
  fields: ["title", "filename", "created_by_label", "upload_state", "created_at", "updated_at"].map((name) => ({
    name, kind: "scalar", scalar: "String", readable: true, filterable: false,
    sortable: false, aggregatable: false, groupable: false, creatable: false,
    updatable: name === "title", requiredOnCreate: false,
  })),
});
const metadata = schemaFieldMetadataFromDataResources([resource]);
afterEach(cleanup);

test("route identity starts the native form read without a preview and resets values while the next file loads", async () => {
  const pending = new Map<string, (value: { data: Row }) => void>();
  const getOne = vi.fn(({ id }: { id?: string | number }) => new Promise<{ data: Row }>((resolve) => {
    pending.set(String(id), resolve);
  }));
  const update = vi.fn(async () => ({ data: {} }));
  const provider = {
    getApiUrl: () => "test://files", getOne, update, create: vi.fn(), deleteOne: vi.fn(),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
  } as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const onChanged = vi.fn();
  function Tree({ id, filename }: { id: string; filename?: string }) {
    return (
      <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } } } }}>
        <RouterContextProvider router={router}>
          <ModelMetadataProvider metadata={metadata}>
            <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets }}>
              <ModalsHost><ToastProvider><FileDetail id={id} filename={filename} onChanged={onChanged} /></ToastProvider></ModalsHost>
            </AppRuntimeProvider>
          </ModelMetadataProvider>
        </RouterContextProvider>
      </Refine>
    );
  }
  const view = render(<Tree id="file-a" />);
  await waitFor(() => expect(pending.has("file-a")).toBe(true));
  await act(async () => pending.get("file-a")!({ data: { id: "file-a", title: "Alpha", filename: "alpha.txt", upload_state: "ready" } }));
  const title = await screen.findByDisplayValue("Alpha") as HTMLInputElement;
  expect(title.readOnly || title.disabled).toBe(false);

  view.rerender(<Tree id="file-b" />);
  await waitFor(() => expect(pending.has("file-b")).toBe(true));
  expect(screen.queryByDisplayValue("Alpha")).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(update).not.toHaveBeenCalled();

  await act(async () => pending.get("file-b")!({ data: { id: "file-b", title: "Beta", filename: "beta.txt", upload_state: "ready" } }));
  const loaded = await screen.findByDisplayValue("Beta") as HTMLInputElement;
  expect(loaded.readOnly || loaded.disabled).toBe(false);
  view.rerender(<Tree id="file-b" filename="beta.txt" />);
  expect(screen.getByDisplayValue("Beta")).toBe(loaded);
  expect(getOne).toHaveBeenCalledTimes(2);
  expect(update).not.toHaveBeenCalled();
  expect(onChanged).not.toHaveBeenCalled();
});
