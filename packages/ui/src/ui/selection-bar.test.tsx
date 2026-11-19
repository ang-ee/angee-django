// @vitest-environment happy-dom

import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { SelectionBar } from "./selection-bar";

const REASON = "Select two records to compare them.";

/** The verb's accessible description: the text of the nodes its aria-describedby names. */
function description(element: HTMLElement): string {
  return (element.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? "").join(" ");
}

test("a verb that cannot run carries its reason as its tooltip and description, focusable and inert", () => {
  const onClick = vi.fn();
  render(
    <SelectionBar
      count={1}
      actions={<SelectionBar.Action disabledReason={REASON} onClick={onClick}>Merge…</SelectionBar.Action>}
    />,
  );

  const verb = screen.getByRole("button", { name: "Merge…" });
  expect(description(verb)).toBe(REASON);
  expect(verb.getAttribute("aria-disabled")).toBe("true");
  // The bar renders no reason text: the description node is hidden, the tooltip shows it on demand.
  expect(screen.getByText(REASON).hidden).toBe(true);
  verb.focus();
  expect(document.activeElement).toBe(verb);
  fireEvent.click(verb);
  for (const key of ["Enter", " "]) {
    fireEvent.keyDown(verb, { key });
    fireEvent.keyUp(verb, { key });
  }
  expect(onClick).not.toHaveBeenCalled();
});

test("a reason joins the verb's own description, and each verb names its own reason", () => {
  render(
    <>
      <p id="hint">Opens the comparison.</p>
      <SelectionBar count={1} actions={<>
        <SelectionBar.Action aria-describedby="hint" disabledReason={REASON}>Merge…</SelectionBar.Action>
        <SelectionBar.Action disabledReason="Archived records cannot be shared.">Share…</SelectionBar.Action>
      </>} />
    </>,
  );

  const merge = screen.getByRole("button", { name: "Merge…" });
  const share = screen.getByRole("button", { name: "Share…" });
  expect(description(merge)).toBe(`Opens the comparison. ${REASON}`);
  expect(description(share)).toBe("Archived records cannot be shared.");
  expect(merge.getAttribute("aria-describedby")).not.toBe(share.getAttribute("aria-describedby"));
});

test("a runnable verb, or one with an empty reason, has no reason", () => {
  const onClick = vi.fn();
  render(
    <SelectionBar count={2} actions={<>
      <SelectionBar.Action onClick={onClick}>Merge…</SelectionBar.Action>
      <SelectionBar.Action disabledReason="">Share…</SelectionBar.Action>
    </>} />,
  );

  for (const name of ["Merge…", "Share…"]) {
    const verb = screen.getByRole("button", { name });
    expect(verb.hasAttribute("aria-describedby")).toBe(false);
    expect(verb.hasAttribute("aria-disabled")).toBe(false);
    expect((verb as HTMLButtonElement).disabled).toBe(false);
  }
  fireEvent.click(screen.getByRole("button", { name: "Merge…" }));
  expect(onClick).toHaveBeenCalledTimes(1);
});
