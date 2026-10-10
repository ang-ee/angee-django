// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { Badge } from "./badge";

afterEach(cleanup);

test("ghost fill and dot mark compose the status row without toning its label", () => {
  render(<Badge density="bare" mark="dot" tone="danger" variant="ghost">Blocked</Badge>);

  const badge = screen.getByText("Blocked");
  expect(badge.className).toContain("bg-transparent");
  expect(badge.className).toContain("border-transparent");
  expect(badge.className).toContain("text-inherit");
  expect(badge.className).not.toContain("text-danger-text");
  expect(badge.className).not.toContain("h-tag-h");
  expect(badge.firstElementChild?.className).toContain("bg-danger");
  expect(badge.firstElementChild?.className).toContain("size-2");
});

test("icon mark renders only for feedback tones", () => {
  const { rerender } = render(<Badge mark="icon" tone="warning">Warning</Badge>);
  expect(screen.getByText("Warning").firstElementChild?.className).toContain("text-warning-text");

  rerender(<Badge mark="icon" tone="brand">Branded</Badge>);
  expect(screen.getByText("Branded").firstElementChild).toBeNull();

  rerender(<Badge mark="icon" tone="neutral">Neutral</Badge>);
  expect(screen.getByText("Neutral").firstElementChild).toBeNull();
});
