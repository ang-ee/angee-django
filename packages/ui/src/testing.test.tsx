// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import {
  createUiRouteTestDoubles,
  createUiTestModule,
  createUiTestProviders,
} from "./testing";
import { Tooltip } from "./ui/tooltip";

describe("shared UI mock contracts", () => {
  test("groups overrides over one imported UI module", async () => {
    const imported = { useRouteSearch: () => ({ original: true }), untouched: "owner" };
    const module = await createUiTestModule(
      async () => imported as never,
      createUiRouteTestDoubles({ search: { decision: "decision-1" } }),
    );

    expect((module as unknown as { untouched: string }).untouched).toBe("owner");
    expect(module.useRouteSearch()).toEqual({ decision: "decision-1" });
  });
});

describe("shared UI providers", () => {
  test("a hovered tooltip opens in the same event, without waiting for its open delay", () => {
    const { Provider, clearClients } = createUiTestProviders();
    try {
      render(<Provider><Tooltip label="Imported source"><button type="button">Source</button></Tooltip></Provider>);
      fireEvent.mouseEnter(screen.getByRole("button", { name: "Source" }));
      expect(screen.getByText("Imported source")).toBeTruthy();
    } finally {
      cleanup();
      clearClients();
    }
  });
});
