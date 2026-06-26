// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { DashboardView } from "./DashboardView";
import { Metric } from "./dashboard/Metric";

afterEach(() => cleanup());

describe("DashboardView", () => {
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
});
