// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { statusbarWidget } from "./statusbar";

describe("statusbar widget", () => {
  afterEach(() => {
    cleanup();
  });

  const Statusbar = statusbarWidget.edit;

  test("renders the current step with solid button contrast", () => {
    render(
      <Statusbar
        value="active"
        field={{
          options: [
            { value: "draft", label: "Draft" },
            { value: "active", label: "Active" },
            { value: "paused", label: "Paused" },
          ],
        }}
        onChange={() => undefined}
      />,
    );

    const active = screen.getByText("Active");
    expect(active.className).toContain("bg-brand");
    expect(active.className).toContain("text-on-brand");
    expect(active.closest("button")?.className).toContain("-ml-2.5");
    expect(active.closest("button")?.className).toContain("p-px");
  });
});
