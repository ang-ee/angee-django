// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider, ModalsHost, RecordChromeProvider, ToastProvider } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

import { recordTagsWidget } from "./RecordTagsWidget";

const NAME_FIELD = { name: "name", kind: "scalar", scalar: "String", readable: true, aggregatable: false,
  creatable: false, updatable: false, requiredOnCreate: false } as const;
const tag = testDataResource("tags.Tag", { capabilities: ["list"], recordRepresentation: "name", fields: [NAME_FIELD] });
const { Provider, clearClients } = createUiTestProviders({
  resources: [tag, testDataResource("notes.Note", { resourceType: "notes/note" }), testDataResource("tags.TagAssignment")],
  apiUrl: "test://record-tags", queryClientConfig: { defaultOptions: { queries: { retry: false } } },
});
// Categories exist: tags read where each is filed, so their list carries a filtered query contract.
const categorised = createUiTestProviders({
  resources: [
    testDataResource("tags.Tag", {
      capabilities: ["list"], recordRepresentation: "name", fields: [NAME_FIELD],
      roots: { aggregate: "tags_aggregate" }, typeNames: { node: "TagType", filter: "tags_bool_exp", order: "tags_order_by" },
    }),
    testDataResource("tags.TagCategory", { capabilities: ["list"], recordRepresentation: "name", fields: [NAME_FIELD] }),
    testDataResource("notes.Note", { resourceType: "notes/note" }),
    testDataResource("tags.TagAssignment"),
  ],
  apiUrl: "test://record-tags-categorised", queryClientConfig: { defaultOptions: { queries: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); categorised.clearClients(); });
const Widget = recordTagsWidget.edit!;

function fixture({ readOnly = false, permissions = ["write"] } = {}) {
  const custom = vi.fn(async (_request: { meta?: Record<string, unknown> }) => ({ data: { tag: [{ id: "assignment" }], untag: true } }));
  const getOne = vi.fn();
  const getList = vi.fn(async () => ({ data: [{ id: "tag", name: "Urgent" }], total: 1 }));
  render(<Provider dataProvider={{ custom, getOne, getList }}><AppRuntimeProvider runtime={{}}>
    <ModalsHost><ToastProvider><RecordChromeProvider value={{
      resource: "notes.Note", canonicalResource: "notes.Note", dataProviderName: "console", recordId: "note",
      record: { id: "note", tags: [], permissions }, formReadOnly: false,
    }}><Widget readOnly={readOnly} /></RecordChromeProvider></ToastProvider></ModalsHost>
  </AppRuntimeProvider></Provider>);
  return { custom, getOne };
}

test("the labelled record widget uses the tags verb and the owning resource identity", async () => {
  const f = fixture();
  expect(screen.getByText("Tags", { selector: "span" })).toBeTruthy();
  fireEvent.input(screen.getByRole("combobox", { name: "Tags" }), { target: { value: "urg" }, inputType: "insertText" });
  fireEvent.click(await screen.findByRole("option", { name: "Urgent" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledOnce());
  expect(f.custom.mock.calls[0]?.[0]?.meta?.gqlVariables).toEqual({ type: "notes/note", id: "note", tags: ["tag"] });
  expect(f.getOne).not.toHaveBeenCalled();
});

test.each([{ readOnly: true }, { permissions: ["read"] }])("read-only tags keep a visible label without an editor: %j", (options) => {
  const f = fixture(options);
  expect(screen.getByText("Tags", { selector: "span" })).toBeTruthy();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(f.custom).not.toHaveBeenCalled();
});

const RELATIONSHIP = { id: "cat", name: "Relationship", position: 1024, max_per_record: 1, is_archived: false };

function categorisedFixture({ permissions = ["read"], readOnly = true } = {}) {
  const custom = vi.fn(async (_request: { meta?: Record<string, unknown> }) => ({ data: { tag: [{ id: "assignment" }], untag: true } }));
  const getList = vi.fn(async ({ resource }: { resource: string }) => resource === "tagcategorys"
    ? { data: [RELATIONSHIP], total: 1 }
    : { data: [
      { id: "tag", name: "Urgent", category: RELATIONSHIP },
      { id: "calm", name: "Calm", category: RELATIONSHIP },
      { id: "loose", name: "Loose", category: null },
    ], total: 3 });
  render(<categorised.Provider dataProvider={{ custom, getOne: vi.fn(), getList }}><AppRuntimeProvider runtime={{}}>
    <ModalsHost><ToastProvider><RecordChromeProvider value={{
      resource: "notes.Note", canonicalResource: "notes.Note", dataProviderName: "console", recordId: "note",
      record: { id: "note", tags: [{ id: "tag", name: "Urgent" }, { id: "loose", name: "Loose" }], permissions },
      formReadOnly: false,
    }}><Widget readOnly={readOnly} /></RecordChromeProvider></ToastProvider></ModalsHost>
  </AppRuntimeProvider></categorised.Provider>);
  return { custom };
}

const field = (label: string) => screen.getByText(label, { selector: "span" }).parentElement as HTMLElement;

test("each category holds its own labelled picker with its maximum, before the uncategorised tags", async () => {
  categorisedFixture();
  const group = await screen.findByRole("group", { name: "Tags" });
  await waitFor(() => expect(within(field("Relationship tags")).getByText("Urgent")).toBeTruthy());
  expect(within(field("Relationship tags")).getByText("At most 1 tag")).toBeTruthy();
  expect(within(field("Other tags")).getByText("Loose")).toBeTruthy();
  expect(within(field("Relationship tags")).queryByText("Loose")).toBeNull();
  // Category pickers come first, in category order; the uncategorised tags close the group.
  expect(within(group).getAllByText(/ tags$/, { selector: "span" }).map((label) => label.childNodes[0]?.textContent))
    .toEqual(["Relationship tags", "Other tags"]);
});

test("a pick at a maximum of one replaces the held tag, untagging before tagging", async () => {
  const f = categorisedFixture({ permissions: ["write"], readOnly: false });
  const picker = await screen.findByRole("combobox", { name: "Relationship tags" });
  fireEvent.input(picker, { target: { value: "cal" }, inputType: "insertText" });
  fireEvent.click(await screen.findByRole("option", { name: "Calm" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(2));
  expect(f.custom.mock.calls.map((call) => call[0]?.meta?.gqlVariables)).toEqual([
    { type: "notes/note", id: "note", tags: ["tag"] },
    { type: "notes/note", id: "note", tags: ["calm"] },
  ]);
});
