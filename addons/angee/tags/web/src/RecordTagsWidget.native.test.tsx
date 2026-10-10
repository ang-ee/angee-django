// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider, ModalsHost, RecordChromeProvider, ToastProvider } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

import { recordTagsWidget } from "./RecordTagsWidget";

const tag = testDataResource("tags.Tag", { capabilities: ["list"], recordRepresentation: "name",
  fields: [{ name: "name", kind: "scalar", scalar: "String", readable: true, aggregatable: false,
    creatable: false, updatable: false, requiredOnCreate: false }],
});
const { Provider, clearClients } = createUiTestProviders({
  resources: [tag, testDataResource("notes.Note", { resourceType: "notes/note" }), testDataResource("tags.TagAssignment")],
  apiUrl: "test://record-tags", queryClientConfig: { defaultOptions: { queries: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); });
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
