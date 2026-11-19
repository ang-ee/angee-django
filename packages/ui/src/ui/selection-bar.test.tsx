// @vitest-environment happy-dom

import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { SelectionBar } from "./selection-bar";

test("a verb that cannot run shows its reason in the bar and is described by it, staying focusable", () => {
  const onClick = vi.fn();
  render(
    <SelectionBar
      count={1}
      actions={<SelectionBar.Action disabledReason="Select two records to compare them." onClick={onClick}>
        Merge…
      </SelectionBar.Action>}
    />,
  );

  const verb = screen.getByRole("button", { name: "Merge…" });
  // The reason is visible text in the bar, not a tooltip.
  const reason = screen.getByText("Select two records to compare them.");
  expect(verb.getAttribute("aria-describedby")).toBe(reason.id);
  expect(verb.getAttribute("aria-disabled")).toBe("true");
  verb.focus();
  expect(document.activeElement).toBe(verb);
  fireEvent.click(verb);
  expect(onClick).not.toHaveBeenCalled();
});

test("a runnable verb carries no reason and keeps its own description", () => {
  render(
    <>
      <p id="hint">Opens the comparison.</p>
      <SelectionBar count={2} actions={<SelectionBar.Action aria-describedby="hint">Merge…</SelectionBar.Action>} />
    </>,
  );

  const verb = screen.getByRole("button", { name: "Merge…" });
  expect(verb.getAttribute("aria-describedby")).toBe("hint");
  expect(verb.hasAttribute("aria-disabled")).toBe(false);
  expect(screen.queryByText("Select two records to compare them.")).toBeNull();
});
