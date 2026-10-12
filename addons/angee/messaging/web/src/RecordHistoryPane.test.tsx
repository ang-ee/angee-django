// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { ChatterViewContext } from "@angee/ui/runtime";

const mocks = vi.hoisted(() => ({
  threadData: undefined as unknown,
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

import { RecordHistoryPane } from "./RecordHistoryPane";

function context(sqid?: string): ChatterViewContext {
  return {
    route: { modelLabel: "tests.Record" },
    view: sqid ? { kind: "record", sqid } : { kind: "list" },
  } as unknown as ChatterViewContext;
}

function update(id: string, preview: string) {
  return {
    id,
    preview,
    sent_at: "2026-07-06T12:00:00Z",
    created_at: "2026-07-06T12:00:00Z",
    sender: { display_name: "Grace Hopper", value: "grace" },
    tracking_values: [{ id: `${id}-t`, position: 0, field_label: "Status", old_display: "Open", new_display: "Closed" }],
  };
}

beforeEach(() => {
  mocks.threadData = undefined;
  mocks.useAuthoredQuery.mockImplementation(() => ({ data: mocks.threadData, isFetching: false, error: null }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RecordHistoryPane", () => {
  test("renders the record's retained changes", () => {
    mocks.threadData = {
      record_thread: { error: null, error_code: null, message_result_count: 1, messages: [update("m1", "Closed it")] },
    };
    render(<RecordHistoryPane context={context("rec_1")} />);
    expect(screen.getByText("Record history")).toBeTruthy();
    expect(screen.getByText("Closed it")).toBeTruthy();
    expect(screen.getByText("Status")).toBeTruthy();
    expect(screen.queryByText("Schedule")).toBeNull();
  });

  test("notes a truncated history", () => {
    mocks.threadData = {
      record_thread: { error: null, error_code: null, message_result_count: 3, messages: [update("m1", "Closed it")] },
    };
    render(<RecordHistoryPane context={context("rec_1")} />);
    expect(screen.getByText("Showing the latest 1 updates.")).toBeTruthy();
  });

  test("shows the empty state when nothing changed yet", () => {
    mocks.threadData = { record_thread: { error: null, error_code: null, message_result_count: 0, messages: [] } };
    render(<RecordHistoryPane context={context("rec_1")} />);
    expect(screen.getByText("No changes yet")).toBeTruthy();
  });

  test("shows the not-enabled state for a record without a thread", () => {
    mocks.threadData = { record_thread: { error: "no chatter", error_code: "BAD_RECORD", messages: [] } };
    render(<RecordHistoryPane context={context("rec_1")} />);
    expect(screen.getByText("History is not enabled")).toBeTruthy();
  });

  test("asks for a record outside a record view", () => {
    render(<RecordHistoryPane context={context()} />);
    expect(screen.getByText("No record selected")).toBeTruthy();
  });
});
