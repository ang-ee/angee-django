// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, test, vi } from "vitest";

import {
  createMutationDialogTestDouble,
  createUiRouteTestDoubles,
  createUiTestModule,
  createUiTestProviders,
} from "./testing";
import { Tooltip } from "./ui/tooltip";
import type { KeyedMutationDialogProps } from "./views/form/MutationDialog";

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

  test("a keyed double sends one key per draft: kept through refusals, ended by an accepted answer", async () => {
    const Dialog = createMutationDialogTestDouble({ values: { name: "Ada" } });
    const keys: string[] = [];
    const answers = [
      { status: "invalid" as const, issues: { fieldErrors: {}, formErrors: ["Taken"] } },
      { status: "ok" as const, data: "saved" },
      { status: "ok" as const, data: "again" },
    ];
    // A keyed dialog's props, as a keyed caller renders the double in its place.
    const Keyed = Dialog as unknown as (props: KeyedMutationDialogProps<Record<string, unknown>>) => ReactElement | null;
    render(<Keyed open title="Create" fields={[]} submitLabel="Create" parseValues={(values) => values}
      idempotencyScope="dashboards.create-personal"
      onSubmit={async (_values, _options, key) => { keys.push(key); return answers.shift()!; }} />);
    for (let count = 1; count <= 3; count += 1) {
      fireEvent.click(screen.getByRole("button", { name: "Submit mutation dialog" }));
      await waitFor(() => expect(keys).toHaveLength(count));
    }
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
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
