// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { NumberFlowProps } from "@number-flow/react";
import { InAppLinkProvider } from "../../lib/in-app-link";
import { afterEach, describe, expect, test, vi } from "vitest";

import { DashboardView } from "./DashboardView";
import { Metric } from "./Metric";

const numberFlow = vi.hoisted(() => ({ props: [] as NumberFlowProps[] }));

vi.mock("@number-flow/react", () => ({
  default: (props: NumberFlowProps) => {
    numberFlow.props.push(props);
    return <span data-testid="number-flow">{String(props.value)}{props.suffix}</span>;
  },
}));

afterEach(() => {
  cleanup();
  numberFlow.props = [];
});

describe("DashboardView", () => {
  test("counts animate through the tile's numeric path and cap at max+", () => {
    render(
      <DashboardView>
        <Metric label="Users" value={128} format="count" />
        <Metric label="Grants" value={1500} format="count" max={999} />
        <Metric label="Pending" format="count" loading />
      </DashboardView>,
    );

    expect(screen.getAllByTestId("number-flow").map((node) => node.textContent)).toEqual(["128", "999+"]);
    expect(numberFlow.props.map(({ animated, suffix, value }) => ({ animated, suffix, value }))).toEqual([
      { animated: true, suffix: undefined, value: 128 },
      { animated: true, suffix: "+", value: 999 },
    ]);
    expect(screen.getByText("—")).toBeTruthy();
  });

  test("folds Metric children into one metric band and renders the rest below", () => {
    render(
      <DashboardView>
        <Metric label="Users" value={128} />
        <Metric label="Roles" value={7} tone="brand" />
        <section aria-label="panel">below the band</section>
      </DashboardView>,
    );

    // Metric values render once, in the band.
    expect(screen.getByText("128")).toBeTruthy();
    expect(screen.getByText("7")).toBeTruthy();
    expect(screen.getByText("Users")).toBeTruthy();
    // Non-Metric children render as page content.
    expect(screen.getByRole("region", { name: "panel" }).textContent).toBe(
      "below the band",
    );
  });

  test("renders no metric band when there are no Metric children", () => {
    render(
      <DashboardView>
        <section aria-label="only">content</section>
      </DashboardView>,
    );
    expect(screen.getByRole("region", { name: "only" })).toBeTruthy();
    // No <dl> metric grid emitted.
    expect(document.querySelector("dl")).toBeNull();
  });

  test("preserves a metric deep link and its client-side navigation handler", () => {
    const onNavigate = vi.fn();
    render(
      <InAppLinkProvider navigate={onNavigate}><DashboardView>
        <Metric
          label="Users"
          value={128}
          href="/iam/users"
        />
      </DashboardView></InAppLinkProvider>,
    );

    const link = screen.getByRole("link", { name: /Users/ });
    expect(link.getAttribute("href")).toBe("/iam/users");
    fireEvent.click(link);
    expect(onNavigate).toHaveBeenCalledWith("/iam/users");
  });
});
