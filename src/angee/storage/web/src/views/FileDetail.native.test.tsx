// @vitest-environment happy-dom

import { createUiTestProviders } from "@angee/ui/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import type { Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider, ControlBandProvider, ModalsHost, ToastProvider, baseIcons, defaultWidgets } from "@angee/ui";
import * as React from "react";
import { afterEach, expect, test, vi } from "vitest";

import { FileDetail } from "./FileDetail";

const resource = testDataResource("storage.File", {
  recordRepresentation: "title",
  updateFields: ["title"],
  fields: ["title", "filename", "created_by_label", "upload_state", "visibility", "created_at", "updated_at"].map((name) => ({
    name, kind: "scalar", scalar: "String", readable: true, filterable: false,
    sortable: false, aggregatable: false, groupable: false, creatable: false,
    updatable: name === "title", requiredOnCreate: false,
  })),
});
const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://files" });
afterEach(() => { cleanup(); clearClients(); });

test("route identity starts the native form read without a preview and resets values while the next file loads", async () => {
  const pending = new Map<string, (value: { data: Row }) => void>();
  const getOne = vi.fn(({ id }: { id?: string | number }) => new Promise<{ data: Row }>((resolve) => {
    pending.set(String(id), resolve);
  }));
  const update = vi.fn(async () => ({ data: {} }));
  const provider = {
    getOne, update,
    getList: vi.fn(async () => ({ data: [], total: 0 })),
  } satisfies RefineTestDataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const onChanged = vi.fn();
  function Tree({ id, filename }: { id: string; filename?: string }) {
    return (
      <Provider resources={[resource]} dataProvider={provider} options={{ reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } } } }}>
        <RouterContextProvider router={router}>
          <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets }}>
            <ModalsHost><ToastProvider><FileDetail id={id} filename={filename} onChanged={onChanged} /></ToastProvider></ModalsHost>
          </AppRuntimeProvider>
        </RouterContextProvider>
      </Provider>
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

test("the editable details card draws no empty command strip above its fields", async () => {
  const provider = {
    getOne: vi.fn(async () => ({ data: { id: "file-a", title: "Alpha", filename: "alpha.txt", upload_state: "ready" } as Row })),
    update: vi.fn(async () => ({ data: {} })),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
  } satisfies RefineTestDataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  // As the file page renders it: the page's own band holds the layout's control row, and the
  // details card keeps the metadata form's band inline beside its fields.
  function FilePage() {
    const [host, setHost] = React.useState<HTMLElement | null>(null);
    return (
      <ControlBandProvider host={host}>
        <div data-testid="control-row" ref={setHost} />
        <section data-testid="details">
          <ControlBandProvider host={undefined}>
            <FileDetail id="file-a" onChanged={vi.fn()} />
          </ControlBandProvider>
        </section>
      </ControlBandProvider>
    );
  }
  render(
    <Provider resources={[resource]} dataProvider={provider}>
      <RouterContextProvider router={router}>
        <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets }}>
          <ModalsHost><ToastProvider><FilePage /></ToastProvider></ModalsHost>
        </AppRuntimeProvider>
      </RouterContextProvider>
    </Provider>,
  );
  const title = await screen.findByDisplayValue("Alpha") as HTMLInputElement;
  expect(title.readOnly || title.disabled).toBe(false);

  const band = screen.getByTestId("details").querySelector<HTMLElement>("[role=toolbar]");
  expect(screen.getByTestId("control-row").childElementCount).toBe(0);
  expect(band?.hasAttribute("data-layout-band")).toBe(false);
  // With no edit yet, none of its groups holds an item, and the inline band collapses on that.
  // (happy-dom does not evaluate the `:has` rule, so the test reads the DOM fact it reads.)
  const items = [...band!.children].flatMap((group) => [...group.children])
    .filter((child) => !child.hasAttribute("hidden") && child.childNodes.length > 0);
  expect(items).toEqual([]);
  expect(band!.classList).toContain("not-data-layout-band:not-has-[>div>:not([hidden]):not(:empty)]:hidden");
});
