// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRuntimeProvider, ToastProvider, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import type { ComparisonAnswer, ComparisonProposal, ComparisonTopic } from "./comparison-data";
import { RoundComparisonGrid } from "./comparison-grid";
import { ProposalStatements } from "./proposal-statements";

const mocks = vi.hoisted(() => ({
  saveAnswer: vi.fn(async () => undefined),
  saveStatement: vi.fn(async () => undefined),
}));
vi.mock("./comparison-writes", () => ({
  useComparisonAnswerWrite: () => ({ pending: false, save: mocks.saveAnswer }),
  useComparisonStatementWrite: () => ({ pending: false, save: mocks.saveStatement }),
}));
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));
vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useEnumOptions: () => [
      { value: "round", label: "Round" },
      { value: "responder", label: "Responder" },
      { value: "sealed", label: "Managers only" },
    ],
    useAuthoredResourceMutation: () => [vi.fn(), { fetching: false }],
  };
});

const { Provider, clearClients } = createUiTestProviders();
const topic: ComparisonTopic = { id: "topic-1", name: "Scope", sort_order: 1 };
const own: ComparisonProposal = {
  id: "proposal-own", responder: { id: "user-own", display_name: "Alice" },
  permissions: ["write", "read_offer"], revision: 4,
};
const other: ComparisonProposal = {
  id: "proposal-other", responder: { id: "user-other", display_name: "Bob" },
  permissions: ["read_offer"], revision: 3,
};
const auth = { user: { id: "user-own", name: "Alice" }, status: "authenticated" as const, hasRole: () => false };

function renderView(view: ReactNode) {
  return render(<Provider><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets, auth }}>
    {view}
  </AppRuntimeProvider></ToastProvider></Provider>);
}

afterEach(() => {
  cleanup();
  clearClients();
  mocks.saveAnswer.mockClear();
  mocks.saveStatement.mockClear();
});

test("headers use host labels and own empty cells prompt while other empty cells stay muted", () => {
  renderView(<RoundComparisonGrid topics={[topic]} proposals={[own, other]} answers={[]}
    labels={{ heading: "Approach", hint: "Compare each response", audience: "Usually shared" }} />);
  expect(screen.getByText("Approach")).toBeTruthy();
  expect(screen.getByText("Compare each response")).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: "Subject" })).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: /Alice/ })).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: /Bob/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Write your scope" })).toBeTruthy();
  expect(screen.getByText("Nothing yet")).toBeTruthy();
});

test("own answer saves inside the cell with the selected audience and an observed revision", async () => {
  const answer: ComparisonAnswer = {
    id: "answer-1", proposal: { id: own.id }, topic: { id: topic.id },
    body: "First answer", visibility: "ROUND", allowed_visibility: ["ROUND", "RESPONDER"],
    permissions: ["write", "narrow"], revision: 7,
  };
  renderView(<RoundComparisonGrid topics={[topic]} proposals={[own]} answers={[answer]} />);
  fireEvent.click(screen.getByRole("button", { name: "Edit Scope" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Write your Scope" }), { target: { value: "Revised answer" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(mocks.saveAnswer).toHaveBeenCalledExactlyOnceWith(
    own.id, topic.id, "Revised answer", "round", answer,
  ));
});

test("only answer managers receive the audience menu", () => {
  const answer: ComparisonAnswer = {
    id: "answer-1", proposal: { id: own.id }, topic: { id: topic.id },
    body: "A plan", visibility: "ROUND", allowed_visibility: ["ROUND", "RESPONDER", "SEALED"],
    permissions: ["manage"], revision: 2,
  };
  const view = renderView(<RoundComparisonGrid topics={[topic]} proposals={[{ ...own, permissions: [] }]} answers={[answer]} />);
  expect(screen.getByRole("button", { name: "Posting to" })).toBeTruthy();
  view.rerender(<Provider><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets, auth }}>
    <RoundComparisonGrid topics={[topic]} proposals={[{ ...own, permissions: [] }]}
      answers={[{ ...answer, permissions: [] }]} />
  </AppRuntimeProvider></ToastProvider></Provider>);
  expect(screen.queryByRole("button", { name: "Posting to" })).toBeNull();
  expect(screen.getAllByText("Round").length).toBeGreaterThan(0);
});

test("statements render separately and the responder files their own statement", async () => {
  renderView(<ProposalStatements proposals={[own, { ...other, statement: "Existing statement" }]}
    labels={{ heading: "Statements" }} />);
  expect(screen.getByText("Statements")).toBeTruthy();
  // The statement renders through the code-split markdown preview; its cold import can outlast
  // the default 1s wait on a loaded runner.
  expect(await screen.findByText("Existing statement", {}, { timeout: 5000 })).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox", { name: /Your statement, in your own words/ }), { target: { value: "Six weeks, subject to access" } });
  fireEvent.click(screen.getByRole("button", { name: "File statement" }));
  await waitFor(() => expect(mocks.saveStatement).toHaveBeenCalledExactlyOnceWith(own, "Six weeks, subject to access"));
});

test("a single server-returned proposal produces one column before disclosure", () => {
  renderView(<RoundComparisonGrid topics={[topic]} proposals={[own]}
    answers={[{ id: "hidden", proposal: { id: other.id }, topic: { id: topic.id }, body: "Not a column" }]} />);
  expect(screen.getByRole("table").getAttribute("aria-colcount")).toBe("2");
  expect(screen.queryByText("Bob")).toBeNull();
  expect(screen.queryByText("Not a column")).toBeNull();
});

test("a wholly empty topic spans unreadable cells when nobody can write it", () => {
  renderView(<RoundComparisonGrid topics={[topic]} proposals={[
    { ...own, permissions: [] }, other,
  ]} answers={[]} />);
  expect(screen.getByRole("cell", { name: "Nothing yet" }).getAttribute("colspan")).toBe("2");
  expect(screen.queryByRole("button", { name: /Write your/ })).toBeNull();
});
