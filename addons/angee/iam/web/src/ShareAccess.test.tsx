// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  models: new Map<string, unknown>(),
  record: {
    resource: "notes.Note",
    canonicalResource: "notes.Note",
    dataProviderName: "console",
    recordId: "note-1",
    record: { id: "note-1", title: "Welcome", permissions: ["share"] },
  },
  list: {
    resource: "notes.Note",
    fields: [] as readonly string[],
    refresh: vi.fn(),
    selectedIds: new Set<string>(),
    selectable: true,
    record: null as unknown,
  },
  query: {
    data: {
      record_access: [] as Array<{ target_id: string; relation: string; subject: string; subject_type: string; label: string }>,
      record_access_options: [{ relation: "reader", permission: "share" }],
    },
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  },
  readerQuery: {
    data: { record_readers: [{ subject: "auth/user:ada", label: "Ada", you: true, following: true }] },
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  },
  queryVariables: [] as unknown[],
  queryOptions: null as unknown,
  readerOptions: null as unknown,
  dialogProps: null as Record<string, unknown> | null,
  directSlot: true,
  roleEntries: [] as { id: string; content: unknown }[],
  visibilityEntries: [] as { id: string; content: unknown }[],
}));

vi.mock("./documents", () => ({ RecordAccessDocument: { kind: "access" }, RecordReadersDocument: { kind: "readers" } }));
vi.mock("./i18n", () => ({ useIamT: () => (key: string) => key }));

vi.mock("@angee/metadata", () => ({
  holdsPermission: (record: { permissions?: readonly string[] }, permission: string) => record.permissions?.includes(permission) ?? false,
  modelLabelSegment: (label: string) => label.slice(label.lastIndexOf(".") + 1),
  rowValueAtPath: (row: Record<string, unknown>, path: string) => row[path],
  useModelMetadata: (resource: string) => mocks.models.get(resource),
  useResourceInvalidates: () => [],
}));

vi.mock("@angee/refine", () => ({
  useActionMutation: () => [vi.fn()],
  useActionResultRun: () => async (run: () => unknown) => run(),
  useAuthoredQuery: (document: { kind: string }, variables: unknown, options: unknown) => {
    mocks.queryVariables.push(variables);
    if (document.kind === "readers") {
      mocks.readerOptions = options;
      return mocks.readerQuery;
    }
    mocks.queryOptions = options;
    return mocks.query;
  },
  useStableArray: (value: readonly string[]) => value,
}));

vi.mock("@angee/ui", () => ({
  titleCase: (value: string) => value.replace(/[-_./:]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()),
  FormView: { RailGroup: () => null },
  ManageAccessDialog: (props: Record<string, unknown>) => {
    mocks.dialogProps = props;
    return <button
      type="button"
      onClick={() => (props.onOpenChange as (open: boolean) => void)(true)}
    >Open access dialog</button>;
  },
  useActionResultRun: () => async (run: () => unknown) => run(),
  useRecordChromeContext: () => mocks.record,
  useModelSlot: ({ slot }: { slot: string }) => slot === "access.roles" ? mocks.roleEntries : mocks.visibilityEntries,
  useResourceViewUtilityContext: () => mocks.list,
  useSlot: () => mocks.directSlot ? [{ id: "iam.direct" }] : [],
  useUiT: () => (key: string) => key,
}));

import { ShareAccessCompact, ShareAccessDialog, ShareListChrome, ShareRecordChrome, useAccessRole, useAccessVisibility } from "./ShareAccess";

const resource = {
  modelLabel: "notes.Note",
  resourceType: "notes/note",
  recordRepresentation: "title",
  grantable: [{
    relation: "reader",
    permission: "share",
    subjects: [{ type: "auth/user", relation: null, resource: "iam.User" }],
  }],
};
const taskResource = {
  ...resource,
  modelLabel: "projects.Task",
  resourceType: "projects/task",
};

describe("shared record access chrome", () => {
  beforeEach(() => {
    mocks.models = new Map([["notes.Note", { resource }]]);
    mocks.record = {
      resource: "notes.Note",
      canonicalResource: "notes.Note",
      dataProviderName: "console",
      recordId: "note-1",
      record: { id: "note-1", title: "Welcome", permissions: ["share"] },
    };
    mocks.list = {
      resource: "notes.Note",
      fields: [],
      refresh: vi.fn(),
      selectedIds: new Set(),
      selectable: true,
      record: null,
    };
    mocks.queryVariables = [];
    mocks.queryOptions = null;
    mocks.readerOptions = null;
    mocks.query.data.record_access = [];
    mocks.readerQuery.data.record_readers = [{ subject: "auth/user:ada", label: "Ada", you: true, following: true }];
    mocks.dialogProps = null;
    mocks.directSlot = true;
    mocks.roleEntries = [];
    mocks.visibilityEntries = [];
  });

  afterEach(cleanup);

  test("delegates the default Share trigger and loads access on demand", () => {
    render(<ShareRecordChrome />);

    const trigger = screen.getByRole("button", { name: "Open access dialog" });
    expect(mocks.dialogProps).toMatchObject({ label: "Welcome", targetIds: ["note-1"] });
    expect(mocks.dialogProps?.trigger).toBeUndefined();
    expect(mocks.queryOptions).toMatchObject({ enabled: false });
    expect(mocks.readerOptions).toMatchObject({ enabled: true });
    expect(mocks.dialogProps?.peopleLoaded).toBe(true);
    expect(mocks.dialogProps?.people).toMatchObject([{ label: "Ada", you: true, following: true }]);

    fireEvent.click(trigger);
    expect(mocks.queryOptions).toMatchObject({ enabled: true });
    expect(mocks.queryVariables).toContainEqual({ targetType: "notes/note", targetIds: ["note-1"] });
  });

  test("shares the selected collection records in deterministic order", () => {
    mocks.list.selectedIds = new Set(["note-2", "note-1"]);

    render(<ShareListChrome />);

    expect(mocks.dialogProps).toMatchObject({
      targetIds: ["note-1", "note-2"],
    });
    expect(mocks.dialogProps?.label).toBeUndefined();
  });

  test("omits Share when a collection has no selection", () => {
    mocks.list.selectable = false;
    render(<ShareListChrome />);
    expect(screen.queryByRole("button", { name: "Open access dialog" })).toBeNull();
  });

  test("opens the same access adapter from an external record action", () => {
    const onOpenChange = vi.fn();
    const { rerender } = render(<ShareAccessDialog
      resource="notes.Note" targetIds={["note-1"]} label="Review folder"
      open onOpenChange={onOpenChange} trigger={null}
    />);

    expect(mocks.queryOptions).toMatchObject({ enabled: true });
    expect(mocks.queryVariables).toContainEqual({ targetType: "notes/note", targetIds: ["note-1"] });
    expect(mocks.dialogProps).toMatchObject({ open: true, label: "Review folder", trigger: null, onOpenChange });

    rerender(<ShareAccessDialog
      resource="notes.Note" targetIds={["note-1"]}
      open={false} onOpenChange={onOpenChange} trigger={null}
    />);
    expect(mocks.queryOptions).toMatchObject({ enabled: false });
  });

  test("keeps the People surface while the app admits no direct share", () => {
    mocks.directSlot = false;
    render(<ShareRecordChrome />);
    expect(mocks.dialogProps?.directShare).toBe(false);
    expect(mocks.dialogProps?.grantable).toEqual([]);
  });

  test("omits Share when the record projection lacks share permission", () => {
    mocks.record.record = { id: "note-1", title: "Welcome", permissions: [] };
    render(<ShareRecordChrome />);
    expect(screen.queryByRole("button", { name: "Open access dialog" })).toBeNull();
  });

  test("keeps Share when the model does not project permissions", () => {
    mocks.record.record = { id: "note-1", title: "Welcome" } as typeof mocks.record.record;
    render(<ShareRecordChrome />);
    expect(screen.getByRole("button", { name: "Open access dialog" })).toBeTruthy();
  });

  test("uses the model's declared permission instead of a literal share", () => {
    mocks.models.set("notes.Note", { resource: { ...resource, grantable: [{
      ...resource.grantable[0], permission: "manage",
    }] } });
    mocks.record.record = { id: "note-1", title: "Welcome", permissions: ["manage"] };
    render(<ShareRecordChrome />);
    expect(screen.getByRole("button", { name: "Open access dialog" })).toBeTruthy();
  });

  test("uses the same dialog with compact rail presentation", () => {
    render(<ShareAccessCompact />);
    expect(mocks.dialogProps?.compact).toBe(true);
    expect(mocks.queryOptions).toMatchObject({ enabled: false });
    expect(mocks.readerOptions).toMatchObject({ enabled: true });
    expect(mocks.dialogProps?.people).toMatchObject([{ label: "Ada" }]);
  });

  test("projects the declared direct relation label without changing its id", () => {
    mocks.models.set("notes.Note", { resource: { ...resource, grantable: [{ ...resource.grantable[0], label: "Can read" }] } });
    mocks.query.data.record_access = [{ target_id: "note-1", relation: "reader", subject: "auth/user:ada",
      subject_type: "auth/user", label: "Ada" }];
    render(<ShareRecordChrome />);
    expect(mocks.dialogProps?.entries).toMatchObject([{ relation: "reader", relationLabel: "Can read" }]);
  });

  test("nested collections share their selected records", () => {
    mocks.list.resource = "projects.Task";
    mocks.list.selectedIds = new Set(["task-1"]);
    mocks.list.record = mocks.record;
    mocks.models.set("projects.Task", { resource: taskResource });

    render(<ShareListChrome />);

    expect(mocks.dialogProps).toMatchObject({ targetIds: ["task-1"] });
    expect(mocks.queryVariables).toContainEqual({
      targetType: "projects/task",
      targetIds: ["task-1"],
    });
  });

  test("omits Share when the model declares no grant surface", () => {
    mocks.models = new Map([["notes.Note", { resource: { ...resource, grantable: [] } }]]);

    render(<ShareRecordChrome />);

    expect(screen.queryByRole("button", { name: "Open access dialog" })).toBeNull();
  });

  test("loads a model-scoped role without direct grant relations", async () => {
    const role = { id: "round.responder", label: "Responder", subjectResource: "iam.User", offered: true };
    const state = { role, people: [], add: vi.fn(async () => true), remove: vi.fn(async () => undefined) };
    function ResponderRole() { useAccessRole(role.id, state); return null; }
    mocks.models = new Map([["notes.Note", { resource: { ...resource, grantable: [] } }]]);
    mocks.record.record = { id: "note-1", title: "Welcome", permissions: [] };
    mocks.roleEntries = [{ id: role.id, content: ResponderRole }];

    render(<ShareRecordChrome />);
    fireEvent.click(screen.getByRole("button", { name: "Open access dialog" }));
    await waitFor(() => expect(mocks.dialogProps?.roles).toEqual([role]));
  });

  test("loads a model-scoped visibility policy without direct grant relations", async () => {
    const state = { id: "round.opening", label: "Opening policy", value: "ANSWERS",
      consequence: "The round reveals answers.", actionLabel: "Open", onAct: vi.fn(async () => undefined) };
    function OpeningPolicy() { useAccessVisibility(state.id, state); return null; }
    mocks.models = new Map([["notes.Note", { resource: { ...resource, grantable: [] } }]]);
    mocks.record.record = { id: "note-1", title: "Welcome", permissions: [] };
    mocks.visibilityEntries = [{ id: state.id, content: OpeningPolicy }];

    render(<ShareRecordChrome />);
    fireEvent.click(screen.getByRole("button", { name: "Open access dialog" }));
    await waitFor(() => expect(mocks.dialogProps?.visibility).toEqual([state]));
  });
});
