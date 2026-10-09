// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { Badge } from "./badge";

afterEach(cleanup);

test("text fill and dot mark compose the status row without tag geometry", () => {
  render(<Badge density="bare" mark="dot" tone="danger" variant="text">Blocked</Badge>);

  const badge = screen.getByText("Blocked");
  expect(badge.className).toContain("bg-transparent");
  expect(badge.className).toContain("border-transparent");
  expect(badge.className).toContain("text-danger-text");
  expect(badge.className).not.toContain("h-tag-h");
  expect(badge.firstElementChild?.className).toContain("bg-danger");
  expect(badge.firstElementChild?.className).toContain("size-1.5");
});

test("icon mark narrows feedback tones and renders muted for other palettes", () => {
  const { rerender } = render(<Badge mark="icon" tone="warning">Warning</Badge>);
  expect(screen.getByText("Warning").firstElementChild?.className).toContain("text-warning-text");

  rerender(<Badge mark="icon" tone="brand">Branded</Badge>);
  expect(screen.getByText("Branded").firstElementChild?.className).toContain("text-fg-muted");
});
