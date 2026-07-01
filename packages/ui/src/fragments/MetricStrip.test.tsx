// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { MetricStrip, MetricTile } from "./MetricStrip";

afterEach(() => cleanup());

describe("MetricStrip", () => {
  test("renders a tile per metric with label and value", () => {
    render(
      <MetricStrip
        metrics={[
          { label: "Fields", value: 12 },
          { label: "Relations", value: 3 },
        ]}
      />,
    );
    expect(screen.getByText("Fields")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.getByText("Relations")).toBeTruthy();
  });

  test("a non-navigable tile renders no link", () => {
    render(<MetricTile label="Relations" value={3} />);
    expect(screen.queryByRole("link")).toBeNull();
  });

  test("a tile with href is a link and routes via onNavigate on a plain click", () => {
    const onNavigate = vi.fn();
    render(
      <MetricTile label="Fields" value={12} href="/fields?model=Note" onNavigate={onNavigate} />,
    );
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("/fields?model=Note");
    fireEvent.click(link);
    expect(onNavigate).toHaveBeenCalledWith("/fields?model=Note");
  });
});
