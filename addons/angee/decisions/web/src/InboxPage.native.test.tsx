// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, expect, test } from "vitest";
import { Inbox, Open, Multiple, ReadOnly, Closed, Conflict, InvalidAttempt } from "./InboxPage.stories";

beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(cleanup);

test("the inbox card links concerned records and lists proposed field and record actions", async () => {
  render(Open.render());
  const links = await screen.findAllByRole("link", { name: "Review notes" });
  expect(links.every((link) => link.getAttribute("href") === "/notes/nte_7")).toBe(true);
  expect(screen.getByText("display_name")).toBeTruthy();
  expect(screen.getByText('"Proposed name"')).toBeTruthy();
  expect(screen.getByText("archive")).toBeTruthy();
  expect(await screen.findByRole("radio", { name: "Accept" })).toBeTruthy();
});

test("records an answer using the shared generated mutation and renders its retained values", async () => {
  render(Inbox.render());
  fireEvent.click(await screen.findByRole("link", { name: "Open Review" }));
  fireEvent.click(await screen.findByRole("radio", { name: "Accept" }));
  fireEvent.click(screen.getByRole("button", { name: "Decide" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Decide" })).toBeNull());
  expect((screen.getByRole("radio", { name: "Accept" }) as HTMLInputElement).checked).toBe(true);
  expect(within(screen.getByRole("article")).getByText("Answered at")).toBeTruthy();
});

test("a closed card shows its chosen answer without an editing path", async () => {
  render(Closed.render());
  const choice = await screen.findByRole("radio", { name: "Accept" });
  expect((choice as HTMLInputElement).checked).toBe(true);
  expect((choice.closest("fieldset") as HTMLFieldSetElement).disabled).toBe(true);
  const card = within(screen.getByRole("article"));
  expect(card.getByText("Reviewer")).toBeTruthy();
  expect(card.getByText("Answered at")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Decide" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Note" })).toBeNull();
});

test("a reader sees alternatives with no submitting path", async () => {
  render(ReadOnly.render());
  expect(await screen.findByText("Accept")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Decide" })).toBeNull();
});

test("a stale snapshot reports the native conflict", async () => {
  render(Conflict.render());
  fireEvent.click(await screen.findByRole("radio", { name: "Accept" }));
  fireEvent.click(screen.getByRole("button", { name: "Decide" }));
  expect(await screen.findByText("This decision has changed. Reload the page to review the current question.")).toBeTruthy();
});

test("invalid answers retain their field errors and leave the card answerable", async () => {
  render(InvalidAttempt.render());
  fireEvent.click(await screen.findByRole("radio", { name: "Accept" }));
  fireEvent.click(screen.getByRole("button", { name: "Decide" }));
  expect(await screen.findByText(/Choose an offered alternative/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Decide" })).toBeTruthy();
});


test("multiple alternatives use checkboxes and retain both chosen keys", async () => {
  render(Multiple.render());
  fireEvent.click(await screen.findByRole("checkbox", { name: "Accept" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Keep what is on the record" }));
  fireEvent.click(screen.getByRole("button", { name: "Decide" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Decide" })).toBeNull());
  expect((screen.getByRole("checkbox", { name: "Accept" }) as HTMLInputElement).checked).toBe(true);
  expect((screen.getByRole("checkbox", { name: "Keep what is on the record" }) as HTMLInputElement).checked).toBe(true);
});
