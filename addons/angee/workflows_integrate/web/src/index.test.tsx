// @vitest-environment happy-dom

import { expectValidBaseAddon } from "@angee/app/testing";
import { INTEGRATION_MODEL } from "@angee/integrate";
import { formViewRecordActionsSlot } from "@angee/ui";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  run: "run_sqid" as string | null,
  read: vi.fn(),
  href: vi.fn<(id: string) => string | undefined>(),
}));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/refine")>(),
  useAuthoredQuery: (_document: unknown, variables: unknown, options: unknown) => {
    mocks.read(variables, options);
    return { data: { integrations_by_pk: { sync_run: mocks.run } } };
  },
}));
vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  useRecordChromeContext: () => ({ recordId: "bridge_sqid", resource: "messaging.Channel", dataProviderName: "console", record: {} }),
  useResourceRecordHref: () => mocks.href,
}));

import workflowsIntegrate, { IntegrationSyncRunLink } from "./index";

afterEach(cleanup);
beforeEach(() => {
  mocks.run = "run_sqid";
  mocks.href.mockReset().mockReturnValue("/workflows/runs/run_sqid");
  mocks.read.mockClear();
});

test("contributes the run link through the Integration card's existing action slot", () => {
  expectValidBaseAddon(workflowsIntegrate);
  expect(workflowsIntegrate.slots).toEqual([expect.objectContaining({
    ...formViewRecordActionsSlot(INTEGRATION_MODEL),
    id: "workflows-integrate.sync-run",
  })]);
});

test("reads its declared workflow identity even when the child form omits telemetry", () => {
  render(<IntegrationSyncRunLink />);
  expect(mocks.read).toHaveBeenCalledWith({ id: "bridge_sqid" }, {
    dataProviderName: "console", models: [INTEGRATION_MODEL, "messaging.Channel"],
  });
  expect(mocks.href).toHaveBeenCalledWith("run_sqid");
  expect(screen.getByRole("link", { name: "View sync run" }).getAttribute("href"))
    .toBe("/workflows/runs/run_sqid");
});

test("hides the run link when no run is dispatched", () => {
  mocks.run = null;
  render(<IntegrationSyncRunLink />);
  expect(mocks.href).not.toHaveBeenCalled();
  expect(screen.queryByRole("link")).toBeNull();
});

test("hides the run link when the host has no workflow record route", () => {
  mocks.href.mockReturnValue(undefined);
  render(<IntegrationSyncRunLink />);
  expect(screen.queryByRole("link")).toBeNull();
});
