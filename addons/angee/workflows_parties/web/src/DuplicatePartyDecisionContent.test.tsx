// @vitest-environment happy-dom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { DuplicatePartyDecisionContent } from "./DuplicatePartyDecisionContent";

afterEach(cleanup);

test("shows both retained candidate names and their shared contact", () => {
  render(<DuplicatePartyDecisionContent basis={{ left: "pty_left", right: "pty_right", left_name: "First party", right_name: "Second party", evidence: "shared@example.test" }} />);
  expect(screen.getByRole("columnheader", { name: "Left party" })).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: "Right party" })).toBeTruthy();
  expect(screen.getByText("First party")).toBeTruthy();
  expect(screen.getByText("Second party")).toBeTruthy();
  expect(screen.getByText("shared@example.test")).toBeTruthy();
});

test("rejects a malformed pair instead of rendering partial retained data", () => {
  render(<DuplicatePartyDecisionContent basis={{ left: "pty_left", right: "pty_right", evidence: "shared@example.test" }} />);
  expect(screen.getByText("Review details unavailable")).toBeTruthy();
  expect(screen.queryByText("shared@example.test")).toBeNull();
});
