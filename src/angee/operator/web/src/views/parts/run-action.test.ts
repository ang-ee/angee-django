import { describe, expect, test, vi } from "vitest";

import { runDaemonAction } from "./run-action";

// Every case shares the same field/variables/label; each test supplies its own
// `run` outcome plus fresh `setError`/`refetch` spies.
const base = {
  field: "serviceStart",
  variables: { id: "web" },
  label: "Start service",
};

describe("runDaemonAction", () => {
  test("returns true and only clears the error on a returned payload", async () => {
    const setError = vi.fn();
    const refetch = vi.fn();
    // The daemon returns a MutationResult; its `status` is descriptive, not a
    // pass/fail flag — any returned root field counts as success.
    const run = vi.fn(async () => ({ serviceStart: { status: "started", name: null, message: null } }));

    const ok = await runDaemonAction({ ...base, run, setError, refetch });

    expect(ok).toBe(true);
    expect(setError).toHaveBeenCalledTimes(1);
    expect(setError).toHaveBeenCalledWith(null);
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  test("treats any non-null payload as success (failure is a GraphQL error)", async () => {
    const setError = vi.fn();
    // e.g. jobRun returns a bare job-id string; a non-null payload is success.
    const run = vi.fn(async () => ({ serviceStart: "job-123" }));

    const ok = await runDaemonAction({ ...base, run, setError, refetch: vi.fn() });

    expect(ok).toBe(true);
    expect(setError).toHaveBeenCalledWith(null);
  });

  test("treats a missing root payload as failure, not silent success", async () => {
    const setError = vi.fn();
    const run = vi.fn(async () => ({})); // the mutation's root field is absent

    const ok = await runDaemonAction({ ...base, run, setError, refetch: vi.fn() });

    expect(ok).toBe(false);
    expect(setError).toHaveBeenLastCalledWith("Start service returned no result.");
  });

  test("surfaces a thrown Error and still refetches in finally", async () => {
    const setError = vi.fn();
    const refetch = vi.fn();
    const run = vi.fn(async () => {
      throw new Error("network down");
    });

    const ok = await runDaemonAction({ ...base, run, setError, refetch });

    expect(ok).toBe(false);
    expect(setError).toHaveBeenLastCalledWith("network down");
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  test("uses a label fallback when a non-Error is thrown", async () => {
    const setError = vi.fn();
    const run = vi.fn(async () => {
      throw "boom";
    });

    const ok = await runDaemonAction({ ...base, run, setError, refetch: vi.fn() });

    expect(ok).toBe(false);
    expect(setError).toHaveBeenLastCalledWith("Start service failed.");
  });
});
