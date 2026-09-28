// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FormProvider, useForm, useFormContext, useWatch } from "react-hook-form";
import { afterEach, describe, expect, test } from "vitest";
import { composeAddons, defineAddon } from "@angee/app";
import { ShellPageTestProviders } from "@angee/app/testing";
import type { SlotContribution } from "@angee/ui";

import {
  DECISION_ORIGIN_SLOT, DecisionContentOutlet, DecisionContentProvider, DecisionOriginOutlet,
  decisionContent, useDecisionContent, type DecisionContentProps,
} from "./slots";
import { decisionFixture } from "./testing";

afterEach(cleanup);
const canonicalizer = { canonicalModelLabel: (model: string) => model };
const decision = decisionFixture();

function Consumer({ decision: current, basis, context }: DecisionContentProps) {
  const form = useFormContext<{ note: string }>();
  const note = useWatch({ control: form.control, name: "note" });
  return <>
    <p>{current.kind}</p><output>{JSON.stringify({ basis, context })}</output>
    <label>Consumer note<input {...form.register("note")} /></label><output aria-label="Form note">{note}</output>
  </>;
}
function Origin() {
  const { decision: current } = useDecisionContent();
  return <p>Waiting on {current.group.id}</p>;
}
function Harness({ slots = [] }: { slots?: readonly SlotContribution[] }) {
  const form = useForm({ defaultValues: { note: "Initial note" } });
  return <ShellPageTestProviders runtime={{ slots }}><FormProvider {...form}>
    <DecisionContentProvider value={{ decision, basis: decision.basis, context: decision.context }}>
      <DecisionContentOutlet /><DecisionOriginOutlet />
    </DecisionContentProvider>
  </FormProvider></ShellPageTestProviders>;
}

describe("decision content contracts", () => {
  test("duplicate contributions for one kind fail at native addon composition", () => {
    expect(() => composeAddons([
      defineAddon({ id: "first", slots: [decisionContent("review", Consumer)] }),
      defineAddon({ id: "second", slots: [decisionContent("review", Consumer)] }),
    ], canonicalizer)).toThrow(/slot entry/);
  });

  test("only the selected kind renders with retained props and inherited form context", () => {
    const other = () => <p>Other kind</p>;
    render(<Harness slots={[decisionContent("review", Consumer), decisionContent("other", other)]} />);
    expect(screen.getByText("review")).toBeTruthy();
    expect(screen.queryByText("Other kind")).toBeNull();
    expect(screen.getByText(JSON.stringify({ basis: decision.basis, context: decision.context }))).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Consumer note" }), { target: { value: "Updated note" } });
    expect(screen.getByLabelText("Form note").textContent).toBe("Updated note");
  });

  test("an independent waiting owner can consume the same decision and empty slots add no output", () => {
    const { container, rerender } = render(<Harness />);
    expect(container.textContent).toBe("");
    rerender(<Harness slots={[{ slot: DECISION_ORIGIN_SLOT, id: "waiting-owner", content: <Origin /> }]} />);
    expect(screen.getByText("Waiting on dcg_review")).toBeTruthy();
  });
});
