// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { AppRuntimeProvider } from "@angee/ui";
import { afterEach, expect, test, vi } from "vitest";

import { runStatusColumn, runStatusWidget } from "./run-status";

const owner = vi.hoisted(() => ({ query: vi.fn() }));

vi.mock("@angee/refine", async (original) => ({
  ...await original<typeof import("@angee/refine")>(),
  useAuthoredQuery: owner.query,
}));
vi.mock("@angee/ui", async (original) => ({
  ...await original<typeof import("@angee/ui")>(),
  useResourceViewMaybe: () => ({ resource: "storage.File" }),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("the shared run-status column is render-only and selects the record identity", () => {
  expect(runStatusColumn()).toMatchObject({
    field: "workflow_run",
    selectionPaths: ["id"],
    sortable: false,
    widget: "runStatus",
  });
});

test("the run-status widget reads the latest run for its resource record", () => {
  owner.query.mockReturnValue({
    data: {
      record_timeline: {
        records: [{ record_model: "storage.File", record_id: "fil_7", runs: [{ id: "wfr_1", status: "RUNNING" }] }],
      },
    },
  });
  const Cell = runStatusWidget.cell!;
  render(<AppRuntimeProvider runtime={{ statusTones: { RUNNING: "info" } }}>
    <Cell row={{ id: "fil_7" }} />
  </AppRuntimeProvider>);

  expect(screen.getByText("Running")).toBeTruthy();
  expect(owner.query.mock.calls[0]?.[1]).toEqual({ records: [{ model: "storage.File", id: "fil_7" }] });
  expect(owner.query.mock.calls[0]?.[2]).toMatchObject({ enabled: true });
});
