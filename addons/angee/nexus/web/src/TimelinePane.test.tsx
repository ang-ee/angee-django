// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  pageParam: undefined as ((rows: unknown[], data: unknown) => unknown) | undefined,
  variables: {} as Record<string, unknown>,
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredInfiniteQuery: (_document: unknown, variables: Record<string, unknown>, options: {
    getPageParam: (rows: unknown[], data: unknown) => unknown;
  }) => {
    mocks.variables = variables;
    mocks.pageParam = options.getPageParam;
    return {
      rows: [], data: undefined, isFetching: false, error: null,
      hasNextPage: false, isFetchingNextPage: false,
    };
  },
}));

import { TimelinePane } from "./TimelinePane";

afterEach(cleanup);

describe("TimelinePane server continuation", () => {
  test.each(["party", "circle"] as const)("uses the %s feed's cursor without deriving an anchor ID", (kind) => {
    render(kind === "party" ? <TimelinePane partyId="root" /> : <TimelinePane circleId="root" />);
    const field = `${kind}_message_feed`;
    const rows = [{ id: "not-a-cursor" }];
    expect(mocks.variables).toMatchObject({ beforeCursor: null, circle: kind === "circle" });
    expect(mocks.pageParam?.(rows, { [field]: {
      messages: rows, older_cursor: "signed-position", has_older: true,
    } })).toEqual({ beforeCursor: "signed-position" });
    expect(mocks.pageParam?.(rows, { [field]: {
      messages: rows, older_cursor: "signed-position", has_older: false,
    } })).toBeUndefined();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
