// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { NumberFlowElement } from "@number-flow/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { InAppLinkProvider } from "../lib/in-app-link";
import { MetricStrip, MetricTile } from "./MetricStrip";

afterEach(() => cleanup());

describe("MetricStrip", () => {
  test("renders a tile per metric with label and value", () => {
    const { container } = render(
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
    expect(container.querySelectorAll("dt")).toHaveLength(2);
    expect(container.querySelectorAll("dd")).toHaveLength(2);
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
    expect(value.className).toContain("text-xl");
    expect(value.className).toContain("font-semibold");
    expect(value.className).toContain("leading-6");
    expect(value.className).toContain("tabular-nums");
    expect(value.closest("dd")?.parentElement?.className).toContain("gap-y-1");
  });

  test("formats a typed numeric value and forwards animation to NumberFlow", () => {
    const expected = new Intl.NumberFormat(undefined, {
      maximumFractionDigits: 2,
    }).format(1234.56);
    const { container } = render(
      <MetricTile
        animate
        format={{ maximumFractionDigits: 2 }}
        label="Revenue"
        numericValue={1234.56}
        suffix=" total"
        value="Fallback"
      />,
    );

    const numberFlow = container.querySelector<NumberFlowElement>("number-flow-react");
    expect(numberFlow).toBeTruthy();
    const renderedValue = numberFlow?.shadowRoot
      ? Array.from(numberFlow.shadowRoot.querySelectorAll(
        '[part~="digit"] > :not([inert]), [part~="symbol"] > :not([inert])',
      )).map((part) => part.textContent).join("")
      : numberFlow?.textContent;
    expect(renderedValue).toBe(`${expected} total`);
    expect(numberFlow?.animated).toBe(true);
  });

  test("routes tile detail through the definition pair caption slot", () => {
    render(<MetricTile detail="Since last month" label="Revenue" value={24} />);
    const detail = screen.getByText("Since last month");
    expect(detail.className).toContain("text-2xs");
    expect(detail.className).toContain("text-fg-muted");
    expect(detail.parentElement?.querySelector("dd")?.textContent).toBe("24");
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
    expect(link.getAttribute("type")).toBeNull();
    expect(link.className).toContain("hover:border-border-strong");
    expect(link.className).toContain("hover:shadow-sm");
    fireEvent.click(link);
    expect(onNavigate).toHaveBeenCalledWith("/fields?model=Note");
  });
});
