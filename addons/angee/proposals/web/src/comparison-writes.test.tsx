// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { useComparisonAnswerWrite, useComparisonStatementWrite } from "./comparison-writes";

const mocks = vi.hoisted(() => ({
  create: vi.fn(async () => ({ data: { id: "answer-new" } })),
  update: vi.fn(async () => ({ data: { id: "answer-1", revision: 8 } })),
  visibility: vi.fn(async () => ({ set_proposal_answer_visibility: { ok: true, message: "Saved" } })),
}));
vi.mock("@angee/metadata", () => ({
  refineResourceName: () => "resource",
  useModelMetadata: () => ({ resource: { schemaName: "console" } }),
}));
vi.mock("@refinedev/core", () => ({
  useCreate: () => ({ mutateAsync: mocks.create, mutation: { isPending: false } }),
  useUpdate: () => ({ mutateAsync: mocks.update, mutation: { isPending: false } }),
}));
vi.mock("@angee/ui", () => ({
  createNamespaceT: () => () => (key: string) => key,
  optionToken: (value: unknown) => String(value ?? "").toLowerCase(),
  useAuthoredResourceMutation: () => [mocks.visibility, { fetching: false }],
  useActionResultRun: () => (fire: () => Promise<unknown>) => fire(),
}));

test("answer edit sends its observed revision, then uses the returned revision for audience", async () => {
  mocks.update.mockClear();
  mocks.visibility.mockClear();
  const { result } = renderHook(() => useComparisonAnswerWrite());
  await act(async () => result.current.save("proposal-1", "topic-1", "Revised", "responder", {
    id: "answer-1", body: "Original", visibility: "ROUND", revision: 7,
  }));
  expect(mocks.update).toHaveBeenCalledWith(expect.objectContaining({
    id: "answer-1", values: { body: "Revised" },
    meta: expect.objectContaining({ gqlVariables: { expected_revision: 7 } }),
  }));
  expect(mocks.visibility).toHaveBeenCalledWith({
    answer: "answer-1", revision: 8, visibility: "RESPONDER",
  });
});

test("new answers and statements use the existing resource writes", async () => {
  mocks.create.mockClear();
  mocks.update.mockClear();
  const answer = renderHook(() => useComparisonAnswerWrite());
  await act(async () => answer.result.current.save("proposal-1", "topic-1", "New answer", "round"));
  expect(mocks.create).toHaveBeenCalledWith({
    values: { proposal: "proposal-1", topic: "topic-1", body: "New answer", visibility: "round" },
  });
  const statement = renderHook(() => useComparisonStatementWrite());
  await act(async () => statement.result.current.save({ id: "proposal-1", revision: 4 }, "Six weeks"));
  expect(mocks.update).toHaveBeenCalledWith(expect.objectContaining({
    id: "proposal-1", values: { statement: "Six weeks" },
    meta: expect.objectContaining({ gqlVariables: { expected_revision: 4 } }),
  }));
});
