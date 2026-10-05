// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, test } from "vitest";
import { Inbox, Open, Multiple, ReadOnly, Closed, Conflict, InvalidAttempt } from "./InboxPage.stories";

beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(cleanup);

test("the inbox card links concerned records and lists proposed field and record actions", async () => {
  render(Open.render());
  expect((await screen.findByRole("link", { name: /Review notes/ })).getAttribute("href")).toBe("/notes/nte_7");
  expect(screen.getByText("Display Name")).toBeTruthy();
  expect(screen.getByText("Proposed name")).toBeTruthy();
  expect(screen.queryByText("archive")).toBeNull();
  expect(await screen.findByRole("radio", { name: /Accept and archive/ })).toBeTruthy();
});

test("records an answer using the shared generated mutation and renders its retained values", async () => {
  render(Inbox.render());
  fireEvent.click(await screen.findByRole("link", { name: "Open Review" }));
  fireEvent.click(await screen.findByRole("radio", { name: /Accept/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull());
  expect(screen.getByText("Chose: Accept and archive")).toBeTruthy();
  expect(screen.queryByRole("radio")).toBeNull();
});

test("a closed card shows its chosen answer without an editing path", async () => {
  render(Closed.render());
  expect(await screen.findByText("Chose: Accept and archive")).toBeTruthy();
  expect(screen.getAllByText("Reviewer").length).toBeGreaterThan(0);
  expect(screen.queryByRole("radio")).toBeNull();
  expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Note" })).toBeNull();
});

test("a reader sees alternatives with no submitting path", async () => {
  render(ReadOnly.render());
  expect(await screen.findByText("Accept and archive")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
});

test("a stale snapshot reports the native conflict", async () => {
  render(Conflict.render());
  fireEvent.click(await screen.findByRole("radio", { name: /Accept/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  expect(await screen.findByText("This decision has changed. Reload the page to review the current question.")).toBeTruthy();
});

test("invalid answers retain their field errors and leave the card answerable", async () => {
  render(InvalidAttempt.render());
  fireEvent.click(await screen.findByRole("radio", { name: /Accept/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  expect(await screen.findByText(/Choose an offered alternative/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
});


test("multiple alternatives use checkboxes and retain both chosen keys", async () => {
  render(Multiple.render());
  fireEvent.click(await screen.findByRole("checkbox", { name: /Accept/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Keep what is on the record" }));
  expect(screen.getByRole("checkbox", { name: /Accept/ }).getAttribute("aria-checked")).toBe("true");
  expect(screen.getByRole("checkbox", { name: "Keep what is on the record" }).getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull());
  expect(screen.getByText("Chose: Accept and archive; Keep what is on the record")).toBeTruthy();
  expect(screen.queryByRole("checkbox")).toBeNull();
});
