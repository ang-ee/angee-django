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
  transcriptData: undefined as unknown,
  queryCalls: [] as Array<Record<string, unknown>>,
  useAuthoredQuery: vi.fn(),
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

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: mocks.useAuthoredQuery,
}));

import { ThreadTranscript } from "./ThreadTranscript";

function message(overrides: Partial<ThreadTranscriptRow> = {}): ThreadTranscriptRow {
  return {
    id: "msg_1",
    direction: "INBOUND",
    subject: "Re: hello",
    preview: "Hi there",
    message_type: "EMAIL",
    sent_at: "2026-07-01T10:00:00Z",
    created_at: "2026-07-01T10:00:00Z",
    sender: { id: "hnd_1", display_name: "Ada Lovelace", value: "ada@example.com" },
    parts: [{ role: "body", fragment: { text: "Hi there" }, file: null }],
    reaction_groups: [],
    ...overrides,
  } as unknown as ThreadTranscriptRow;
}

function transcriptPayload(rows: ThreadTranscriptRow[]): unknown {
  // The document returns the window newest-first; the view reverses it.
  return {
    messages: [...rows].reverse(),
    messages_aggregate: { aggregate: { count: rows.length } },
  };
}

beforeEach(() => {
  mocks.queryCalls = [];
  mocks.useAuthoredQuery.mockReset();
  mocks.useAuthoredQuery.mockImplementation(
    (_document: unknown, variables: Record<string, unknown>) => {
      mocks.queryCalls.push(variables);
      return { data: mocks.transcriptData, fetching: false, error: null, refetch: vi.fn() };
    },
  );
});

afterEach(cleanup);

describe("ThreadTranscript", () => {
  test("renders inbound, outbound, and internal turns with their distinct treatments", () => {
    mocks.transcriptData = transcriptPayload([
      message({ id: "in", direction: "INBOUND", parts: [{ role: "body", fragment: { text: "Inbound hello" }, file: null }] as never }),
      message({ id: "out", direction: "OUTBOUND", sender: { id: "hnd_2", display_name: "Support", value: "us@example.com" }, parts: [{ role: "body", fragment: { text: "Outbound reply" }, file: null }] as never }),
      message({ id: "note", direction: "INTERNAL", parts: [{ role: "body", fragment: { text: "Internal jotting" }, file: null }] as never }),
    ]);

    render(<ThreadTranscript threadId="thr_1" />);

    expect(screen.getByText("Inbound hello")).toBeTruthy();
    expect(screen.getByText("Outbound reply")).toBeTruthy();
    expect(screen.getByText("Internal jotting")).toBeTruthy();
    // The internal note carries its distinct label; inbound/outbound do not.
    expect(screen.getByText("Internal note")).toBeTruthy();
  });

  test("renders read-only reaction pills from reaction groups", () => {
    mocks.transcriptData = transcriptPayload([
      message({
        reaction_groups: [
          { reaction: "👍", count: 2, self_reacted: true, handles: [{ id: "h", display_name: "Ada", value: "ada" }] },
        ] as never,
      }),
    ]);

    render(<ThreadTranscript threadId="thr_1" />);

    expect(screen.getByRole("button", { name: "👍 reaction, 2" })).toBeTruthy();
  });

  test("offers Load older only while the window is short of the thread total, growing the limit", () => {
    mocks.transcriptData = {
      messages: [message()],
      messages_aggregate: { aggregate: { count: 120 } },
    };

    render(<ThreadTranscript threadId="thr_1" />);

    expect(mocks.queryCalls.at(-1)).toMatchObject({ threadId: "thr_1", limit: 50 });
    fireEvent.click(screen.getByRole("button", { name: "Load older messages" }));
    expect(mocks.queryCalls.at(-1)).toMatchObject({ threadId: "thr_1", limit: 100 });
  });

  test("shows the empty state when the thread has no messages", () => {
    mocks.transcriptData = transcriptPayload([]);

    render(<ThreadTranscript threadId="thr_1" />);

    expect(screen.getByText("No messages yet")).toBeTruthy();
  });
});
