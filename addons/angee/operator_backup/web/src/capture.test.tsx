// @vitest-environment happy-dom
import { renderHook, act, cleanup } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useBackupCapture } from "./capture";
import { defaultBackupSelection } from "./selection";
const mocks = vi.hoisted(() => ({ mutate: vi.fn(), sequence: 0 }));
vi.mock("@angee/refine", async (original) => ({ ...await original<typeof import("@angee/refine")>(),
  useAuthoredMutation: () => [mocks.mutate, { fetching: false }],
}));
vi.mock("@angee/ui", async (original) => ({ ...await original<typeof import("@angee/ui")>(),
  createClientKey: () => `backup-${++mocks.sequence}`,
  useActionResultRun: () => async (fire: () => Promise<unknown>) => { try { return await fire(); } catch { return undefined; } },
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
test("capture retains its native request key after ambiguous failure and clears it after acceptance", async () => {
  const selection = defaultBackupSelection();
  mocks.mutate.mockRejectedValueOnce(new Error("Disconnected")).mockResolvedValue({ run_backup_plan: { ok: true, message: "Started", id: "run_one", validation_errors: null } });
  const hook = renderHook(useBackupCapture);
  await act(async () => { expect(await hook.result.current.run("bpl_one", selection)).toBe(false); });
  const first = mocks.mutate.mock.calls[0]?.[0];
  hook.rerender();
  await act(async () => { expect(await hook.result.current.run("bpl_one", selection)).toBe(true); });
  expect(mocks.mutate.mock.calls[1]?.[0]).toEqual(first);
  await act(async () => { await hook.result.current.run("bpl_one", selection); });
  expect(mocks.mutate.mock.calls[2]?.[0].requestKey).not.toBe(first.requestKey);
  expect(first.expectedSelection).toEqual(selection);
});

test("a different accepted selection gets a new key while its ambiguous retry keeps that key", async () => {
  const first = defaultBackupSelection();
  const changed = defaultBackupSelection(); changed.storage.mode = "none";
  mocks.mutate.mockRejectedValue(new Error("Disconnected"));
  const hook = renderHook(useBackupCapture);
  await act(async () => { await hook.result.current.run("bpl_one", first); });
  await act(async () => { await hook.result.current.run("bpl_one", changed); });
  await act(async () => { await hook.result.current.run("bpl_one", changed); });
  const [a, b, retry] = mocks.mutate.mock.calls.map(([variables]) => variables);
  expect(a.requestKey).not.toBe(b.requestKey);
  expect(retry).toEqual(b);
  expect(retry.expectedSelection).toEqual(changed);
});
