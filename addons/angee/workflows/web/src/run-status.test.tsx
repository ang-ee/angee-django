// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { AppRuntimeProvider } from "@angee/ui";
import { afterEach, expect, test, vi } from "vitest";

import { runStatusColumn, runStatusWidget } from "./run-status";

const owner = vi.hoisted(() => ({ query: vi.fn(), invalidate: vi.fn() }));

vi.mock("@angee/refine", async (original) => ({
  ...await original<typeof import("@angee/refine")>(),
  useAuthoredQuery: owner.query,
}));
vi.mock("@refinedev/core", async (original) => ({
  ...await original<typeof import("@refinedev/core")>(),
  useInvalidate: () => owner.invalidate,
}));
vi.mock("@angee/metadata", async (original) => ({
  ...await original<typeof import("@angee/metadata")>(),
  useModelMetadata: () => ({ resource: { schemaName: "console", modelLabel: "storage.File", roots: { list: "files" } } }),
  refineResourceName: () => "files",
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
  // The first observed status is the row's current state, not a transition.
  expect(owner.invalidate).not.toHaveBeenCalled();
});

test("a run transition refetches the row's own resource", () => {
  const run = (status: string) => ({
    data: { record_timeline: { records: [{ record_model: "storage.File", record_id: "fil_7", runs: [{ id: "wfr_1", status }] }] } },
  });
  owner.query.mockReturnValue(run("RUNNING"));
  const Cell = runStatusWidget.cell!;
  const view = render(<AppRuntimeProvider runtime={{ statusTones: { RUNNING: "info", WAITING: "warning" } }}>
    <Cell row={{ id: "fil_7" }} />
  </AppRuntimeProvider>);
  expect(owner.invalidate).not.toHaveBeenCalled();

  owner.query.mockReturnValue(run("WAITING"));
  view.rerender(<AppRuntimeProvider runtime={{ statusTones: { RUNNING: "info", WAITING: "warning" } }}>
    <Cell row={{ id: "fil_7" }} />
  </AppRuntimeProvider>);
  expect(screen.getByText("Waiting")).toBeTruthy();
  expect(owner.invalidate).toHaveBeenCalledTimes(1);
  expect(owner.invalidate).toHaveBeenCalledWith({
    resource: "files",
    dataProviderName: "console",
    invalidates: ["list", "many", "detail"],
  });
});
