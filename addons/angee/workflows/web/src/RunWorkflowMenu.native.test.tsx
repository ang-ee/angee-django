// @vitest-environment happy-dom

import { ModelMetadataProvider, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  attempt: 0,
  start: vi.fn(async () => ({ start_workflow_run: { ok: true, message: "Started.", id: "run_1" } })),
  settle: vi.fn(async (fire: () => Promise<unknown>) => {
    await fire();
    mocks.attempt += 1;
    return mocks.attempt === 1
      ? { ok: false, message: "Cannot start yet.", validationErrors: null }
      : { ok: true, message: "Started.", id: "run_1", validationErrors: null };
  }),
}));

vi.mock("./documents.console", () => ({
  RunWorkflowDocument: "Run",
  WorkflowLaunchDocument: "Launch",
  WorkflowsForSubjectDeclarationDocument: "Catalogue",
}));

vi.mock("../../../../../packages/ui/src/views/relation/relation-options", () => ({
  useRelationOptions: () => ({
    list: { fetching: false, refetch: vi.fn() },
    options: [{ value: "drive_7", label: "Backup drive" }],
    rows: [{ id: "drive_7", name: "Backup drive" }],
  }),
}));

import { CurrentWorkflowLaunch } from "./RunWorkflowMenu";

const driveResource = testDataResource("storage.Drive", {
  modelName: "Drive",
  roots: { list: "storage_drives", detail: "storage_drives_by_pk" },
  typeNames: { node: "DriveType" },
  recordRepresentation: "name",
  capabilities: ["list", "detail"],
  fields: [
    { name: "id", kind: "scalar", scalar: "ID", readable: true, filterable: false, sortable: false, aggregatable: false, groupable: false, creatable: false, updatable: false, requiredOnCreate: false },
    { name: "name", kind: "scalar", scalar: "String", readable: true, filterable: true, sortable: true, aggregatable: false, groupable: false, creatable: false, updatable: false, requiredOnCreate: false },
  ],
});

describe("RunWorkflowMenu native launch setup", () => {
  afterEach(cleanup);
  beforeEach(() => { mocks.attempt = 0; mocks.start.mockClear(); mocks.settle.mockClear(); });

  test("requires a subject, retains it after failure, and closes after success", async () => {
    render(
      <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([driveResource])}>
        <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <CurrentWorkflowLaunch
            workflow={{ id: "draft_1", purpose: "AUTOMATION", status: "DRAFT", version: 0, published_from: null, current_published_id: "published_2", current_published_version: 2, current_published_subject_declaration: "storage.drive" } as never}
            loading={false}
            startState={{ fetching: false }}
            startWorkflow={mocks.start as never}
            settle={mocks.settle as never}
          />
        </AppRuntimeProvider>
      </ModelMetadataProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Run published…" }));
    const submit = screen.getByRole("button", { name: "Run published" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /Record/ }));
    fireEvent.click(await screen.findByText("Backup drive"));
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);

    await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(1));
    expect(mocks.start).toHaveBeenLastCalledWith({
      workflow: "published_2",
      subject: { subject_declaration: "storage.drive", id: "drive_7" },
    });
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Record: Backup drive/ })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Run published" }));
    await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
