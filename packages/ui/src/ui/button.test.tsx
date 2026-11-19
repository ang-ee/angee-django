// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Button } from "./button";

test("a disabled button reads as disabled whether natively disabled or focusable with a reason", () => {
  render(<><Button disabled>Save</Button><Button disabledReason="Nothing changed yet.">Publish</Button></>);
  const focusable = screen.getByRole("button", { name: "Publish" });
  expect(focusable.hasAttribute("disabled")).toBe(false);
  expect(focusable.getAttribute("aria-disabled")).toBe("true");
  // Both carry the attribute Base UI sets in either mode, and the dimming keys on it.
  for (const button of [screen.getByRole("button", { name: "Save" }), focusable]) {
    expect(button.hasAttribute("data-disabled")).toBe(true);
    expect(button.className).toContain("data-[disabled]:opacity-60");
    expect(button.className).toContain("data-[disabled]:cursor-not-allowed");
  }
});

test("a verb given a reason keeps its element and focus as the reason comes and goes", () => {
  const { rerender } = render(<Button disabledReason={undefined}>Merge…</Button>);
  const verb = screen.getByRole("button", { name: "Merge…" });
  verb.focus();
  rerender(<Button disabledReason="Select two people to merge them.">Merge…</Button>);
  expect(screen.getByRole("button", { name: "Merge…" })).toBe(verb);
  expect(document.activeElement).toBe(verb);
  expect(verb.getAttribute("aria-disabled")).toBe("true");
  rerender(<Button disabledReason={null}>Merge…</Button>);
  expect(screen.getByRole("button", { name: "Merge…" })).toBe(verb);
  expect(document.activeElement).toBe(verb);
  expect(verb.getAttribute("aria-disabled") ?? "false").toBe("false");
});

test("a link rendered through a button with a reason has no destination to open", () => {
  render(<>
    <Button asChild disabledReason="The report is still running."><a href="/reports/7">Open report</a></Button>
    <Button asChild disabledReason=""><a href="/reports/8">Open archive</a></Button>
  </>);
  expect(screen.getByText("Open report").closest("a")!.hasAttribute("href")).toBe(false);
  expect(screen.getByText("Open archive").closest("a")!.getAttribute("href")).toBe("/reports/8");
});
