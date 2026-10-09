// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { NumberFlowProps } from "@number-flow/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { InAppLinkProvider } from "../lib/in-app-link";
import { setHumanDateLocale } from "../lib/human-locale";
import { MetricStrip, MetricTile } from "./MetricStrip";

const numberFlow = vi.hoisted(() => ({ props: null as NumberFlowProps | null }));

vi.mock("@number-flow/react", () => ({
  default: (props: NumberFlowProps) => {
    numberFlow.props = props;
    const formatted = new Intl.NumberFormat(props.locales, props.format)
      .format(props.value as number);
    return <span data-testid="number-flow">{formatted}{props.suffix}</span>;
  },
}));

afterEach(() => {
  cleanup();
  numberFlow.props = null;
  setHumanDateLocale("en");
  document.documentElement.style.removeProperty("--dur-base");
  document.documentElement.style.removeProperty("--ease");
});

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

  test("formats a non-animated numeric value as plain text", () => {
    setHumanDateLocale("en-US");
    const expected = new Intl.NumberFormat("en-US", {
      maximumFractionDigits: 2,
    }).format(1234.56);
    render(
      <MetricTile
        format={{ maximumFractionDigits: 2 }}
        label="Revenue"
        numericValue={1234.56}
        suffix=" total"
        value="Fallback"
      />,
    );

    expect(screen.getByText(`${expected} total`)).toBeTruthy();
    expect(screen.queryByTestId("number-flow")).toBeNull();
  });

  test("forwards numeric value, format, suffix, and motion timing to NumberFlow", () => {
    setHumanDateLocale("en-US");
    document.documentElement.style.setProperty("--dur-base", "240ms");
    document.documentElement.style.setProperty("--ease", "cubic-bezier(0.1, 0.7, 0.2, 1)");
    const expected = new Intl.NumberFormat("en-US", {
      maximumFractionDigits: 2,
    }).format(1234.56);
    render(
      <MetricTile
        animate
        format={{ maximumFractionDigits: 2 }}
        label="Revenue"
        numericValue={1234.56}
        suffix=" total"
        value="Fallback"
      />,
    );

    expect(screen.getByTestId("number-flow").textContent).toBe(`${expected} total`);
    expect(numberFlow.props).toMatchObject({
      animated: true,
      format: { maximumFractionDigits: 2 },
      locales: "en-US",
      opacityTiming: { duration: 240, easing: "cubic-bezier(0.1, 0.7, 0.2, 1)" },
      respectMotionPreference: true,
      spinTiming: { duration: 240, easing: "cubic-bezier(0.1, 0.7, 0.2, 1)" },
      suffix: " total",
      transformTiming: { duration: 240, easing: "cubic-bezier(0.1, 0.7, 0.2, 1)" },
      value: 1234.56,
    });
  });

  test("keeps an animated tile mounted when its value changes", () => {
    const { rerender } = render(
      <MetricStrip metrics={[{
        animate: true,
        id: "revenue",
        label: "Revenue",
        numericValue: 1,
        value: 1,
      }]} />,
    );
    const mounted = screen.getByTestId("number-flow");

    rerender(
      <MetricStrip metrics={[{
        animate: true,
        id: "revenue",
        label: "Revenue total",
        numericValue: 2,
        value: 2,
      }]} />,
    );

    expect(screen.getByTestId("number-flow")).toBe(mounted);
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
