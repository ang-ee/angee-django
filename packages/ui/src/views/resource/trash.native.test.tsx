// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { testDataResource } from "@angee/metadata/testing";
import { ModalsHost, ToastProvider } from "../../feedback";
import { createUiTestProviders } from "../../testing";
import { RestoreRecord } from "./documents";
import { RemovedRecords } from "./trash";

const resource = testDataResource("test.Note", { resourceType: "test/note", fields: [{
  name: "is_trashed", kind: "scalar", scalar: "Boolean", readable: true, aggregatable: false,
  creatable: false, updatable: false, requiredOnCreate: false, trashable: true,
}] });
const { Provider, clearClients } = createUiTestProviders({ resources: [resource] });
afterEach(() => { cleanup(); clearClients(); });

test("removed records beyond the first page restore and return to the remaining page", async () => {
  let rows = ["One", "Two", "Three"].map((title, index) => ({
    id: `note_${index}`, title, is_trashed: true, permissions: ["delete"],
  }));
  render(<Provider dataProvider={{
    getList: async ({ pagination }) => {
      const start = ((pagination?.currentPage ?? 1) - 1) * 2;
      return { data: rows.slice(start, start + 2), total: rows.length };
    },
    custom: async ({ meta }) => {
      expect(meta?.gqlMutation).toBe(RestoreRecord);
      rows = rows.filter((row) => row.id !== meta?.gqlVariables?.target_id);
      return { data: { restore_record: { ok: true, message: "Restored." } } };
    },
  }}><ToastProvider><ModalsHost>
    <RemovedRecords resource={resource} pageSize={2} fields={["title"]} label={(row) => String(row.title)} />
  </ModalsHost></ToastProvider></Provider>);

  fireEvent.click(await screen.findByRole("button", { name: "Removed (3)" }));
  expect(await screen.findByText("One")).toBeTruthy();
  expect(screen.queryByText("Three")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByText("Three")).toBeTruthy();
  expect(screen.queryByText("One")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Restore" }));
  await screen.findByRole("button", { name: "Removed (2)" });
  await waitFor(() => expect(screen.queryByText("Three")).toBeNull());
  expect(await screen.findByText("One")).toBeTruthy();
  expect(screen.getByText("Two")).toBeTruthy();
});
