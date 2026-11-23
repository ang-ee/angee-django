// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { Checkbox } from "./checkbox";

afterEach(cleanup);

/** The text of the nodes an element's aria-describedby names. */
const description = (element: HTMLElement) => (element.getAttribute("aria-describedby") ?? "").split(" ")
  .filter(Boolean).map((id) => document.getElementById(id)?.textContent ?? "").join(" ");

test("a box with a reason stays in the tab order, says why and ignores activation; a disabled one leaves it", () => {
  const change = vi.fn();
  render(<>
    <Checkbox aria-label="Plain" disabled />
    <Checkbox aria-label="Explained" checked disabledReason="Expand the group first." onCheckedChange={change} />
  </>);
  expect(screen.getByRole("checkbox", { name: "Plain" }).tabIndex).toBe(-1);
  const box = screen.getByRole("checkbox", { name: "Explained" });
  expect(box.getAttribute("aria-disabled")).toBe("true");
  expect(box.hasAttribute("data-disabled")).toBe(true);
  expect(box.tabIndex).toBe(0);
  expect(description(box)).toBe("Expand the group first.");
  fireEvent.keyDown(box, { key: " " });
  fireEvent.keyUp(box, { key: " " });
  fireEvent.keyDown(box, { key: "Enter" });
  fireEvent.click(box);
  expect(change).not.toHaveBeenCalled();
  expect(box.getAttribute("aria-checked")).toBe("true");
});

/** Dispatch a real keydown on the box: whether it ended prevented, and whether it already was on reaching the box. */
function keyDown(box: HTMLElement, key: string) {
  let atBox = false;
  const seen = (event: Event) => { atBox = event.defaultPrevented; };
  box.addEventListener("keydown", seen);
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
  box.dispatchEvent(event);
  box.removeEventListener("keydown", seen);
  return { prevented: event.defaultPrevented, atBox };
}

test("a box disabled by a reason swallows Space, which changes nothing; an enabled box toggles as before", () => {
  const change = vi.fn();
  render(<>
    <Checkbox aria-label="Explained" disabledReason="Expand the group first." onCheckedChange={change} />
    <Checkbox aria-label="Plain" onCheckedChange={change} />
  </>);
  const box = screen.getByRole("checkbox", { name: "Explained" });
  box.focus();
  // Space would scroll the page from a focused box that cannot change.
  expect(keyDown(box, " ").prevented).toBe(true);
  fireEvent.keyUp(box, { key: " " });
  expect(change).not.toHaveBeenCalled();
  expect(box.getAttribute("aria-checked")).toBe("false");
  // Nothing prevents an enabled box's Space before Base UI's own handler, which toggles it.
  const plain = screen.getByRole("checkbox", { name: "Plain" });
  plain.focus();
  expect(keyDown(plain, " ").atBox).toBe(false);
  fireEvent.keyUp(plain, { key: " " });
  expect(change).toHaveBeenCalledWith(true, expect.anything());
  expect(plain.getAttribute("aria-checked")).toBe("true");
});

test("a box given a reason keeps its element and focus as the reason comes and goes", () => {
  const { rerender } = render(<Checkbox aria-label="Group" disabledReason={null} />);
  const box = screen.getByRole("checkbox", { name: "Group" });
  box.focus();
  rerender(<Checkbox aria-label="Group" disabledReason="Expand the group first." />);
  expect(screen.getByRole("checkbox", { name: "Group" })).toBe(box);
  expect(document.activeElement).toBe(box);
  expect(box.getAttribute("aria-disabled")).toBe("true");
  rerender(<Checkbox aria-label="Group" disabledReason="" />);
  expect(screen.getByRole("checkbox", { name: "Group" })).toBe(box);
  expect(document.activeElement).toBe(box);
  expect(box.getAttribute("aria-disabled") ?? "false").toBe("false");
  expect(description(box)).toBe("");
});
