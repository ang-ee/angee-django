// @vitest-environment happy-dom
import { render } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { StateTag } from "./StateTag";

describe("StateTag", () => {
  // The daemon hands back raw state slugs; the tag humanizes them for display.
  // Asserting on the render's own container keeps each case isolated.
  test.each([
    ["running", "Running"],
    ["git_ops", "Git Ops"],
    ["  up  ", "Up"],
    ["", "Unknown"],
  ])("renders %j as the humanized label %j", (state, label) => {
    const { container } = render(<StateTag state={state} />);
    expect(container.textContent).toContain(label);
  });
});
