// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ChipList } from "./chip";

afterEach(cleanup);

test("a chip list renders each item once, removable only when it can report removals", () => {
  const items = [{ id: "rec-1", label: <em>Ada</em>, text: "Ada" }, { id: "draft", label: "draft" }];
  const { rerender } = render(<ChipList items={items} />);
  expect(screen.getByText("Ada").tagName).toBe("EM");
  expect(screen.getByText("draft")).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();

  const onRemove = vi.fn();
  rerender(<ChipList items={items} onRemove={onRemove} />);
  fireEvent.click(screen.getByRole("button", { name: "Remove Ada" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove draft" }));
  expect(onRemove.mock.calls).toEqual([["rec-1"], ["draft"]]);
});

test("a chip wears its item's own colour with readable ink, and ignores a value that is not a colour", () => {
  render(<ChipList items={[
    { id: "light", label: "Salmon", color: "#fa8072" },
    { id: "dark", label: "Navy", color: "#1f3a5f" },
    { id: "plain", label: "Plain", color: "salmon" },
  ]} />);
  expect(screen.getByText("Salmon").style.backgroundColor).toBe("#fa8072");
  expect(screen.getByText("Salmon").style.color).toBe("rgb(17 24 39)");
  expect(screen.getByText("Navy").style.color).toBe("rgb(255 255 255)");
  expect(screen.getByText("Plain").style.backgroundColor).toBe("");
});
