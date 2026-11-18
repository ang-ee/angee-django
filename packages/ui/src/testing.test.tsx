// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import {
  createMutationDialogTestDouble,
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

describe("MutationDialog test double", () => {
  test("validates before it submits, with the dialog's quiet call options, and submits nothing refused", async () => {
    const Dialog = createMutationDialogTestDouble({ values: { name: "Ada" } });
    const validate = vi.fn()
      .mockResolvedValueOnce({ fieldErrors: { name: ["Taken"] }, formErrors: [] })
      .mockResolvedValueOnce(null);
    const onSubmit = vi.fn(async () => ({ status: "ok" as const, data: "saved" }));
    const onSubmitted = vi.fn();
    render(<Dialog open title="Create" fields={[]} submitLabel="Create" parseValues={(values) => values}
      validate={validate} onSubmit={onSubmit} onSubmitted={onSubmitted} />);

    fireEvent.click(screen.getByRole("button", { name: "Submit mutation dialog" }));
    await waitFor(() => expect(validate).toHaveBeenCalledExactlyOnceWith({ name: "Ada" }, { errorNotification: false }));
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Submit mutation dialog" }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalledExactlyOnceWith("saved", { name: "Ada" }));
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ name: "Ada" }, { errorNotification: false });
    cleanup();
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
