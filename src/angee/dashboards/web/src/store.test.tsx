// @vitest-environment happy-dom

import * as React from "react";
import { createRoot, type Root } from "react-dom/client";
import { failedWriteOutcome } from "@angee/refine";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useAuthoredMutation: () => [mocks.mutate],
}));

import { dashboardStore } from "./store";

type Store = ReturnType<typeof dashboardStore.useDashboard>;

let root: Root;
let host: HTMLDivElement;
let store!: Store;
beforeEach(async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  mocks.mutate.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  function Probe(): null {
    store = dashboardStore.useDashboard({ scope: "personal", id: "dsh_1" });
    return null;
  }
  await React.act(async () => root.render(<Probe />));
});
afterEach(async () => {
  await React.act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

const snapshot: Parameters<Store["duplicate"]>[1]["snapshot"] = { schemaVersion: 1, columns: 12, widgets: [] };

test.each([
  ["a keyed create's conflict without a current revision is its spent key", "create", { status: "conflict", message: "Key used.", current_revision: null }, "conflict"],
  ["a keyed duplicate's conflict without a current revision is its spent key", "duplicate", { status: "conflict", message: "Key used.", current_revision: null }, "conflict"],
  ["a conflict with a current revision is a stale one", "duplicate", { status: "conflict", message: "Changed.", current_revision: 4 }, "refused"],
  ["an unkeyed save's conflict is no spent key", "save", { status: "conflict", message: "The persisted dashboard identity is no longer current.", current_revision: null }, "refused"],
  ["an in-band error is the server's answer", "create", { status: "error", message: "Name taken." }, "refused"],
] as const)("%s: the store throws the refusal typed, as its readers classify it", async (_name, write, payload, outcome) => {
  const answer = { create_personal_dashboard: payload, duplicate_dashboard: payload, save_dashboard: payload };
  mocks.mutate.mockResolvedValueOnce(answer);
  const refusal = await (write === "create"
    ? store.createPersonal({ name: "Q4", idempotencyKey: "draft-key" })
    : write === "duplicate"
      ? store.duplicate({ scope: "personal", id: "dsh_1" }, { name: "Q4", idempotencyKey: "draft-key", snapshot })
      : store.save({ target: { scope: "personal", id: "dsh_1" }, snapshot, persistedId: "dsh_1", expectedRevision: 3 })
  ).catch((error: unknown) => error);
  expect(refusal).toBeInstanceOf(Error);
  // A spent key is told in the create's own words; every other refusal in the server's.
  const told = outcome !== "conflict" ? payload.message
    : write === "create" ? "An earlier attempt already created a personal dashboard." : "An earlier attempt already made a copy of this dashboard.";
  expect((refusal as Error).message).toContain(told);
  expect(failedWriteOutcome(refusal)).toBe(outcome);
});
