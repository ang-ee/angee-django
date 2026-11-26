// @vitest-environment happy-dom

import { render, screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { ErrorBanner } from "./ErrorBanner";

describe("ErrorBanner", () => {
  test("announces an appearing error summary", () => {
    render(<ErrorBanner description="Resolution failed" />);

    expect(screen.getByRole("alert").textContent).toContain(
      "Resolution failed",
    );
  });

  test("several messages are a list, one item each; one is the body; none renders nothing", () => {
    const view = render(<ErrorBanner title="Save failed" messages={["The key is taken.", "A template names its layout."]} />);
    const alert = screen.getByRole("alert");
    expect(within(alert).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["The key is taken.", "A template names its layout."]);
    // The accessible text keeps the messages apart: no run-on sentence across two of them.
    expect(alert.textContent).not.toContain("taken. A template");

    view.rerender(<ErrorBanner title="Save failed" messages={["The key is taken."]} />);
    expect(within(screen.getByRole("alert")).queryAllByRole("listitem")).toEqual([]);
    expect(screen.getByRole("alert").textContent).toContain("The key is taken.");

    view.rerender(<ErrorBanner title="Save failed" messages={[]} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
