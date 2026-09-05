// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

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
  }),
}));

const mocks = vi.hoisted(() => ({
  transcriptRows: [] as unknown[],
  total: 0,
  hasMore: false,
  queryCalls: [] as Array<Record<string, unknown>>,
  fetchOlder: vi.fn(),
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
      (key: string) =>
        messages[key] ?? key,
  };
});

vi.mock("./thread-message-feed", () => ({ useThreadMessageFeed: mocks.useThreadMessageFeed }));

import { ThreadTranscript } from "./ThreadTranscript";

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
  mocks.useThreadMessageFeed.mockReset();
  mocks.error = null;
  mocks.fetching = false;
  mocks.useThreadMessageFeed.mockImplementation((threadId: string) => {
    mocks.queryCalls.push({ threadId });
    return {
      data: { pages: [{ messages: mocks.transcriptRows, count: mocks.total }], pageParams: [null] },
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
  test("renders inbound, outbound, and internal turns with their distinct treatments", () => {
    mocks.transcriptRows = [
      message({ id: "in", direction: "INBOUND", parts: [{ role: "BODY", fragment: { text: "Inbound hello" }, file: null }] as never }),
      message({ id: "out", direction: "OUTBOUND", sender: { id: "hnd_2", display_name: "Support", value: "us@example.com", party_link_confirmed: false, party: null }, parts: [{ role: "BODY", fragment: { text: "Outbound reply" }, file: null }] as never }),
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
    const items = screen.getAllByRole("listitem");
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
