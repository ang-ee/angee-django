// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  useTimelineMessageFeed: vi.fn(),
  older: vi.fn(),
  error: null as Error | null,
  fetching: false,
}));

vi.mock("./timeline-message-feed", () => ({ useTimelineMessageFeed: mocks.useTimelineMessageFeed }));

import { TimelinePane } from "./TimelinePane";

beforeEach(() => {
  mocks.error = null; mocks.fetching = false; mocks.older.mockReset();
  mocks.useTimelineMessageFeed.mockImplementation(() => ({
    data: { pages: [{ count: 2, messages: [{
      id: "message", feed_order_key: "v1:1", preview: "Retained message", sender: null,
    }] }], pageParams: [null] },
    isFetching: mocks.fetching, error: mocks.error, hasNextPage: true, fetchNextPage: mocks.older,
  }));
});
afterEach(cleanup);

describe("TimelinePane", () => {
  test.each(["party", "circle"] as const)("binds the %s scope to the native feed", (kind) => {
    render(kind === "party" ? <TimelinePane partyId="root" /> : <TimelinePane circleId="root" />);
    expect(mocks.useTimelineMessageFeed).toHaveBeenCalledWith("root", kind === "circle");
    expect(screen.getByText("Retained message")).toBeTruthy();
    fireEvent.click(screen.getByRole("button"));
    expect(mocks.older).toHaveBeenCalledWith({ cancelRefetch: false });
  });

  test("hides retained messages on refetch failure", () => {
    mocks.error = new Error("Unreadable party");
    render(<TimelinePane partyId="root" />);
    expect(screen.getByText("Unreadable party")).toBeTruthy();
    expect(screen.queryByText("Retained message")).toBeNull();
  });

  test("disables older loading while the feed is refreshing", () => {
    mocks.fetching = true;
    render(<TimelinePane circleId="root" />);
    expect((screen.getByRole("button") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button"));
    expect(mocks.older).not.toHaveBeenCalled();
  });
});
