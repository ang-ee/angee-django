// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { formatDayLabel } from "@angee/ui";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ThreadTranscript } from "./ThreadTranscript";
import type { ThreadTranscriptRow } from "./documents";

// The virtualizer's windowing is the library owner's concern (and needs a real
// layout the happy-dom test environment lacks). Stub it to a passthrough so this
// suite exercises the transcript's own rendering — the bubble treatments,
// reactions, and paging affordance — over the full row set.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 96,
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * 96, size: 96 })),
    measureElement: () => {},
    scrollToIndex: (...args: unknown[]) => mocks.scrollToIndex(...args),
  }),
}));

const mocks = vi.hoisted(() => ({
  transcriptRows: [] as unknown[],
  total: 0,
  hasMore: false,
  queryCalls: [] as Array<Record<string, unknown>>,
  fetchOlder: vi.fn(),
  scrollToIndex: vi.fn(),
  useThreadMessageFeed: vi.fn(),
  error: null as Error | null,
  fetching: false,
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useNamespaceT:
      (_namespace: string, messages: Record<string, string>) =>
      (key: string, values?: Record<string, unknown>) =>
        (messages[key] ?? key).replace(/\{(\w+)\}/g, (_, name: string) => String(values?.[name] ?? `{${name}}`)),
    // The jump-to-date control is the runtime's date widget; the suite stands in a plain field for it.
    useResolvedWidget: (key: string) => key === "date" ? {
      edit: ({ value, onChange, field }: { value: unknown; onChange?: (next: unknown) => void; field?: { label?: string } }) => (
        <input aria-label={field?.label} value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange?.(event.target.value || null)} />
      ),
    } : undefined,
  };
});

vi.mock("./thread-message-feed", () => ({ useThreadMessageFeed: mocks.useThreadMessageFeed }));
vi.mock("./MessageSourceCopies", () => ({ MessageSourceCopies: () => null }));

function message(overrides: Partial<ThreadTranscriptRow> = {}): ThreadTranscriptRow {
  return {
    id: "msg_1",
    feed_order_key: "v1:0001",
    direction: "INBOUND",
    title: "Re: hello",
    preview: "Hi there",
    message_type: "EMAIL",
    sent_at: "2026-07-01T10:00:00Z",
    created_at: "2026-07-01T10:00:00Z",
    sender: {
      id: "hnd_1",
      display_name: "Ada Lovelace",
      value: "ada@example.com",
      party_link_confirmed: false,
      party: null,
      avatar: null,
    },
    parts: [{ role: "BODY", fragment: { text: "Hi there" }, file: null }],
    reaction_groups: [],
    ...overrides,
  } as unknown as ThreadTranscriptRow;
}

beforeEach(() => {
  mocks.transcriptRows = [];
  mocks.total = 0;
  mocks.hasMore = false;
  mocks.queryCalls = [];
  mocks.fetchOlder.mockReset();
  mocks.scrollToIndex.mockReset();
  mocks.useThreadMessageFeed.mockReset();
  mocks.error = null;
  mocks.fetching = false;
  mocks.useThreadMessageFeed.mockImplementation((threadId: string) => {
    mocks.queryCalls.push({ threadId });
    return {
      data: { pages: [{ rows: mocks.transcriptRows, count: mocks.total }], pageParams: [null] },
      isFetching: mocks.fetching,
      isFetchingNextPage: false,
      error: mocks.error,
      hasNextPage: mocks.hasMore,
      fetchNextPage: mocks.fetchOlder,
    };
  });
});

afterEach(cleanup);

describe("ThreadTranscript", () => {
  test("shows the reply author and opens the quoted parent through the host", () => {
    const parent = {
      id: "msg_parent", preview: "Meet at Casa Cosecha.", message_type: "COMMENT" as const,
      sender: { ...message().sender!, display_name: "Dane" }, thread: { id: "thr_1", modality: "GROUP" as const },
    };
    mocks.transcriptRows = [message({ parent })];
    const openReply = vi.fn();
    const changeAnchor = vi.fn();

    render(<ThreadTranscript threadId="thr_1" anchor="message:msg_1" onOpenReply={openReply} onAnchorChange={changeAnchor} />);

    const quote = screen.getByRole("button", { name: /Replying to Dane Meet at Casa Cosecha/ });
    expect(quote.closest("li")?.getAttribute("aria-current")).toBe("true");
    expect(mocks.scrollToIndex).toHaveBeenCalledWith(0, { align: "center" });
    fireEvent.click(quote);
    expect(openReply).toHaveBeenCalledWith(parent);
    fireEvent.click(screen.getByRole("button", { name: "Latest messages" }));
    expect(changeAnchor).toHaveBeenCalledWith("");
  });

  test("a quoted parent in the same thread uses the native transcript anchor", () => {
    mocks.transcriptRows = [message({ parent: {
      id: "msg_parent", preview: "Earlier message", message_type: "COMMENT", sender: null, thread: { id: "thr_1", modality: "GROUP" as const },
    } })];
    const changeAnchor = vi.fn();
    render(<ThreadTranscript threadId="thr_1" onAnchorChange={changeAnchor} />);

    fireEvent.click(screen.getByRole("button", { name: /Replying to message Earlier message/ }));
    expect(changeAnchor).toHaveBeenCalledWith("message:msg_parent");
  });

  test("a conversation opens at the latest turn while history remains top-down", () => {
    mocks.transcriptRows = [message(), message({ id: "msg_2", feed_order_key: "v1:0002" })];
    const view = render(<ThreadTranscript threadId="thr_1" />);
    expect(mocks.scrollToIndex).toHaveBeenCalledWith(1, { align: "end" });
    view.unmount();
    mocks.scrollToIndex.mockClear();
    render(<ThreadTranscript threadId="thr_1" order="history" />);
    expect(mocks.scrollToIndex).not.toHaveBeenCalled();
  });

  test("restarts native anchor positioning when StrictMode replays a cached conversation", () => {
    mocks.transcriptRows = [message()];
    render(<StrictMode><ThreadTranscript threadId="thr_1" anchor="message:msg_1" /></StrictMode>);
    expect(mocks.scrollToIndex).toHaveBeenCalledTimes(2);
    expect(mocks.scrollToIndex).toHaveBeenLastCalledWith(0, { align: "center" });
  });

  test("renders inbound, outbound, and internal turns with their distinct treatments", () => {
    mocks.transcriptRows = [
      message({ id: "in", direction: "INBOUND", parts: [{ role: "BODY", fragment: { text: "Inbound hello" }, file: null }] as never }),
      message({ id: "out", direction: "OUTBOUND", sender: { id: "hnd_2", display_name: "Support", value: "us@example.com", party_link_confirmed: false, party: null, avatar: null }, parts: [{ role: "BODY", fragment: { text: "Outbound reply" }, file: null }] as never }),
      message({ id: "note", direction: "INTERNAL", parts: [{ role: "BODY", fragment: { text: "Internal jotting" }, file: null }] as never }),
    ];
    mocks.total = mocks.transcriptRows.length;

    render(<ThreadTranscript threadId="thr_1" />);

    expect(screen.getByText("Inbound hello")).toBeTruthy();
    expect(screen.getByText("Outbound reply")).toBeTruthy();
    expect(screen.getByText("Internal jotting")).toBeTruthy();
    // The internal note carries its distinct label; inbound/outbound do not.
    expect(screen.getByText("Internal note")).toBeTruthy();
  });

  test("keeps the envelope name for an unconfirmed 1.0 email-match auto-link", () => {
    mocks.transcriptRows = [
      message({
        sender: {
          id: "hnd_1",
          display_name: "Ada Envelope",
          value: "ada@example.com",
          party_link_confirmed: false,
          party: { display_name: "Ada Curated" },
        } as never,
      }),
    ];
    mocks.total = mocks.transcriptRows.length;

    render(<ThreadTranscript threadId="thr_1" />);

    expect(screen.getByText("Ada Envelope")).toBeTruthy();
    expect(screen.queryByText("Ada Curated")).toBeNull();
  });

  test("prefers the curated party name after the resolving link is confirmed", () => {
    mocks.transcriptRows = [
      message({
        sender: {
          id: "hnd_1",
          display_name: "Ada Envelope",
          value: "ada@example.com",
          party_link_confirmed: true,
          party: { display_name: "Ada Curated" },
        } as never,
      }),
    ];
    mocks.total = mocks.transcriptRows.length;

    render(<ThreadTranscript threadId="thr_1" />);

    expect(screen.getByText("Ada Curated")).toBeTruthy();
    expect(screen.queryByText("Ada Envelope")).toBeNull();
  });

  test("renders read-only reaction pills from reaction groups", () => {
    mocks.transcriptRows = [
      message({
        reaction_groups: [
          { reaction: "👍", count: 2, self_reacted: true, handles: [{ id: "h", display_name: "Ada", value: "ada" }] },
        ] as never,
      }),
    ];
    mocks.total = mocks.transcriptRows.length;

    render(<ThreadTranscript threadId="thr_1" />);

    expect(screen.getByRole("button", { name: "👍 reaction, 2" })).toBeTruthy();
  });

  test("opens an attachment in the host's preview with its message", () => {
    const attachment = {
      id: "prt_pdf",
      name: "contract.pdf",
      role: "BODY",
      disposition: "ATTACHMENT",
      fragment: null,
      file: { id: "fil_pdf", filename: "contract.pdf", url: "/files/fil_pdf" },
    };
    mocks.transcriptRows = [message({ parts: [attachment] as never })];
    const preview = vi.fn();

    render(<ThreadTranscript threadId="thr_1" onPreviewFile={preview} />);

    expect(screen.queryByRole("link", { name: /contract\.pdf/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /contract\.pdf/ }));
    expect(preview).toHaveBeenCalledWith(
      expect.objectContaining({ id: "msg_1" }),
      expect.objectContaining({ id: "prt_pdf" }),
    );
  });

  test("offers Load older using the server continuation", () => {
    mocks.transcriptRows = [message()];
    mocks.total = 120;
    mocks.hasMore = true;

    render(<ThreadTranscript threadId="thr_1" />);

    expect(mocks.queryCalls.at(-1)).toMatchObject({
      threadId: "thr_1",
    });
    fireEvent.click(screen.getByRole("button", { name: "Load older messages" }));
    expect(mocks.fetchOlder).toHaveBeenCalledWith({ cancelRefetch: false });
  });

  test("hides retained messages after a refresh error", () => {
    mocks.transcriptRows = [message()];
    mocks.error = new Error("Unreadable thread");
    render(<ThreadTranscript threadId="thr_1" />);
    expect(screen.queryByText("Hi there")).toBeNull();
  });

  test("blocks older loading during an authoritative refresh", () => {
    mocks.transcriptRows = [message()];
    mocks.hasMore = true;
    mocks.fetching = true;
    render(<ThreadTranscript threadId="thr_1" />);
    fireEvent.click(screen.getByRole("button", { name: "Load older messages" }));
    expect(mocks.fetchOlder).not.toHaveBeenCalled();
  });

  test("keeps older loading available when every retained message disappears", () => {
    mocks.hasMore = true;
    render(<ThreadTranscript threadId="thr_1" />);
    fireEvent.click(screen.getByRole("button", { name: "Load older messages" }));
    expect(mocks.fetchOlder).toHaveBeenCalledWith({ cancelRefetch: false });
  });

  test("renders server tie order oldest-to-newest without sorting public IDs", () => {
    mocks.transcriptRows = [
      message({ id: "a", feed_order_key: "v1:0002", parts: [{ role: "BODY", fragment: { text: "Newest" }, file: null }] as never }),
      message({ id: "z", feed_order_key: "v1:0001", parts: [{ role: "BODY", fragment: { text: "Oldest" }, file: null }] as never }),
    ];
    render(<ThreadTranscript threadId="thr_1" />);
    // The transcript's own rows (each wraps its feed row), in reading order.
    const items = Array.from(screen.getByRole("list", { name: "Conversation transcript" }).querySelectorAll(":scope > li"));
    expect(items[0]?.textContent).toContain("Oldest");
    expect(items[1]?.textContent).toContain("Newest");
  });

  test("shows the empty state when the thread has no messages", () => {
    mocks.transcriptRows = [];
    mocks.total = 0;

    render(<ThreadTranscript threadId="thr_1" />);

    expect(screen.getByText("No messages yet")).toBeTruthy();
  });
});


test("day separators and date anchors share the explicitly selected timezone", () => {
  mocks.transcriptRows = [
    message({ id: "late", sent_at: "2026-07-02T00:30:00Z" }),
    message({ id: "early", sent_at: "2026-07-01T23:30:00Z" }),
  ];
  const onAnchorChange = vi.fn();
  render(<ThreadTranscript threadId="thr_1" order="history" timezone="America/New_York" onAnchorChange={onAnchorChange} />);
  const day = formatDayLabel("2026-07-02T00:30:00Z", { timeZone: "America/New_York" });
  expect(screen.getAllByText(day)).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("Jump to date"), { target: { value: "2026-07-01" } });
  expect(onAnchorChange).toHaveBeenCalledWith("date:2026-07-01@America/New_York");
});
