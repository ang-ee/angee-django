// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { operationDocuments } from "@angee/gql/console/actions";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { AppRuntimeProvider, ModalsHost, RecordChromeProvider, ToastProvider, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";

import { ChangeFileVisibilityAction } from "./file-form";

const { Provider, clearClients } = createUiTestProviders({
  // Under a router, as an app page is: a form dialog guards route moves while it holds work.
  router: true,
  resources: [testDataResource("storage.File", { fields: [{
    name: "visibility", kind: "enum", scalar: "String", readable: true, aggregatable: false,
    creatable: false, updatable: false, requiredOnCreate: false,
    values: [{ value: "INHERITED", description: "Inherited" }, { value: "RECORD", description: "Record" }],
  }] })],
});
afterEach(() => { cleanup(); clearClients(); });

function renderVisibility(permissions: string[], readOnly = false) {
  const provider = {
    custom: vi.fn(async () => ({ data: {
      set_file_visibility: { ok: true, message: "Visibility changed.", id: "file_1", validation_errors: null },
    } })),
  } satisfies RefineTestDataProvider;
  render(<Provider dataProvider={provider}>
    <OperationDocumentsProvider documents={{ console: operationDocuments }}>
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><ModalsHost><ToastProvider>
    <RecordChromeProvider value={{ resource: "storage.File", canonicalResource: "storage.File", dataProviderName: "console",
      recordId: "file_1", record: { id: "file_1", visibility: "INHERITED", permissions }, formReadOnly: readOnly }}>
      <ChangeFileVisibilityAction />
    </RecordChromeProvider>
    </ToastProvider></ModalsHost></AppRuntimeProvider>
    </OperationDocumentsProvider>
  </Provider>);
  return provider.custom;
}

test.each([
  { permissions: ["write"], choices: ["Record"], value: "RECORD" },
  { permissions: ["inherit_visibility"], choices: ["Inherited"], value: "INHERITED" },
  { permissions: ["write", "inherit_visibility"], choices: ["Inherited", "Record"], value: "RECORD" },
])("Change visibility offers only the projected choices: $permissions", async ({ permissions, choices, value }) => {
  const mutate = renderVisibility(permissions);
  fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Change visibility" }));
  const dialog = await screen.findByRole("dialog", { name: "Change visibility" });
  fireEvent.click(within(dialog).getByRole("combobox", { name: "Audience" }));
  expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual(choices);
  const option = screen.getByRole("option", { name: value === "RECORD" ? "Record" : "Inherited" });
  fireEvent.pointerDown(option, { pointerType: "mouse", button: 0 });
  fireEvent.click(option);
  fireEvent.click(within(dialog).getByRole("button", { name: "Change visibility" }));
  await waitFor(() => expect(mutate).toHaveBeenCalledWith(expect.objectContaining({
    meta: expect.objectContaining({
      gqlMutation: operationDocuments.actions.set_file_visibility,
      gqlVariables: { id: "file_1", visibility: value },
    }),
  })));
  expect(await screen.findByText("Visibility changed.")).toBeTruthy();
});

test.each([{ permissions: ["share"] }, { permissions: ["write"], readOnly: true }])(
  "Change visibility is absent without admission or in a read-only form: %j", ({ permissions, readOnly }) => {
    renderVisibility(permissions, readOnly);
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  },
);
