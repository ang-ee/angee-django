// @vitest-environment happy-dom

import { cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import type { DataResourceFieldMetadata } from "@angee/metadata";
import { afterEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  prompt: vi.fn(),
  mutate: vi.fn(),
  listOptions: null as unknown,
  rows: [] as Record<string, unknown>[],
}));

vi.mock("../../feedback", () => ({
  usePrompt: () => mocks.prompt,
}));

vi.mock("./authored-resource-mutation", () => ({
  useAuthoredResourceMutation: (document: unknown) => [
    (variables: unknown) => mocks.mutate(document, variables),
    { fetching: false, error: null },
  ],
}));

vi.mock("./action-result-run", () => ({
  useActionResultRun: () => async (fire: () => Promise<unknown>) => await fire(),
}));

vi.mock("@refinedev/core", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@refinedev/core")>()),
  useList: (options: unknown) => {
    mocks.listOptions = options;
    return { result: { data: mocks.rows, total: mocks.rows.length }, query: { error: null } };
  },
}));

import { RestoreRecord, TrashRecord } from "./documents";
import { RemovedRecords, useTrashRecord, useTrashRowActions } from "./trash";

const flag: DataResourceFieldMetadata = {
  name: "is_trashed",
  kind: "scalar",
  scalar: "Boolean",
  readable: true,
  aggregatable: false,
  creatable: false,
  updatable: false,
  requiredOnCreate: false,
  trashable: true,
};
const PAGES = testDataResource("knowledge.Page", { resourceType: "knowledge/page", fields: [flag] });
const TAGS = testDataResource("tags.Tag", { resourceType: "tags/tag", fields: [] });

describe("shared trash verbs", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  test("the removed list reads trashed rows and offers Restore only to their managers", async () => {
    mocks.rows = [
      { id: "pg_1", title: "Plan", permissions: ["delete"], trash_reason: "Superseded", trashed_by_label: "Ada" },
      { id: "pg_2", title: "Draft", permissions: ["write"], trash_reason: "", trashed_by_label: null },
    ];
    mocks.mutate.mockResolvedValue({ restore_record: { ok: true, message: "Restored from the trash." } });
    render(<RemovedRecords resource={PAGES} label={(row) => String(row.title)} fields={["title"]} />);

    expect(mocks.listOptions).toMatchObject({
      resource: "pages",
      filters: [{ field: "is_trashed", operator: "eq", value: true }],
    });
    fireEvent.click(screen.getByRole("button", { name: "Removed (2)" }));
    expect(screen.getByText("Reason: Superseded")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Restore" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(mocks.mutate).toHaveBeenCalledWith(RestoreRecord, {
      target_type: "knowledge/page", target_id: "pg_1",
    }));
  });

  test("an untrashable resource renders no removed list", () => {
    mocks.rows = [{ id: "tag_1", title: "Gone", permissions: ["delete"] }];
    const { container } = render(<RemovedRecords resource={TAGS} label={(row) => String(row.title)} />);
    expect(container.textContent).toBe("");
  });

  test("trash asks for an optional reason naming the record, then runs the shared verb", async () => {
    mocks.prompt.mockResolvedValue({ reason: "  Superseded  " });
    mocks.mutate.mockResolvedValue({ trash_record: { ok: true, message: "Moved to the trash." } });
    const { result } = renderHook(() => useTrashRecord(PAGES));

    await expect(result.current.trash("pg_1", "Plan")).resolves.toBe(true);
    expect(mocks.prompt).toHaveBeenCalledWith(expect.objectContaining({
      title: "Move “Plan” to the trash?",
      cancel: "Keep “Plan”",
      danger: true,
      fields: [expect.objectContaining({ name: "reason", type: "textarea", maxLength: 1000 })],
    }));
    expect(mocks.mutate).toHaveBeenCalledWith(TrashRecord, {
      target_type: "knowledge/page", target_id: "pg_1", reason: "Superseded",
    });
  });

  test("keeping the record runs nothing", async () => {
    mocks.prompt.mockResolvedValue(null);
    const { result } = renderHook(() => useTrashRecord(PAGES));

    await expect(result.current.trash("pg_1")).resolves.toBe(false);
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  test("restore runs the shared verb without a reason and reports a refusal", async () => {
    mocks.mutate.mockResolvedValue({ restore_record: { ok: false, message: "Restoring the record failed." } });
    const { result } = renderHook(() => useTrashRecord(PAGES));

    await expect(result.current.restore("pg_1")).resolves.toBe(false);
    expect(mocks.mutate).toHaveBeenCalledWith(RestoreRecord, { target_type: "knowledge/page", target_id: "pg_1" });
  });

  test("an untrashable resource offers no verbs", () => {
    const { result } = renderHook(() => ({ record: useTrashRecord(TAGS), rows: useTrashRowActions(TAGS) }));

    expect(result.current.record.available).toBe(false);
    expect(result.current.rows).toEqual([]);
  });

  test("row verbs follow the delete permission and the trash flag", () => {
    const { result } = renderHook(() => useTrashRowActions(PAGES));
    const [trash, restore] = result.current;
    const live = { id: "pg_1", is_trashed: false, permissions: ["delete"] };
    const removed = { id: "pg_2", is_trashed: true, permissions: ["delete"] };
    const authored = { id: "pg_3", is_trashed: false, permissions: ["write"] };

    expect([trash!.visible(live), restore!.visible(live)]).toEqual([true, false]);
    expect([trash!.visible(removed), restore!.visible(removed)]).toEqual([false, true]);
    expect([trash!.visible(authored), restore!.visible(authored)]).toEqual([false, false]);
  });
});
