// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, expect, test } from "vitest";
import { TriggerStory, type TriggerRequest } from "./TriggersPage.stories";

beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(cleanup);

test("enable and disable dispatch the displayed trigger through native actions", async () => {
  const requests: TriggerRequest[] = [];
  render(<TriggerStory onRequest={(request) => requests.push(request)} />);
  const enabled = await screen.findByRole("checkbox", { name: "Enabled" });
  expect(enabled.getAttribute("aria-checked")).toBe("false");
  expect(enabled.getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(await screen.findByRole("button", { name: "Enable trigger" }));
  await screen.findByText("Trigger enabled.");
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "Enabled" }).getAttribute("aria-checked")).toBe("true"));
  expect(requests.find(({ query }) => query.includes("enable_workflow_trigger("))?.variables).toEqual({ id: "wft_review" });
  fireEvent.click((await screen.findAllByRole("button", { name: "Actions" })).at(-1)!);
  fireEvent.click(await screen.findByRole("menuitem", { name: "Disable trigger" }));
  await screen.findByText("Trigger disabled.");
  expect(requests.find(({ query }) => query.includes("disable_workflow_trigger("))?.variables).toEqual({ id: "wft_review" });
});

test("the ledger tab uses native trigger filtering and retains record and run references", async () => {
  const requests: TriggerRequest[] = [];
  render(<TriggerStory onRequest={(request) => requests.push(request)} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Events" }));
  await screen.findByText("Review notes");
  expect(requests.find(({ query }) => query.includes("triggerevent("))?.variables.where).toEqual({ _and: [{ trigger: { _eq: "wft_review" } }] });
  expect((await screen.findByRole("link", { name: "wfr_review" })).getAttribute("href")).toBe("/workflows/runs/wfr_review");
});

test("the principal grants tab lists direct tuples and revokes through the trigger action", async () => {
  const requests: TriggerRequest[] = [];
  render(<TriggerStory onRequest={(request) => requests.push(request)} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Principal grants" }));
  expect((await screen.findAllByText("notes/role:trigger_editor")).length).toBeGreaterThan(0);
  expect(requests.some(({ query }) => query.includes("TriggerGrants"))).toBe(true);
  fireEvent.click((await screen.findAllByRole("button", { name: "Actions" })).at(-1)!);
  fireEvent.click(await screen.findByRole("menuitem", { name: "Revoke grant" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Revoke grant" }));
  await waitFor(() => expect(requests.find(({ query }) => query.includes("revoke_workflow_trigger_grant("))?.variables).toEqual({
    id: "wft_review", resourceType: "notes/role", resourceId: "trigger_editor", relation: "member",
  }));
});

test("a new embedded trigger preserves the workflow default in its native create payload", async () => {
  const requests: TriggerRequest[] = [];
  render(<TriggerStory embedded onRequest={(request) => requests.push(request)} />);
  await screen.findByText("Review admission");
  expect(screen.getByText("Record changed")).toBeTruthy();
  expect(screen.queryByText("RECORD_CHANGED")).toBeNull();
  expect(screen.getByRole("checkbox", { name: "Enabled" }).getAttribute("aria-checked")).toBe("false");
  expect(requests.find(({ query }) => query.includes("trigger("))?.variables.where).toEqual({ _and: [{ workflow: { _eq: "wfl_review" } }] });
  fireEvent.click(screen.getByRole("button", { name: /New/ }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(await within(dialog).findByLabelText("Subject model"), { target: { value: "notes.note" } });
  expect(await within(dialog).findByRole("combobox", { name: "Filter field" })).toBeTruthy();
  expect(within(dialog).queryByText("Select a record model to edit its condition.")).toBeNull();
  fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
  await waitFor(() => expect(requests.some(({ query }) => query.includes("insert_trigger_one"))).toBe(true));
  expect(requests.find(({ query }) => query.includes("insert_trigger_one"))?.variables.object).toMatchObject({
    workflow: "wfl_review", source: "record_changed", model_label: "notes.note", condition: {},
  });
});

test("viewer facts make the existing trigger immutable and avoid implementation-choice queries", async () => {
  const requests: TriggerRequest[] = [];
  render(<TriggerStory canEdit={false} onRequest={(request) => requests.push(request)} />);
  await screen.findByRole("heading", { name: "Review admission" });
  expect(await screen.findByText(/Status.*in_review/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Enable trigger" })).toBeNull();
  expect(screen.queryByRole("menuitem", { name: "Disable trigger" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  expect(screen.queryByLabelText("Filter value")).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Subject model" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Source" })).toBeNull();
  const save = screen.queryByRole("button", { name: "Save" });
  expect(save == null || save.hasAttribute("disabled")).toBe(true);
  expect(requests.some(({ query }) => query.includes("impl_choices"))).toBe(false);
});

test("a stored generic trigger renders its retained condition through the resolved model", async () => {
  const requests: TriggerRequest[] = [];
  render(<TriggerStory onRequest={(request) => requests.push(request)} />);
  expect(await screen.findByText(/Status.*in_review/)).toBeTruthy();
  expect(screen.queryByText("Select a record model to edit its condition.")).toBeNull();
  const detail = requests.find(({ query }) => query.includes("trigger_by_pk"));
  expect(detail?.query).toContain("source_model");
  fireEvent.click(screen.getByRole("button", { name: "Remove rule" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(requests.find(({ query }) => query.includes("update_trigger_by_pk"))?.variables._set).toMatchObject({ condition: {} }));
});

test("a fixed source supplies its model to the new condition editor", async () => {
  const requests: TriggerRequest[] = [];
  render(<TriggerStory embedded onRequest={(request) => requests.push(request)} />);
  fireEvent.click(await screen.findByRole("button", { name: /New/ }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("combobox", { name: "Source" }));
  const source = await screen.findByRole("option", { name: "Message ingested" });
  fireEvent.pointerDown(source, { pointerType: "mouse", button: 0 });
  fireEvent.click(source);
  await within(dialog).findByLabelText("Filter value");
  expect(within(dialog).getByRole("combobox", { name: "Filter field" }).textContent).toContain("Subject");
  fireEvent.change(within(dialog).getByLabelText("Filter value"), { target: { value: "Review" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Add" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
  await waitFor(() => expect(requests.find(({ query }) => query.includes("insert_trigger_one"))?.variables.object).toMatchObject({
    source: "message_ingested", model_label: "", condition: { subject: { _eq: "Review" } },
  }));
});

test("generic sources beyond record_changed can author the model and stored condition", async () => {
  const requests: TriggerRequest[] = [];
  render(<TriggerStory embedded onRequest={(request) => requests.push(request)} />);
  fireEvent.click(await screen.findByRole("button", { name: /New/ }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("combobox", { name: "Source" }));
  const source = await screen.findByRole("option", { name: "Custom changed" });
  fireEvent.pointerDown(source, { pointerType: "mouse", button: 0 });
  fireEvent.click(source);
  await waitFor(() => expect(within(dialog).getByRole("combobox", { name: "Source" }).textContent).toContain("Custom changed"));
  fireEvent.change(within(dialog).getByLabelText("Subject model"), { target: { value: "notes.Note" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
  await waitFor(() => expect(requests.some(({ query }) => query.includes("insert_trigger_one"))).toBe(true));
  expect(requests.find(({ query }) => query.includes("insert_trigger_one"))?.variables.object).toMatchObject({
    workflow: "wfl_review", source: "custom_changed", model_label: "notes.Note", condition: {},
  });
});
