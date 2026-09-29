// @vitest-environment happy-dom
import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DecisionRecordActions } from "./DecisionRecordActions";

const mocks = vi.hoisted(() => ({ decide: vi.fn(), record: {
  id: "decision-1", revision: 7, permissions: ["act"], is_open: true, can_revisit: false,
  form_schema: { type: "object", properties: { action: { type: "string", options: [
    { value: "approve", label: "Approve", verdict: "completed" },
  ] } }, oneOf: [{ type: "object", properties: {
    action: { type: "string", const: "approve" }, reason: { type: "string" },
  } }] },
} }));
vi.mock("@angee/ui", async (original) => ({ ...await original<object>(),
  useRecordChromeContext: () => ({ recordId: "decision-1", dataProviderName: "console", record: mocks.record }),
  useRecordChromeActionOutcome: () => [mocks.decide, {}],
  useActionResultRun: () => (fire: () => Promise<unknown>) => fire(),
}));
const { Provider, clearClients } = createUiTestProviders();
const view = <Provider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
  <ModalsHost><ToastProvider><DecisionRecordActions /></ToastProvider></ModalsHost>
</AppRuntimeProvider></Provider>;
afterEach(() => { cleanup(); clearClients(); vi.clearAllMocks(); mocks.record.permissions = ["act"]; mocks.record.revision = 7; });

test("the frozen action is absent without act permission", () => {
  mocks.record.permissions = [];
  render(view);
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
});

test("server field errors retain the dialog and its opening revision", async () => {
  mocks.decide.mockResolvedValue({ ok: false, message: "Review the form", validationErrors: { reason: ["Add evidence"] } });
  const { rerender } = render(view);
  fireEvent.click(screen.getByRole("button", { name: "Actions" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Approve" }));
  const input = await screen.findByRole("textbox");
  fireEvent.change(input, { target: { value: "Reviewed" } });
  mocks.record.revision = 8;
  rerender(<>{view}</>);
  fireEvent.submit(screen.getByRole("dialog").querySelector("form")!);
  await waitFor(() => expect(mocks.decide).toHaveBeenCalledWith("decision-1", {
    revision: 7, action: "approve", values: { reason: "Reviewed" },
  }));
  expect(await screen.findByText("Add evidence")).toBeTruthy();
  expect(screen.getByRole("dialog")).toBeTruthy();
});
