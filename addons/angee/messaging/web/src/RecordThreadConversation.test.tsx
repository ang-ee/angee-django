// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, ToastProvider } from "@angee/ui";

import type { RecordActivityRow, RecordMessageRow, RecordThreadPayload } from "./documents";
import type { RecordThreadConversationChrome } from "./RecordThreadConversation";

const mocks = vi.hoisted(() => ({
  threadData: undefined as unknown,
  threadError: null as unknown,
  recipientData: { colleagues: [] as unknown[] } as unknown,
  mutateCalls: [] as Array<{ op: string; vars: Record<string, unknown> }>,
  failOps: new Set<string>(),
  useAuthoredQuery: vi.fn(),
  upload: vi.fn(),
  uploadTasks: [] as Array<{ id: string; name: string; status: "uploading" | "failed" | "done" }>,
  onUploaded: null as null | ((files: readonly { id: string; filename: string }[]) => void),
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
  useStorageT: () => (key: string) => key,
  useStorageUpload: ({ onUploaded }: { onUploaded: typeof mocks.onUploaded }) => {
    mocks.onUploaded = onUploaded;
    return { tasks: mocks.uploadTasks, upload: mocks.upload, clearFinished: vi.fn(), retry: vi.fn() };
  },
  StorageUploadTasks: ({ uploads }: { uploads: { tasks: typeof mocks.uploadTasks } }) => (
    <div data-testid="storage-upload-tasks">{uploads.tasks.map((task) => <span key={task.id}>{task.name}</span>)}</div>
  ),
}));

import { RecordThreadConversation } from "./RecordThreadConversation";
import { RecordThreadStream } from "./RecordThreadStream";

function part(id: string, text: string): RecordMessageRow["parts"][number] {
  return { id, name: "body", type: "text/plain", position: 0, role: "BODY", disposition: "INLINE", cid: "",
    parent: null, fragment: { id: `frag-${id}`, text }, file: null };
}

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
    author_label: "Grace Hopper",
    is_self: false,
    is_reply: false,
    edited_at: null,
    sender: { id: "hdl_1", display_name: "Grace Hopper", value: "grace@example.com" },
    parent: null,
    subtype: null,
    sent_at: "2026-07-06T00:00:00Z",
    created_at: "2026-07-06T00:00:00Z",
    reaction_groups: [],
    tracking_values: [],
    parts: [part("part-1", "Ping the room")],
    ...overrides,
  } as unknown as RecordMessageRow;
}

function activity(overrides: Partial<RecordActivityRow> = {}): RecordActivityRow {
  return {
    id: "activity_1", activity_type: "call", summary: "Exchange", note: "Exchange note",
    due_date: "2026-07-06", completed_at: "2026-07-06T12:00:00Z", feedback: "",
    status: "DONE", state: "done", user: null, created_by: null, ...overrides,
  };
}

function threadPayload(
  messages: RecordMessageRow[],
  activities: RecordActivityRow[] = [],
  glyph = "phone",
  access: Pick<RecordThreadPayload, "permissions" | "thread_post_access"> = {
    permissions: ["write"], thread_post_access: "write",
  },
) {
  return {
    record_thread: {
      ...access,
      error: null,
      error_code: null,
      thread: { id: "thr_1", title: { text: "Room" }, message_count: messages.length, last_message_at: null },
      message_result_count: messages.length,
      audience_label: "People with access to this record" as string | null,
      post_kinds: ["COMMENT", "NOTE"],
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
      activity_count: activities.length,
      activities,
      activity_types: [{ id: "act_call", key: "call", name: "Call", glyph }],
    },
  };
}

function conversationPreview(active: boolean, pending = false) {
  const user = { id: "usr_preview", name: "Preview viewer" };
  return (
    <AppRuntimeProvider runtime={{
      auth: {
        user, status: "authenticated", hasRole: () => false,
        viewAs: {
          viewAs: active ? { userId: user.id } : null,
          currentUser: user,
          realUser: { id: "usr_admin", name: "Administrator" },
          viewablePeople: [user], enter: vi.fn(), exit: vi.fn(), pending,
        },
      },
    }}>
      <RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />
    </AppRuntimeProvider>
  );
}

beforeEach(() => {
  mocks.upload.mockReset();
  mocks.uploadTasks = [];
  mocks.onUploaded = null;
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
  test("stream shows You, a server-supplied withheld author, and the latest edit time", () => {
    mocks.threadData = threadPayload([
      message({ id: "own", is_self: true, author_label: "Account name", edited_at: "2026-07-07T12:00:00Z" }),
      message({ id: "hidden", is_self: false, is_reply: true, author_label: "A contributor", sender: null,
        preview: "Second entry", parts: [part("part-2", "Second entry")] }),
    ]);
    render(<RecordThreadConversation modelLabel="projects.Task" recordId="task_1"
      stream={{ audience: "Team readers", verbs: { root: "answered", reply: "replied" } }} />);

    expect(screen.getByText("You")).toBeTruthy();
    expect(screen.getByText("A contributor")).toBeTruthy();
    expect(screen.getAllByText("Team readers").length).toBeGreaterThan(0);
    expect(screen.getByText("answered")).toBeTruthy();
    expect(screen.getByText("replied")).toBeTruthy();
    expect(screen.getByText(/edited/)).toBeTruthy();
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Add recipient" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Note" })).toBeNull();
  });

  test("a late reply retains its parent quote after another entry", () => {
    mocks.threadData = threadPayload([
      message({ id: "a", preview: "First entry", parts: [part("part-3", "First entry")] }),
      message({ id: "b", preview: "Second entry", parts: [part("part-4", "Second entry")] }),
      message({ id: "reply", is_reply: true, parent: { id: "a", preview: "First entry", message_type: "COMMENT", subtype: null } as never,
        preview: "A late reply", parts: [part("part-5", "A late reply")] }),
    ]);
    render(<RecordThreadConversation modelLabel="projects.Task" recordId="task_1" stream={{}} />);
    const feed = screen.getByRole("list", { name: "Comments" }).textContent ?? "";
    expect(feed.indexOf("Second entry")).toBeLessThan(feed.indexOf("A late reply"));
    expect(screen.getByText("Replying to message")).toBeTruthy();
    expect(screen.getAllByText("First entry")).toHaveLength(2);
  });

  test("the plain comment has no kind pill and an edited aside row has no leading separator", () => {
    mocks.threadData = threadPayload([message({ status: "EDITED", edited_at: null,
      subtype: { key: "comment", name: "Comment", description: "Comment", hidden: false } as never })]);
    const { rerender } = render(<RecordThreadConversation modelLabel="projects.Task" recordId="task_1" stream={{}} />);
    expect(screen.queryByText("Comment")).toBeNull();
    rerender(<RecordThreadConversation modelLabel="projects.Task" recordId="task_1" />);
    expect(screen.getByText("edited").textContent).toBe("edited");
  });

  test("only a declaration-named subtype gets a stream kind pill", () => {
    mocks.threadData = threadPayload([message({ subtype: {
      key: "notice", name: "Notice", description: "Notice", hidden: false,
    } as never })]);
    const { rerender } = render(<RecordThreadConversation modelLabel="projects.Task" recordId="task_1" stream={{}} />);
    expect(screen.queryByText("Notice")).toBeNull();
    mocks.threadData = threadPayload([message({ subtype: {
      key: "notice", name: "Notice", description: "Notice", hidden: true,
    } as never })]);
    rerender(<RecordThreadConversation modelLabel="projects.Task" recordId="task_1" stream={{}} />);
    expect(screen.queryByText("Notice")).toBeNull();
    rerender(<RecordThreadConversation modelLabel="projects.Task" recordId="task_1"
      stream={{ kindLabels: { notice: "Announcement" } }} />);
    expect(screen.getByText("Announcement")).toBeTruthy();
  });

  test("web vocabulary supplies withheld author and default audience", () => {
    const payload = threadPayload([message({ author_label: null, sender: null })]);
    payload.record_thread.audience_label = null;
    mocks.threadData = payload;
    render(<RecordThreadConversation modelLabel="projects.Task" recordId="task_1" stream={{}} />);
    expect(screen.getByText("Someone")).toBeTruthy();
    expect(screen.getAllByText("Visible to people with access to this record.").length).toBeGreaterThan(0);
  });

  test("stream composer keeps the reader line, prompt, named verb, and submit key", () => {
    render(<RecordThreadConversation modelLabel="projects.Task" recordId="task_1"
      submitKey="mod-enter" stream={{ prompt: "Add a reply", readerLine: "The same readers see this reply.",
        submitLabel: "Reply" }} />);
    expect(screen.getByPlaceholderText("Add a reply")).toBeTruthy();
    expect(screen.getByText("The same readers see this reply.")).toBeTruthy();
    const button = screen.getByRole<HTMLButtonElement>("button", { name: "Reply" });
    expect(button.disabled).toBe(true);
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "A reply" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mocks.mutateCalls).toHaveLength(0);
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
    expect(mocks.mutateCalls).toEqual([expect.objectContaining({
      op: "MessagingPostRecordMessage",
      vars: expect.objectContaining({ body: "A reply", modelLabel: "projects.Task" }),
    })]);
  });

  test("uses only the post kind offered by the record payload", () => {
    const payload = threadPayload([message()]);
    payload.record_thread.post_kinds = ["NOTE"];
    mocks.threadData = payload;
    render(<RecordThreadConversation modelLabel="projects.Project" recordId="project_1"
      stream={{ prompt: "Add a note", submitLabel: "Add note", postKind: "note" }} />);
    expect(screen.queryByRole("button", { name: "Comment" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Private thought" } });
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    expect(mocks.mutateCalls).toEqual([expect.objectContaining({
      op: "MessagingPostRecordMessage", vars: expect.objectContaining({ kind: "note" }),
    })]);
  });

  test("omits a stream composer when the server offers no post kind", () => {
    const payload = threadPayload([message()]);
    payload.record_thread.post_kinds = [];
    mocks.threadData = payload;
    render(<RecordThreadConversation modelLabel="projects.Task" recordId="task_1" stream={{}} />);
    expect(screen.getByText("Ping the room")).toBeTruthy();
    expect(screen.queryByLabelText("Message")).toBeNull();
  });

  test.each([
    { permissions: [], thread_post_access: "write" },
    { permissions: ["write"], thread_post_access: "comment" },
    { permissions: ["write"], thread_post_access: null },
  ])("keeps the transcript without a composer or reply when post access is absent: %j", (access) => {
    mocks.threadData = threadPayload([message()], [], "phone", access);
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);

    expect(screen.getByText("Ping the room")).toBeTruthy();
    expect(screen.queryByLabelText("Message")).toBeNull();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reply to message" })).toBeNull();
    expect(mocks.mutateCalls).toEqual([]);
    expect(mocks.useAuthoredQuery.mock.calls.find(
      ([document]) => operationName(document) === "MessagingRecipientUsers",
    )?.[2]).toMatchObject({ enabled: false });
  });

  test("does not show a composer before permissions have loaded", () => {
    mocks.threadData = undefined;
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);
    expect(screen.queryByLabelText("Message")).toBeNull();
  });

  test("uses the record's post permission even when it is not write", () => {
    mocks.threadData = threadPayload([message()], [], "phone", {
      thread_post_access: "comment", permissions: ["comment"],
    });
    render(<RecordThreadConversation modelLabel="projects.Task" recordId="task_1" />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "A comment" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(mocks.mutateCalls).toEqual([expect.objectContaining({
      op: "MessagingPostRecordMessage",
      vars: expect.objectContaining({ body: "A comment", modelLabel: "projects.Task" }),
    })]);
  });

  test("removes a drafted composer when posting permission is revoked", () => {
    const view = <RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />;
    const { rerender } = render(view);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Draft" } });
    mocks.threadData = threadPayload([message()], [], "phone", {
      thread_post_access: "write", permissions: [],
    });
    rerender(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);
    expect(screen.queryByLabelText("Message")).toBeNull();
    expect(screen.getByText("Ping the room")).toBeTruthy();
    expect(mocks.mutateCalls).toEqual([]);
  });

  test.each(["comment", "note"])("freezes a permitted %s draft in preview and blocks every submission path", (kind) => {
    const { rerender, container } = render(conversationPreview(false));
    if (kind === "note") fireEvent.click(screen.getByRole("button", { name: "Note" }));
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Keep this draft" } });

    rerender(conversationPreview(true));
    const input = screen.getByLabelText<HTMLTextAreaElement>("Message");
    const send = screen.getByRole<HTMLButtonElement>("button", { name: kind === "note" ? "Log" : "Send" });
    expect(input.readOnly).toBe(true);
    expect(input.value).toBe("Keep this draft");
    expect(send.disabled).toBe(true);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Attach files" }).disabled).toBe(true);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Reply to message" }).disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("This preview is read-only.");
    expect(screen.getByText("Ping the room")).toBeTruthy();
    fireEvent.click(send);
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.submit(input.closest("form")!);
    const files = [new File(["attachment"], "note.txt", { type: "text/plain" })];
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files } });
    fireEvent.drop(input, { dataTransfer: { files, types: ["Files"] } });
    expect(mocks.mutateCalls).toEqual([]);
    expect(mocks.upload).not.toHaveBeenCalled();

    rerender(conversationPreview(false));
    expect(screen.getByLabelText<HTMLTextAreaElement>("Message").readOnly).toBe(false);
    fireEvent.keyDown(screen.getByLabelText("Message"), { key: "Enter" });
    expect(mocks.mutateCalls).toEqual([expect.objectContaining({
      op: "MessagingPostRecordMessage",
      vars: expect.objectContaining({ body: "Keep this draft", kind }),
    })]);
  });

  test("keeps posting disabled while the preview identity is changing", () => {
    render(conversationPreview(false, true));
    expect(screen.getByLabelText<HTMLTextAreaElement>("Message").readOnly).toBe(true);
  });

  test("does not show a disabled composer for a preview viewer without permission", () => {
    mocks.threadData = threadPayload([message()], [], "phone", {
      thread_post_access: "write", permissions: [],
    });
    render(conversationPreview(true));
    expect(screen.getByText("Ping the room")).toBeTruthy();
    expect(screen.queryByLabelText("Message")).toBeNull();
    expect(screen.queryByText("This preview is read-only.")).toBeNull();
  });

  test("defaults composer copy and accepts independently translated consumer overrides", () => {
    const { rerender } = render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);
    expect(screen.getByText("Visible to people with access to this record.")).toBeTruthy();
    expect(screen.getByText("Use comments for discussion and notes for internal updates.")).toBeTruthy();

    rerender(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1"
      composerCopy={{ audience: "For the project team" }} />);
    expect(screen.getByText("For the project team")).toBeTruthy();
    expect(screen.getByText("Use comments for discussion and notes for internal updates.")).toBeTruthy();

    rerender(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1"
      composerCopy={{ audience: "For the project team", help: "Share the next step" }} />);
    expect(screen.getByText("Share the next step")).toBeTruthy();
    expect(screen.queryByText("Use comments for discussion and notes for internal updates.")).toBeNull();
    expect(screen.getAllByRole("list", { name: "Comments" })).toHaveLength(1);
  });

  test("merges exchanges without reordering equal instants from the server", () => {
    mocks.threadData = threadPayload([
      message({ id: "first", preview: "First from server", parts: [part("part-first", "First from server")], sent_at: "2026-07-06T12:00:00+02:00" }),
      message({ id: "second", preview: "Second from server", parts: [part("part-second", "Second from server")], sent_at: "2026-07-06T10:00:00Z" }),
    ], [activity({ due_date: "2026-07-05" })]);
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);
    const text = screen.getByRole("list", { name: "Comments" }).textContent ?? "";
    expect(text.indexOf("Exchange note")).toBeLessThan(text.indexOf("First from server"));
    expect(text.indexOf("First from server")).toBeLessThan(text.indexOf("Second from server"));
  });

  test("uses the activity glyph when the catalog glyph is unknown", () => {
    mocks.threadData = threadPayload([], [activity()], "unknown-catalog-glyph");
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);
    expect(screen.getByRole("list", { name: "Comments" }).querySelector("svg.glyph")).not.toBeNull();
  });

  test("clears exchanges when the server returns an empty search window", async () => {
    mocks.threadData = threadPayload([], [activity()]);
    const { rerender } = render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);
    expect(screen.getByText("Exchange note")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "absent" } });
    mocks.threadData = threadPayload([]);
    rerender(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);
    await waitFor(() => expect(screen.getByText("No matching comments")).toBeTruthy());
    expect(screen.queryByText("Exchange note")).toBeNull();
  });

  test("renders the record-thread transcript for the given record", () => {
    mocks.threadData = threadPayload([message()], [{
      id: "activity_1", activity_type: "call", summary: "Earlier exchange",
      note: "Agreed on the next step.", due_date: "2026-07-05",
      completed_at: "2026-07-07T12:00:00Z", feedback: "", status: "DONE", state: "done",
      user: null, created_by: null,
    }]);
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1"
      activityCopy={{ recordedOn: (day) => `Logged ${day}` }} />);

    // The record-attached chatter — not the .inbox()-scoped generic messages.
    expect(screen.getByText("Ping the room")).toBeTruthy();
    expect(screen.getByText("Grace Hopper")).toBeTruthy();
    expect(screen.getByText("Call")).toBeTruthy();
    expect(screen.getByText(/Logged /)).toBeTruthy();
    const feed = screen.getByRole("list", { name: "Comments" });
    expect(feed.textContent?.indexOf("Agreed on the next step.")).toBeLessThan(
      feed.textContent?.indexOf("Ping the room") ?? -1,
    );
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
    expect(post?.vars.clientCreationKey).toEqual(expect.any(String));
  });

  test("defaults to Enter to submit and keeps Shift+Enter for a newline", () => {
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "Hello room" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(mocks.mutateCalls).toHaveLength(0);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mocks.mutateCalls).toHaveLength(1);
  });

  test("mod-enter keeps plain Enter for a newline and sends with Ctrl or Cmd", () => {
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" submitKey="mod-enter" />);
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "Hello room" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true, ctrlKey: true });
    expect(mocks.mutateCalls).toHaveLength(0);
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
    expect(mocks.mutateCalls).toHaveLength(1);
  });

  test("mod-enter also sends with Cmd", () => {
    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" submitKey="mod-enter" />);
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "Hello room" } });
    fireEvent.keyDown(input, { key: "Enter", metaKey: true });
    expect(mocks.mutateCalls).toHaveLength(1);
  });

  test("renders storage upload tasks beside removable send-draft attachments", () => {
    mocks.uploadTasks = [{ id: "upload-1", name: "pending.txt", status: "uploading" }];
    const { rerender } = render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);
    expect(screen.getByTestId("storage-upload-tasks").textContent).toBe("pending.txt");
    act(() => mocks.onUploaded?.([{ id: "file-1", filename: "ready.txt" }]));
    expect(screen.getByText("ready.txt")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove ready.txt" })).toBeTruthy();
    mocks.uploadTasks = [{ id: "upload-1", name: "pending.txt", status: "done" }];
    rerender(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);
    expect(screen.queryByTestId("storage-upload-tasks")).toBeNull();
    expect(screen.getByText("ready.txt")).toBeTruthy();
  });

  test("offers visible people from IAM colleagues as message recipients", async () => {
    mocks.recipientData = {
      colleagues: [
        {
          id: "usr_ada",
          username: "ada",
          display_name: "Ada Lovelace",
          email: "ada@example.com",
          is_active: true,
        },
      ],
    };

    render(<RecordThreadConversation modelLabel="discuss/room" recordId="rom_1" />);

    fireEvent.click(screen.getByRole("combobox", { name: "Add recipient" }));

    expect(await screen.findByText("Ada Lovelace · ada@example.com")).toBeTruthy();
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
    const first = mocks.mutateCalls.find((call) => call.op === "MessagingPostRecordMessage");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("alert");
    const posts = mocks.mutateCalls.filter((call) => call.op === "MessagingPostRecordMessage");
    expect(posts).toHaveLength(2);
    expect(posts[1]?.vars.clientCreationKey).toBe(first?.vars.clientCreationKey);
  });
});

describe("RecordThreadStream", () => {
  test("uses one thread presentation for a record source", () => {
    render(<RecordThreadStream heading={{ label: "Conversation", counts: "8 entries" }}
      source={{ kind: "record", modelLabel: "projects.Project", recordId: "project_1",
        conversation: { prompt: "What was said?", submitLabel: "Record exchange" } }} />);
    expect(screen.getByText("Conversation")).toBeTruthy();
    expect(screen.getByText("8 entries")).toBeTruthy();
    expect(screen.getByText("Ping the room")).toBeTruthy();
    expect(screen.getByPlaceholderText("What was said?")).toBeTruthy();
  });

  test("accepts a consumer-translated count node", () => {
    render(<RecordThreadStream heading={{ label: "Conversation",
      counts: <strong>1 reply</strong> }}
      source={{ kind: "record", modelLabel: "projects.Project", recordId: "project_1" }} />);
    expect(screen.getByText("1 reply")).toBeTruthy();
  });

  test("a loading stream shows item shapes without search chrome", () => {
    mocks.useAuthoredQuery.mockImplementation((document: unknown) => {
      if (operationName(document) === "MessagingRecordThread") {
        return { data: undefined, isFetching: true, error: null, refetch: vi.fn() };
      }
      return { data: mocks.recipientData, isFetching: false, error: null, refetch: vi.fn() };
    });
    render(<RecordThreadStream heading={{ label: "Conversation" }}
      source={{ kind: "record", modelLabel: "projects.Project", recordId: "project_1" }} />);
    const skeleton = screen.getByRole("status");
    expect(skeleton.textContent).toContain("Loading comments");
    expect(skeleton.querySelectorAll('[aria-hidden="true"]')).toHaveLength(9);
    expect(screen.queryByRole("searchbox")).toBeNull();
  });

  test("renders child title and body over its thread and preserves server count annotations", () => {
    render(<RecordThreadStream heading={{ label: "Questions", counts: "3 answered · 4 replies" }}
      source={{ kind: "children", modelLabel: "projects.Task", items: [{
      id: "task_1", title: "What happens next?", body: "Please describe the next step.",
      authorLabel: "A contributor", isSelf: false, audienceLabel: "Team readers",
      createdAt: "2026-07-06T00:00:00Z", verbs: { root: "answered", reply: "replied" },
      prompt: "Add a reply", submitLabel: "Reply", readerLine: "The same readers see this reply.",
    }] }} />);
    expect(screen.getByText("3 answered · 4 replies")).toBeTruthy();
    expect(screen.getByText("What happens next?")).toBeTruthy();
    expect(screen.getByText("Please describe the next step.")).toBeTruthy();
    expect(mocks.useAuthoredQuery.mock.calls.filter(([doc]) => operationName(doc) === "MessagingRecordThread")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Show conversation" }));
    expect(mocks.useAuthoredQuery.mock.calls.filter(([doc]) => operationName(doc) === "MessagingRecordThread")).toHaveLength(1);
    expect(screen.getByText("Ping the room")).toBeTruthy();
    expect(screen.getByPlaceholderText("Add a reply")).toBeTruthy();
  });

  test("a complex create action uses the shared dialog instead of throwing", () => {
    render(<RecordThreadStream heading={{ label: "Updates" }} source={{
      kind: "children", modelLabel: "projects.Task", items: [],
      createAction: { id: "create-update", label: "Post update", args: [{ name: "body" }, { name: "audience" }],
        submit: vi.fn(async () => ({ ok: true, message: "Created" })) },
      createComposer: { bodyArg: "body", prompt: "What moved?" },
    }} />);
    expect(screen.getByRole("button", { name: "Post update" })).toBeTruthy();
    expect(screen.queryByPlaceholderText("What moved?")).toBeNull();
  });

  test("runs the declared create verb from an always-open foot composer", async () => {
    const submit = vi.fn(async () => ({ ok: true, message: "Created" }));
    render(<ToastProvider><RecordThreadStream heading={{ label: "Updates" }} source={{
      kind: "children", modelLabel: "projects.Task", items: [],
      createAction: { id: "create-update", label: "Post update", permission: "write",
        record: { id: "prj_1", permissions: ["write"] }, args: [{ name: "body" }], submit },
      createComposer: { bodyArg: "body", prompt: "What moved?", audience: "Team readers" },
    }} /></ToastProvider>);
    const input = screen.getByPlaceholderText("What moved?");
    const button = screen.getByRole<HTMLButtonElement>("button", { name: "Post update" });
    expect(button.disabled).toBe(true);
    expect(screen.getByText("Posting to Team readers")).toBeTruthy();
    fireEvent.change(input, { target: { value: "A new milestone" } });
    fireEvent.click(button);
    await waitFor(() => expect(submit).toHaveBeenCalledWith(
      { body: "A new milestone" }, { record: { id: "prj_1", permissions: ["write"] }, selectedIds: [] },
    ));
  });
});
