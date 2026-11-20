// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ModalsHost, RecordChromeProvider, ToastProvider } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";

import { PromoteProjectAction } from "./record-sections";

const mutation = vi.hoisted(() => ({ promote: vi.fn(), options: vi.fn() }));
vi.mock("@angee/ui", async () => ({
  ...await vi.importActual<typeof import("@angee/ui")>("@angee/ui"),
  useRecordChromeActionOutcome: (field: string, options: unknown) => {
    mutation.options(field, options);
    return [mutation.promote, { fetching: false }];
  },
  useActionResultRun: () => (run: () => Promise<unknown>) => run(),
}));

const { Provider, clearClients } = createUiTestProviders();
beforeEach(() => {
  vi.clearAllMocks();
  mutation.promote.mockResolvedValue({ ok: true, message: "Project promoted.", id: "pdt_1" });
});
afterEach(() => { cleanup(); clearClients(); });

function renderPromotion({ record, readOnly = false }: { record: Record<string, unknown> | null; readOnly?: boolean }) {
  render(<Provider><ModalsHost><ToastProvider>
    <RecordChromeProvider value={{ resource: "projects.Project", canonicalResource: "projects.Project", dataProviderName: "console",
      recordId: record ? "prj_1" : "", record, formReadOnly: readOnly }}>
      <PromoteProjectAction />
    </RecordChromeProvider>
  </ToastProvider></ModalsHost></Provider>);
}

test.each(["OPEN", "PAUSED", "DONE", "DROPPED"])("a writable %s project's menu promotes the open record", async (status) => {
  renderPromotion({ record: { id: "prj_1", status, permissions: ["write"] } });
  expect(screen.queryByRole("button", { name: "Promote to product" })).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Promote to product" }));
  await waitFor(() => expect(mutation.promote).toHaveBeenCalledExactlyOnceWith("prj_1"));
  expect(mutation.options).toHaveBeenCalledWith("promote_project_to_product", { invalidateModels: ["portfolio.Product"] });
});

test.each([
  { record: null },
  { record: { id: "prj_1", permissions: [] } },
  { record: { id: "prj_1", permissions: ["write"] }, readOnly: true },
])("promotion is absent without a saved writable record: %j", (options) => {
  renderPromotion(options);
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  expect(screen.queryByRole("menuitem", { name: "Promote to product" })).toBeNull();
  expect(mutation.promote).not.toHaveBeenCalled();
});
