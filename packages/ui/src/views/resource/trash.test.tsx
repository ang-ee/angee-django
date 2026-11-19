// @vitest-environment happy-dom

import { cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import type { DataResourceFieldMetadata } from "@angee/metadata";
import { afterEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  prompt: vi.fn(),
  mutate: vi.fn(),
  mutationOptions: vi.fn(),
  listOptions: null as unknown,
  rows: [] as Record<string, unknown>[],
}));

vi.mock("../../feedback", () => ({
  usePrompt: () => mocks.prompt,
}));

vi.mock("./authored-resource-mutation", () => ({
  useAuthoredResourceMutation: (document: unknown, options: unknown) => {
    mocks.mutationOptions(document, options);
    return [
      (variables: unknown) => mocks.mutate(document, variables),
      { fetching: false, error: null },
    ];
  },
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
import { RemovedList, RemovedRecords, useTrashActions, useTrashRecord, useTrashRowActions } from "./trash";

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
const permissionField: DataResourceFieldMetadata = {
  name: "permissions", kind: "list", scalar: "String", readable: true, aggregatable: false,
  creatable: false, updatable: false, requiredOnCreate: false,
};
const PAGES = testDataResource("knowledge.Page", { resourceType: "knowledge/page", fields: [flag, permissionField] });
const UNPROJECTED_PAGES = { ...PAGES, fields: [flag] };
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

  test("a domain read's removed rows restore through the domain's own verb", async () => {
    const restore = vi.fn(async () => undefined);
    render(<RemovedList
      rows={[{ id: "tsk_1", title: "Ship it", removed_by_label: "Ada", removal_reason: "Duplicate" }]}
      label={(row) => String(row.title)}
      stamps={(row) => ({ trashedByLabel: String(row.removed_by_label), trashReason: String(row.removal_reason) })}
      canRestore={() => true}
      restore={restore}
    />);
    fireEvent.click(screen.getByRole("button", { name: "Removed (1)" }));
    expect(screen.getByText("Reason: Duplicate")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(restore).toHaveBeenCalledWith(expect.objectContaining({ id: "tsk_1" })));
    expect(mocks.mutate).not.toHaveBeenCalled();
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
    const { result } = renderHook(() => ({ record: useTrashRecord(TAGS), actions: useTrashActions(TAGS), rows: useTrashRowActions(TAGS) }));

    expect(result.current.record.available).toBe(false);
    expect(result.current.actions).toEqual([]);
    expect(result.current.rows).toEqual([]);
    expect(mocks.mutationOptions.mock.calls.every(([, options]) => Object.keys(options).length === 0)).toBe(true);
  });

  test("record trash needs a resource type as well as the flag", () => {
    const { result } = renderHook(() => useTrashActions({ ...PAGES, resourceType: undefined }));
    expect(result.current).toEqual([]);
    expect(mocks.mutationOptions).toHaveBeenCalledWith(TrashRecord, {});
    expect(mocks.mutationOptions).toHaveBeenCalledWith(RestoreRecord, {});
  });

  test("record verbs stay in the menu and require delete when the model declares permissions", () => {
    const { result } = renderHook(() => useTrashActions(PAGES));
    const [trash, restore] = result.current;
    expect(result.current.map((action) => action.placement)).toEqual(["menu", "menu"]);
    for (const is_trashed of [undefined, null, false, true]) {
      const record = { id: "pg_1", is_trashed };
      for (const permissions of [[], ["write"], null, ["delete"]]) {
        const row = { ...record, permissions };
        const manages = permissions?.includes("delete") ?? false;
        expect([trash!.visibleWhen!(row), restore!.visibleWhen!(row)]).toEqual([
          manages && is_trashed === false, manages && is_trashed === true,
        ]);
      }
      expect([trash!.visibleWhen!(record), restore!.visibleWhen!(record)]).toEqual([false, false]);
    }
  });

  test.each([false, true])("row and record verbs share the declared permission contract (%s)", (declaresPermissions) => {
    const resource = declaresPermissions ? PAGES : UNPROJECTED_PAGES;
    const { result } = renderHook(() => ({ rows: useTrashRowActions(resource), record: useTrashActions(resource) }));
    for (const is_trashed of [undefined, null, false, true]) {
      for (const permissions of [undefined, null, [], ["write"], ["delete"]]) {
        const row = { id: "pg_1", is_trashed, ...(permissions === undefined ? {} : { permissions }) };
        const manages = !declaresPermissions || Boolean(permissions?.includes("delete"));
        const expected = [manages && is_trashed === false, manages && is_trashed === true];
        expect(result.current.rows.map((action) => action.visible(row))).toEqual(expected);
        expect(result.current.record.map((action) => action.visibleWhen!(row))).toEqual(expected);
      }
    }
    const unselected = { id: "pg_1", permissions: ["delete"] };
    expect(result.current.rows.map((action) => action.visible(unselected))).toEqual([false, false]);
    expect(result.current.record.map((action) => action.visibleWhen!(unselected))).toEqual([false, false]);
  });

  test.each([false, true])("removed rows use the model's permission contract, including omitted projections (%s)", (declaresPermissions) => {
    const rows = [
      { id: "pg_1", title: "Missing" }, { id: "pg_2", title: "Writer", permissions: ["write"] },
      { id: "pg_3", title: "Manager", permissions: ["delete"] },
    ];
    render(<RemovedList resource={declaresPermissions ? PAGES : UNPROJECTED_PAGES}
      rows={rows} label={(row) => row.title} restore={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Removed (3)" }));
    expect(screen.getAllByRole("button", { name: "Restore" })).toHaveLength(declaresPermissions ? 1 : 3);
  });

  test("a removed-record read does not select undeclared permissions and still offers Restore", () => {
    mocks.rows = [{ id: "pg_1", title: "Plan" }];
    render(<RemovedRecords resource={UNPROJECTED_PAGES} label={(row) => String(row.title)} />);
    expect(mocks.listOptions).toMatchObject({ meta: { fields: ["id", "trashed_at", "trash_reason", "trashed_by_label"] } });
    fireEvent.click(screen.getByRole("button", { name: "Removed (1)" }));
    expect(screen.getByRole("button", { name: "Restore" })).toBeTruthy();
  });
});
