// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FormProvider, useForm, useFormContext, useWatch } from "react-hook-form";
import { afterEach, describe, expect, test } from "vitest";
import { composeAddons, defineAddon, type AddonManifest } from "@angee/app";
import { ShellPageTestProviders } from "@angee/app/testing";

import decisions from "./index";
import { DecisionContext } from "./DecisionContext";
import {
  DecisionContentOutlet, DecisionContentProvider, DecisionOriginOutlet,
  decisionContent, useDecisionContent, useDecisionContentEntries, type DecisionContentProps,
} from "./content";
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
  return <p>Waiting on {current.group?.id}</p>;
}
function ContextWithContent() {
  const { decision: current } = useDecisionContent();
  const entries = useDecisionContentEntries(current.kind);
  return <>
    <DecisionContext context={{ facts: [{ pointer: "/review", label: "Review fact", value: { outcome: "Ready" }, authority: "source" }] }} showFacts={entries.length === 0} />
    <DecisionContentOutlet />
  </>;
}
/** The decisions addon's containers, composed with the contributing addons. */
function composed(contributors: readonly AddonManifest[] = []) {
  return composeAddons([decisions, ...contributors], canonicalizer).containers;
}
const contributor = (id: string, containers: AddonManifest["containers"]): AddonManifest =>
  defineAddon({ id, dependsOn: ["decisions"], containers });

function Harness({ contributors = [], withContext = false }: { contributors?: readonly AddonManifest[]; withContext?: boolean }) {
  const form = useForm({ defaultValues: { note: "Initial note" } });
  return <ShellPageTestProviders runtime={{ containers: composed(contributors) }}><FormProvider {...form}>
    <DecisionContentProvider value={{ decision, basis: decision.basis, context: decision.context }}>
      {withContext ? <ContextWithContent /> : <DecisionContentOutlet />}<DecisionOriginOutlet />
    </DecisionContentProvider>
  </FormProvider></ShellPageTestProviders>;
}

describe("decision content contracts", () => {
  test("duplicate contributions for one kind fail at native addon composition", () => {
    expect(() => composed([
      contributor("first", { "decisions#content": { "first.review": decisionContent("review", Consumer) } }),
      contributor("second", { "decisions#content": { "second.review": decisionContent("review", Consumer) } }),
    ])).toThrow(/share key "review"/);
  });

  test("only the selected kind renders with retained props and inherited form context", () => {
    const other = () => <p>Other kind</p>;
    render(<Harness contributors={[contributor("consumer", { "decisions#content": {
      "consumer.review": decisionContent("review", Consumer),
      "consumer.other": decisionContent("other", other),
    } })]} />);
    expect(screen.getByText("review")).toBeTruthy();
    expect(screen.queryByText("Other kind")).toBeNull();
    expect(screen.getByText(JSON.stringify({ basis: decision.basis, context: decision.context }))).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Consumer note" }), { target: { value: "Updated note" } });
    expect(screen.getByLabelText("Form note").textContent).toBe("Updated note");
  });

  test("registered content replaces the generic facts for its kind", () => {
    const { rerender } = render(<Harness withContext contributors={[contributor("consumer", { "decisions#content": { "consumer.review": decisionContent("review", Consumer) } })]} />);
    expect(screen.getByText("review")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Facts" })).toBeNull();
    rerender(<Harness withContext />);
    expect(screen.getByRole("region", { name: "Facts" })).toBeTruthy();
    expect(screen.getByText("Ready")).toBeTruthy();
  });

  test("an independent waiting owner can consume the same decision and empty containers add no output", () => {
    const { container, rerender } = render(<Harness />);
    expect(container.textContent).toBe("");
    rerender(<Harness contributors={[contributor("waiting", { "decisions#origin": { "waiting.owner": { content: <Origin /> } } })]} />);
    expect(screen.getByText("Waiting on dcg_review")).toBeTruthy();
  });
});
