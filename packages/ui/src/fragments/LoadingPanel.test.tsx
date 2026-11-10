// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { LoadingPanel, type LoadingPanelProps } from "./LoadingPanel";

const SHAPES: readonly LoadingPanelProps[] = [
  {},
  { density: "inline" },
  { shape: "page" },
];

describe("LoadingPanel", () => {
  test.each(SHAPES)("announces its label once and renders no visible loading copy: %o", (props) => {
    const { container } = render(<LoadingPanel {...props} loadingLabel="Loading folder" />);

    const status = screen.getByRole("status");
    // The label lives only in the status's screen-reader text; nothing else carries copy.
    expect(screen.getAllByText("Loading folder")).toHaveLength(1);
    expect(screen.getByText("Loading folder").className).toContain("sr-only");
    expect(screen.queryAllByText(/\S/, { ignore: ".sr-only, script, style" })).toEqual([]);
    expect(status.textContent).toBe("Loading folder");
    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(status.querySelectorAll("[aria-hidden='true']").length).toBeGreaterThan(0);
  });

  test("falls back to the shared default label", () => {
    render(<LoadingPanel density="inline" />);

    expect(screen.getByRole("status").textContent).toBe("Loading…");
  });

  test("density decides the status skeleton's line count and height", () => {
    const { rerender } = render(<LoadingPanel density="inline" />);
    const inlineLines = screen.getByRole("status").querySelectorAll(".rounded-full");
    expect(inlineLines).toHaveLength(2);
    expect([...inlineLines].every((line) => line.className.includes("h-3"))).toBe(true);

    rerender(<LoadingPanel />);
    const pageLines = screen.getByRole("status").querySelectorAll(".rounded-full");
    expect(pageLines).toHaveLength(3);
    expect([...pageLines].every((line) => line.className.includes("h-4"))).toBe(true);
  });
});
