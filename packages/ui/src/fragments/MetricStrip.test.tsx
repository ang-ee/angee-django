// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { InAppLinkProvider } from "../lib/in-app-link";
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

  test("prominent density keeps tone labels in the same metric family", () => {
    const { container } = render(
      <MetricStrip
        density="prominent"
        metrics={[{ label: "Failures", value: 4, tone: "danger" }]}
      />,
    );
    expect(screen.getByText("Failures")).toBeTruthy();
    expect(container.querySelector("dd")?.className).toContain("text-2xl");
  });

  test("large value typography stays independent of compact tile density", () => {
    render(<MetricTile density="compact" label="Revenue" value={24} valueSize="lg" />);
    const value = screen.getByText("24");
    expect(value.className).toContain("mt-1");
    expect(value.className).toContain("text-xl");
    expect(value.className).toContain("font-semibold");
    expect(value.className).toContain("leading-6");
    expect(value.className).toContain("tabular-nums");
  });

  test("a non-navigable tile renders no link", () => {
    render(<MetricTile label="Relations" value={3} />);
    expect(screen.queryByRole("link")).toBeNull();
  });

  test("a tile with href is a link and routes through the provider on a plain click", () => {
    const onNavigate = vi.fn();
    render(
      <InAppLinkProvider navigate={onNavigate}><MetricTile label="Fields" value={12} href="/fields?model=Note" /></InAppLinkProvider>,
    );
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("/fields?model=Note");
    fireEvent.click(link);
    expect(onNavigate).toHaveBeenCalledWith("/fields?model=Note");
  });
});
