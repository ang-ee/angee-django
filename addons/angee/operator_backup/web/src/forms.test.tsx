// @vitest-environment happy-dom
import type { ReactNode } from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ActionContext, UseRecordActionOptions } from "@angee/ui";
import { backupPlanForm } from "./forms";

const calls = vi.hoisted(() => ({ options: [] as UseRecordActionOptions[] }));
vi.mock("@angee/ui", async (original) => ({
  ...await original<typeof import("@angee/ui")>(),
  FormView: ({ children }: { children?: ReactNode }) => children,
  useRecordActionMutation: (_field: string, options: UseRecordActionOptions) => {
    calls.options.push(options); return [vi.fn(), { fetching: false, error: null }];
  },
}));
beforeEach(() => { calls.options.length = 0; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("a plan retains its request identity across ambiguous retries and rerenders until success", async () => {
  // Exercise createClientKey's supported non-secure browser path as well.
  vi.stubGlobal("crypto", { getRandomValues: (bytes: Uint8Array) => { bytes.fill(7); return bytes; } });
  const Form = backupPlanForm.Component;
  const view = render(<Form resource="operator_backup.BackupPlan" id="bpl_one" />);
  const first = calls.options.at(-1)!;
  const key = first.actionArguments!("bpl_one");
  expect(key).toEqual({ request_key: expect.stringMatching(/^backup-/) });
  // An ambiguous transport failure does not reach afterSuccess.
  view.rerender(<Form resource="operator_backup.BackupPlan" id="bpl_one" />);
  const retry = calls.options.at(-1)!;
  expect(retry.actionArguments!("bpl_one")).toEqual(key);
  expect(retry.actionArguments!("bpl_two")).toEqual({ request_key: expect.any(String) });
  const context: ActionContext = { record: { id: "bpl_one" }, values: {}, refresh: vi.fn(),
    update: vi.fn(async () => null), prompt: vi.fn(async () => null) };
  await retry.afterSuccess!(context, undefined);
  // A deterministic crypto fixture needs new bytes to make the reset observable.
  vi.stubGlobal("crypto", { getRandomValues: (bytes: Uint8Array) => { bytes.fill(8); return bytes; } });
  expect(retry.actionArguments!("bpl_one")).not.toEqual(key);
  expect(retry.actionArguments!("bpl_two")).toEqual(key);
});
