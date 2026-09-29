// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, expect, test } from "vitest";
import { TriggerStory, type TriggerRequest } from "./TriggersPage.stories";

beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(cleanup);

test("enable and disable dispatch the displayed trigger through native actions", async () => {
  const requests: TriggerRequest[] = [];
  render(<TriggerStory onRequest={(request) => requests.push(request)} />);
  fireEvent.click(await screen.findByRole("button", { name: "Enable trigger" }));
  await screen.findByText("Trigger enabled.");
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

test("a new embedded trigger preserves the workflow default in its native create payload", async () => {
  const requests: TriggerRequest[] = [];
  render(<TriggerStory embedded onRequest={(request) => requests.push(request)} />);
  await screen.findByText("Review admission");
  expect(requests.find(({ query }) => query.includes("trigger("))?.variables.where).toEqual({ _and: [{ workflow: { _eq: "wfl_review" } }] });
  fireEvent.click(screen.getByRole("button", { name: /New/ }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(await within(dialog).findByLabelText("Subject model"), { target: { value: "notes.Note" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
  await waitFor(() => expect(requests.some(({ query }) => query.includes("insert_trigger_one"))).toBe(true));
  expect(requests.find(({ query }) => query.includes("insert_trigger_one"))?.variables.object).toMatchObject({
    workflow: "wfl_review", source: "record_changed", model_label: "notes.Note", condition: {},
  });
});

test("viewer facts make the existing trigger immutable and avoid implementation-choice queries", async () => {
  const requests: TriggerRequest[] = [];
  render(<TriggerStory canEdit={false} onRequest={(request) => requests.push(request)} />);
  await screen.findByRole("heading", { name: "Review admission" });
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
