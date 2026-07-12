// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { RecordMessageRow } from "./documents";
import type { RecordThreadConversationChrome } from "./RecordThreadConversation";

const mocks = vi.hoisted(() => ({
  threadData: undefined as unknown,
  threadError: null as unknown,
  recipientData: { colleagues: [] as unknown[] } as unknown,
  mutateCalls: [] as Array<{ op: string; vars: Record<string, unknown> }>,
  failOps: new Set<string>(),
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
      if (mocks.failOps.has(op)) throw new Error("Network down");
      return {};
    });
    return [mutate, { fetching: false }];
  },
}));

vi.mock("@angee/storage", () => ({
  useStorageUpload: () => ({ tasks: [], upload: vi.fn(), clearFinished: vi.fn() }),
}));

import { RecordThreadConversation } from "./RecordThreadConversation";

function message(overrides: Partial<RecordMessageRow> = {}): RecordMessageRow {
  return {
    id: "msg_1",
    title: "",
    preview: "Ping the room",
    direction: "INTERNAL",
    status: "SENT",
    starred: false,
    needaction: false,
    message_type: "COMMENT",
    can_edit: false,
    can_delete: false,
    sender: { id: "hdl_1", display_name: "Grace Hopper", value: "grace@example.com" },
    parent: null,
    subtype: null,
    sent_at: "2026-07-06T00:00:00Z",
    created_at: "2026-07-06T00:00:00Z",
    reaction_groups: [],
    tracking_values: [],
    parts: [{ role: "user", fragment: { text: "Ping the room" }, file: null }],
    ...overrides,
  } as unknown as RecordMessageRow;
}

function threadPayload(messages: RecordMessageRow[]): unknown {
  return {
    record_thread: {
      error: null,
      error_code: null,
      thread: { id: "thr_1", title: { text: "Room" }, message_count: messages.length, last_message_at: null },
      message_result_count: messages.length,
      messages,
      follower_count: 2,
      is_following: true,
      self_follower: null,
      suggested_recipients: [],
      subtypes: [],
      unread_count: 1,
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

beforeEach(() => {
  mocks.mutateCalls = [];
  mocks.failOps = new Set();
  mocks.recipientData = { colleagues: [] };
  mocks.threadData = threadPayload([message()]);
  mocks.threadError = null;
  mocks.useAuthoredQuery.mockReset();
  mocks.useAuthoredQuery.mockImplementation((document: unknown) => {
    const op = operationName(document);
    if (op === "MessagingRecordThread") {
      return { data: mocks.threadData, fetching: false, error: mocks.threadError, refetch: vi.fn() };
    }
    if (op === "MessagingRecipientUsers") {
      return { data: mocks.recipientData, fetching: false, error: null, refetch: vi.fn() };
    }
    throw new Error(`Unexpected authored query: ${op}`);
  });
});

afterEach(cleanup);

describe("RecordThreadConversation", () => {
  test("renders the record-thread transcript for the given record", () => {
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);

    // The record-attached chatter — not the .inbox()-scoped generic messages.
    expect(screen.getByText("Ping the room")).toBeTruthy();
    expect(screen.getByText("Grace Hopper")).toBeTruthy();
  });

  test("posts a message through the composer keyed by the record", () => {
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);

    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Hello room" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const post = mocks.mutateCalls.find((call) => call.op === "MessagingPostRecordMessage");
    expect(post).toBeTruthy();
    expect(post?.vars.modelLabel).toBe("discuss/room");
    expect(post?.vars.recordId).toBe("rom_1");
    expect(post?.vars.body).toBe("Hello room");
  });

  test("wires mark-read through the header seam", () => {
    // A room composes its own chrome via `header`; here a minimal header surfaces the
    // shared mark-read owner so the extracted wiring is exercised standalone.
    const header = (chrome: RecordThreadConversationChrome) => (
      <button type="button" onClick={() => void chrome.markRead()}>
        mark read
      </button>
    );
    render(
      <RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" header={header} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "mark read" }));

    const markRead = mocks.mutateCalls.find(
      (call) => call.op === "MessagingMarkRecordThreadRead",
    );
    expect(markRead).toBeTruthy();
    expect(markRead?.vars.modelLabel).toBe("discuss/room");
    expect(markRead?.vars.recordId).toBe("rom_1");
  });

  test("passes the resolved payload to the header chrome", () => {
    render(
      <RecordThreadConversation
        modelLabel="discuss/room"
        recordId="rom_1"
        header={(chrome) => <span>followers:{chrome.payload?.follower_count ?? 0}</span>}
      />,
    );

    expect(screen.getByText("followers:2")).toBeTruthy();
  });

  test("renders a no-access surface with NO composer for a NOT_FOUND record", () => {
    const base = (threadPayload([]) as { record_thread: Record<string, unknown> }).record_thread;
    mocks.threadData = {
      record_thread: { ...base, error: "record not found", error_code: "NOT_FOUND" },
    };
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);

    // A NOT_FOUND record must never render a phantom room a non-member could post into.
    expect(screen.getByText("Record unavailable")).toBeTruthy();
    expect(screen.queryByLabelText("Message")).toBeNull();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  });

  test("keeps the chatter-disabled copy for a BAD_RECORD, with no composer", () => {
    const base = (threadPayload([]) as { record_thread: Record<string, unknown> }).record_thread;
    mocks.threadData = {
      record_thread: { ...base, error: "bad record", error_code: "BAD_RECORD" },
    };
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);

    expect(screen.getByText("Comments are not enabled")).toBeTruthy();
    expect(screen.queryByLabelText("Message")).toBeNull();
  });

  test("announces a post failure through an alert banner", async () => {
    mocks.failOps = new Set(["MessagingPostRecordMessage"]);
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Hello room" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Network down");
  });
});
