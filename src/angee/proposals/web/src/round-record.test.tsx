// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { ActionDescriptor } from "@angee/ui";
import { holdsPermission, type Row } from "@angee/metadata";

import { ProjectRoundRecord } from "./round-record";

const mocks = vi.hoisted(() => ({ mutate: vi.fn(), round: {
  id: "round-1", name: "Review", status: "COLLECTING", opening_policy: "ANSWERS",
  permissions: ["write"], can_open: true, can_admit: true, revision: 4, roster: [],
} }));
vi.mock("@angee/ui", async (original) => ({ ...await original<object>(),
  useRecordChromeContext: () => ({ recordId: "project-1", dataProviderName: "console", record: { active_proposal_round: mocks.round } }),
  useAuthoredResourceMutation: () => [mocks.mutate, {}],
  useActionResultRun: () => (fire: () => Promise<unknown>) => fire(),
  RecordActionBar: ({ record, actions }: { record: Row; actions: readonly ActionDescriptor[] }) => <>
    {actions.filter((action) => !action.visibleWhen || action.visibleWhen(record)).map((action) =>
      <button key={action.id} onClick={() => action.run?.({ record, values: {}, refresh: vi.fn(), update: vi.fn(), prompt: vi.fn() })}>
        {action.label}
      </button>)}
  </>,
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); mocks.round.can_open = true; });

test("project record verbs target the returned round and its revision", async () => {
  render(<ProjectRoundRecord surface="primary" />);
  fireEvent.click(screen.getByRole("button", { name: "Open round" }));
  await waitFor(() => expect(mocks.mutate).toHaveBeenCalledExactlyOnceWith({ round: "round-1", revision: 4 }));
});

test("open remains hidden when the server predicate fails", () => {
  mocks.round.can_open = false;
  render(<ProjectRoundRecord surface="primary" />);
  expect(screen.queryByRole("button")).toBeNull();
  expect(holdsPermission({ permissions: ["write"] }, "write")).toBe(true);
  expect(holdsPermission({ permissions: [] }, "write")).toBe(false);
});
