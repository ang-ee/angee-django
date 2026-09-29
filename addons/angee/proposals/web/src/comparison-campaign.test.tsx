// @vitest-environment happy-dom

import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { ComparisonAnswer, ComparisonProposal, ComparisonTopic } from "./comparison-data";
import { RoundComparisonGrid, type RoundComparisonGridProps } from "./comparison-grid";

afterEach(cleanup);

test("custom headers, cells, selected facts and labels compose the shared comparison grid", () => {
  const topics: ComparisonTopic[] = [{ id: "topic-scope", key: "scope", name: "Scope", sort_order: 1 }];
  const proposals: ComparisonProposal[] = [
    { id: "proposal-one", state: "DRAFT", statement: "Visible statement" },
    { id: "proposal-two", state: "DRAFT", statement: null },
  ];
  const answers: ComparisonAnswer[] = [{
    id: "answer-one", proposal: { id: "proposal-one" }, topic: { id: "topic-scope" }, body: "Visible answer",
  }];
  const cell = vi.fn<NonNullable<RoundComparisonGridProps["renderCell"]>>(
    (_row, proposal, answer) => <span>{answer?.body ?? `Cell ${proposal.id}`}</span>,
  );
  render(
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <RoundComparisonGrid topics={topics} proposals={proposals} answers={answers}
        facts={["statement"]} renderColumnHeader={(proposal) => <span>Header {proposal.id}</span>}
        renderCell={cell} labels={{ grid: "Review grid", subject: "Criterion", facts: { statement: "Commitment" } }} />
    </AppRuntimeProvider>,
  );
  expect(screen.getByRole("table", { name: "Review grid" }).getAttribute("aria-colcount")).toBe("3");
  expect(screen.getByRole("columnheader", { name: "Criterion" })).toBeTruthy();
  expect(screen.getByRole("rowheader", { name: "Commitment" })).toBeTruthy();
  expect(screen.getByText("Header proposal-one")).toBeTruthy();
  expect(screen.getByText("Visible answer")).toBeTruthy();
  expect(cell).toHaveBeenCalledTimes(4);
  expect(cell).toHaveBeenCalledWith(expect.objectContaining({ kind: "topic" }), proposals[0], answers[0]);
  expect(cell).toHaveBeenCalledWith(expect.objectContaining({ kind: "topic" }), proposals[1], undefined);
  expect(cell).toHaveBeenCalledWith(expect.objectContaining({ kind: "fact" }), proposals[0], undefined);
});

test("redacted statements stay empty and removing a disclosed column removes its content", async () => {
  const visible: ComparisonProposal = { id: "visible", state: "DRAFT", statement: "Visible commitment" };
  const redacted: ComparisonProposal = { id: "redacted", state: "DRAFT", statement: null };
  const view = (proposals: ComparisonProposal[]) => (
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <RoundComparisonGrid topics={[]} proposals={proposals} answers={[]} facts={["statement"]} />
    </AppRuntimeProvider>
  );
  const { rerender } = render(view([visible, redacted]));
  await screen.findByText("Visible commitment");
  expect(screen.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["Visible commitment", "—"]);
  rerender(view([redacted]));
  expect(screen.queryByText("Visible commitment")).toBeNull();
  expect(screen.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["—"]);
  expect(screen.getByRole("table").getAttribute("aria-colcount")).toBe("2");
});


test("audience is stated once per column while cells contain only their answer", () => {
  render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <RoundComparisonGrid facts={[]} topics={[{ id: "topic", key: "scope", name: "Scope" }]}
      proposals={[{ id: "proposal", state: "DRAFT" }]}
      answers={[{ id: "answer", topic: { id: "topic" }, proposal: { id: "proposal" },
        visibility: "RESPONDER", body: "The proposed approach" }]} />
  </AppRuntimeProvider>);
  expect(screen.getAllByText(/Visibility:/)).toHaveLength(1);
  expect(screen.getByRole("cell").textContent).toBe("The proposed approach");
});
