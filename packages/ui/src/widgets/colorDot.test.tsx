// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { colorDotWidget } from "./colorDot";

const Dot = colorDotWidget.read;

/** The dot is the `role="img"` mark; assert on the solid-fill class it carries. */
function dotClass(container: HTMLElement): string {
  return container.querySelector('[role="img"]')?.className ?? "";
}

describe("colorDot widget tone", () => {
  afterEach(() => {
    cleanup();
  });

  // The run-state axis the dot was built for, colored from the shared STATUS_TONES
  // vocabulary by value alone. Neutral keeps the muted-grey dot treatment.
  test.each([
    ["RUNNING", "bg-success"],
    ["STOPPED", "bg-fg-muted"],
    ["ERROR", "bg-danger"],
    ["WARNING", "bg-warning"],
  ])("colors %s via the shared run-state vocabulary", (value, bg) => {
    const { container } = render(
      <Dot value={value} field={{ options: [{ value, label: value }] }} />,
    );
    expect(dotClass(container)).toContain(bg);
  });

  test("an explicit tone override wins — a task's blocked reads danger", () => {
    const { container } = render(
      <Dot
        value="BLOCKED"
        field={{
          options: [{ value: "BLOCKED", label: "Blocked" }],
          tone: { BLOCKED: "danger" },
        }}
      />,
    );
    expect(dotClass(container)).toContain("bg-danger");
  });

  test("renders the option label beside the dot", () => {
    const { getByText } = render(
      <Dot value="RUNNING" field={{ options: [{ value: "RUNNING", label: "Running" }] }} />,
    );
    expect(getByText("Running")).toBeTruthy();
  });
});
