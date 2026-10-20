// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { OverflowChipList } from "./OverflowChipList";

afterEach(cleanup);

test("shows the first items as chips, links the ones with a destination, and folds the rest", () => {
  const items = Array.from({ length: 8 }, (_, index) => ({ id: `a${index}`, label: `Addon ${index}`, href: index === 0 ? "/addons/a0" : undefined }));
  render(<OverflowChipList items={items} max={6} />);
  expect(screen.getAllByText(/^Addon \d$/)).toHaveLength(6);
  expect(screen.getByRole("link", { name: "Addon 0" }).getAttribute("href")).toBe("/addons/a0");
  expect(screen.getByText("+2")).toBeTruthy();
  expect(screen.queryByText("Addon 7")).toBeNull();
});

test("renders nothing for an empty list so the cell owner shows its empty mark", () => {
  const { container } = render(<OverflowChipList items={[]} />);
  expect(container.innerHTML).toBe("");
});
