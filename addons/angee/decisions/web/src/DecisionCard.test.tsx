// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "@angee/ui/testing";
import { DecisionCard } from "./DecisionCard";
import { decisionFixture } from "./testing";

const decide = vi.hoisted(() => vi.fn());
vi.mock("@angee/ui", async (original) => ({ ...await original<typeof import("@angee/ui")>(), useActionOutcomeMutation: () => [decide] }));
const { Provider, clearClients } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); vi.clearAllMocks(); });

test("choices reset with the revision and an accepted answer remains locked until it closes", async () => {
  decide.mockResolvedValue({ ok: true });
  const decision = decisionFixture();
  const view = render(<Provider><DecisionCard decision={decision} /></Provider>);
  fireEvent.click(screen.getByRole("radio", { name: /^Accept/ }));
  view.rerender(<Provider><DecisionCard decision={{ ...decision, revision: 4 }} /></Provider>);
  expect((screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole("radio", { name: /^Accept/ }).getAttribute("aria-checked")).toBe("false");
  fireEvent.click(screen.getByRole("radio", { name: /^Accept/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(decide).toHaveBeenCalledExactlyOnceWith(decision.id, { revision: 4, chosen: ["accept"] }));
  await waitFor(() => expect((screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement).disabled).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  expect(decide).toHaveBeenCalledTimes(1);
  view.rerender(<Provider><DecisionCard decision={{ ...decision, revision: 4, is_open: false, verdict: ["accept"], verdict_label: "Accept" }} /></Provider>);
  expect(document.activeElement?.textContent).toContain("Chose: Accept");
});

test("multiple placements of a question have distinct checkbox ids", () => {
  const decision = decisionFixture({ proposal: { multiple: true, alternatives: [{ key: "accept", label: "Accept", outcome: "done" }] } });
  render(<Provider><DecisionCard decision={decision} /><DecisionCard decision={decision} /></Provider>);
  const ids = screen.getAllByRole("checkbox").map((input) => input.id);
  expect(new Set(ids).size).toBe(2);
});

test("a record action uses its human alternative label", () => {
  const decision = decisionFixture({ proposal: { alternatives: [{ key: "approve", label: "Approve change", outcome: "done",
    actions: { ent_target: { record: { call: "internal_method_name" } } } }] } });
  render(<Provider><DecisionCard decision={decision} /></Provider>);
  expect(screen.getByRole("radio", { name: "Approve change" })).toBeTruthy();
  expect(screen.queryByText("internal method name")).toBeNull();
});
