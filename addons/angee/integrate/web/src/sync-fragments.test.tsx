// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({ href: vi.fn<(resource: string, id: string) => string | undefined>() }));
vi.mock("@angee/ui", async () => {
  const actual = await vi.importActual<typeof import("@angee/ui")>("@angee/ui");
  return {
    ...actual,
    JsonValueView: ({ value }: { value: unknown }) => <pre>{JSON.stringify(value)}</pre>,
    useResourceRecordHrefLookup: () => mocks.href,
  };
});

import { integrationSyncProgressWidget } from "./sync-fragments";

const SyncProgress = integrationSyncProgressWidget.read;
afterEach(cleanup);
beforeEach(() => mocks.href.mockReset());

test("links bridge progress to the composed workflow record route", () => {
  mocks.href.mockReturnValue("/workflows/runs/run_sqid");
  render(<SyncProgress value={{ details: { run: "run_sqid" }, items: 3 }} />);
  expect(mocks.href).toHaveBeenCalledWith("workflows.WorkflowRun", "run_sqid");
  expect(screen.getByRole("link", { name: "View sync run" }).getAttribute("href"))
    .toBe("/workflows/runs/run_sqid");
  expect(screen.getByText('{"details":{"run":"run_sqid"},"items":3}')).toBeTruthy();
});

test("keeps progress visible when the workflow addon is absent", () => {
  render(<SyncProgress value={{ details: { run: "run_sqid" } }} />);
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByText('{"details":{"run":"run_sqid"}}')).toBeTruthy();
});

test.each([null, {}, { details: null }, { details: { run: 12 } }])(
  "does not invent run identity from malformed progress %j",
  (value) => {
    render(<SyncProgress value={value} />);
    expect(mocks.href).not.toHaveBeenCalled();
    expect(screen.queryByRole("link")).toBeNull();
  },
);
