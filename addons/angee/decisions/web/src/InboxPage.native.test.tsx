// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, test } from "vitest";

import { Conflict, Inbox, InvalidAttempt, Open, PendingWithoutFacts, ReadOnly, Settled, SettledWithoutFacts, SiblingClosed } from "./InboxPage.stories";

beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(cleanup);

async function chooseAction(label: string) {
  fireEvent.click(await screen.findByRole("combobox", { name: "Action" }));
  const option = await screen.findByRole("option", { name: label });
  fireEvent.pointerDown(option, { pointerType: "mouse" });
  fireEvent.click(option);
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Action" }).textContent).toContain(label));
}

describe("decision stories with native router, queries, and generated mutations", () => {
  test("pending records link the subject, promote Decide, and hide settlement facts", async () => {
    render(Open.render());
    expect(await screen.findByRole("button", { name: "Decide" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
    expect((await screen.findByRole("link", { name: "Review notes" })).getAttribute("href")).toBe("/notes/nte_7");
    for (const label of ["Subject model", "notes.Note", "nte_7", "Expires", "Resolved by", "Resolved at", "Closed reason"]) {
      expect(screen.queryByText(label)).toBeNull();
    }
  });

  test("context loads seats and links their assignees", async () => {
    render(Open.render());
    fireEvent.click(await screen.findByRole("tab", { name: "Context" }));
    expect((await screen.findByRole("link", { name: "Reviewer" })).getAttribute("href"))
      .toBe("/iam/users/usr_reviewer");
  });

  test("settled records omit empty facts and retain populated settlement facts", async () => {
    const { unmount } = render(SettledWithoutFacts.render());
    await screen.findByText(/Already reviewed/);
    for (const label of ["Expires", "Resolved by", "Resolved at", "Closed reason"]) expect(screen.queryByText(label)).toBeNull();
    unmount();
    render(Settled.render());
    await screen.findByText(/Already reviewed/);
    for (const label of ["Expires", "Resolved by", "Resolved at", "Closed reason"]) expect(screen.getByText(label)).toBeTruthy();
  });

  test("pending decisions show their core facts even without a requester or expiry", async () => {
    render(PendingWithoutFacts.render());
    await screen.findByRole("heading", { name: "Review" });
    expect(screen.queryByText("Requester")).toBeNull();
    expect(screen.getByRole("heading", { name: "Decision" })).toBeTruthy();
    expect(screen.getByText("Kind")).toBeTruthy();
    expect(screen.getByText("Status")).toBeTruthy();
    expect(screen.getByText("Assignees")).toBeTruthy();
  });

  test("a sibling-settled seat shows its closed reason as its status", async () => {
    render(SiblingClosed.render());
    expect((await screen.findAllByText("Sibling settled")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("listitem", { name: "Pending" })).toBeNull();
  });

  test("opens an inbox seat, validates its action branch, and records the answer through the real transport", async () => {
    render(Inbox.render());
    fireEvent.click(await screen.findByRole("link", { name: "Open Review" }));
    expect(document.querySelectorAll("main")).toHaveLength(1);
    expect(await screen.findByRole("heading", { name: "Review" })).toBeTruthy();
    // Actors answer inline on the decision page; there is no Decide dialog.
    expect((await screen.findByRole("textbox", { name: "Note" }) as HTMLInputElement).value).toBe("Read");
    expect(screen.queryByRole("textbox", { name: "Reference" })).toBeNull();
    await chooseAction("Reject");
    fireEvent.change(screen.getByRole("textbox", { name: /Reason/ }), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    expect(await screen.findByText("Enter at least 3 characters.")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: /Reason/ }).getAttribute("aria-invalid")).toBe("true");
    fireEvent.change(screen.getByRole("textbox", { name: /Reason/ }), { target: { value: "Needs another review" } });
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Decide" })).toBeNull());
    expect((await screen.findAllByText("Rejected")).length).toBeGreaterThan(0);
    expect(await screen.findByText(/Needs another review/)).toBeTruthy();
    expect(screen.getByText("Resolved at")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: /Reason/ })).toBeNull();
  });

  test("a conflicting snapshot stops the inline answer and asks for a reload", async () => {
    render(Conflict.render());
    fireEvent.click(await screen.findByRole("button", { name: "Decide" }));
    expect(await screen.findByText("This decision has changed. Reload the page to review the current question.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Decide" }).hasAttribute("disabled")).toBe(true);
  });

  test("loads a retained settlement without an editing or submitting path", async () => {
    render(Settled.render());
    expect(await screen.findByText(/Already reviewed/)).toBeTruthy();
    expect(screen.getAllByText("Completed").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Decide" })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Action" })).toBeNull();
    expect(screen.getByText("Note")).toBeTruthy();
    expect(screen.getByText("Reference")).toBeTruthy();
  });

  test("retains the answer draft and refreshes the revision after a rejected attempt", async () => {
    render(InvalidAttempt.render());
    fireEvent.change(await screen.findByRole("textbox", { name: "Note" }), { target: { value: "Draft answer" } });
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    expect(await screen.findByText("Add the missing detail.")).toBeTruthy();
    expect((screen.getByRole("textbox", { name: "Note" }) as HTMLInputElement).value).toBe("Draft answer");
    fireEvent.change(screen.getByRole("textbox", { name: "Note" }), { target: { value: "Corrected answer" } });
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    expect(await screen.findByText(/Corrected answer/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Decide" })).toBeNull();
  });

  test("hides the deciding action when the backend denies acting on an open seat", async () => {
    render(ReadOnly.render());
    expect(await screen.findByRole("heading", { name: "Review" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Note" })).toBeNull();
  });
});
