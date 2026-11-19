// @vitest-environment happy-dom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { operationDocuments } from "@angee/gql/console/actions";
import { RecordChromeProvider } from "@angee/ui";
import { RuntimeFixture, storySchema, testDataResource, type GraphQLWireRequest } from "@angee/storybook/testing";

import { ChangeFileVisibilityAction } from "./file-form";

function renderVisibility(permissions: string[], readOnly = false) {
  const requests: GraphQLWireRequest[] = [];
  const schemas = { console: { ...storySchema({
    set_file_visibility: (args) => ({ ok: true, message: "Visibility changed.", id: args.id, validation_errors: null }),
  }, (request) => requests.push(request)).public!, metadata: { angee: { resources: [
    testDataResource("storage.File", { fields: [{
      name: "visibility", kind: "enum", scalar: "String", readable: true, aggregatable: false,
      creatable: false, updatable: false, requiredOnCreate: false,
      values: [{ value: "INHERITED", description: "Inherited" }, { value: "RECORD", description: "Record" }],
    }] }),
  ] } } } };
  render(<RuntimeFixture activeSchema="console" schemas={schemas} operationDocuments={{ console: operationDocuments }}>
    <RecordChromeProvider value={{ resource: "storage.File", canonicalResource: "storage.File", dataProviderName: "console",
      recordId: "file_1", record: { id: "file_1", visibility: "INHERITED", permissions }, formReadOnly: readOnly }}>
      <ChangeFileVisibilityAction />
    </RecordChromeProvider>
  </RuntimeFixture>);
  return requests;
}

test.each([
  { permissions: ["write"], choices: ["Record"], value: "RECORD" },
  { permissions: ["inherit_visibility"], choices: ["Inherited"], value: "INHERITED" },
  { permissions: ["write", "inherit_visibility"], choices: ["Inherited", "Record"], value: "RECORD" },
])("Change visibility offers only the projected choices: $permissions", async ({ permissions, choices, value }) => {
  const requests = renderVisibility(permissions);
  fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Change visibility" }));
  const dialog = await screen.findByRole("dialog", { name: "Change visibility" });
  fireEvent.click(within(dialog).getByRole("combobox", { name: "Audience" }));
  expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual(choices);
  const option = screen.getByRole("option", { name: value === "RECORD" ? "Record" : "Inherited" });
  fireEvent.pointerDown(option, { pointerType: "mouse", button: 0 });
  fireEvent.click(option);
  fireEvent.click(within(dialog).getByRole("button", { name: "Change visibility" }));
  await waitFor(() => expect(requests.find(({ fields }) => fields.includes("set_file_visibility"))?.variables)
    .toEqual({ id: "file_1", visibility: value }));
  expect(await screen.findByText("Visibility changed.")).toBeTruthy();
});

test.each([{ permissions: ["share"] }, { permissions: ["write"], readOnly: true }])(
  "Change visibility is absent without admission or in a read-only form: %j", ({ permissions, readOnly }) => {
    renderVisibility(permissions, readOnly);
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  },
);
