// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useAuthoredQuery } from "@angee/refine";
import { createUiTestProviders } from "@angee/ui/testing";
import type { MetaQuery } from "@refinedev/core";
import { afterEach, expect, test, vi } from "vitest";

import { DecisionCard } from "./DecisionCard";
import { DECISION_MODELS, DecisionDocument, type Decision } from "./documents.console";
import { decisionFixture, decisionRecordFixture, decisionResourceFixture } from "./testing";

const { Provider, clearClients, createClient } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); });

function RunDecisionRead() {
  // A run drill-in has its own native key; the card accepts at DecisionDetail's exact key.
  const read = useAuthoredQuery(DecisionDocument, { id: "dcn_review", view: "run" } as never, {
    models: DECISION_MODELS,
  });
  return read.data?.decisions_by_pk ? <DecisionCard decision={read.data.decisions_by_pk} /> : <p>Loading</p>;
}

test("Decide closes the card before its run read refetch resolves", async () => {
  const open = decisionFixture({ records: [] });
  const answered = decisionFixture({
    ...open,
    revision: open.revision + 1, is_open: false, permissions: [], verdict: ["accept"],
    verdict_label: "Accept and archive", answered_at: "2026-11-03T10:00:00Z",
    answered_by: { display_name: "Reviewer" },
  });
  const authoritative = decisionFixture({ ...answered, answered_by: { display_name: "Final reviewer" },
    answered_at: "2026-11-03T11:00:00Z" });
  const pending: Array<(response: { data: { decisions_by_pk: Decision } }) => void> = [];
  let reads = 0;
  const custom = vi.fn(async ({ meta }: { meta?: MetaQuery }) => {
    if (meta?.gqlMutation) return { data: { decide: {
      ok: true, message: "Decision recorded.", id: open.id, code: null, validation_errors: null, decision: answered,
    } } };
    reads += 1;
    if (reads === 1) return { data: { decisions_by_pk: open } };
    return new Promise<{ data: { decisions_by_pk: Decision } }>((resolve) => { pending.push(resolve); });
  });
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  render(<Provider queryClient={client} resources={[decisionResourceFixture, decisionRecordFixture]}
    dataProvider={{ custom }}><RunDecisionRead /></Provider>);
  await screen.findByRole("button", { name: "Confirm" });
  fireEvent.click(screen.getByRole("radio", { name: /^Accept/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(pending).toHaveLength(1));
  expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  expect(screen.getByText("Reviewer")).toBeTruthy();
  expect(screen.getByText("Chose: Accept and archive").closest("p")?.querySelector("time")?.dateTime)
    .toBe("2026-11-03T10:00:00.000Z");

  // A read started before the answer can finish late without reopening the card.
  await act(async () => { pending.shift()!({ data: { decisions_by_pk: open } }); });
  expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  void client.invalidateQueries({ predicate: (query) => JSON.stringify(query.queryKey).includes('"view":"run"') });
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () => { pending.shift()!({ data: { decisions_by_pk: authoritative } }); });
  expect(await screen.findByText("Final reviewer")).toBeTruthy();
  expect(screen.getByText("Chose: Accept and archive").closest("p")?.querySelector("time")?.dateTime)
    .toBe("2026-11-03T11:00:00.000Z");
  expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  expect(reads).toBe(3);
  expect(custom.mock.calls.filter(([input]) => input.meta?.gqlMutation)).toHaveLength(1);
});
