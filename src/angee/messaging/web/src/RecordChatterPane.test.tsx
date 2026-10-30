// @vitest-environment happy-dom

import { containerChildren } from "@angee/app/testing";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { ChatterTabContent, ChatterViewContext } from "@angee/ui/runtime";
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
    // The delete verb confirms through the shared ceremony; the test answers yes.
    useConfirm: () => async () => true,
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
  useStorageT: () => (key: string) => key,
  StorageUploadTasks: () => null,
  useStorageUpload: () => ({ tasks: [], upload: vi.fn(), clearFinished: vi.fn() }),
}));

import { RecordChatterPane } from "./RecordChatterPane";
import messaging from "./index";

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
    author_label: "Ada Lovelace",
    is_self: false,
    is_reply: false,
    edited_at: null,
    sender: { id: "hdl_1", display_name: "Ada Lovelace", value: "ada@example.com" },
    parent: null,
    subtype: null,
    sent_at: "2026-06-27T00:00:00Z",
    created_at: "2026-06-27T00:00:00Z",
    reaction_groups: [],
    parts: [{ role: "user", fragment: { text: "Hello there" }, file: null }],
    ...overrides,
  } as unknown as RecordMessageRow;
}

function threadPayload(messages: RecordMessageRow[]): unknown {
  return {
    record_thread: {
      thread_post_access: "write",
      permissions: ["write"],
      error: null,
      error_code: null,
      thread: { id: "thr_1", title: { text: "Rec" }, message_count: messages.length, last_message_at: null },
      message_result_count: messages.length,
      audience_label: null,
      post_kinds: ["COMMENT", "NOTE"],
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
      return { data: mocks.threadData, isFetching: mocks.threadData === undefined, error: null, refetch: vi.fn() };
    }
    if (op === "MessagingRecipientUsers") {
      return { data: mocks.recipientData, fetching: false, error: null, refetch: vi.fn() };
    }
    throw new Error(`Unexpected authored query: ${op}`);
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RecordChatterPane", () => {
  test("a Timeline selection clears retained filters and focuses the message when its window arrives", async () => {
    const scrollIntoView = vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(() => {});
    const discussion = containerChildren(messaging, "record#aside").find(([id]) => id === "messaging.discussion")?.[1].content as ChatterTabContent;
    mocks.threadData = threadPayload([message()]);
    const view = render(<>{discussion.render?.(context)}</>);
    fireEvent.click(screen.getByRole("button", { name: "Notes" }));
    fireEvent.click(screen.getByRole("button", { name: "Search comments" }));
    fireEvent.change(screen.getByLabelText("Search comments", { selector: "input" }), { target: { value: "unrelated" } });
    await waitFor(() => {
      const last = mocks.useAuthoredQuery.mock.calls.filter(([document]) => operationName(document) === "MessagingRecordThread").at(-1);
      expect(last?.[1]).toMatchObject({ search: "unrelated", messageTypes: ["notification"] });
    });

    const selection = { target: "msg_older", focus: "message" };
    mocks.threadData = threadPayload([]);
    mocks.useAuthoredQuery.mockClear();
    view.rerender(<>{discussion.render?.(context, selection)}</>);
    const reads = mocks.useAuthoredQuery.mock.calls.filter(([document]) => operationName(document) === "MessagingRecordThread");
    expect(reads.length).toBeGreaterThan(0);
    for (const [, variables] of reads) {
      expect(variables).toMatchObject({ around: "msg_older", search: "", messageTypes: [] });
    }
    expect(screen.getByRole("button", { name: "All" }).getAttribute("aria-pressed")).toBe("true");
    expect(scrollIntoView).not.toHaveBeenCalled();

    mocks.threadData = threadPayload([message({ id: "msg_older", can_edit: true }), message({ id: "msg_newer" })]);
    view.rerender(<>{discussion.render?.(context, selection)}</>);
    const focused = document.getElementById("message-msg_older");
    expect(focused?.getAttribute("aria-current")).toBe("true");
    expect(document.getElementById("message-msg_newer")?.hasAttribute("aria-current")).toBe(false);
    expect(scrollIntoView).toHaveBeenCalledExactlyOnceWith({ block: "center" });
    expect(scrollIntoView.mock.instances[0]).toBe(focused);

    fireEvent.click(screen.getByRole("button", { name: "Edit comment" }));
    expect(document.getElementById("message-msg_older")?.getAttribute("aria-current")).toBe("true");
    expect(scrollIntoView).toHaveBeenCalledTimes(1);

    view.rerender(<>{discussion.render?.(context, { target: "msg_newer", focus: "message" })}</>);
    expect(document.getElementById("message-msg_older")?.hasAttribute("aria-current")).toBe(false);
    expect(document.getElementById("message-msg_newer")?.getAttribute("aria-current")).toBe("true");
    expect(scrollIntoView).toHaveBeenCalledTimes(2);
    expect(scrollIntoView.mock.instances[1]).toBe(document.getElementById("message-msg_newer"));
  });

  test.each([
    undefined,
    { focus: "message" },
    { target: "msg_1", focus: "field" },
    { target: "msg_1" },
  ])("does not focus a message without a message selection: %j", (selection) => {
    const scrollIntoView = vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(() => {});
    mocks.threadData = threadPayload([message()]);
    render(<RecordChatterPane context={context} selection={selection} />);
    const last = mocks.useAuthoredQuery.mock.calls.filter(([document]) => operationName(document) === "MessagingRecordThread").at(-1);
    expect(last?.[1].around).toBeUndefined();
    expect(document.getElementById("message-msg_1")?.hasAttribute("aria-current")).toBe(false);
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  test("renders the feed with author and the composer", () => {
    mocks.threadData = threadPayload([message()]);

    render(<RecordChatterPane context={context} />);

    expect(screen.getByText("Hello there")).toBeTruthy();
    expect(screen.getByText("Ada Lovelace")).toBeTruthy();
    // The composer send affordance; the head carries no Following button (that is the record header's).
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Following" })).toBeNull();
  });

  test("the head's scope narrows the thread read to the picked message types", () => {
    mocks.threadData = threadPayload([message()]);
    render(<RecordChatterPane context={context} />);
    fireEvent.click(screen.getByRole("button", { name: "Notes" }));
    const last = mocks.useAuthoredQuery.mock.calls
      .filter(([document]) => operationName(document) === "MessagingRecordThread").at(-1);
    expect(last?.[1]).toMatchObject({ messageTypes: ["notification"] });
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    const after = mocks.useAuthoredQuery.mock.calls
      .filter(([document]) => operationName(document) === "MessagingRecordThread").at(-1);
    expect(after?.[1]).toMatchObject({ messageTypes: [] });
  });

  test("the head reveals search on demand and marks the thread read from its badge", () => {
    mocks.threadData = threadPayload([message()]);
    (mocks.threadData as { record_thread: { unread_count: number } }).record_thread.unread_count = 2;
    render(<RecordChatterPane context={context} />);
    expect(screen.queryByLabelText("Search comments", { selector: "input" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Search comments" }));
    expect(screen.getByLabelText("Search comments", { selector: "input" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Mark read" }));
    expect(mocks.mutateCalls.some((call) => call.op === "MessagingMarkRecordThreadRead")).toBe(true);
  });

  test("search stays open through a query change and closes with its filter cleared", () => {
    mocks.threadData = threadPayload([message()]);
    const view = render(<RecordChatterPane context={context} />);
    fireEvent.click(screen.getByRole("button", { name: "Search comments" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "Search comments" }), { target: { value: "Hello" } });
    mocks.threadData = undefined;
    view.rerender(<RecordChatterPane context={context} />);
    expect(screen.getByRole("searchbox", { name: "Search comments" })).toBeTruthy();
    mocks.threadData = threadPayload([message()]);
    view.rerender(<RecordChatterPane context={context} />);
    fireEvent.click(screen.getByRole("button", { name: "Search comments" }));
    expect(screen.queryByRole("searchbox", { name: "Search comments" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Search comments" }));
    expect((screen.getByRole("searchbox", { name: "Search comments" }) as HTMLInputElement).value).toBe("");
  });

  test("passes the host's submit key to its record conversation", () => {
    mocks.threadData = threadPayload([message()]);
    render(<RecordChatterPane context={context} submitKey="mod-enter" />);
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "Next step" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mocks.mutateCalls).toHaveLength(0);
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
    expect(mocks.mutateCalls).toEqual([expect.objectContaining({
      op: "MessagingPostRecordMessage",
      vars: expect.objectContaining({ body: "Next step" }),
    })]);
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

  test("deletes through the mutation keyed by message id", async () => {
    mocks.threadData = threadPayload([
      message({ id: "msg_editable", can_edit: true, can_delete: true }),
    ]);

    render(<RecordChatterPane context={context} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete message" }));

    // The verb confirms first (answered yes by the mock), then fires.
    await waitFor(() => expect(
      mocks.mutateCalls.some(
        (call) =>
          call.op === "MessagingDeleteRecordMessage" && call.vars.messageId === "msg_editable",
      ),
    ).toBe(true));
  });

  test("shows the not-enabled state for a record without a thread", () => {
    mocks.threadData = { record_thread: { error_code: "BAD_RECORD" } };

    render(<RecordChatterPane context={context} />);

    expect(screen.getByText("Comments are not enabled")).toBeTruthy();
  });
});
