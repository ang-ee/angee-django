// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, test } from "vitest";

import { Conflict, Inbox, Settled } from "./DecisionPage.stories";

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
  test("opens an inbox seat, validates its action branch, and records the answer through the real transport", async () => {
    render(Inbox.render());
    fireEvent.click(await screen.findByRole("link", { name: /review/ }));
    expect((await screen.findByRole("textbox", { name: "Note" }) as HTMLInputElement).value).toBe("Read");
    expect(await screen.findByText("Review notes")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Review", level: 1 })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Reference" })).toBeNull();
    await chooseAction("Reject");
    fireEvent.change(screen.getByRole("textbox", { name: /Reason/ }), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    expect(await screen.findByText("Enter at least 3 characters.")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: /Reason/ }).getAttribute("aria-invalid")).toBe("true");
    fireEvent.change(screen.getByRole("textbox", { name: /Reason/ }), { target: { value: "Needs another review" } });
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Decide" })).toBeNull());
    expect(screen.getAllByText("Rejected").length).toBeGreaterThan(0);
    expect(screen.getByText("Needs another review")).toBeTruthy();
    expect(screen.getByText("Resolved at")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: /Reason/ })).toBeNull();
  });

  test("refreshes a conflicting snapshot before a second generated deciding mutation succeeds", async () => {
    render(Conflict.render());
    fireEvent.click(await screen.findByRole("button", { name: "Decide" }));
    expect(await screen.findByText("This decision has changed. Reload before submitting again.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Decide" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Decide" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Decide" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Decide" })).toBeNull());
    expect(screen.getAllByText("Completed").length).toBeGreaterThan(0);
    expect(screen.getByText("Resolved at")).toBeTruthy();
  });

  test("loads a retained settlement without an editing or submitting path", async () => {
    render(Settled.render());
    expect(await screen.findByText("Already reviewed")).toBeTruthy();
    expect(screen.getAllByText("Completed").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Decide" })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Action" })).toBeNull();
  });
});
