// @vitest-environment happy-dom

import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";
import { markdownPreviewWidget } from "@angee/ui/widgets/markdown";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { ComparisonAnswer, ComparisonProposal, ComparisonTopic } from "./comparison-data";
import { RoundComparisonGrid, type RoundComparisonGridProps } from "./comparison-grid";

afterEach(cleanup);

// The registry loads the markdown widget lazily; statements and answers render
// it eagerly here, so the assertions never race the chunk on a loaded runner.
const widgets = { ...defaultWidgets, "markdown.preview": markdownPreviewWidget };

test("custom headers, cells and selected facts compose the shared comparison grid", () => {
  const topics: ComparisonTopic[] = [{ id: "topic-scope", key: "scope", name: "Scope", sort_order: 1 }];
  const proposals: ComparisonProposal[] = [
    { id: "proposal-one", state: "DRAFT", statement: "Visible statement" },
    { id: "proposal-two", state: "DRAFT", statement: null },
  ];
  const answers: ComparisonAnswer[] = [{
    id: "answer-one", proposal: { id: "proposal-one" }, topic: { id: "topic-scope" }, body: "Visible answer",
  }];
  const renderCell: NonNullable<RoundComparisonGridProps["renderCell"]> =
    (_row, proposal, answer) => <span>{String(answer?.body ?? `Cell ${proposal.id}`)}</span>;
  const cell = vi.fn(renderCell);
  render(
    <AppRuntimeProvider runtime={{ widgets }}>
      <RoundComparisonGrid topics={topics} proposals={proposals} answers={answers}
        facts={["statement"]} renderColumnHeader={(proposal) => <span>Header {proposal.id}</span>}
        renderCell={cell} />
    </AppRuntimeProvider>,
  );
  expect(screen.getByRole("table", { name: "Proposal comparison" }).getAttribute("aria-colcount")).toBe("3");
  expect(screen.getByRole("columnheader", { name: "Subject" })).toBeTruthy();
  expect(screen.getByRole("rowheader", { name: "Statement" })).toBeTruthy();
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
    <AppRuntimeProvider runtime={{ widgets }}>
      <RoundComparisonGrid topics={[]} proposals={proposals} answers={[]} facts={["statement"]} />
    </AppRuntimeProvider>
  );
  const { rerender } = render(view([visible, redacted]));
  await screen.findByText("Visible commitment");
  expect(screen.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["Visible commitment", "Nothing yet"]);
  rerender(view([redacted]));
  expect(screen.queryByText("Visible commitment")).toBeNull();
  expect(screen.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["Nothing yet"]);
  expect(screen.getByRole("table").getAttribute("aria-colcount")).toBe("2");
});


test("each answer carries an audience chip and the column summarizes it", () => {
  render(<AppRuntimeProvider runtime={{ widgets }}>
    <RoundComparisonGrid facts={[]} topics={[{ id: "topic", key: "scope", name: "Scope" }]}
      proposals={[{ id: "proposal", state: "DRAFT" }]}
      answers={[{ id: "answer", topic: { id: "topic" }, proposal: { id: "proposal" },
        visibility: "RESPONDER", body: "The proposed approach" }]} />
  </AppRuntimeProvider>);
  expect(screen.getByRole("columnheader", { name: /Responder/ }).textContent).toContain("RESPONDER");
  expect(screen.getByRole("cell").textContent).toContain("The proposed approach");
  expect(screen.getByRole("cell").textContent).toContain("RESPONDER");
});
