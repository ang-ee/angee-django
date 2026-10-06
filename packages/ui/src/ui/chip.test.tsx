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
