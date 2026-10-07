// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import type { DataResourceFieldMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import {
  AppRuntimeProvider,
  ModalsHost,
  RecordChromeProvider,
  ToastProvider,
  defaultWidgets,
  type RecordChromeContext,
} from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { TagsField } from "./TagsField";

const mocks = vi.hoisted(() => ({ tag: vi.fn(), untag: vi.fn() }));

vi.mock("./documents.console", () => ({ TagDocument: { kind: "tag" }, UntagDocument: { kind: "untag" } }));
vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  useAuthoredResourceMutation: (document: { kind: string }) =>
    [document.kind === "tag" ? mocks.tag : mocks.untag, { fetching: false, error: null, reset: vi.fn() }],
}));

const field = (name: string, overrides: Partial<DataResourceFieldMetadata> = {}): DataResourceFieldMetadata => ({
  name, kind: "scalar", scalar: "String", readable: true, aggregatable: false,
  creatable: true, updatable: true, requiredOnCreate: false, ...overrides,
});
const tag = testDataResource("tags.Tag", {
  recordRepresentation: "name",
  fields: [field("id", { scalar: "ID", creatable: false, updatable: false }), field("name", { requiredOnCreate: true })],
});
const file = testDataResource("storage.File", {
  resourceType: "storage/file",
  recordRepresentation: "title",
  fields: [field("id", { scalar: "ID", creatable: false, updatable: false }), field("title")],
});
const chrome: RecordChromeContext = {
  resource: "storage.File", canonicalResource: "storage.File", dataProviderName: undefined,
  recordId: "fil_1", record: { id: "fil_1", title: "Brief" }, formReadOnly: false,
};
const urgent = { id: "tag-1", name: "Urgent" };
const billing = { id: "tag-2", name: "Billing" };

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://tags-field", resources: [tag, file],
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); vi.clearAllMocks(); });

function harness(record: RecordChromeContext | null = chrome) {
  const getList = vi.fn(async () => ({ data: [urgent, billing], total: 2 }));
  const create = vi.fn(async ({ variables }: { variables?: unknown }) => ({
    data: { id: "tag-new", ...(variables as Record<string, unknown>) },
  }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  function Wrapper({ children }: { children?: ReactNode }) {
    const content = record ? <RecordChromeProvider value={record}>{children}</RecordChromeProvider> : children;
    return <Provider dataProvider={{ getList, create }}>
      <RouterContextProvider router={router}>
        <ModalsHost><ToastProvider>
          <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>{content}</AppRuntimeProvider>
        </ToastProvider></ModalsHost>
      </RouterContextProvider>
    </Provider>;
  }
  return { getList, create, Wrapper };
}

function choose(option: HTMLElement) {
  fireEvent.pointerDown(option, { pointerType: "mouse", button: 0 });
  fireEvent.click(option);
}

test("picking a tag attaches it to the open record at once and removing a chip detaches it", async () => {
  mocks.tag.mockResolvedValue({ tag: [] });
  mocks.untag.mockResolvedValue({ untag: true });
  const { Wrapper } = harness();
  render(<Wrapper><TagsField value={[urgent]} field={{ label: "Tags" }} /></Wrapper>);

  expect(screen.getByText("Urgent")).toBeTruthy();
  // One chips field: the search sits inside it, beside the chips.
  fireEvent.input(screen.getByRole("combobox", { name: "Tags" }), { target: { value: "Bill" }, inputType: "insertText" });
  choose(await screen.findByRole("option", { name: "Billing" }));
  await waitFor(() => expect(mocks.tag).toHaveBeenCalledWith({ targetType: "storage/file", targetId: "fil_1", tagIds: ["tag-2"] }));
  expect(mocks.untag).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: /Urgent/ }));
  await waitFor(() => expect(mocks.untag).toHaveBeenCalledWith({ targetType: "storage/file", targetId: "fil_1", tagIds: ["tag-1"] }));
  expect(mocks.tag).toHaveBeenCalledOnce();
});

test("Create “name” makes the tag at once and attaches it; Create and edit… opens its form", async () => {
  mocks.tag.mockResolvedValue({ tag: [] });
  const { create, Wrapper } = harness();
  render(<Wrapper><TagsField value={[]} field={{ label: "Tags" }} /></Wrapper>);

  // Create is the search's last option, never a button beside the field.
  expect(screen.queryByRole("button", { name: "New tag" })).toBeNull();
  const search = screen.getByRole("combobox", { name: "Tags" });
  fireEvent.input(search, { target: { value: "Follow up" }, inputType: "insertText" });
  fireEvent.click(await screen.findByRole("option", { name: "Create “Follow up”" }));

  await waitFor(() => expect(mocks.tag).toHaveBeenCalledWith({ targetType: "storage/file", targetId: "fil_1", tagIds: ["tag-new"] }));
  expect(create.mock.calls[0]?.[0]).toMatchObject({ variables: { name: "Follow up" } });
  expect(screen.queryByRole("dialog")).toBeNull();

  fireEvent.input(search, { target: { value: "Later" }, inputType: "insertText" });
  fireEvent.click(await screen.findByRole("option", { name: "Create and edit…" }));
  const dialog = await screen.findByRole("dialog", { name: "New tag" });
  expect(await within(dialog).findByLabelText("Name")).toHaveProperty("value", "Later");
});

test("a read-only form or reader renders linked chips only", () => {
  const { getList, Wrapper } = harness({ ...chrome, formReadOnly: true });
  render(<Wrapper><TagsField value={[urgent, billing]} field={{ label: "Tags" }} /></Wrapper>);

  expect(screen.getByText("Urgent")).toBeTruthy();
  expect(screen.getByText("Billing")).toBeTruthy();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(screen.queryByRole("button", { name: "New tag" })).toBeNull();
  expect(getList).not.toHaveBeenCalled();
});

test("an unset form value is no tags, not a crash", () => {
  const { Wrapper } = harness(chrome);
  render(<Wrapper><TagsField value={"" as unknown as readonly unknown[]} field={{ label: "Tags" }} /></Wrapper>);
  expect(screen.getByRole("combobox")).toBeTruthy();
});

test("a create form has no record to tag yet", () => {
  const { getList, Wrapper } = harness(null);
  render(<Wrapper><TagsField value={[]} field={{ label: "Tags" }} /></Wrapper>);

  expect(screen.getByText("Save the record to add tags.")).toBeTruthy();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(getList).not.toHaveBeenCalled();
});
