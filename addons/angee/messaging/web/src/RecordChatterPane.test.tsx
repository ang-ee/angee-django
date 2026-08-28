// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { ChatterViewContext } from "@angee/ui/runtime";
import type { RecordMessageRow } from "./documents";

const mocks = vi.hoisted(() => ({
  threadData: undefined as unknown,
  recipientData: { colleagues: [] as unknown[] } as unknown,
  mutateCalls: [] as Array<{ op: string; vars: Record<string, unknown> }>,
  useAuthoredQuery: vi.fn(),
}));

function operationName(document: unknown): string {
  const definitions = (document as { definitions?: Array<{ name?: { value?: string } }> })
    .definitions;
  return definitions?.[0]?.name?.value ?? "";
}

function interpolate(template: string, vars?: Record<string, unknown>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (_match, key: string) =>
    key in vars ? String(vars[key]) : `{${key}}`,
  );
}

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useNamespaceT:
      (_namespace: string, messages: Record<string, string>) =>
      (key: string, vars?: Record<string, unknown>) =>
        interpolate(messages[key] ?? key, vars),
  };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: mocks.useAuthoredQuery,
  useAuthoredMutation: (document: unknown) => {
    const op = operationName(document);
    const mutate = vi.fn(async (vars: Record<string, unknown>) => {
      mocks.mutateCalls.push({ op, vars });
      return {};
    });
    return [mutate, { fetching: false }];
  },
}));

vi.mock("@angee/storage", () => ({
  useStorageUpload: () => ({ tasks: [], upload: vi.fn(), clearFinished: vi.fn() }),
}));

import { RecordChatterPane } from "./RecordChatterPane";

function message(overrides: Partial<RecordMessageRow> = {}): RecordMessageRow {
  return {
    id: "msg_1",
    title: "",
    preview: "Hello there",
    direction: "INTERNAL",
    status: "SENT",
    starred: false,
    needaction: false,
    message_type: "COMMENT",
    can_edit: false,
    can_delete: false,
    sender: { id: "hdl_1", display_name: "Ada Lovelace", value: "ada@example.com" },
    parent: null,
    subtype: null,
    sent_at: "2026-06-27T00:00:00Z",
    created_at: "2026-06-27T00:00:00Z",
    reaction_groups: [],
    tracking_values: [],
    parts: [{ role: "user", fragment: { text: "Hello there" }, file: null }],
    ...overrides,
  } as unknown as RecordMessageRow;
}

function threadPayload(messages: RecordMessageRow[]): unknown {
  return {
    record_thread: {
      error: null,
      error_code: null,
      thread: { id: "thr_1", title: { text: "Rec" }, message_count: messages.length, last_message_at: null },
      message_result_count: messages.length,
      messages,
      follower_count: 3,
      is_following: false,
      self_follower: null,
      suggested_recipients: [],
      subtypes: [],
      unread_count: 0,
      needaction_count: 0,
      message_has_error: false,
      message_has_error_counter: 0,
      attachment_count: 0,
      notifications: [],
      followers: [],
      activity_count: 0,
      activities: [],
    },
  };
}

const context: ChatterViewContext = {
  pathname: "/notes/note/nte_1",
  params: { id: "nte_1" },
  route: { name: "notes.note.record", path: "/notes/note/$id", viewType: "notes/note", modelLabel: "notes/note" },
  view: { kind: "record", type: "notes/note", sqid: "nte_1" },
};

beforeEach(() => {
  mocks.mutateCalls = [];
  mocks.recipientData = { colleagues: [] };
  mocks.useAuthoredQuery.mockReset();
  mocks.useAuthoredQuery.mockImplementation((document: unknown) => {
    const op = operationName(document);
    if (op === "MessagingRecordThread") {
      return { data: mocks.threadData, fetching: false, error: null, refetch: vi.fn() };
    }
    if (op === "MessagingRecipientUsers") {
      return { data: mocks.recipientData, fetching: false, error: null, refetch: vi.fn() };
    }
    throw new Error(`Unexpected authored query: ${op}`);
  });
});

afterEach(cleanup);

describe("RecordChatterPane", () => {
  test("renders the feed with author and the composer", () => {
    mocks.threadData = threadPayload([message()]);

    render(<RecordChatterPane context={context} />);

    expect(screen.getByText("Hello there")).toBeTruthy();
    expect(screen.getByText("Ada Lovelace")).toBeTruthy();
    // Server-resolved follower count, interpolated through the namespace.
    expect(screen.getByText(/3 following/)).toBeTruthy();
    // The composer send affordance.
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Follow" })).toBeTruthy();
  });

  test("gates edit/delete on the server can_edit/can_delete flags", () => {
    mocks.threadData = threadPayload([
      message({ id: "msg_editable", preview: "Editable", can_edit: true, can_delete: true, parts: [{ role: "user", fragment: { text: "Editable" }, file: null }] as never }),
      message({ id: "msg_locked", preview: "Locked", can_edit: false, can_delete: false, parts: [{ role: "user", fragment: { text: "Locked" }, file: null }] as never }),
    ]);

    render(<RecordChatterPane context={context} />);

    // Exactly one editable + one deletable message — the flags, not a client heuristic.
    expect(screen.getAllByRole("button", { name: "Edit comment" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Delete message" })).toHaveLength(1);
  });

  test("renders existing reactions with an accessible pill name", () => {
    mocks.threadData = threadPayload([
      message({
        reaction_groups: [
          { reaction: "👍", count: 2, self_reacted: true, handles: [] },
        ] as never,
      }),
    ]);

    render(<RecordChatterPane context={context} />);

    expect(screen.getByRole("button", { name: "👍 reaction, 2" })).toBeTruthy();
  });

  test("deletes through the mutation keyed by message id", () => {
    mocks.threadData = threadPayload([
      message({ id: "msg_editable", can_edit: true, can_delete: true }),
    ]);

    render(<RecordChatterPane context={context} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete message" }));

    expect(
      mocks.mutateCalls.some(
        (call) =>
          call.op === "MessagingDeleteRecordMessage" && call.vars.messageId === "msg_editable",
      ),
    ).toBe(true);
  });

  test("shows the not-enabled state for a record without a thread", () => {
    mocks.threadData = { record_thread: { error_code: "BAD_RECORD" } };

    render(<RecordChatterPane context={context} />);

    expect(screen.getByText("Comments are not enabled")).toBeTruthy();
  });
});
